"""Seasonal re-selection, configuration B (post hoc, outside the protocol).
select <ctrl> [max_minutes]: replay every development-front point of <ctrl> on November 2025 (walk-forward model m2)
test: apply the summer rule scaled to the month (5 h per 22 days) per controller, replay the selected points on
      December 2025 - March 2026 (m3-m6), SoC carried over, starting from the summer chain's SoC at the end of m2."""
import os as _os  # paths: see experiments/extended/README.md
UA_WORK = _os.environ.get("UA_WORK", "work/extended")      # working tree (layout in README)
UA_CODE = _os.environ.get("UA_CODE", "code")               # controller code (Kairos)
UA_KAIROS = _os.environ.get("UA_KAIROS", "work")           # work copies made by experiments/make_work.sh
import json, os, sys, time
sys.path.insert(0, UA_CODE + "/src")
import warnings; warnings.filterwarnings("ignore")
import pandas as pd
from concurrent.futures import ProcessPoolExecutor, as_completed
from replay import load_config, metrics, simulate, with_overrides
S = UA_WORK; W = UA_KAIROS + "/W"
O = f"{S}/rev3/season"; os.makedirs(f"{O}/select", exist_ok=True); os.makedirs(f"{O}/test", exist_ok=True)
SCEN = {"SoC": {"E_capacity": 72}, "degradation": {"c_deg": 2e-5}, "limits": {"p_charge_max_w": 24.0},
        "replay": {"plant": "bms", "demand_profile_csv": "industrial_load_profile.csv", "demand_mean_w": 10.788},
        "mpc": {"architecture": "self_consumption", "soc_recovery": True}}
FR = pd.read_csv(f"{S}/ind/results_B/dev_fronts/fronts_dev.csv")
def ov(ctrl, trial):
    p = json.load(open(UA_KAIROS + f"/B/results/tuning/{ctrl}_pareto.json"))["points"]
    return next(x["overrides"] for x in p if x["trial"] == trial)
def month(m):
    cal = json.load(open(f"{W}/m{m}/results/rewind/calibration.json"))
    return cal, pd.read_pickle(f"{W}/m{m}/results/rewind/data.pkl")
def run(a):
    ctrl, trial, m, soc0, out = a
    cal, data = month(m)
    cfg = with_overrides(load_config(), SCEN)
    cfg = with_overrides(cfg, {"mpc": {**SCEN["mpc"], "sigma_scale": cal["sigma_scale"], "error_autocorr": cal["error_autocorr_step"]}})
    cfg = with_overrides(cfg, ov(ctrl, trial))
    t = time.perf_counter()
    df, st = simulate(data, cfg, ctrl, pd.Timestamp(cal["test_start"]), pd.Timestamp(cal["test_end"]), soc0=soc0)
    mt = metrics(df, st, cfg)
    df.to_csv(f"{out}.csv"); pd.DataFrame(st).to_csv(f"{out}_solves.csv", index=False)
    json.dump({"ctrl": ctrl, "trial": trial, "month": m, "soc0": soc0, "soc_end": float(df.soc.iloc[-1]), "metrics": mt},
              open(f"{out}.json", "w"), default=float, indent=1)
    return out, mt, time.perf_counter() - t
if sys.argv[1] == "select":
    ctrl = sys.argv[2]; limit = float(sys.argv[3]) if len(sys.argv) > 3 else 1e9; t_start = time.time()
    jobs = [(ctrl, int(tr), 2, 0.5, f"{O}/select/{ctrl}_t{int(tr):02d}") for tr in FR[FR.ctrl == ctrl].trial
            if not os.path.exists(f"{O}/select/{ctrl}_t{int(tr):02d}.json")]
    print(f"[{time.strftime('%H:%M')}] select {ctrl}: {len(jobs)} runs", flush=True)
    with ProcessPoolExecutor(2) as ex:
        futs = []
        for j in jobs:
            futs.append(ex.submit(run, j))
        for f in as_completed(futs):
            out, mt, s = f.result()
            print(f"[{time.strftime('%H:%M')}] done {os.path.basename(out)} net {mt['net_cost_eur']:.4f} prot_h {mt['protection_h']:.2f} {s:.0f}s", flush=True)
else:
    rows = []
    for f in sorted(os.listdir(f"{O}/select")):
        if f.endswith(".json") and not f.endswith("_solves.json"):
            j = json.load(open(f"{O}/select/{f}")); rows.append(dict(ctrl=j["ctrl"], trial=j["trial"], cost=j["metrics"]["net_cost_eur"],
                                                                     prot=j["metrics"]["protection_h"], days=j["metrics"]["days"]))
    r = pd.DataFrame(rows); thr = 5.0 * r.days.iloc[0] / 22.0; sel = {}
    for c, g in r.groupby("ctrl"):
        ok = g[g.prot <= thr]; x = ok.sort_values("cost").iloc[0] if len(ok) else g.sort_values("prot").iloc[0]
        sel[c] = {"trial": int(x.trial), "nov_cost": float(x.cost), "nov_prot_h": float(x.prot), "met": bool(len(ok))}
    json.dump({"threshold_h": thr, "selected": sel, "candidates": rows}, open(f"{O}/reselection.json", "w"), indent=1, default=float)
    print(json.dumps(sel, indent=1))
    def chain(c):
        soc = json.load(open(f"{S}/rw/results/B/{c}_m2.json"))["soc_end"]; log = []
        for m in (3, 4, 5, 6):
            out = f"{O}/test/{c}_m{m}"
            if not os.path.exists(f"{out}.json"):
                _, mt, s = run((c, sel[c]["trial"], m, soc, out)); log.append(f"{c} m{m} net {mt['net_cost_eur']:.4f} prot {mt['protection_h']:.2f} {s:.0f}s")
            soc = json.load(open(f"{out}.json"))["soc_end"]
        return log
    with ProcessPoolExecutor(2) as ex:
        for f in as_completed([ex.submit(chain, c) for c in sel]):
            print("\n".join(f.result()), flush=True)
