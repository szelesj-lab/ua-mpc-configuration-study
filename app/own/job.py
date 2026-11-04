"""One own-data simulation, run in its own process by app/own/runner.py.

    python -m app.own.job <jobdir>

<jobdir>/params.json   mode ("quick" | "screen"), period ("final_test" or a list of months YYYY-MM), configs,
                       controllers, sizes (site units), initial_soc, workers
<jobdir>/load.pkl      the checked load (kW, 15-min UTC grid) from app/own/profile.read
<jobdir>/upload.json   the check report (mean load)

Quick mode: every month with the GP trained before it (walk-forward) and sigma calibrated on its held-out tail;
the controllers with the reference weights tuned for each configuration (results/summer/<X>); DET-BO with
the reference tuned back-off and, in A and B, a DET-BO whose back-off is re-sized at the start of every month from
the r_E of the month before, b = r_E_past (x_max - x_min) / 0.75 (the rule of test T1). The SoC carries over
from month to month. Screen mode: only r_E and Gamma per month and configuration, no controller runs.

Writes status.json (stage), progress/<X>_<controller>.jsonl (one line per MPC solve), runs/<X>/<ctrl>_<m>.json
and .csv, months.json, and at the end report.json / report.md (app/own/report.py).
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

PKG = Path(__file__).resolve().parents[2]
CODE = PKG / "code"
SUMMER = PKG / "results" / "summer"
ARCH = {"A": "renewable_buffer", "B": "self_consumption", "C": "setpoint"}
GAMMA_TARGET = 0.75
REPLAN = 4
SOLAR = ("solar_mu", "solar_sd", "solar_real", "solar_base")
WIND = ("wind_mu", "wind_sd", "wind_real", "wind_base")


def _code():
    if str(CODE / "src") not in sys.path:
        sys.path.insert(0, str(CODE / "src"))


def point(X: str, ctrl: str) -> dict:
    """Overrides of the controller selected by the summer tuning protocol for configuration X (reference weights)."""
    reg = json.loads((SUMMER / X / "final_test" / "preregistration.json").read_text())["selected"]["primary_threshold_pp_h"]
    p = json.loads((SUMMER / X / "tuning" / f"{ctrl}_pareto.json").read_text())
    return next(x["overrides"] for x in p["points"] if x["trial"] == reg[ctrl]["trial"])


def has_det_bo(X: str) -> bool:
    return (SUMMER / X / "tuning" / "det_bo_pareto.json").exists()


def cfg_for(X: str, cell: dict, cal: dict):
    _code()
    from replay import load_config, with_overrides
    p = cell["p_max_w"]
    scen = {"SoC": {"E_capacity": cell["E_capacity"]}, "degradation": {"c_deg": 2e-5},
            "limits": {"p_charge_max_w": p, "p_discharge_max_w": p},
            "replay": {"plant": "bms", "replan_steps": REPLAN},
            "mpc": {"architecture": ARCH[X], "soc_recovery": True, "sigma_scale": cal["sigma_scale"],
                    "error_autocorr": cal["error_autocorr_step"]}}
    return with_overrides(load_config(), scen)


def r_E(d: pd.DataFrame, cfg) -> float:
    """Share of the forecast error that reaches the battery: 95 % quantile of the error accumulated over one
    re-planning interval, routed as the configuration routes it, over the usable capacity (stages.indicators_gen)."""
    _code()
    from MPC_costfunction import _mpc_params
    p = _mpc_params(cfg)
    if len(d) < REPLAN:
        return float("nan")
    real = d.solar_real + d.wind_real
    mu = d.solar_mu + d.wind_mu
    e = mu - real
    usable = p["e_cap"] * (p["soc_max"] - p["soc_min"])
    if p["architecture"] == "renewable_buffer":
        x = np.minimum(e.clip(lower=0), np.minimum(mu.clip(lower=0), p["p_ch_max"]))
    elif p["architecture"] == "self_consumption":
        lim = p["p_ch_max"] + p["p_dis_max"]
        x = e.clip(-lim, lim)
    else:
        return 0.0
    deficit = (x.rolling(REPLAN).sum() * 0.25).clip(lower=0)
    return float(deficit.quantile(0.95)) / usable if usable > 0 else float("nan")


def band(cfg) -> float:
    _code()
    from MPC_costfunction import _mpc_params
    p = _mpc_params(cfg)
    return p["soc_max"] - p["soc_min"]


class Job:
    def __init__(self, d: Path):
        self.d = d
        self.p = json.loads((d / "params.json").read_text())
        self.up = json.loads((d / "upload.json").read_text())
        from app.own.profile import sizes_to_cell
        self.cell = sizes_to_cell(self.p["sizes"], self.up.get("scale_mean_kw", self.up["mean_kw"]))
        self.months = ["final_test"] if self.p["period"] == "final_test" else list(self.p["period"])

    def status(self, stage, **kw):
        s = {"stage": stage, "time": time.time(), **kw}
        (self.d / "status.json").write_text(json.dumps(s, default=str))
        with open(self.d / "events.log", "a") as f:
            f.write(time.strftime("%H:%M:%S ") + stage + (" " + json.dumps(kw, default=str) if kw else "") + "\n")

    def prepare_months(self):
        from app.own import bundles
        from app.own.profile import map_to
        load = pd.read_pickle(self.d / "load.pkl")
        valid = load.dropna().index
        only_data = self.p.get("fill", "profile") == "data"
        (self.d / "data").mkdir(exist_ok=True)
        info = []
        for i, m in enumerate(self.months):
            self.status("forecasts", month=m, i=i + 1, n=len(self.months))
            bundles.build(m, self.d / "bundles.log", lambda s: self.status("forecasts", month=m, i=i + 1, n=len(self.months), detail=s))
            data, cal = bundles.load(m)
            d = data.copy()
            for c in SOLAR:
                d[c] = d[c] * self.cell["pv_scale"]
            for c in WIND:
                d[c] = d[c] * self.cell["wind_scale"]
            kw, share = map_to(d.index, load)
            d["demand_w"] = kw * 1000.0 * self.cell["factor"]
            d.to_pickle(self.d / "data" / f"{m}.pkl")
            t0, t1 = pd.Timestamp(cal["test_start"]), pd.Timestamp(cal["test_end"])
            if only_data:   # only the days the data cover: the month is cut to the first and last valid step
                t0, t1 = max(t0, valid[0]), min(t1, valid[-1] + pd.Timedelta(minutes=15))
                if t1 <= t0:
                    self.status("forecasts", month=m, detail="no load data in this month, skipped")
                    continue
                t0 = d.index[d.index.searchsorted(t0)]
            test = d[(d.index >= t0) & (d.index < t1)]
            p0, p1 = (pd.Timestamp(x) for x in cal["past_window"])
            past = d[(d.index >= p0) & (d.index < p1)]
            rec = {"month": m, "test_start": str(t0), "test_end": str(t1), "steps": int(len(test)),
                   "sigma_scale": cal["sigma_scale"], "error_autocorr": cal["error_autocorr_step"],
                   "forecast_test": cal.get("test_month_forecast"), "past_label": cal.get("past_label"),
                   "load_direct_pct": 100 * share, "price_mean_eur_mwh": float(test.price_eur_mwh.mean()),
                   "renewable_kwh_cell": float((test.solar_real + test.wind_real).sum() * 0.25 / 1000),
                   "demand_kwh_cell": float(test.demand_w.sum() * 0.25 / 1000), "rE": {}, "rE_past_window": {}}
            for X in self.p["configs"]:
                cfg = cfg_for(X, self.cell, cal)
                rec["rE"][X] = r_E(test, cfg)
                rec["rE_past_window"][X] = r_E(past, cfg)
            info.append(rec)
        if not info:
            raise ValueError("the load data cover none of the chosen months")
        # the back-off of the re-sized DET-BO: r_E of the month before (the first month: the window before it)
        for k, rec in enumerate(info):
            rec["rE_past"], rec["b_sized"] = {}, {}
            for X in self.p["configs"]:
                rp = info[k - 1]["rE"][X] if k > 0 else rec["rE_past_window"][X]
                rec["rE_past"][X] = rp
                rec["b_sized"][X] = rp * 0.8 / GAMMA_TARGET if ARCH[X] != "setpoint" else 0.0
        (self.d / "months.json").write_text(json.dumps(info, indent=1, default=float))
        return info

    def chains(self) -> list[tuple]:
        out = []
        for X in self.p["configs"]:
            out.append((X, "grid_only"))
            for c in self.p["controllers"]:
                if c in ("det_bo", "det_bo_sized") and not has_det_bo(X):
                    continue
                out.append((X, c))
        return sorted(out, key=lambda t: (t[1] == "grid_only", t[1] not in ("ua", "det_bo_sized")))

    def run(self):
        self.status("starting")
        info = self.prepare_months()
        if self.p["mode"] != "screen":
            ch = self.chains()
            exp = {f"{X}_{c}": (0 if c == "grid_only" else sum(math.ceil(r["steps"] / REPLAN) for r in info)) for X, c in ch}
            (self.d / "expected.json").write_text(json.dumps(exp))
            self.status("simulating", chains=len(ch))
            w = max(1, int(self.p.get("workers") or os.cpu_count() or 1))
            s0 = self.p.get("initial_soc", 0.5)
            args = [(str(self.d), X, c, info, self.cell, float(s0.get(f"{X}_{c}", 0.5) if isinstance(s0, dict) else s0))
                    for X, c in ch]
            with ProcessPoolExecutor(w) as ex:
                for _ in ex.map(run_chain, args):
                    pass
        from app.own.report import write
        self.status("report")
        write(self.d)
        self.status("done")


def _hook(path: Path, month: str):
    import MPC_costfunction
    orig = getattr(MPC_costfunction, "_orig_optimize", MPC_costfunction.optimize)
    MPC_costfunction._orig_optimize = orig
    f = open(path, "a", buffering=1)

    def optimize(h, cfg, x_0=None, init=None):
        f.write(json.dumps({"m": month, "soc": None if x_0 is None else round(float(x_0), 4), "wall": round(time.time(), 1)}) + "\n")
        return orig(h, cfg, x_0=x_0, init=init)
    MPC_costfunction.optimize = optimize
    import replay  # simulate imports optimize at call time from MPC_costfunction
    return replay


def run_chain(a):
    jd, X, ctrl, info, cell, soc0 = a
    jd = Path(jd)
    import warnings
    warnings.filterwarnings("ignore")
    for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ[k] = "1"
    _code()
    from replay import metrics, simulate, with_overrides
    out = jd / "runs" / X
    out.mkdir(parents=True, exist_ok=True)
    (jd / "progress").mkdir(exist_ok=True)
    soc = soc0
    try:
        for rec in info:
            m = rec["month"]
            tag = out / f"{ctrl}_{m}"
            if (tag.with_suffix(".json")).exists():
                soc = json.loads(tag.with_suffix(".json").read_text())["soc_end"]
                continue
            _hook(jd / "progress" / f"{X}_{ctrl}.jsonl", m)
            data = pd.read_pickle(jd / "data" / f"{m}.pkl")
            cal = {"sigma_scale": rec["sigma_scale"], "error_autocorr_step": rec["error_autocorr"]}
            cfg = cfg_for(X, cell, cal)
            b = None
            if ctrl != "grid_only":
                ov = point(X, "det_bo" if ctrl == "det_bo_sized" else ctrl)
                if ctrl == "det_bo_sized":
                    b = rec["b_sized"][X]
                    ov = {**ov, "mpc": {**ov.get("mpc", {}), "soc_backoff": b}}
                cfg = with_overrides(cfg, ov)
                if ctrl == "det_bo":
                    b = float(ov["mpc"]["soc_backoff"])
            t = time.perf_counter()
            df, st = simulate(data, cfg, "det_bo" if ctrl == "det_bo_sized" else ctrl,
                              pd.Timestamp(rec["test_start"]), pd.Timestamp(rec["test_end"]), soc0=soc)
            mt = metrics(df, st, cfg)
            soc = float(df["soc"].iloc[-1])
            df.to_csv(tag.with_suffix(".csv"))
            json.dump({"config": X, "controller": ctrl, "month": m, "start": rec["test_start"], "end": rec["test_end"],
                       "b": b, "soc0": float(df.attrs["soc0"]), "soc_end": soc, "seconds": time.perf_counter() - t,
                       "metrics": mt}, open(tag.with_suffix(".json"), "w"), default=float, indent=1)
    except Exception:
        (out / f"{ctrl}.error.txt").write_text(traceback.format_exc())
        raise
    return X, ctrl


if __name__ == "__main__":
    j = Job(Path(sys.argv[1]))
    try:
        j.run()
    except Exception as e:
        j.status("failed", error=str(e), trace=traceback.format_exc()[-3000:])
        raise
