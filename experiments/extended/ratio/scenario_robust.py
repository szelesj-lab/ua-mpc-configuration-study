"""Scenario and robust (tube) tightening on the Γ scale, configuration B, from the forecast archive (no replays).

With the SoC an integrator and a re-plan every hour, a scenario or a tube MPC tightens the lower SoC limit by an
energy fixed before the period, as the back-off does; the methods differ only in how that energy is chosen from
the archive of past forecast errors. Three ex-ante rules, all from the same lookback (the issued forecasts of the
30 days before the period; winter: the previous month, as in T1):

    back-off re-sized (T1)   95 % quantile of the routed 1-h window deficits / 0.75          (b = r_E,past (x_max-x_min) / 0.75)
    scenario                 the largest routed 1-h window deficit of the lookback            (scenario program with one decision
                             variable: with N exchangeable windows a new one exceeds it with probability 1/(N+1))
    robust (tube)            W Δt times the largest routed per-step deficit of the lookback   (worst case: every step of the
                             window at the bound; a box disturbance set and a constant tube, as for an integrator)

For each rule and period: the margin b (share of the capacity), the share of the usable band it locks when both
edges are tightened (as in B), and Γ = r_E (x_max - x_min) / b with the period's own r_E; the summer-tuned back-off
and the mean margin of UA-MPC (results/extended/ratio/effective_gamma.csv) for comparison.

Inputs: the summer forecasts (UA_SUMMER_DATA, default work/B/results/replay/data.pkl; the simulator's bundle
out/own/bundles/final_test/results/replay/data.pkl holds the same series) and the walk-forward months
(UA_WINTER_DATA with {k} or {month}, as in gamma_windows.py). Output: results/extended/ratio/scenario_robust.csv.
"""
import json
import os
from pathlib import Path

import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from app.own.gammacheck import archive_rules  # noqa: E402
EXT = ROOT / "results" / "extended"
E_CAP, LIM, BAND, W, DT = 72.0, 48.0, 0.8, 4, 0.25
B_TUNED = 0.030298875705271892
MONTH = {1: "2025-10", 2: "2025-11", 3: "2025-12", 4: "2026-01", 5: "2026-02", 6: "2026-03"}
SUMMER = os.environ.get("UA_SUMMER_DATA", os.environ.get("UA_KAIROS", "work") + "/B/results/replay/data.pkl")
WINTER = os.environ.get("UA_WINTER_DATA", os.environ.get("UA_KAIROS", "work") + "/W/m{k}/results/rewind/data.pkl")
# summer periods (code/config: calibration 21 d, tuning 35 d, development 22 d from 2026-06-15 22:00 UTC; final test to the
# end of the plant data less one horizon)
T0 = pd.Timestamp("2026-06-15 22:00", tz="UTC")
PER = {"tuning": (T0 + pd.Timedelta(days=21), T0 + pd.Timedelta(days=56)),
       "development": (T0 + pd.Timedelta(days=56), T0 + pd.Timedelta(days=78)),
       "final_test": (T0 + pd.Timedelta(days=78), pd.Timestamp("2026-09-29 03:45", tz="UTC"))}


def routed(x: pd.DataFrame) -> pd.Series:
    return ((x.solar_mu + x.wind_mu) - (x.solar_real + x.wind_real)).clip(-LIM, LIM)    # B: both signs, power range


def deficits(e: pd.Series) -> pd.Series:
    return (e.rolling(W).sum() * DT).clip(lower=0).dropna()                           # Wh, overlapping 1-h windows


def rules(look: pd.Series) -> dict:
    """The three ex-ante rules of app/own/gammacheck.archive_rules (the same code as the simulator)."""
    r = archive_rules(look, E_CAP)
    return {"backoff_resized": r["resized"], "scenario": r["scenario"], "robust": r["robust"], "lookback_windows": r["windows"]}


def winter(k: int) -> pd.Series:
    p = WINTER.format(k=k, month=MONTH[k])
    cal = json.loads((Path(p).parent / "calibration.json").read_text())
    return routed(pd.read_pickle(p)).loc[pd.Timestamp(cal["test_start"]):pd.Timestamp(cal["test_end"])]


def main():
    eg = pd.read_csv(EXT / "ratio/effective_gamma.csv").set_index("period")
    s = routed(pd.read_pickle(SUMMER))
    cases = []
    for name, (a, b) in PER.items():
        cases.append((name, s.loc[a:b], s.loc[max(s.index[0], a - pd.Timedelta(days=30)):a - pd.Timedelta(minutes=15)]))
    prev = winter(1)
    for k in range(2, 7):
        cur = winter(k)
        cases.append((f"winter m{k}", cur, prev)); prev = cur
    rows = []
    for name, cur, look in cases:
        r_e = float(deficits(cur).quantile(0.95)) / (E_CAP * BAND)
        rr = rules(look)
        row = {"period": name, "r_E": r_e, "lookback_windows": rr.pop("lookback_windows")}
        for rule, b in {"backoff_tuned": B_TUNED, **rr}.items():
            row[f"b_{rule}"] = b
            row[f"Gamma_{rule}"] = r_e * BAND / b
            row[f"band_locked_pct_{rule}"] = 100 * 2 * b / BAND
        row["Gamma_UA"] = float(eg.loc[name, "Gamma_UA"]) if name in eg.index else np.nan
        rows.append(row)
    out = pd.DataFrame(rows)
    out.to_csv(EXT / "ratio/scenario_robust.csv", index=False)
    cols = ["period", "r_E"] + [f"{p}_{r}" for r in ("backoff_tuned", "backoff_resized", "scenario", "robust") for p in ("b", "Gamma")] + ["Gamma_UA"]
    print(out[cols].round(3).to_string(index=False))


if __name__ == "__main__":
    main()
