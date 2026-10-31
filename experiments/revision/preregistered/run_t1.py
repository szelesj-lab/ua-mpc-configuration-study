"""T1: DET-BO with a back-off re-sized from the previous month's r_E (winter, B) and from the tuning period (summer final test)."""
import os as _os  # paths: see experiments/revision/README.md
UA_WORK = _os.environ.get("UA_WORK", "work/revision")      # revision working tree (layout in README)
UA_CODE = _os.environ.get("UA_CODE", "code")               # controller code (Kairos)
UA_KAIROS = _os.environ.get("UA_KAIROS", "work")           # work copies made by experiments/make_work.sh
import json, os, sys, time
sys.path.insert(0, UA_CODE + "/src")
import warnings; warnings.filterwarnings("ignore")
import pandas as pd
from concurrent.futures import ProcessPoolExecutor
from replay import load_config, metrics, simulate, with_overrides
S = UA_WORK; W = UA_KAIROS + "/W"
O = f"{S}/rev3/t12/t1"; os.makedirs(O, exist_ok=True)
REG = json.load(open(f"{S}/rev3/t12/preregistration_T1T2.json"))["T1"]
SCEN = {"SoC": {"E_capacity": 72}, "degradation": {"c_deg": 2e-5}, "limits": {"p_charge_max_w": 24.0},
        "replay": {"plant": "bms", "demand_profile_csv": "industrial_load_profile.csv", "demand_mean_w": 10.788},
        "mpc": {"architecture": "self_consumption", "soc_recovery": True}}
trial = json.load(open(f"{S}/ind/results_B/final_test/preregistration.json"))["selected"]["primary_threshold_pp_h"]["det_bo"]["trial"]
BO = next(x["overrides"] for x in json.load(open(UA_KAIROS + "/B/results/tuning/det_bo_pareto.json"))["points"] if x["trial"] == trial)
def winter():
    soc = json.load(open(f"{S}/rw/results/B/det_bo_m1.json"))["soc_end"]
    for x in REG["months"]:
        m = x["month"]; out = f"{O}/det_bo_sized_m{m}"
        if not os.path.exists(f"{out}.json"):
            cal = json.load(open(f"{W}/m{m}/results/rewind/calibration.json")); data = pd.read_pickle(f"{W}/m{m}/results/rewind/data.pkl")
            cfg = with_overrides(load_config(), SCEN)
            cfg = with_overrides(cfg, {"mpc": {**SCEN["mpc"], "sigma_scale": cal["sigma_scale"], "error_autocorr": cal["error_autocorr_step"]}})
            cfg = with_overrides(cfg, {**BO, "mpc": {**cfg["mpc"], **BO["mpc"], "soc_backoff": x["b"]}})
            t = time.perf_counter()
            df, st = simulate(data, cfg, "det_bo", pd.Timestamp(cal["test_start"]), pd.Timestamp(cal["test_end"]), soc0=soc)
            mt = metrics(df, st, cfg); df.to_csv(f"{out}.csv"); pd.DataFrame(st).to_csv(f"{out}_solves.csv", index=False)
            json.dump({"month": m, "b": x["b"], "soc0": soc, "soc_end": float(df.soc.iloc[-1]), "metrics": mt}, open(f"{out}.json", "w"), default=float, indent=1)
            print(f"[{time.strftime('%H:%M')}] winter m{m} b={x['b']:.4f} net {mt['net_cost_eur']:.4f} prot {mt['protection_h']:.2f} {time.perf_counter()-t:.0f}s", flush=True)
        soc = json.load(open(f"{out}.json"))["soc_end"]
def summer():
    if os.path.exists(f"{O}/steps/det_bo_sized_final.json"): return
    os.environ["KAIROS_ROOT"] = UA_KAIROS + "/B"
    sys.path.insert(0, f"{S}/ind")
    os.environ["KAIROS_SCEN"] = json.dumps({"limits": {"p_charge_max_w": 24.0}, "mpc": {"architecture": "self_consumption"},
        "replay": {"demand_profile_csv": "industrial_load_profile.csv", "demand_mean_w": 10.788, "plant": "bms"}})
    import subprocess
    code = f"""
import sys,json,os; sys.path.insert(0,'{S}/ind')
from engine import SCEN, _job
from replay import build_data, named_periods, with_overrides, load_config
base=with_overrides(with_overrides(load_config(),SCEN),{{'replay':{{'plant':'bms'}}}}); d=build_data(base); t0,t1=named_periods(d,base)['final_test']
BO={json.dumps(BO)}; BO['mpc']['soc_backoff']={REG['summer_final_test']['b']}
cfg=with_overrides(base,BO); os.makedirs('{O}/steps',exist_ok=True)
tag,m,s=_job(('{O}','det_bo_sized_final','det_bo',d,cfg,t0,t1,{{'b':{REG['summer_final_test']['b']}}}))
print('summer final net',round(m['net_cost_eur'],4),'prot',m['protection_h'],round(s))
"""
    subprocess.run([sys.executable, "-c", code], cwd=UA_KAIROS + "/B", env=os.environ, check=True)
with ProcessPoolExecutor(2) as ex:
    a = ex.submit(winter); b = ex.submit(summer); a.result(); b.result()
print("T1 DONE")
