"""Walk-forward winter test: the pre-registered controllers of configurations B and C replayed month by month
on 2025-10 .. 2026-03, each month with the GPR trained before it and the sigma calibrated on that model's own
held-out tail (prep_month.py). The SoC carries over from one month to the next. Resumable per month.

    python run_rewind.py            # all chains
    python run_rewind.py stats      # tables
"""
import glob
import json
import os
import sys
import time
import warnings
from concurrent.futures import ProcessPoolExecutor

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.abspath(os.path.join(HERE, "..", ".."))
KROOT = os.environ.get("KAIROS_CODE", os.path.join(PKG, "code"))
SUMMER = os.environ.get("SUMMER_RESULTS", os.path.join(PKG, "results", "summer"))
sys.path.insert(0, f"{KROOT}/src")
import pandas as pd  # noqa: E402
from replay import load_config, metrics, simulate, with_overrides  # noqa: E402

W = os.path.join(os.environ.get("WORK", os.path.join(PKG, "work")), "W")
MONTHS = [f"m{i}" for i in range(1, 7)]
OUT = os.environ.get("WINTER_RESULTS", os.path.join(PKG, "results", "winter"))
ARCH = {"B": "self_consumption", "C": "setpoint"}
SCEN = {"SoC": {"E_capacity": 72}, "degradation": {"c_deg": 2e-5}, "limits": {"p_charge_max_w": 24.0},
        "replay": {"plant": "bms", "demand_profile_csv": "industrial_load_profile.csv", "demand_mean_w": 10.788}}


def point(cfg_letter, ctrl):
    reg = json.load(open(f"{SUMMER}/{cfg_letter}/final_test/preregistration.json"))
    trial = reg["selected"]["primary_threshold_pp_h"][ctrl]["trial"]
    p = json.load(open(f"{SUMMER}/{cfg_letter}/tuning/{ctrl}_pareto.json"))
    return next(x["overrides"] for x in p["points"] if x["trial"] == trial), trial


def chains():
    out = []
    for X in ("B", "C"):
        ctrls = ["grid_only", "det", "ua"] + (["det_bo"] if X == "B" else [])
        for c in ctrls:
            out.append((X, c))
    # the slow ones first
    return sorted(out, key=lambda t: (t[1] == "grid_only", t[1] != "ua"))


def run_chain(arg):
    X, ctrl = arg
    os.makedirs(f"{OUT}/{X}", exist_ok=True)
    ov, trial = point(X, ctrl) if ctrl != "grid_only" else ({}, None)
    soc = None
    log = []
    for m in MONTHS:
        tag = f"{OUT}/{X}/{ctrl}_{m}"
        if os.path.exists(f"{tag}.json"):
            soc = json.load(open(f"{tag}.json"))["soc_end"]
            continue
        cal = json.load(open(f"{W}/{m}/results/rewind/calibration.json"))
        data = pd.read_pickle(f"{W}/{m}/results/rewind/data.pkl")
        t0 = pd.Timestamp(cal["test_start"])
        t1 = pd.Timestamp(cal["test_end"])
        cfg = with_overrides(load_config(), SCEN)
        cfg = with_overrides(cfg, {"mpc": {"architecture": ARCH[X], "sigma_scale": cal["sigma_scale"],
                                           "error_autocorr": cal["error_autocorr_step"]}})
        cfg = with_overrides(cfg, ov)
        t = time.perf_counter()
        df, st = simulate(data, cfg, ctrl, t0, t1, soc0=soc)
        df.to_csv(f"{tag}.csv")
        pd.DataFrame(st).to_csv(f"{tag}_solves.csv", index=False)
        mt = metrics(df, st, cfg)
        soc = float(df["soc"].iloc[-1])
        json.dump({"config": X, "controller": ctrl, "trial": trial, "month": m, "start": str(t0), "end": str(t1),
                   "sigma_scale": cal["sigma_scale"], "error_autocorr": cal["error_autocorr_step"],
                   "soc_end": soc, "metrics": mt}, open(f"{tag}.json", "w"), default=float, indent=1)
        log.append(f"{X} {ctrl} {m}: net {mt['net_cost_eur']:.4f} prot {mt.get('protection_steps')} "
                   f"{time.perf_counter() - t:.0f}s")
        print(f"[{time.strftime('%H:%M')}] done {log[-1]}", flush=True)
    return log


def stats():
    sys.path.insert(0, f"{HERE}/../summer")
    os.environ.setdefault("KAIROS_ROOT", KROOT)
    from engine import daily, paired
    rows, summ = [], []
    for X in ("B", "C"):
        D = {}
        for f in sorted(glob.glob(f"{OUT}/{X}/*_m1.csv")):
            ctrl = os.path.basename(f)[:-7]
            parts = sorted(glob.glob(f"{OUT}/{X}/{ctrl}_m*.csv"))
            parts = [p for p in parts if not p.endswith("_solves.csv")]
            if len(parts) < len(MONTHS):
                continue
            df = pd.concat([pd.read_csv(p) for p in parts])
            path = f"{OUT}/{X}/{ctrl}_all.csv"
            df.to_csv(path, index=False)
            D[ctrl] = daily(path)
            js = [json.load(open(p[:-4] + ".json"))["metrics"] for p in parts]
            summ.append({"config": X, "controller": ctrl,
                         "net_cost_eur": sum(j["net_cost_eur"] for j in js),
                         "protection_steps": sum(j.get("protection_steps", 0) for j in js),
                         "extra_grid_eur": sum(j.get("extra_grid_eur", 0) for j in js),
                         "curtailed_kwh": sum(j["curtailed_kwh"] for j in js),
                         "grid_kwh": sum(j["grid_kwh"] for j in js)})
        if "ua" in D:
            for b in [c for c in D if c != "ua"]:
                for r in paired(D["ua"], D[b], ("cost", "viol", "prot", "extra")):
                    rows.append({"config": X, "a": "ua", "b": b, **r})
    pd.DataFrame(summ).to_csv(f"{OUT}/summary.csv", index=False)
    pd.DataFrame(rows).to_csv(f"{OUT}/paired_bootstrap.csv", index=False)
    print(pd.DataFrame(summ).round(4).to_string(index=False))
    print(pd.DataFrame(rows).round(4).to_string(index=False))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "stats":
        stats()
    else:
        with ProcessPoolExecutor(2) as ex:
            for log in ex.map(run_chain, chains()):
                pass
        print("REWIND DONE", flush=True)
