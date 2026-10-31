"""Item 1: the r_E rule (tie if r_E <= 0.023, difference if r_E >= 0.047; fixed on the development map) applied to
periods it was not set on: the summer final test (A, B, C) and the six winter months (B, C)."""
import os as _os  # paths: see experiments/revision/README.md
UA_WORK = _os.environ.get("UA_WORK", "work/revision")      # revision working tree (layout in README)
UA_CODE = _os.environ.get("UA_CODE", "code")               # controller code (Kairos)
UA_KAIROS = _os.environ.get("UA_KAIROS", "work")           # work copies made by experiments/make_work.sh
import sys, json, glob, numpy as np, pandas as pd
sys.path.insert(0, UA_CODE + "/src")
from MPC_costfunction import _mpc_params
from replay import load_config, with_overrides, build_data, named_periods
S = UA_WORK
O = f"{S}/rev2/rE"
ARCH = {"A": "renewable_buffer", "B": "self_consumption", "C": "setpoint"}
SC = {"SoC": {"E_capacity": 72}, "degradation": {"c_deg": 2e-5}, "limits": {"p_charge_max_w": 24.0},
      "replay": {"plant": "bms", "demand_profile_csv": "industrial_load_profile.csv", "demand_mean_w": 10.788}}

def rE(d, cfg, replan=4):   # = stages.indicators_gen with err = 1
    p = _mpc_params(cfg)
    real = d.solar_real + d.wind_real; mu = d.solar_mu + d.wind_mu; e = mu - real
    usable = p["e_cap"] * (p["soc_max"] - p["soc_min"]) if False else 72 * (p["soc_max"] - p["soc_min"])
    if p["architecture"] == "renewable_buffer":
        x = np.minimum(e.clip(lower=0), np.minimum(mu.clip(lower=0), p["p_ch_max"]))
    elif p["architecture"] == "self_consumption":
        lim = p["p_ch_max"] + p["p_dis_max"]; x = e.clip(-lim, lim)
    else:
        x = e * 0.0
    dn = (x.rolling(replan).sum() * 0.25).clip(lower=0)
    up = ((-x).rolling(replan).sum() * 0.25).clip(lower=0) if p["architecture"] == "self_consumption" else dn * 0
    return float(dn.quantile(0.95)) / usable, float(up.quantile(0.95)) / usable

def cfg_for(X, extra=None):
    c = with_overrides(with_overrides(load_config(), SC), {"mpc": {"architecture": ARCH[X]}})
    return with_overrides(c, extra or {})

rows = []
# winter months
for X, (a, b) in (("B", ("det_bo", "ua")), ("C", ("det", "ua"))):
    for m in range(1, 7):
        cal = json.load(open(UA_KAIROS + f"/W/m{m}/results/rewind/calibration.json"))
        d = pd.read_pickle(UA_KAIROS + f"/W/m{m}/results/rewind/data.pkl")
        d = d[(d.index >= pd.Timestamp(cal["test_start"])) & (d.index < pd.Timestamp(cal["test_end"]))]
        r, ru = rE(d, cfg_for(X))
        ma = json.load(open(f"{S}/rw/results/{X}/{a}_m{m}.json"))["metrics"]; mb = json.load(open(f"{S}/rw/results/{X}/{b}_m{m}.json"))["metrics"]
        rows.append(dict(period=f"winter m{m}", start=cal["test_start"][:10], config=X, ref=a, r_E=r, r_E_up=ru, days=ma["days"],
                         ref_prot_h=ma["protection_h"], ua_prot_h=mb["protection_h"],
                         ref_cost=ma["net_cost_eur"], ua_cost=mb["net_cost_eur"]))
# summer final test (load is the same for every configuration; forecasts from the summer models)
for X in ("A", "B", "C"):
    import os; os.chdir(UA_KAIROS + f"/{X}")
    cfg = cfg_for(X); data = build_data(cfg); t0, t1 = named_periods(data, cfg)["final_test"]
    d = data[(data.index >= t0) & (data.index < t1)]
    r, ru = rE(d, cfg)
    ref = "det_bo" if X != "C" else "det"
    ma = json.load(open(f"{S}/ind/results_{X}/final_test/steps/bms_{ref}.json"))["metrics"]
    mb = json.load(open(f"{S}/ind/results_{X}/final_test/steps/bms_ua.json"))["metrics"]
    rows.append(dict(period="summer final test", start=str(t0)[:10], config=X, ref=ref, r_E=r, r_E_up=ru, days=ma["days"],
                     ref_prot_h=ma["protection_h"], ua_prot_h=mb["protection_h"], ref_cost=ma["net_cost_eur"], ua_cost=mb["net_cost_eur"]))
r = pd.DataFrame(rows)
r["pred"] = np.where(r.r_E <= 0.023, "tie", np.where(r.r_E >= 0.047, "difference", "between"))
r.to_csv(f"{O}/rE_outofsample.csv", index=False)
print(r.round(4).to_string(index=False))
