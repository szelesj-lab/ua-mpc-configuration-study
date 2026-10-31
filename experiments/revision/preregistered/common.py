"""Shared set-up for the r_E validation experiments (configuration B, development period, BMS plant)."""
import os as _os  # paths: see experiments/revision/README.md
UA_WORK = _os.environ.get("UA_WORK", "work/revision")      # revision working tree (layout in README)
UA_CODE = _os.environ.get("UA_CODE", "code")               # controller code (Kairos)
UA_KAIROS = _os.environ.get("UA_KAIROS", "work")           # work copies made by experiments/make_work.sh
import json, os, sys
os.environ.setdefault("KAIROS_ROOT", UA_KAIROS + "/B")
os.environ.setdefault("KAIROS_SCEN", json.dumps({"limits": {"p_charge_max_w": 24.0}, "mpc": {"architecture": "self_consumption"},
    "replay": {"demand_profile_csv": "industrial_load_profile.csv", "demand_mean_w": 10.788, "plant": "bms"}}))
S = UA_WORK
sys.path.insert(0, f"{S}/ind")
import numpy as np, pandas as pd
import engine
from engine import SCEN, point
from replay import build_data, named_periods, with_overrides, load_config
import stages

REG = json.load(open(f"{S}/ind/results_B/final_test/preregistration.json"))["selected"]["primary_threshold_pp_h"]
BO, UA = point("det_bo", REG["det_bo"]["trial"]), point("ua", REG["ua"]["trial"])
B_TUNED = float(BO["mpc"]["soc_backoff"])

def base_cfg():
    return with_overrides(with_overrides(load_config(), SCEN), {"replay": {"plant": "bms"}})

def synth(data, phi, seed=7):
    """Synthetic forecast error with the calibrated variance and AR(1) coefficient phi per 15-min step:
    realised power = max(mu - kappa sigma_GP z, 0), z standard AR(1). The forecast (mu, sigma) is unchanged."""
    kappa = float(base_cfg()["mpc"]["sigma_scale"])
    d = data.copy(); n = len(d); rng = np.random.default_rng(seed)
    z = np.empty(n); z[0] = rng.normal(); w = rng.normal(0, np.sqrt(1 - phi ** 2), n)
    for k in range(1, n):
        z[k] = phi * z[k - 1] + w[k]
    mu = (d.solar_mu + d.wind_mu).to_numpy(); sd = kappa * np.sqrt(d.solar_sd ** 2 + d.wind_sd ** 2).to_numpy()
    d["solar_real"] = np.clip(mu - sd * z, 0, None); d["wind_real"] = 0.0
    return d

def rE(data, cfg, t0, t1, cap, err=1.0, rp=4):
    c = with_overrides(cfg, {"SoC": {"E_capacity": cap}, "replay": {"replan_steps": rp}})
    return stages.indicators_gen(data, c, t0, t1, cap, err, rp)
