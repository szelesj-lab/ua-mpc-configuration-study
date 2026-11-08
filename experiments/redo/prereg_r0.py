"""Replication with south-facing PV, record R0: written after the GP training and the uncertainty calibration and
before any controller of the replication is tuned or replayed.

The first study used weather data downloaded with Open-Meteo azimuth 180 (a north-facing panel) by a configuration
error, while the manuscript described a south-facing panel. The replication repeats the whole protocol of the
manuscript on the same site, periods and load with azimuth 0 (south). This record fixes, before any replay:
the data (SHA-256), the calibration, r_E of every period and decision-map cell, and the predictions that follow from
the rules the manuscript states and that do not need the tuned weights. Predictions that need the tuned back-off b
follow in record R1, written after the selection and before the decision map and the final test.

    KAIROS_ROOT=<work>/B python experiments/redo/prereg_r0.py   (refuses to overwrite)
"""
import hashlib
import json
import os
import sys
from pathlib import Path

import pandas as pd

PKG = Path(__file__).resolve().parents[2]
OUT = PKG / "results/redo/preregistration_R0.json"
sys.path.insert(0, str(PKG / "experiments/summer"))
from engine import SCEN  # noqa: E402  (KAIROS_ROOT selects the work copy and its models)
from replay import build_data, load_config, named_periods, with_overrides  # noqa: E402
from stages import CELLS, indicators_gen  # noqa: E402

ARCH = {"A": "renewable_buffer", "B": "self_consumption", "C": "setpoint"}
TIE, DIFF = 0.035, 0.047


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():
    assert not OUT.exists(), "R0 exists: written once"
    data_dir = PKG / "code/data"
    base0 = with_overrides(load_config(), SCEN)
    rows, per_rows = [], []
    for X, arch in ARCH.items():
        base = with_overrides(base0, {"mpc": {"architecture": arch}, "replay": {"plant": "bms"}})
        data = build_data(base); per = named_periods(data, base)
        for name in ("tuning", "development", "final_test"):
            t0, t1 = per[name]
            per_rows.append({"config": X, "period": name, **indicators_gen(data, base, t0, t1, 72, 1.0, 4)})
        t0, t1 = per["development"]
        for cap, err, rp in CELLS:
            cfg = with_overrides(base, {"SoC": {"E_capacity": cap}, "replay": {"replan_steps": rp}})
            g = indicators_gen(data, cfg, t0, t1, cap, err, rp)
            pred = None
            if X == "C":
                pred = "tie"
            elif X == "B":
                pred = "tie" if g["r_E_gen"] <= TIE else ("difference" if g["r_E_gen"] >= DIFF else None)
            rows.append({"config": X, "cap_wh": cap, "error_scale": err, "replan_steps": rp, **g, "prediction": pred})
    cfg = load_config()
    reg = {"written_utc": pd.Timestamp.now(tz="UTC").isoformat(),
           "record": "R0, replication with south-facing PV (Open-Meteo azimuth 0, tilt 35)",
           "reason": "the first study used azimuth 180 (north-facing) by a configuration error; its results are archived",
           "replays_of_the_replication_so_far": 0,
           "data_sha256": {f: sha(data_dir / f) for f in ("Past_Forecast_Values.csv", "Past_Real_Values.csv",
                                                          "Past_Forecast_Values_day1.csv", "ENTSOE_HU_Dataset_2025.csv",
                                                          "industrial_load_profile.csv")},
           "calibration": {k: cfg["mpc"][k] for k in ("sigma_scale", "error_autocorr", "sigma_model")},
           "protocol": "as stated in the manuscript: ParEGO tuning 30 trials x 4 two-day episodes per controller, selection "
                       "rule 5 h (secondary 10 h) on the development period, BMS plant, label rule (difference if DET-BO needs "
                       ">= 4 h more protection than UA-MPC and at least twice as many), decision map on the development period, "
                       "one-shot final test, walk-forward winter test, the pre-registered tests and T1-T3 with the same rules",
           "rules": {"bounds": f"tie if r_E <= {TIE}, difference if r_E >= {DIFF} (the bounds of the manuscript, P2)",
                     "configuration": "set-point (C): r_E = 0, DET and UA-MPC tie by the label rule in every test"},
           "periods": per_rows, "map_cells": rows,
           "predictions": {"Q1": "C: DET and UA-MPC tie by the label rule in every map cell, in the final test and in "
                                 "every winter month",
                           "Q2": "B decision map: the label of each cell follows the bounds (column prediction; none between the bounds)",
                           "Q3": f"B final test: label by the bounds from its r_E ({next(r['r_E_gen'] for r in per_rows if r['config'] == 'B' and r['period'] == 'final_test'):.4f})"}}
    txt = json.dumps(reg, indent=1, default=float)
    OUT.parent.mkdir(parents=True, exist_ok=True); OUT.write_text(txt)
    print(txt[:2500]); print("sha256", hashlib.sha256(txt.encode()).hexdigest())


if __name__ == "__main__":
    main()
