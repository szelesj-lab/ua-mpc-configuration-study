"""T2 of the replication (record R2): back-off sweep in the second load case (configuration A, production schedule,
physical plant, development period). As experiments/extended/preregistered/run_t2.py, with the cells and the selected
point of record R2.
    KAIROS_ROOT=<work>/P STUDY_RESULTS=<out>/second python experiments/redo/run_t2_redo.py"""
import json, os, sys, time
from pathlib import Path
PKG = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PKG / "experiments/summer"))
from engine import SCEN, point, _job, scale_errors  # noqa: E402
from replay import build_data, named_periods, with_overrides, load_config  # noqa: E402
from concurrent.futures import ProcessPoolExecutor, as_completed  # noqa: E402

S = os.environ["STUDY_RESULTS"]; O = f"{S}/t2"; os.makedirs(f"{O}/steps", exist_ok=True)
R2 = json.loads((PKG / "results/redo/preregistration_R2.json").read_text())
BO = point("det_bo", R2["selected_primary"]["det_bo"])
base = with_overrides(load_config(), SCEN); data = build_data(base); t0, t1 = named_periods(data, base)["development"]
jobs = []
for x in R2["T2"]["cells"]:
    tag = f"bsw2_det_bo_cap{x['cap']}_err{x['err']}_rp{x['rp']}_G{x['Gamma']}"
    if os.path.exists(f"{O}/steps/{tag}.json"):
        continue
    cfg = with_overrides(with_overrides(base, {**BO, "mpc": {**BO.get("mpc", {}), "soc_backoff": x["b"]}}),
                         {"SoC": {"E_capacity": x["cap"]}, "replay": {"replan_steps": x["rp"], "plant": "physical"}})
    jobs.append((O, tag, "det_bo", scale_errors(data, x["err"]), cfg, t0, t1, x))
print(f"[{time.strftime('%H:%M')}] T2: {len(jobs)} runs", flush=True)
with ProcessPoolExecutor(2) as ex:
    for f in as_completed([ex.submit(_job, j) for j in jobs]):
        tag, m, s = f.result()
        print(f"[{time.strftime('%H:%M')}] done {tag} net {m['net_cost_eur']:.4f} viol {m['violation_pp_h']:.3f} {s:.0f}s", flush=True)
print("T2 DONE")
