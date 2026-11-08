"""T3: DET-BO with the scenario and the robust back-off of preregistration_T3.json (winter B, Nov-Mar, and the summer
final test); the same code path and weights as run_t1.py, only the back-off differs. Writes results/extended/
preregistered/t3/det_bo_{scenario,robust}_m{2..6}.{csv,json} and t3/steps/det_bo_{scenario,robust}_final.*"""
import os as _os  # paths: see experiments/extended/README.md
UA_WORK = _os.environ.get("UA_WORK", "work/extended")      # working tree (layout in README)
UA_CODE = _os.environ.get("UA_CODE", "code")               # controller code (Kairos)
UA_KAIROS = _os.environ.get("UA_KAIROS", "work")           # work copies made by experiments/make_work.sh
import json, os, sys, time
from pathlib import Path
sys.path.insert(0, UA_CODE + "/src")
import warnings; warnings.filterwarnings("ignore")
import pandas as pd
from concurrent.futures import ProcessPoolExecutor
from replay import load_config, metrics, simulate, with_overrides
ROOT = Path(__file__).resolve().parents[3]
S = UA_WORK; W = UA_KAIROS + "/W"
O = str(ROOT / "results/extended/preregistered/t3"); os.makedirs(O, exist_ok=True)
REG = json.load(open(ROOT / "results/extended/preregistered/preregistration_T3.json"))["T3"]
SCEN = {"SoC": {"E_capacity": 72}, "degradation": {"c_deg": 2e-5}, "limits": {"p_charge_max_w": 24.0},
        "replay": {"plant": "bms", "demand_profile_csv": "industrial_load_profile.csv", "demand_mean_w": 10.788},
        "mpc": {"architecture": "self_consumption", "soc_recovery": True}}
trial = json.load(open(f"{S}/ind/results_B/final_test/preregistration.json"))["selected"]["primary_threshold_pp_h"]["det_bo"]["trial"]
BO = next(x["overrides"] for x in json.load(open(UA_KAIROS + "/B/results/tuning/det_bo_pareto.json"))["points"] if x["trial"] == trial)


def winter(rule):
    soc = json.load(open(f"{S}/rw/results/B/det_bo_m1.json"))["soc_end"]
    for x in REG["months"]:
        m, b = x["month"], x[f"b_{rule}"]; out = f"{O}/det_bo_{rule}_m{m}"
        if not os.path.exists(f"{out}.json"):
            cal = json.load(open(f"{W}/m{m}/results/rewind/calibration.json")); data = pd.read_pickle(f"{W}/m{m}/results/rewind/data.pkl")
            cfg = with_overrides(load_config(), SCEN)
            cfg = with_overrides(cfg, {"mpc": {**SCEN["mpc"], "sigma_scale": cal["sigma_scale"], "error_autocorr": cal["error_autocorr_step"]}})
            cfg = with_overrides(cfg, {**BO, "mpc": {**cfg["mpc"], **BO["mpc"], "soc_backoff": b}})
            t = time.perf_counter()
            df, st = simulate(data, cfg, "det_bo", pd.Timestamp(cal["test_start"]), pd.Timestamp(cal["test_end"]), soc0=soc)
            mt = metrics(df, st, cfg); df.to_csv(f"{out}.csv"); pd.DataFrame(st).to_csv(f"{out}_solves.csv", index=False)
            json.dump({"month": m, "rule": rule, "b": b, "soc0": soc, "soc_end": float(df.soc.iloc[-1]), "metrics": mt},
                      open(f"{out}.json", "w"), default=float, indent=1)
            print(f"[{time.strftime('%H:%M')}] {rule} winter m{m} b={b:.4f} net {mt['net_cost_eur']:.4f} prot {mt['protection_h']:.2f} "
                  f"{time.perf_counter()-t:.0f}s", flush=True)
        soc = json.load(open(f"{out}.json"))["soc_end"]


def summer(rule):
    tag = f"det_bo_{rule}_final"
    if os.path.exists(f"{O}/steps/{tag}.json"):
        return
    import subprocess
    b = REG["summer_final_test"][f"b_{rule}"]
    env = {**os.environ, "KAIROS_ROOT": UA_KAIROS + "/B",
           "KAIROS_SCEN": json.dumps({"limits": {"p_charge_max_w": 24.0}, "mpc": {"architecture": "self_consumption"},
                                      "replay": {"demand_profile_csv": "industrial_load_profile.csv", "demand_mean_w": 10.788, "plant": "bms"}})}
    code = f"""
import sys,json,os; sys.path.insert(0,'{S}/ind')
from engine import SCEN, _job
from replay import build_data, named_periods, with_overrides, load_config
base=with_overrides(with_overrides(load_config(),SCEN),{{'replay':{{'plant':'bms'}}}}); d=build_data(base); t0,t1=named_periods(d,base)['final_test']
BO={json.dumps(BO)}; BO['mpc']['soc_backoff']={b}
cfg=with_overrides(base,BO); os.makedirs('{O}/steps',exist_ok=True)
tag,m,s=_job(('{O}','{tag}','det_bo',d,cfg,t0,t1,{{'b':{b},'rule':'{rule}'}}))
print('{rule} summer final net',round(m['net_cost_eur'],4),'prot',m['protection_h'],round(s), flush=True)
"""
    subprocess.run([sys.executable, "-c", code], cwd=UA_KAIROS + "/B", env=env, check=True)


def chain(rule):
    winter(rule); summer(rule)


if __name__ == "__main__":
    with ProcessPoolExecutor(2) as ex:
        fs = [ex.submit(chain, r) for r in ("scenario", "robust")]
        for f in fs:
            f.result()
    print("T3 DONE")
