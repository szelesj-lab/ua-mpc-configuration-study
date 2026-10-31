"""Grid import limit (tariff variant c): final test, BMS plant, selected points, limits.p_grid_max_w = highest load of
the tuning and development periods. usage: KAIROS_ROOT=... KAIROS_SCEN=... python3 run_glim.py <B|C>"""
import os as _os  # paths: see experiments/revision/README.md
UA_WORK = _os.environ.get("UA_WORK", "work/revision")      # revision working tree (layout in README)
UA_CODE = _os.environ.get("UA_CODE", "code")               # controller code (Kairos)
UA_KAIROS = _os.environ.get("UA_KAIROS", "work")           # work copies made by experiments/make_work.sh
import json, os, sys, time
sys.path.insert(0, UA_WORK + "/ind")
from engine import SCEN, point, _job
from replay import build_data, named_periods, with_overrides, load_config
from concurrent.futures import ProcessPoolExecutor, as_completed
X = sys.argv[1]; S = UA_WORK
OUT = f"{S}/rev3/tariff/glim_{X}"; os.makedirs(f"{OUT}/steps", exist_ok=True)
reg = json.load(open(f"{S}/ind/results_{X}/final_test/preregistration.json"))["selected"]["primary_threshold_pp_h"]
base = with_overrides(with_overrides(load_config(), SCEN), {"replay": {"plant": "bms"}})
data = build_data(base); P = named_periods(data, base); t0, t1 = P["final_test"]
lim = float(data.loc[P["tuning"][0]:P["development"][1]].demand_w.max())
json.dump({"p_grid_max_w": lim}, open(f"{OUT}/setup.json", "w"))
jobs = []
for c in [c for c in ("det", "det_bo", "ua") if c in reg]:
    tag = f"glim_{c}"
    if os.path.exists(f"{OUT}/steps/{tag}.json"): continue
    cfg = with_overrides(with_overrides(base, point(c, reg[c]["trial"])), {"limits": {"p_grid_max_w": lim}})
    jobs.append((OUT, tag, c, data, cfg, t0, t1, {"tag": tag, "config": X, "p_grid_max_w": lim}))
print(f"[{time.strftime('%H:%M')}] {X}: {len(jobs)} runs, limit {lim:.2f} W", flush=True)
with ProcessPoolExecutor(2) as ex:
    for f in as_completed([ex.submit(_job, j) for j in jobs]):
        tag, m, s = f.result()
        print(f"[{time.strftime('%H:%M')}] {X} done {tag} net {m['net_cost_eur']:.4f} prot_h {m.get('protection_h',0):.2f} solver {m.get('solver_success_pct',100):.1f}% {s:.0f}s", flush=True)
