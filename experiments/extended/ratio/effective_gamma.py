"""Gamma of the UA-MPC margin: Gamma_UA = r_E (x_max - x_min) / mean margin, with the margin z k_ch s^W_k of the
selected UA-MPC point of configuration B (calibrated sigma, AR(1), W = 4), against Gamma of the tuned back-off."""
import os as _os  # paths: see experiments/extended/README.md
UA_WORK = _os.environ.get("UA_WORK", "work/extended")      # working tree (layout in README)
UA_CODE = _os.environ.get("UA_CODE", "code")               # controller code (Kairos)
UA_KAIROS = _os.environ.get("UA_KAIROS", "work")           # work copies made by experiments/make_work.sh
import sys, json, numpy as np, pandas as pd
S = UA_WORK
sys.path.insert(0, f"{S}/rev3"); import common
from replay import build_data, named_periods
cfg = common.base_cfg(); data = build_data(cfg); per = named_periods(data, cfg)
z = float(common.UA["mpc"]["soc_chance_z"]); kch = 0.95 * 0.25 / 72; b = common.B_TUNED
def mean_margin(d, kap, ph, W=4):
    s = (kap * np.sqrt(d.solar_sd ** 2 + d.wind_sd ** 2)).to_numpy(); M = ph ** np.abs(np.subtract.outer(np.arange(W), np.arange(W)))
    v = np.array([s[i - W + 1:i + 1] @ M @ s[i - W + 1:i + 1] for i in range(W - 1, len(s))])
    return float((z * kch * np.sqrt(v)).mean())
def rE(d):
    e = ((d.solar_mu + d.wind_mu) - (d.solar_real + d.wind_real)).clip(-48, 48)
    return float((e.rolling(4).sum() * 0.25).clip(lower=0).quantile(0.95)) / (72 * 0.8)
rows = []
for name in ("tuning", "development", "final_test"):
    t0, t1 = per[name]; d = data.loc[t0:t1]; m = mean_margin(d, 1.345, 0.857); r = rE(d)
    rows.append(dict(period=name, r_E=r, mean_margin=m, Gamma_UA=r * 0.8 / m, Gamma_BO=r * 0.8 / b))
W = UA_KAIROS + "/W"
for i in range(1, 7):
    cal = json.load(open(f"{W}/m{i}/results/rewind/calibration.json"))
    d = pd.read_pickle(f"{W}/m{i}/results/rewind/data.pkl").loc[pd.Timestamp(cal["test_start"]):pd.Timestamp(cal["test_end"])]
    m = mean_margin(d, cal["sigma_scale"], cal["error_autocorr_step"]); r = rE(d)
    rows.append(dict(period=f"winter m{i}", r_E=r, mean_margin=m, Gamma_UA=r * 0.8 / m, Gamma_BO=r * 0.8 / b))
df = pd.DataFrame(rows); df.to_csv(f"{S}/rev5/link/effective_gamma.csv", index=False); print(df.round(4).to_string())
