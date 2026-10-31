"""python run_rE.py grid|correlation|b_sweep  — runs of the pre-registered r_E experiments (resumable)."""
import os as _os  # paths: see experiments/extended/README.md
UA_WORK = _os.environ.get("UA_WORK", "work/extended")      # working tree (layout in README)
UA_CODE = _os.environ.get("UA_CODE", "code")               # controller code (Kairos)
UA_KAIROS = _os.environ.get("UA_KAIROS", "work")           # work copies made by experiments/make_work.sh
import sys, time; sys.path.insert(0, UA_WORK + "/rev3")
from common import *
from engine import _job, scale_errors
from concurrent.futures import ProcessPoolExecutor, as_completed
EXP = sys.argv[1]; OUT = f"{S}/rev3/runs_rE"; os.makedirs(f"{OUT}/steps", exist_ok=True)
reg = json.load(open(f"{S}/rev3/preregistration_rE.json"))
cfg = base_cfg(); data = build_data(cfg); t0, t1 = named_periods(data, cfg)["development"]
jobs = []
def add(tag, ctrl, ov, d, cap, rp, meta, extra_mpc=None):
    if os.path.exists(f"{OUT}/steps/{tag}.json"): return
    c = with_overrides(with_overrides(cfg, ov), {"SoC": {"E_capacity": cap}, "replay": {"replan_steps": rp}})
    if extra_mpc: c = with_overrides(c, {"mpc": {**c.get("mpc", {}), **extra_mpc}})
    jobs.append((OUT, tag, ctrl, d, c, t0, t1, {"tag": tag, **meta}))
if EXP == "grid":
    for x in [c for c in reg["cells"] if c["experiment"] == "grid"]:
        d = scale_errors(data, x["err"])
        for n, ov in (("det_bo", BO), ("ua", UA)):
            add(f"grid_{n}_cap{x['cap']}_err{x['err']}", n, ov, d, x["cap"], 4, x)
elif EXP == "correlation":
    for x in [c for c in reg["cells"] if c["experiment"] == "correlation"]:
        d = synth(data, x["phi"], x["seed"])
        for n, ov in (("det_bo", BO), ("ua", UA)):
            add(f"corr_{n}_cap{x['cap']}_rp{x['rp']}_phi{x['phi']}", n, ov, d, x["cap"], x["rp"], x,
                extra_mpc={"error_autocorr": x["phi"]} if n == "ua" else None)
elif EXP == "b_sweep":
    for x in reg["b_sweep"]:
        d = scale_errors(data, x["err"])
        ov = {**BO, "mpc": {**BO.get("mpc", {}), "soc_backoff": x["b"]}}
        add(f"bsw_det_bo_cap{x['cap']}_err{x['err']}_rp{x['rp']}_G{x['Gamma']}", "det_bo", ov, d, x["cap"], x["rp"], x)
print(f"[{time.strftime('%H:%M')}] {EXP}: {len(jobs)} runs", flush=True)
jobs.sort(key=lambda j: j[2] != "ua")   # slow ones first
with ProcessPoolExecutor(2) as ex:
    for f in as_completed([ex.submit(_job, j) for j in jobs]):
        tag, m, s = f.result()
        print(f"[{time.strftime('%H:%M')}] done {tag:<44} net {m['net_cost_eur']:.4f} prot_h {m.get('protection_h',0):.2f} solver {m.get('solver_success_pct',100):.1f}% {s:.0f}s", flush=True)
