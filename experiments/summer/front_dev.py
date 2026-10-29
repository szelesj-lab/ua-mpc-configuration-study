"""Every Pareto point of det, det_bo and ua (tuning period) replayed on the development period,
physical plant. Resumable."""
import json, os, sys, time, warnings
warnings.filterwarnings("ignore")
ROOT = os.environ.get("KAIROS_ROOT", os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "uncertainty-aware-mpc")))
PAPER = os.environ.get("STUDY_RESULTS", os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "results")))
sys.path.insert(0, os.path.join(ROOT, "src"))
from concurrent.futures import ProcessPoolExecutor, as_completed
from replay import build_data, named_periods, simulate, metrics, with_overrides, load_config, TUNING_DIR
OUT = os.path.join(PAPER, "dev_fronts")
# SOC_RECOVERY=0 reproduces the runs made before mpc.soc_recovery existed (see docs/protocol.md)
SCEN = {"SoC": {"E_capacity": 72}, "degradation": {"c_deg": 2e-5},
        "mpc": {"soc_recovery": os.environ.get("SOC_RECOVERY", "1") == "1"}}
# scenario extension from the environment (KAIROS_SCEN = JSON, merged per section)
for _k, _v in json.loads(os.environ.get("KAIROS_SCEN", "{}")).items():
    SCEN.setdefault(_k, {}).update(_v)

def job(a):
    tag, ctrl, data, cfg, t0, t1, meta = a
    t = time.perf_counter(); df, st = simulate(data, cfg, ctrl, t0, t1)
    df.to_csv(f"{OUT}/steps/{tag}.csv"); m = metrics(df, st, cfg)
    json.dump({**meta, "dev": m}, open(f"{OUT}/steps/{tag}.json", "w"), default=float)
    return tag, m, time.perf_counter() - t

if __name__ == "__main__":
    os.makedirs(f"{OUT}/steps", exist_ok=True)
    base = with_overrides(load_config(), SCEN); data = build_data(base)
    t0, t1 = named_periods(data, base)["development"]
    jobs = []
    for c in ("det", "det_bo", "ua"):
        if not (TUNING_DIR / f"{c}_pareto.json").exists():
            continue
        p = json.loads((TUNING_DIR / f"{c}_pareto.json").read_text())
        for pt in p["points"]:
            tag = f"{c}_t{pt['trial']:02d}"
            if os.path.exists(f"{OUT}/steps/{tag}.json"):
                continue
            meta = {"controller": c, "trial": pt["trial"], "overrides": pt["overrides"], "tuning": pt["kpis"]}
            jobs.append((tag, c, data, with_overrides(base, pt["overrides"]), t0, t1, meta))
    print(f"{len(jobs)} runs to do", flush=True)
    with ProcessPoolExecutor(2) as ex:
        for f in as_completed([ex.submit(job, j) for j in jobs]):
            tag, m, s = f.result()
            print(f"done {tag:<12} net {m['net_cost_eur']:.4f} viol {m['violation_pp_h']:.4f} solver {m.get('solver_success_pct', 100):.1f}% {s:.0f}s", flush=True)
    print("ALL RUNS DONE", flush=True)
