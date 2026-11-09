"""Replication with south-facing PV, record R1: written after the summer tuning and selection of B, C and A and before
the decision map, the final test, the winter replay and the tests T1 and T3 of the replication.

R0 fixed the data, the calibration, r_E of every period and map cell and the predictions that need no tuned weights.
R1 adds what needs the selected points: the back-off b_tuned of the selected DET-BO of B, Γ = r_E (x_max - x_min) / b
for every cell and period of B, and the back-off sizes of T1 (re-sized) and T3 (scenario, robust), which follow from
the forecast archive alone. The predictions use the rules of the manuscript unchanged: P1 (tie if Γ < 1, difference if
Γ > 1), P3 (b-sweep: tie if Γ <= 1, difference if Γ >= 1.5), P6-P9 (T1), P12-P16 (T3), the label rule of the decision
map (difference if DET-BO needs >= 4 h more protection than UA-MPC and at least twice as many). The second load case
(T2) needs its own selection and follows in a later record.

    KAIROS_ROOT=<work>/B UA_KAIROS=<work> python experiments/redo/prereg_r1.py   (refuses to overwrite)
    DRY=<path> writes the record to <path> instead (for checking the script; not the record)
"""
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PKG = Path(__file__).resolve().parents[2]
OUT = Path(os.environ["DRY"]) if os.environ.get("DRY") else PKG / "results/redo/preregistration_R1.json"
R0 = PKG / "results/redo/preregistration_R0.json"
SUMMER = Path(os.environ.get("STUDY_OUT", "/home/claude/redo/out/summer"))
sys.path.insert(0, str(PKG / "experiments/summer"))
from engine import SCEN, point  # noqa: E402
from replay import build_data, load_config, named_periods, with_overrides  # noqa: E402
from stages import indicators_gen  # noqa: E402

spec = importlib.util.spec_from_file_location("sr", PKG / "experiments/extended/ratio/scenario_robust.py")
sr = importlib.util.module_from_spec(spec); spec.loader.exec_module(sr)
BAND, GT = 0.8, 0.75
GRID = [(60, 1.0), (48, 1.0), (72, 1.5), (90, 2.0), (72, 1.75), (60, 1.5), (48, 1.25), (48, 1.5)]
CORR = [(108, 16, (0.5, 0.857, 0.98)), (144, 16, (0.5, 0.98))]
SWEEP_CELLS = [(36, 1.0, 4), (72, 2.0, 4), (36, 2.0, 4), (72, 1.0, 16)]   # the B cells R0 predicts as differences
SWEEP_G = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def p1(g):
    return "tie" if g < 1 else "difference"


def p3(g):
    return "tie" if g <= 1 + 1e-9 else ("difference" if g >= 1.5 - 1e-9 else None)


def synth(data, phi, kappa, seed=7):
    """Synthetic error with the calibrated variance and AR(1) coefficient phi (as the first study, common.synth)."""
    d = data.copy(); n = len(d); rng = np.random.default_rng(seed)
    z = np.empty(n); z[0] = rng.normal(); w = rng.normal(0, np.sqrt(1 - phi ** 2), n)
    for k in range(1, n):
        z[k] = phi * z[k - 1] + w[k]
    mu = (d.solar_mu + d.wind_mu).to_numpy(); sd = kappa * np.sqrt(d.solar_sd ** 2 + d.wind_sd ** 2).to_numpy()
    d["solar_real"] = np.clip(mu - sd * z, 0, None); d["wind_real"] = 0.0
    return d


def main():
    assert not OUT.exists(), "R1 exists: written once"
    r0 = json.loads(R0.read_text())
    sel = {X: json.loads((SUMMER / X / "final_test/preregistration.json").read_text()) for X in ("B", "C", "A")
           if not os.environ.get("DRY") or (SUMMER / X / "final_test/preregistration.json").exists()}
    P = sel["B"]["selected"]["primary_threshold_pp_h"]
    bo = point("det_bo", P["det_bo"]["trial"]); b = float(bo["mpc"]["soc_backoff"])
    G = lambda r: r * BAND / b  # noqa: E731

    per = {(x["config"], x["period"]): x["r_E_gen"] for x in r0["periods"]}
    periods = [{"period": n, "r_E": per[("B", n)], "Gamma": G(per[("B", n)]), "P1": p1(G(per[("B", n)]))}
               for n in ("tuning", "development", "final_test")]
    cells = [{"cap_wh": x["cap_wh"], "error_scale": x["error_scale"], "replan_steps": x["replan_steps"], "r_E": x["r_E_gen"],
              "Gamma": G(x["r_E_gen"]), "P1": p1(G(x["r_E_gen"])), "R0_bounds": x["prediction"]}
             for x in r0["map_cells"] if x["config"] == "B"]

    base = with_overrides(with_overrides(load_config(), SCEN), {"replay": {"plant": "bms"}})
    kappa = float(base["mpc"]["sigma_scale"])
    data = build_data(base); t0, t1 = named_periods(data, base)["development"]

    def rE(d, cap, err=1.0, rp=4):
        c = with_overrides(base, {"SoC": {"E_capacity": cap}, "replay": {"replan_steps": rp}})
        return indicators_gen(d, c, t0, t1, cap, err, rp)["r_E_gen"]
    new = []
    for cap, err in GRID:
        r = rE(data, cap, err); new.append({"experiment": "grid", "cap": cap, "err": err, "rp": 4, "r_E": r, "Gamma": G(r), "P1": p1(G(r))})
    for cap, rp, phis in CORR:
        for phi in phis:
            r = rE(synth(data, phi, kappa), cap, rp=rp)
            new.append({"experiment": "correlation", "cap": cap, "err": 1.0, "rp": rp, "phi": phi, "seed": 7, "r_E": r,
                        "Gamma": G(r), "P1": p1(G(r))})
    rmap = {(c["cap_wh"], c["error_scale"], c["replan_steps"]): c["r_E"] for c in cells}
    sweep = [{"cap": cap, "err": err, "rp": rp, "r_E": rmap[(cap, err, rp)], "Gamma": g, "b": rmap[(cap, err, rp)] * BAND / g,
              "P3": p3(g)} for cap, err, rp in SWEEP_CELLS for g in SWEEP_G]

    # winter: r_E of each walk-forward month (72 Wh, measured error, 1-h re-planning), same routing as T1/T3
    month = {k: sr.winter(k) for k in range(1, 7)}
    rEm = {k: float(sr.deficits(e).quantile(0.95)) / (sr.E_CAP * BAND) for k, e in month.items()}
    winter = [{"month": k, "label": sr.MONTH[k], "r_E": rEm[k], "Gamma": G(rEm[k]), "P1": p1(G(rEm[k]))} for k in range(1, 7)]
    t1m, t3m = [], []
    for k in range(2, 7):
        rr = sr.rules(month[k - 1])
        t1m.append({"month": k, "r_E_past": rEm[k - 1], "b": rEm[k - 1] * BAND / GT, "Gamma_realised": rEm[k] / rEm[k - 1] * GT})
        t3m.append({"month": k, "b_scenario": rr["scenario"], "b_robust": rr["robust"], "archive_windows": rr["lookback_windows"]})
    s = sr.routed(pd.read_pickle(sr.SUMMER)); a, _ = sr.PER["final_test"]
    look = sr.rules(s.loc[a - pd.Timedelta(days=30):a - pd.Timedelta(minutes=15)])
    t1s = {"r_E_tuning": per[("B", "tuning")], "b": per[("B", "tuning")] * BAND / GT,
           "Gamma_realised": per[("B", "final_test")] / per[("B", "tuning")] * GT}
    t3s = {"b_scenario": look["scenario"], "b_robust": look["robust"], "archive_windows": look["lookback_windows"]}

    reg = {"written_utc": pd.Timestamp.now(tz="UTC").isoformat(),
           "record": "R1, replication with south-facing PV; after the summer selection, before the decision map, the final "
                     "test, the winter replay and T1/T3",
           "R0_sha256": sha(R0),
           "selection_sha256": {X: sha(SUMMER / X / "final_test/preregistration.json") for X in sel},
           "selected_primary": {X: {c: v["trial"] for c, v in s_["selected"]["primary_threshold_pp_h"].items()} for X, s_ in sel.items()},
           "selection_met_threshold": {X: {c: v["met_threshold"] for c, v in s_["selected"]["primary_threshold_pp_h"].items()}
                                       for X, s_ in sel.items()},
           "b_tuned_B": b,
           "gamma": "Gamma = r_E (x_max - x_min) / b, r_E the generalised 1-h deficit ratio of R0 (95 % quantile), "
                    "x_max - x_min = 0.8, b = b_tuned_B unless stated",
           "label_rule": "difference if DET-BO protection >= UA-MPC protection + 4 h and >= 2 x UA-MPC protection, else tie",
           "B_periods": periods, "B_map_cells": cells, "new_cells": new, "b_sweep": sweep, "winter_B": winter,
           "T1": {"months": t1m, "summer_final_test": t1s, "Gamma_target": GT},
           "T3": {"months": t3m, "summer_final_test": t3s},
           "predictions": {
               "P1": "B: every map cell, the final test, every winter month (Nov-Mar, months 2-6) and every grid and "
                     "correlation cell: tie if Gamma < 1, difference if Gamma > 1, with b = b_tuned_B",
               "P2": "the bounds of R0 (tie if r_E <= 0.035, difference if r_E >= 0.047)",
               "P3": "b-sweep: DET-BO with b set for Gamma ties UA-MPC for Gamma <= 1 and differs for Gamma >= 1.5; none at 1.25",
               "P4": "b-sweep: the net cost of DET-BO rises as b rises in each sweep cell",
               "P5": "correlation cells: at the same error variance a higher error autocorrelation raises r_E",
               "P6": "Nov-Mar protection hours of the re-sized DET-BO (T1) <= those of the summer-tuned DET-BO",
               "P7": "Nov-Mar protection hours of the re-sized DET-BO <= those of UA-MPC",
               "P8": "Nov-Mar net cost of the re-sized DET-BO <= 1.02 x that of UA-MPC",
               "P9": "summer final test: the re-sized DET-BO ties UA-MPC by the label rule",
               "P12": "Nov-Mar protection hours with the scenario back-off <= those of the re-sized back-off (T1)",
               "P13": "Nov-Mar protection hours with the robust back-off <= those of the re-sized back-off (T1)",
               "P14": "Nov-Mar net cost: robust >= scenario >= re-sized, where the back-offs are ordered so in every month",
               "P15": "Nov-Mar net cost of the scenario and of the robust back-off above that of UA-MPC",
               "P16": "summer final test: scenario and robust back-off tie UA-MPC by the label rule and cost more than the "
                      "re-sized back-off",
               "Q1-Q3": "as R0 (unchanged)"},
           "planned_checks_without_prediction": "day-ahead (day 1) forecast replay of the B winter test and the B summer final "
                                                "test; three-seed tuning of B; QP solver check of the B final test"}
    txt = json.dumps(reg, indent=1, default=float)
    OUT.parent.mkdir(parents=True, exist_ok=True); OUT.write_text(txt)
    print(txt[:3000]); print("sha256", hashlib.sha256(txt.encode()).hexdigest())


if __name__ == "__main__":
    main()
