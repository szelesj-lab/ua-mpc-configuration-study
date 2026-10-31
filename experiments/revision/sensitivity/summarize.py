"""Summary of the review-2 sensitivity runs -> sens.csv and the LaTeX table body tab_sens.tex."""
import os as _os  # paths: see experiments/revision/README.md
UA_WORK = _os.environ.get("UA_WORK", "work/revision")      # revision working tree (layout in README)
UA_CODE = _os.environ.get("UA_CODE", "code")               # controller code (Kairos)
UA_KAIROS = _os.environ.get("UA_KAIROS", "work")           # work copies made by experiments/make_work.sh
import json, os
import pandas as pd
S = UA_WORK
R = f"{S}/rev2/runs"
NAMES = {"det": r"\DET", "det_bo": r"\DETBO", "ua": r"\UA"}
SC = ["base", "solver", "load10", "renm10", "renp10", "chan"]
rows = []
for X in ("A", "B", "C"):
    for c in ("det", "det_bo", "ua"):
        f0 = f"{S}/ind/results_{X}/final_test/steps/bms_{c}.json"
        if not os.path.exists(f0):
            continue
        for sc in SC:
            if sc == "base":
                m = json.load(open(f0))["metrics"]
            elif sc == "solver" and X == "B" and c == "ua":
                j = json.load(open(f"{S}/ms3/rev/solver_sensitivity.json"))
                m = {k: v[1] for k, v in j.items()}
            else:
                p = f"{R}/{X}/steps/{sc}_{c}.json"
                if not os.path.exists(p):
                    continue
                m = json.load(open(p))["metrics"]
            rows.append(dict(config=X, ctrl=c, scenario=sc, cost=m["net_cost_eur"], prot=m["protection_h"],
                             conv=m.get("solver_success_pct", 100.0)))
d = pd.DataFrame(rows)
d.to_csv(f"{R}/sens.csv", index=False)
w = d.pivot_table(index=["config", "ctrl"], columns="scenario", values=["cost", "prot"])
lines = []
for X in ("A", "B", "C"):
    for c in ("det", "det_bo", "ua"):
        if (X, c) not in w.index:
            continue
        cells = []
        for sc in SC:
            co, pr = w.loc[(X, c)].get(("cost", sc)), w.loc[(X, c)].get(("prot", sc))
            cells.append("--" if pd.isna(co) else f"{co:.3f} / {pr:.2f}")
        first = {"A": "A", "B": "B", "C": "C"}[X] if c == ("det") else ""
        lines.append(f"{first} & {NAMES[c]} & " + " & ".join(cells) + r" \\")
    lines.append(r"\midrule")
open(f"{R}/tab_sens.tex", "w").write("\n".join(lines[:-1]) + "\n")
print(d.pivot_table(index=["config", "ctrl"], columns="scenario", values="prot").round(2))
print(d.pivot_table(index=["config", "ctrl"], columns="scenario", values="cost").round(4))
print(d.groupby(["config", "scenario"]).conv.min())
