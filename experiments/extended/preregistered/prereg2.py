"""Pre-registration of tests T1 (back-off sized from past r_E) and T2 (Gamma test in the second load case)."""
import os as _os  # paths: see experiments/extended/README.md
UA_WORK = _os.environ.get("UA_WORK", "work/extended")      # working tree (layout in README)
UA_CODE = _os.environ.get("UA_CODE", "code")               # controller code (Kairos)
UA_KAIROS = _os.environ.get("UA_KAIROS", "work")           # work copies made by experiments/make_work.sh
import json, os, sys, hashlib
import numpy as np, pandas as pd
S = UA_WORK; W = UA_KAIROS + "/W"
OUT = f"{S}/rev3/t12/preregistration_T1T2.json"; assert not os.path.exists(OUT)
sys.path.insert(0, UA_CODE + "/src")
from MPC_costfunction import _mpc_params
from replay import load_config, with_overrides
SCB = {"SoC": {"E_capacity": 72}, "limits": {"p_charge_max_w": 24.0}, "mpc": {"architecture": "self_consumption"}}
p = _mpc_params(with_overrides(load_config(), SCB)); band = p["soc_max"] - p["soc_min"]
def rE_B(d, cap=72, rp=4):
    e = (d.solar_mu + d.wind_mu) - (d.solar_real + d.wind_real); lim = p["p_ch_max"] + p["p_dis_max"]
    x = e.clip(-lim, lim); dn = (x.rolling(rp).sum() * 0.25).clip(lower=0)
    return float(dn.quantile(0.95)) / (cap * band)
GT = 0.75
months = {}
for m in range(1, 7):
    cal = json.load(open(f"{W}/m{m}/results/rewind/calibration.json")); d = pd.read_pickle(f"{W}/m{m}/results/rewind/data.pkl")
    d = d[(d.index >= pd.Timestamp(cal["test_start"])) & (d.index < pd.Timestamp(cal["test_end"]))]
    months[m] = rE_B(d)
T1 = []
for m in range(2, 7):
    b = months[m - 1] * band / GT
    T1.append({"month": m, "r_E_past (month m-1)": months[m - 1], "b": b, "r_E_month (for reference)": months[m],
               "Gamma_realised": months[m] * band / b})
def tot(c, key):
    return sum(json.load(open(f"{S}/rw/results/B/{c}_m{m}.json"))["metrics"][key] for m in range(2, 7))
ref = {c: {"prot_h": tot(c, "protection_h"), "cost": tot(c, "net_cost_eur")} for c in ("det", "det_bo", "ua")}
# summer final test sized from the tuning period (cell 72 Wh, s = 1, 1-h re-planning)
rE_tun = 0.02647   # tuning-period r_E of the base cell (rev3/rE_periods.py); development 0.0234, final test 0.0345
T1s = {"r_E_tuning": rE_tun, "b": rE_tun * band / GT, "reference_UA_prot_h": 9.5, "reference_DETBO_tuned_prot_h": 9.75}
# T2: second load case (configuration A, production schedule, physical plant, violation in pp h)
K = UA_CODE + "/results/paper"
reg2 = json.load(open(f"{K}/final_test/preregistration.json"))["selected"]["primary_threshold_pp_h"]
mp = pd.read_csv(f"{K}/decision_map_recovery/map.csv")
b2 = 0.0191647101975199
T2 = []
for cap, err, rp in [(36, 1.0, 4), (72, 2.0, 4), (36, 2.0, 4), (72, 1.0, 16)]:
    r = float(mp[(mp.cap_wh == cap) & (mp.error_scale == err) & (mp.replan_steps == rp)].r_E.iloc[0])
    for g in (0.5, 0.75, 1.0, 1.5, 2.0):
        T2.append({"cap": cap, "err": err, "rp": rp, "r_E": r, "Gamma": g, "b": r * band / g,
                   "pred": "tie" if g <= 1 else "difference"})
reg = {"written_utc": pd.Timestamp.now(tz="UTC").isoformat(),
 "T1": {"design": "configuration B, BMS plant, walk-forward winter months Nov 2025 - Mar 2026: DET-BO with the summer-selected weights and a back-off re-sized at the start of each month from the r_E of the previous month (72 Wh, measured error, 1-h re-planning), b = r_E_past (x_max - x_min) / 0.75; SoC carried over, starting from the DET-BO chain at the end of October. Summer: the same rule on the final test with r_E of the tuning period.",
        "months": T1, "summer_final_test": T1s, "reference_Nov_Mar": ref,
        "predictions": {"P6": "total protection hours Nov-Mar of the re-sized DET-BO <= those of the summer-tuned DET-BO",
                        "P7": "total protection hours Nov-Mar of the re-sized DET-BO <= those of UA-MPC",
                        "P8": "net cost Nov-Mar of the re-sized DET-BO <= 1.02 x that of UA-MPC",
                        "P9": "summer final test: the re-sized DET-BO ties UA-MPC by the label rule (protection)"}},
 "T2": {"design": "second load case (configuration A, production schedule, physical plant, development period): DET-BO of the selected point with b set to Gamma in {0.5,0.75,1,1.5,2} in the four difference cells of its decision map, against the UA-MPC run of the same cell; b_tuned = 0.0192",
        "label_rule": "difference if DET-BO violation >= UA violation + 1 pp h and >= 2 x UA violation, else tie",
        "cells": T2,
        "predictions": {"P10": "tie for Gamma <= 1, difference for Gamma >= 1.5", "P11": "the net cost of DET-BO rises as b rises in each cell"}}}
s = json.dumps(reg, indent=1, default=float); open(OUT, "w").write(s); print(s[:3000]); print("sha256", hashlib.sha256(s.encode()).hexdigest())
