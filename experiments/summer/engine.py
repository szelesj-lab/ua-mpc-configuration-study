"""Night pipeline engine: a job = one closed-loop replay; resumable (skips jobs whose .json exists)."""
import json, os, sys, time, warnings
warnings.filterwarnings("ignore")
ROOT = os.environ.get("KAIROS_ROOT", os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "uncertainty-aware-mpc")))
sys.path.insert(0, os.path.join(ROOT, "src"))
import numpy as np, pandas as pd
from concurrent.futures import ProcessPoolExecutor, as_completed
from replay import build_data, named_periods, simulate, metrics, with_overrides, load_config, TUNING_DIR

PAPER = os.environ.get("STUDY_RESULTS", os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "results")))
# SOC_RECOVERY=0 reproduces the runs made before mpc.soc_recovery existed (see docs/protocol.md)
SCEN = {"SoC": {"E_capacity": 72}, "degradation": {"c_deg": 2e-5},
        "mpc": {"soc_recovery": os.environ.get("SOC_RECOVERY", "1") == "1"}}
# scenario extension from the environment (KAIROS_SCEN = JSON, merged per section)
for _k, _v in json.loads(os.environ.get("KAIROS_SCEN", "{}")).items():
    SCEN.setdefault(_k, {}).update(_v)

def point(ctrl, trial):
    p = json.loads((TUNING_DIR / f"{ctrl}_pareto.json").read_text())
    return next(x["overrides"] for x in p["points"] if x["trial"] == trial)

def scale_errors(data, k):
    """Synthetic forecast-error level: mean error and std multiplied by k (k = 1: the real forecast)."""
    d = data.copy()
    for s in ("solar", "wind"):
        d[f"{s}_mu"] = d[f"{s}_real"] + k * (d[f"{s}_mu"] - d[f"{s}_real"])
        d[f"{s}_sd"] = k * d[f"{s}_sd"]
    return d

def reshape_sigma(data, mode, t0, t1):
    """Same mean forecast std over the period, different variability in time.
    mode "constant": sigma_k = its mean over the period (kappa_sigma = 0);
    mode "amplified": sigma_k = max(mean + 2 (sigma_k - mean), 0) (variability doubled)."""
    d = data.copy()
    tot = np.sqrt(d.solar_sd ** 2 + d.wind_sd ** 2)
    m = float(tot.loc[t0:t1].mean())
    new = np.full(len(tot), m) if mode == "constant" else np.clip(m + 2 * (tot - m), 0, None)
    f = np.where(tot > 0, new / np.where(tot > 0, tot, 1), 0.0)
    if mode == "constant":
        f = np.where(tot > 0, f, 0.0)
        d["wind_sd"] = np.where(tot > 0, d.wind_sd * f, m)   # steps without any std get it on the wind channel
        d["solar_sd"] = np.where(tot > 0, d.solar_sd * f, 0.0)
    else:
        d["solar_sd"] = d.solar_sd * f
        d["wind_sd"] = d.wind_sd * f
    return d


def _job(a):
    out, tag, ctrl, data, cfg, t0, t1, meta = a
    t = time.perf_counter()
    df, st = simulate(data, cfg, ctrl, t0, t1)
    df.to_csv(f"{out}/steps/{tag}.csv")
    pd.DataFrame(st).to_csv(f"{out}/steps/{tag}_solves.csv", index=False)
    m = metrics(df, st, cfg)
    json.dump({**meta, "metrics": m}, open(f"{out}/steps/{tag}.json", "w"), default=float, indent=1)
    return tag, m, time.perf_counter() - t

def run(out, specs, period="development", workers=2, base_extra=None, log=None):
    """specs: list of dict(tag, controller, overrides, plant='physical', error_scale=1, extra={})"""
    os.makedirs(f"{out}/steps", exist_ok=True)
    base = with_overrides(with_overrides(load_config(), SCEN), base_extra or {})
    data = build_data(base)
    t0, t1 = named_periods(data, base)[period]
    cache, jobs = {}, []
    for s in specs:
        if os.path.exists(f"{out}/steps/{s['tag']}.json"):
            continue
        k = (s.get("error_scale", 1.0), s.get("sigma_shape", ""))
        if k not in cache:
            dk = data if k[0] == 1.0 else scale_errors(data, k[0])
            cache[k] = reshape_sigma(dk, k[1], t0, t1) if k[1] else dk
        cfg = with_overrides(with_overrides(base, s.get("overrides") or {}),
                             with_overrides({"replay": {"plant": s.get("plant", SCEN.get("replay", {}).get("plant", "physical"))}}, s.get("extra") or {}))
        meta = {k2: v for k2, v in s.items()}
        meta.update(period=period, start=t0.isoformat(), end=t1.isoformat())
        jobs.append((out, s["tag"], s["controller"], cache[k], cfg, t0, t1, meta))
    print(f"[{time.strftime('%H:%M')}] {out}: {len(jobs)} of {len(specs)} runs to do ({period})", flush=True)
    if log and jobs:
        with open(log, "a") as f:
            f.write(f"{pd.Timestamp.now(tz='UTC').isoformat()} night pipeline: {[j[1] for j in jobs]}\n")
    with ProcessPoolExecutor(workers) as ex:
        for f in as_completed([ex.submit(_job, j) for j in jobs]):
            tag, m, s = f.result()
            print(f"[{time.strftime('%H:%M')}] done {tag:<28} net {m['net_cost_eur']:.4f} viol {m['violation_pp_h']:.4f} "
                  f"prot {m.get('protection_steps', 0)} solver {m.get('solver_success_pct', 100):.1f}% {s:.0f}s", flush=True)

def daily(path, soc_min=0.10, soc_max=0.90, tz="Europe/Budapest"):
    s = pd.read_csv(path); s.index = pd.to_datetime(s.t, utc=True)
    s["viol"] = 100 * ((soc_min - s.soc).clip(lower=0) + (s.soc - soc_max).clip(lower=0)) * 0.25
    s["cost"] = s.grid_eur + s.wear_eur
    s["prot"] = ((s.cut_discharge_w > 1e-3) | (s.cut_charge_w > 1e-3)).astype(float)
    s["extra"] = s.cut_discharge_w.clip(lower=0) * s.price_eur_wh * 0.25
    return s.groupby(s.index.tz_convert(tz).date).agg(cost=("cost", "sum"), viol=("viol", "sum"), prot=("prot", "sum"),
                                                      extra=("extra", "sum"), n=("soc", "size"))

def paired(da, db, kpis, B=10000, BL=5, seed=42):
    """Paired circular block bootstrap of daily a − b; totals over the full days."""
    full = (da.n.values >= 92) & (db.n.values >= 92); n = int(full.sum())
    rng = np.random.default_rng(seed); k = int(np.ceil(n / BL))
    idx = ((rng.integers(0, n, (B, k))[:, :, None] + np.arange(BL)) % n).reshape(B, -1)[:, :n]
    rows = []
    for c in kpis:
        x = da[c].values[full] - db[c].values[full]; m = x[idx].mean(1); bt = db[c].values[full].sum()
        rows.append(dict(kpi=c, a_total=da[c].values[full].sum(), b_total=bt, diff_total=x.sum(),
                         diff_pct=100 * x.sum() / bt if bt else np.nan, lo_total=np.percentile(m, 2.5) * n,
                         hi_total=np.percentile(m, 97.5) * n, p_a_better=float((m < 0).mean()), days=n))
    return rows
