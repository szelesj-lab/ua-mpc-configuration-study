"""Pre-registration of test T3: scenario and robust (tube) tightening in closed loop, written before the runs.

Design as T1 (configuration B, BMS plant, DET-BO with the summer-selected weights, walk-forward winter months
November 2025 - March 2026 with the SoC carried over, and the summer final test), but the back-off of each period is
chosen before it by one of two rules from the archive of issued forecasts (experiments/extended/ratio/
scenario_robust.py): scenario = the largest routed 1-h deficit of the archive, robust = W Δt times the largest routed
per-step deficit. Archive: the previous month (winter), the 30 days before the final test (summer).

Writes results/extended/preregistered/preregistration_T3.json with its UTC time and SHA-256; refuses to overwrite.
Inputs as scenario_robust.py (UA_SUMMER_DATA, UA_WINTER_DATA).
"""
import hashlib
import importlib.util
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
EXT = ROOT / "results" / "extended"
OUT = EXT / "preregistered" / "preregistration_T3.json"
spec = importlib.util.spec_from_file_location("sr", ROOT / "experiments/extended/ratio/scenario_robust.py")
sr = importlib.util.module_from_spec(spec); spec.loader.exec_module(sr)


def main():
    assert not OUT.exists(), f"{OUT} exists: the record is written once"
    months, prev = [], sr.winter(1)
    for k in range(2, 7):
        cur = sr.winter(k); r = sr.rules(prev)
        months.append({"month": k, "b_scenario": r["scenario"], "b_robust": r["robust"], "archive_windows": r["lookback_windows"]})
        prev = cur
    s = sr.routed(pd.read_pickle(sr.SUMMER)); a, _ = sr.PER["final_test"]
    r = sr.rules(s.loc[a - pd.Timedelta(days=30):a - pd.Timedelta(minutes=15)])
    final = {"b_scenario": r["scenario"], "b_robust": r["robust"], "archive_windows": r["lookback_windows"]}
    t1 = [json.loads((EXT / f"preregistered/t1/det_bo_sized_m{k}.json").read_text()) for k in range(2, 7)]
    ua = [json.loads((ROOT / f"results/winter/B/ua_m{k}.json").read_text())["metrics"] for k in range(2, 7)]
    ref = {"resized_backoff_T1": {"net_cost_eur": sum(x["metrics"]["net_cost_eur"] for x in t1),
                                  "protection_h": sum(x["metrics"]["protection_h"] for x in t1)},
           "ua": {"net_cost_eur": sum(x["net_cost_eur"] for x in ua), "protection_h": sum(x["protection_h"] for x in ua)},
           "summer_final_test": {"resized_backoff_T1": json.loads((EXT / "preregistered/t1/steps/det_bo_sized_final.json").read_text())["metrics"]
                                 ["net_cost_eur"]}}
    reg = {"written_utc": pd.Timestamp.now(tz="UTC").isoformat(),
           "T3": {"design": ("configuration B, BMS plant: DET-BO with the summer-selected weights (as T1) and a back-off chosen "
                             "before each period by the scenario rule (largest routed 1-h deficit of the archive) or the robust rule "
                             "(W dt times the largest routed per-step deficit); walk-forward winter months Nov 2025 - Mar 2026, "
                             "SoC carried over from the DET-BO chain at the end of October; summer final test, archive the 30 days "
                             "before it. No tuning: the weights are those of T1."),
                  "months": months, "summer_final_test": final, "reference_Nov_Mar": ref,
                  "predictions": {
                      "P12": "Nov-Mar protection hours with the scenario back-off <= those of the re-sized back-off of T1 (7.50 h)",
                      "P13": "Nov-Mar protection hours with the robust back-off <= those of the re-sized back-off of T1 (7.50 h)",
                      "P14": "Nov-Mar net cost: robust >= scenario >= re-sized back-off of T1 (the back-off is larger in every month)",
                      "P15": "Nov-Mar net cost of both above that of UA-MPC",
                      "P16": "summer final test: both tie UA-MPC by the label rule of the decision map (protection) and cost more "
                             "than the re-sized back-off of T1"}}}
    txt = json.dumps(reg, indent=1, default=float)
    OUT.write_text(txt)
    print(txt); print("sha256", hashlib.sha256(txt.encode()).hexdigest())


if __name__ == "__main__":
    main()
