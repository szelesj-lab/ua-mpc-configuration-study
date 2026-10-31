"""Pre-registration of the r_E / Gamma predictions (written before any of these runs)."""
import os as _os  # paths: see experiments/revision/README.md
UA_WORK = _os.environ.get("UA_WORK", "work/revision")      # revision working tree (layout in README)
UA_CODE = _os.environ.get("UA_CODE", "code")               # controller code (Kairos)
UA_KAIROS = _os.environ.get("UA_KAIROS", "work")           # work copies made by experiments/make_work.sh
import sys, json, hashlib; sys.path.insert(0, UA_WORK + "/rev3")
from common import *
OUT = f"{S}/rev3/preregistration_rE.json"
assert not os.path.exists(OUT), "exists, not changed"
cfg = base_cfg(); data = build_data(cfg); t0, t1 = named_periods(data, cfg)["development"]
G = lambda r: r * 0.8 / B_TUNED
def pred(r):
    g = G(r)
    return {"r_E": r, "Gamma": g, "pred_gamma": "difference" if g > 1 else "tie",
            "pred_bounds": "tie" if r <= 0.0351 else ("difference" if r >= 0.047 else "none")}
cells = []
for cap, err in [(60,1.0),(48,1.0),(72,1.5),(90,2.0),(72,1.75),(60,1.5),(48,1.25),(48,1.5)]:
    r = rE(data, cfg, t0, t1, cap, err)["r_E_gen"]
    cells.append({"experiment": "grid", "cap": cap, "err": err, "rp": 4, **pred(r)})
for cap, rp, phis in [(108,16,(0.5,0.857,0.98)),(144,16,(0.5,0.98))]:
    for phi in phis:
        r = rE(synth(data, phi), cfg, t0, t1, cap, rp=rp)["r_E_gen"]
        cells.append({"experiment": "correlation", "cap": cap, "err": 1.0, "rp": rp, "phi": phi, "seed": 7, **pred(r)})
mp = pd.read_csv(f"{S}/ind/results_B/decision_map_recovery/map.csv")
sweep = []
for cap, err, rp in [(36,1.0,4),(72,2.0,4),(36,2.0,4),(72,1.0,16)]:
    r = float(mp[(mp.cap_wh==cap)&(mp.error_scale==err)&(mp.replan_steps==rp)].r_E_gen.iloc[0])
    for g in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0):
        b = r * 0.8 / g
        sweep.append({"experiment": "b_sweep", "cap": cap, "err": err, "rp": rp, "r_E": r, "Gamma": g, "b": b,
                      "pred_gamma": "difference" if g > 1 else "tie"})
reg = {"written_utc": pd.Timestamp.now(tz="UTC").isoformat(), "configuration": "B (self-consumption), BMS plant, development period",
 "controllers": "DET-BO and UA-MPC with the points selected in the summer protocol; b-sweep: DET-BO with b changed, UA-MPC run of the decision map as reference",
 "b_tuned": B_TUNED,
 "label_rule": "difference if back-off protection >= UA protection + 4 h and >= 2 x UA protection (22 days), else tie (same rule as the revision of 30 Oct 2026)",
 "gamma": "Gamma = r_E (x_max - x_min) / b with r_E the generalised deficit ratio (95 % quantile, window = re-planning interval)",
 "predictions": {
   "P1 (Gamma rule)": "every grid and correlation cell: tie if Gamma < 1, difference if Gamma > 1, with b = b_tuned",
   "P2 (empirical bounds)": "r_E <= 0.035: tie; r_E >= 0.047: difference; between: no prediction",
   "P3 (b-sweep)": "DET-BO with b such that Gamma <= 1 ties UA-MPC; with Gamma >= 1.5 differs from it; at Gamma = 1.25 no firm prediction",
   "P4 (b-sweep cost)": "the net cost of DET-BO rises as b rises (Gamma falls) in each b-sweep cell",
   "P5 (correlation)": "at the same error variance, a higher error autocorrelation raises r_E and can turn a tie into a difference; the mean sigma is identical in all correlation cells"},
 "cells": cells, "b_sweep": sweep}
s = json.dumps(reg, indent=1, default=float); open(OUT, "w").write(s)
print(s); print("sha256", hashlib.sha256(s.encode()).hexdigest())
