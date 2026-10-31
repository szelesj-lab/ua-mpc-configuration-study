"""Calibration and tail diagnostics (no controller runs): PIT/coverage per period and channel, and the 95% quantile of the
standardised error accumulated over the re-planning window (empirical vs Gaussian AR(1) 1.645)."""
import os as _os  # paths: see experiments/extended/README.md
UA_WORK = _os.environ.get("UA_WORK", "work/extended")      # working tree (layout in README)
UA_CODE = _os.environ.get("UA_CODE", "code")               # controller code (Kairos)
UA_KAIROS = _os.environ.get("UA_KAIROS", "work")           # work copies made by experiments/make_work.sh
import sys, json, numpy as np, pandas as pd
S = UA_WORK
sys.path.insert(0, f"{S}/rev3"); import common
from replay import build_data, named_periods
from scipy.stats import norm
cfg = common.base_cfg(); data = build_data(cfg); per = named_periods(data, cfg)
kappa = float(cfg["mpc"]["sigma_scale"]); phi = float(cfg["mpc"].get("error_autocorr", 0.857))
def diag(d, kap, ph, name, W=4):
    rows = []
    mu = d.solar_mu + d.wind_mu; R = d.solar_real + d.wind_real
    sd = kap * np.sqrt(d.solar_sd ** 2 + d.wind_sd ** 2)
    day = (d.solar_mu > 0.05) | (d.wind_mu > 0.05)
    # hourly means as in the paper's coverage table
    h = pd.DataFrame({"e": (R - mu), "sd": sd}).resample("1h").mean().dropna()
    h = h[h.sd > 1e-6]
    z = h.e / h.sd
    pit = norm.cdf(z)
    hist = np.histogram(pit, bins=10, range=(0, 1))[0] / len(pit)
    cov1, cov2 = float((z.abs() <= 1).mean()), float((z.abs() <= 2).mean())
    # accumulated error over the window, standardised by the AR(1) std of the sum
    e = (mu - R).to_numpy(); s = sd.to_numpy(); n = len(e)
    acc = pd.Series(e).rolling(W).sum().to_numpy()
    var = np.zeros(n)
    for i in range(W - 1, n):
        ss = s[i - W + 1:i + 1]; M = ph ** np.abs(np.subtract.outer(np.arange(W), np.arange(W)))
        var[i] = ss @ M @ ss
    ok = var > 1e-6
    zw = acc[ok] / np.sqrt(var[ok])
    q95 = float(np.nanquantile(zw, 0.95)); q05 = float(np.nanquantile(zw, 0.05))
    return dict(period=name, kappa=kap, phi=ph, hours=len(h), cov1=round(100 * cov1, 1), cov2=round(100 * cov2, 1),
                pit_lowest_decile=round(100 * hist[0], 1), pit_highest_decile=round(100 * hist[-1], 1),
                pit_max_dev=round(100 * np.abs(hist - 0.1).max(), 1),
                zW_q95=round(q95, 2), zW_q05=round(q05, 2))
out = []
for name in ("calibration", "tuning", "development", "final_test"):
    if name in per:
        t0, t1 = per[name]; out.append(diag(data.loc[t0:t1], kappa, phi, name))
W = UA_KAIROS + "/W"
for m in range(1, 7):
    cal = json.load(open(f"{W}/m{m}/results/rewind/calibration.json")); d = pd.read_pickle(f"{W}/m{m}/results/rewind/data.pkl")
    d = d.loc[pd.Timestamp(cal["test_start"]):pd.Timestamp(cal["test_end"])]
    out.append(diag(d, cal["sigma_scale"], cal["error_autocorr_step"], f"winter m{m}"))
df = pd.DataFrame(out); print(df.to_string()); df.to_csv(f"{S}/rev5/diag/calib_diag.csv", index=False)
print(list(per.keys()))
