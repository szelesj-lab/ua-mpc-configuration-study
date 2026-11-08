"""Γ of a constant back-off in the winter test of B, three checks (Sec. 5.4, after T1):

1. persistence of r_E: ratio of each winter month's r_E to the previous month's (Nov-Mar);
2. protection against the realised Γ over the eleven winter month-runs of DET-BO (five re-sized of T1, six with the
   summer-tuned back-off), Spearman rank correlation;
3. window by window (Nov-Mar, both back-offs): every re-planning hour classified by app/own/gammacheck.py as started
   inside the tightened band with the routed deficit within the reserve / above it, or started with the reserve
   already used, and the hours with a lower-edge BMS intervention counted per class.

Inputs: results/winter/B (summer-tuned DET-BO), results/extended/preregistered/t1 (re-sized), results/extended/
ratio/rE_outofsample.csv (r_E per month, the values T1 was sized from), and the walk-forward forecasts of each month
(<month dir>/results/rewind/data.pkl as written by experiments/winter/run_winter.sh; the default is the work copies
$UA_KAIROS/W/m1..m6, or set UA_WINTER_DATA to a pattern with {k} (1..6) or {month} (2025-10..2026-03), e.g. the
walk-forward bundles of the simulator: out/own/bundles/{month}/results/rewind/data.pkl).

Outputs: results/extended/ratio/gamma_windows.csv (one row per window), gamma_check.json (the numbers of the paper).
Run from the package root: python experiments/extended/ratio/gamma_windows.py   (seconds once the forecasts exist)
"""
import json
import os
import sys
from pathlib import Path

import pandas as pd
from scipy.stats import spearmanr

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from app.own.gammacheck import routed, summary, windows  # noqa: E402

RES = ROOT / "results"
EXT = RES / "extended"
MONTH = {1: "2025-10", 2: "2025-11", 3: "2025-12", 4: "2026-01", 5: "2026-02", 6: "2026-03"}
E_CAP, P_MAX, BAND = 72.0, 24.0, 0.8           # cell of the study (code/config)
B_TUNED = 0.030298875705271892                 # summer-tuned back-off of B (selected point t16)
PATTERN = os.environ.get("UA_WINTER_DATA", os.environ.get("UA_KAIROS", "work") + "/W/m{k}/results/rewind/data.pkl")


def forecast(k: int) -> pd.Series:
    x = pd.read_pickle(PATTERN.format(k=k, month=MONTH[k]))
    return routed(x.solar_mu + x.wind_mu, x.solar_real + x.wind_real, "self_consumption", P_MAX, P_MAX)


def main():
    oos = pd.read_csv(EXT / "ratio/rE_outofsample.csv")
    rE = oos[(oos.config == "B") & oos.period.str.startswith("winter")].set_index("period").r_E
    rE = {k: float(rE[f"winter m{k}"]) for k in MONTH}
    ratio = [rE[k] / rE[k - 1] for k in range(2, 7)]

    pts, rows, soc_prev = [], [], None
    for k in range(1, 7):
        tuned = json.load(open(RES / f"winter/B/det_bo_m{k}.json"))
        pts.append(("det_bo", k, rE[k] * BAND / B_TUNED, tuned["metrics"]["protection_h"]))
        runs = [("det_bo", RES / f"winter/B/det_bo_m{k}.csv", B_TUNED, soc_prev)]
        soc_prev = tuned["soc_end"]
        if k >= 2:
            sized = json.load(open(EXT / f"preregistered/t1/det_bo_sized_m{k}.json"))
            pts.append(("det_bo_sized", k, rE[k] * BAND / sized["b"], sized["metrics"]["protection_h"]))
            runs.append(("det_bo_sized", EXT / f"preregistered/t1/det_bo_sized_m{k}.csv", sized["b"], sized["soc0"]))
        else:
            continue                              # windows: November to March, the months of T1, for both back-offs
        err = forecast(k)
        for name, f, b, s0 in runs:
            w = windows(pd.read_csv(f), err, b, E_CAP, s0)
            w.insert(0, "month", MONTH[k]); w.insert(0, "run", name); w.insert(2, "b", b)
            rows.append(w)
    win = pd.concat(rows, ignore_index=True)
    win.to_csv(EXT / "ratio/gamma_windows.csv", index=False)
    g = pd.DataFrame(pts, columns=["run", "month", "gamma", "protection_h"])
    rho = spearmanr(g.gamma, g.protection_h)
    out = {"rE_ratio_min": min(ratio), "rE_ratio_max": max(ratio),
           "spearman_rho": float(rho.statistic), "spearman_p": float(rho.pvalue), "n_month_runs": len(g),
           "zero_protection_gamma": sorted(float(x) for x in g[g.protection_h == 0].gamma),
           "points": g.to_dict("records"),
           "windows": {r: summary(win[win.run == r]) for r in ("det_bo", "det_bo_sized")}}
    (EXT / "ratio/gamma_check.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k != "points"}, indent=1))


if __name__ == "__main__":
    main()
