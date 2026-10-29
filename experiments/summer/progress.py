"""One-screen progress of the industrial pipeline: trials per tuning, steps done, latest results."""
import glob, json, os, re
import pandas as pd
N = os.path.dirname(os.path.abspath(__file__))
done = sorted(os.path.basename(f) for f in glob.glob(f"{N}/done/*"))
print("done:", ", ".join(done) or "-")
for f in sorted(glob.glob(f"{N}/logs/*_tune_*.log")):
    t = open(f).read()
    tr = re.findall(r"trial\s+(\d+)/30 .*?net_cost_eur=([\d.]+) protection_h=([\d.]+)", t)
    if tr:
        best = min(((float(c), float(v)) for _, c, v in tr), key=lambda x: (x[1] > 1.0, x[0]))
        print(f"{os.path.basename(f)[:-4]:<16} {len(tr):>2}/30 trials; best (cost EUR, protection h): {best}")
for a in "BCA":
    p = f"{N}/results_{a}/final_test/preregistration.json"
    if os.path.exists(p):
        s = json.load(open(p))["selected"]["primary_threshold_pp_h"]
        print(a, "prereg:", {c: (v["trial"], round(v["dev_cost"], 4), round(v["dev_viol"], 3)) for c, v in s.items()})
    m = f"{N}/results_{a}/decision_map_recovery/map.csv"
    if os.path.exists(m):
        print(a, "map:"); print(pd.read_csv(m)[["cap_wh", "error_scale", "replan_steps", "r_E", "r_E_gen", "tau_E",
                                                "bo_prot_h", "ua_prot_h", "bo_curt_pct", "ua_curt_pct", "cost_diff_pct"]].round(3).to_string(index=False))
    s = f"{N}/results_{a}/final_test/summary.csv"
    if os.path.exists(s):
        print(a, "final:"); print(pd.read_csv(s)[["plant", "variant", "net_cost_eur", "violation_pp_h",
                                                  "protection_h", "curtailed_pct"]].round(4).to_string(index=False))
