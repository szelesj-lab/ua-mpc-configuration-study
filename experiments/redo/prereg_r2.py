"""Replication with south-facing PV, record R2: the second load case (configuration A, weekly production schedule,
physical plant). Written after the tuning and the selection of this case (run_second_redo.sh with SELECT_ONLY=1) and
before its decision map, its final test and test T2.

It fixes the back-off b2 of the selected DET-BO, Gamma = r_E (x_max - x_min) / b2 for every map cell and period of the
case (r_E: generalised 1-h deficit ratio, renewable-buffer routing, 95 % quantile; from the data alone) and the T2
sweep. The T2 cells are the four cells of the first study's T2, fixed here before any map run of the replication. The
predictions are those of the first study's T2 (P10, P11), unchanged, and P1 carried over to this case with its label
rule in band violation (difference if DET-BO violation >= UA-MPC violation + 1 pp h and >= 2 x UA-MPC violation).

    KAIROS_ROOT=<work>/P STUDY_RESULTS=<out>/second python experiments/redo/prereg_r2.py   (refuses to overwrite)
    DRY=<path> writes the record to <path> instead (for checking the script; not the record)
"""
import hashlib
import json
import os
import sys
from pathlib import Path

import pandas as pd

PKG = Path(__file__).resolve().parents[2]
OUT = Path(os.environ["DRY"]) if os.environ.get("DRY") else PKG / "results/redo/preregistration_R2.json"
R1 = PKG / "results/redo/preregistration_R1.json"
SECOND = Path(os.environ["STUDY_RESULTS"])
sys.path.insert(0, str(PKG / "experiments/summer"))
from engine import SCEN, point  # noqa: E402
from replay import build_data, load_config, named_periods, with_overrides  # noqa: E402
from stages import CELLS, indicators_gen  # noqa: E402

BAND = 0.8
T2_CELLS = [(36, 1.0, 4), (72, 2.0, 4), (36, 2.0, 4), (72, 1.0, 16)]   # the cells of the first study's T2
T2_G = (0.5, 0.75, 1.0, 1.5, 2.0)


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def p1(g):
    return "tie" if g < 1 else "difference"


def main():
    assert not OUT.exists(), "R2 exists: written once"
    selp = SECOND / "final_test/preregistration.json"
    sel = json.loads(selp.read_text())
    P = sel["selected"]["primary_threshold_pp_h"]
    bo = point("det_bo", P["det_bo"]["trial"]); b2 = float(bo["mpc"]["soc_backoff"])
    G = lambda r: r * BAND / b2  # noqa: E731

    base = with_overrides(load_config(), SCEN)
    assert base["replay"]["plant"] == "physical" and not base["replay"].get("demand_profile_csv")
    data = build_data(base); per = named_periods(data, base)

    def rE(cap, err, rp, period="development"):
        t0, t1 = per[period]
        c = with_overrides(base, {"SoC": {"E_capacity": cap}, "replay": {"replan_steps": rp}})
        return float(indicators_gen(data, c, t0, t1, cap, err, rp)["r_E_gen"])

    periods = []
    for n in ("tuning", "development", "final_test"):
        r = rE(72, 1.0, 4, n); periods.append({"period": n, "r_E": r, "Gamma": G(r), "P1": p1(G(r))})
    cells = []
    for cap, err, rp in CELLS:
        r = rE(cap, err, rp); cells.append({"cap_wh": cap, "error_scale": err, "replan_steps": rp, "r_E": r,
                                            "Gamma": G(r), "P1": p1(G(r))})
    rmap = {(c["cap_wh"], c["error_scale"], c["replan_steps"]): c["r_E"] for c in cells}
    t2 = [{"cap": cap, "err": err, "rp": rp, "r_E": rmap[(cap, err, rp)], "Gamma": g, "b": rmap[(cap, err, rp)] * BAND / g,
           "pred": "tie" if g <= 1 else "difference"} for cap, err, rp in T2_CELLS for g in T2_G]

    reg = {"written_utc": pd.Timestamp.now(tz="UTC").isoformat(),
           "record": "R2, replication with south-facing PV, second load case; after its selection, before its decision "
                     "map, its final test and T2",
           "R1_sha256": sha(R1), "selection_sha256": sha(selp),
           "selected_primary": {c: v["trial"] for c, v in P.items()},
           "selection_met_threshold": {c: v["met_threshold"] for c, v in P.items()},
           "b2": b2,
           "gamma": "Gamma = r_E (x_max - x_min) / b2, r_E the generalised 1-h deficit ratio (renewable-buffer routing, "
                    "95 % quantile) of the development period unless stated, x_max - x_min = 0.8",
           "label_rule": "difference if DET-BO violation >= UA-MPC violation + 1 pp h and >= 2 x UA-MPC violation, else "
                         "tie (physical plant)",
           "periods": periods, "map_cells": cells,
           "T2": {"design": "development period, physical plant: DET-BO of the selected point with b set for Gamma in "
                            "{0.5, 0.75, 1, 1.5, 2} in the four cells of the first study's T2, against the UA-MPC run of "
                            "the same cell of the decision map",
                  "cells": t2},
           "predictions": {
               "P1": "every map cell and the final test of this case: tie if Gamma < 1, difference if Gamma > 1, with b = b2",
               "P10": "T2: tie for Gamma <= 1, difference for Gamma >= 1.5",
               "P11": "T2: the net cost of DET-BO rises as b rises in each cell"}}
    txt = json.dumps(reg, indent=1, default=float)
    OUT.parent.mkdir(parents=True, exist_ok=True); OUT.write_text(txt)
    print(txt[:3000]); print("sha256", hashlib.sha256(txt.encode()).hexdigest())


if __name__ == "__main__":
    main()
