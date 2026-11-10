"""Second load case (configuration A, weekly production schedule, physical plant): the selection rule of the first
study's second load case (band violation on the physical plant, thresholds 0.5 and 1.0 pp h). The other stages
(map_recovery, final) are those of experiments/summer/stages.py.
usage: KAIROS_ROOT=... STUDY_RESULTS=... python3 stages_second.py prereg|map_recovery|final"""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "summer"))
import pandas as pd
import stages
from stages import PAPER

RULE = {"primary_threshold_pp_h": 0.5, "secondary_threshold_pp_h": 1.0,
        "rule": "per controller, among its tuning-period Pareto points replayed on the development period (physical "
                "plant): the cheapest development net cost with development violation <= threshold; if none, the "
                "point with the lowest development violation"}

def prereg():
    out = f"{PAPER}/final_test"; os.makedirs(out, exist_ok=True)
    path = f"{out}/preregistration.json"
    if os.path.exists(path):
        print("pre-registration exists, not changed:", path); return
    f = pd.read_csv(f"{PAPER}/dev_fronts/fronts_dev.csv")
    pick = {}
    for thr_name in ("primary_threshold_pp_h", "secondary_threshold_pp_h"):
        thr = RULE[thr_name]; sel = {}
        for c, g in f.groupby("ctrl"):
            ok = g[g.dev_viol <= thr]
            r = ok.sort_values("dev_cost").iloc[0] if len(ok) else g.sort_values("dev_viol").iloc[0]
            sel[c] = {"trial": int(r.trial), "dev_cost": float(r.dev_cost), "dev_viol": float(r.dev_viol),
                      "met_threshold": bool(len(ok))}
        pick[thr_name] = sel
    reg = {"written_utc": pd.Timestamp.now(tz="UTC").isoformat(), **RULE, "selected": pick,
           "final_test_set": "grid_only, rule, det, det_bo, ua, ua_rho_only, ua_margin_only, oracle (det weights); "
                             "physical and bms plant; primary selection; ua also with the secondary selection if it differs"}
    json.dump(reg, open(path, "w"), indent=2); print(json.dumps(reg, indent=2))

if __name__ == "__main__":
    {"prereg": prereg, "final": stages.final,
     "map_recovery": lambda: stages.map_(f"{PAPER}/decision_map_recovery")}[sys.argv[1]]()
