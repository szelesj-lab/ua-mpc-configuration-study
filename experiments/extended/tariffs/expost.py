"""Ex-post tariff variants on the existing replays (control unchanged):
export: curtailed renewable energy sold at the day-ahead price; demand: a monthly charge on the peak 15-min import,
with a rate that adds 20 % to the grid-only bill of the same period."""
import os as _os  # paths: see experiments/extended/README.md
UA_WORK = _os.environ.get("UA_WORK", "work/extended")      # working tree (layout in README)
UA_CODE = _os.environ.get("UA_CODE", "code")               # controller code (Kairos)
UA_KAIROS = _os.environ.get("UA_KAIROS", "work")           # work copies made by experiments/make_work.sh
import json, glob, os
import numpy as np, pandas as pd
S = UA_WORK
O = f"{S}/rev3/tariff"; DT = 0.25
def load(p):
    d = pd.read_csv(p); d.index = pd.to_datetime(d.t, utc=True); return d
def parts(d):
    month = d.index.tz_convert("Europe/Budapest").to_period("M")
    return dict(export_eur=float((d.curtailed_w * d.price_eur_wh * DT).sum()),
                peak_sum_w=float(d.grid_w.groupby(month).max().sum()))
rows = []
# summer final test
for X in "ABC":
    st = f"{S}/ind/results_{X}/final_test/steps"
    g = parts(load(f"{st}/bms_grid_only.csv")); g_cost = json.load(open(f"{st}/bms_grid_only.json"))["metrics"]["net_cost_eur"]
    rate = 0.2 * g_cost / g["peak_sum_w"]
    for c in ("grid_only", "det", "det_bo", "ua"):
        p = f"{st}/bms_{c}.csv"
        if not os.path.exists(p): continue
        m = json.load(open(p.replace(".csv", ".json")))["metrics"]; q = parts(load(p))
        rows.append(dict(test="summer", config=X, ctrl=c, base=m["net_cost_eur"], export=m["net_cost_eur"] - q["export_eur"],
                         demand=m["net_cost_eur"] + rate * q["peak_sum_w"], peak_w=q["peak_sum_w"], export_eur=q["export_eur"]))
# winter (B, C), months chained
for X in "BC":
    def chain(c):
        ds = [load(f"{S}/rw/results/{X}/{c}_m{i}.csv") for i in range(1, 7)]
        cost = sum(json.load(open(f"{S}/rw/results/{X}/{c}_m{i}.json"))["metrics"]["net_cost_eur"] for i in range(1, 7))
        return pd.concat(ds), cost
    gd, gc = chain("grid_only"); rate = 0.2 * gc / parts(gd)["peak_sum_w"]
    for c in ("grid_only", "det", "det_bo", "ua"):
        if not os.path.exists(f"{S}/rw/results/{X}/{c}_m1.csv"): continue
        d, cost = chain(c); q = parts(d)
        rows.append(dict(test="winter", config=X, ctrl=c, base=cost, export=cost - q["export_eur"], demand=cost + rate * q["peak_sum_w"],
                         peak_w=q["peak_sum_w"], export_eur=q["export_eur"]))
r = pd.DataFrame(rows); r.to_csv(f"{O}/expost.csv", index=False)
pd.set_option("display.width", 200); print(r.round(4).to_string(index=False))
for t in ("summer", "winter"):
    for col in ("base", "export", "demand"):
        x = r[(r.test == t) & (r.ctrl == "ua")].set_index("config")[col]
        print(t, col, "UA cost by config:", x.round(4).to_dict(), "cheapest:", x.idxmin())
