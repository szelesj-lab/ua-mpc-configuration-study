"""T2: back-off sweep in the second load case (configuration A, production schedule, physical plant, development)."""
import os as _os  # paths: see experiments/revision/README.md
UA_WORK = _os.environ.get("UA_WORK", "work/revision")      # revision working tree (layout in README)
UA_CODE = _os.environ.get("UA_CODE", "code")               # controller code (Kairos)
UA_KAIROS = _os.environ.get("UA_KAIROS", "work")           # work copies made by experiments/make_work.sh
import json, os, sys, time
sys.path.insert(0, UA_CODE + "/results/paper/scripts")
from engine import SCEN, point, _job, scale_errors
from replay import build_data, named_periods, with_overrides, load_config
from concurrent.futures import ProcessPoolExecutor, as_completed
S = UA_WORK; O = f"{S}/rev3/t12/t2"; os.makedirs(f"{O}/steps", exist_ok=True)
REG = json.load(open(f"{S}/rev3/t12/preregistration_T1T2.json"))["T2"]
K = UA_CODE + "/results/paper"
tr = json.load(open(f"{K}/final_test/preregistration.json"))["selected"]["primary_threshold_pp_h"]["det_bo"]["trial"]
BO = point("det_bo", tr)
base = with_overrides(load_config(), SCEN); data = build_data(base); t0, t1 = named_periods(data, base)["development"]
jobs = []
for x in REG["cells"]:
    tag = f"bsw2_det_bo_cap{x['cap']}_err{x['err']}_rp{x['rp']}_G{x['Gamma']}"
    if os.path.exists(f"{O}/steps/{tag}.json"): continue
    cfg = with_overrides(with_overrides(base, {**BO, "mpc": {**BO.get("mpc", {}), "soc_backoff": x["b"]}}),
                         {"SoC": {"E_capacity": x["cap"]}, "replay": {"replan_steps": x["rp"], "plant": "physical"}})
    jobs.append((O, tag, "det_bo", scale_errors(data, x["err"]), cfg, t0, t1, x))
print(f"[{time.strftime('%H:%M')}] T2: {len(jobs)} runs", flush=True)
with ProcessPoolExecutor(2) as ex:
    for f in as_completed([ex.submit(_job, j) for j in jobs]):
        tag, m, s = f.result()
        print(f"[{time.strftime('%H:%M')}] done {tag} net {m['net_cost_eur']:.4f} viol {m['violation_pp_h']:.3f} {s:.0f}s", flush=True)
print("T2 DONE")
