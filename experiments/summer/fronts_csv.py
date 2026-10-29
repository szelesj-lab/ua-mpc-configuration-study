"""dev_fronts/steps/*.json -> dev_fronts/fronts_dev.csv (ctrl, trial, development and tuning KPIs, joint front)."""
import glob, json, os
import pandas as pd
P = os.environ["STUDY_RESULTS"]
rows = []
for f in sorted(glob.glob(f"{P}/dev_fronts/steps/*.json")):
    j = json.load(open(f))
    d, t = j["dev"], j["tuning"]
    rows.append(dict(ctrl=j["controller"], trial=j["trial"], dev_cost=d["net_cost_eur"], dev_viol=d["violation_pp_h"],
                     dev_max=d.get("max_violation_pp"), solver=d.get("solver_success_pct"),
                     dev_prot=d.get("protection_h"), dev_extra=d.get("extra_grid_eur"), dev_curt=d.get("curtailed_pct"),
                     tun_cost=t.get("net_cost_eur"), tun_viol=t.get("violation_pp_h"), tun_prot=t.get("protection_h")))
r = pd.DataFrame(rows)
r["joint_front"] = [not ((r.dev_cost <= a.dev_cost) & (r.dev_prot <= a.dev_prot)
                         & ((r.dev_cost < a.dev_cost) | (r.dev_prot < a.dev_prot))).any() for _, a in r.iterrows()]
r.sort_values("dev_cost").to_csv(f"{P}/dev_fronts/fronts_dev.csv", index=False)
print(r.sort_values("dev_cost").round(4).to_string(index=False))
