"""Channel calibration (review 2): per-channel sigma scales fitted on the calibration period; coverage by channel,
period, error-magnitude quarter and time of day, for the single scale and the per-channel scales."""
import os as _os  # paths: see experiments/extended/README.md
UA_WORK = _os.environ.get("UA_WORK", "work/extended")      # working tree (layout in README)
UA_CODE = _os.environ.get("UA_CODE", "code")               # controller code (Kairos)
UA_KAIROS = _os.environ.get("UA_KAIROS", "work")           # work copies made by experiments/make_work.sh
import sys, json, numpy as np, pandas as pd
sys.path.insert(0, "src")
from replay import build_data, named_periods, load_config, with_overrides
O = UA_WORK + "/rev2/calib"
cfg = with_overrides(load_config(), {"replay": {"demand_profile_csv": "industrial_load_profile.csv", "demand_mean_w": 10.788}})
data = build_data(cfg); per = named_periods(data, cfg); K = float(cfg["mpc"]["sigma_scale"])
def hourly(a, b):
    d = data[(data.index >= a) & (data.index < b)]; return d[d.index.minute == 0]
c = hourly(*per["calibration"]); ks = {}
for ch in ("solar", "wind"):
    ok = c[f"{ch}_sd"] > 0; ks[ch] = float(np.sqrt(((c[f"{ch}_real"] - c[f"{ch}_mu"])[ok] ** 2).mean() / (c[f"{ch}_sd"][ok] ** 2).mean()))
rows = []
for pn in ("development", "final_test"):
    a, b = per[pn]; d = data[(data.index >= a) & (data.index < b)].resample("1h").mean().dropna(subset=["solar_mu"])  # hourly means, as Table 5
    loc = d.index.tz_convert("Europe/Budapest").hour
    for ch in ("solar", "wind"):
        m = (d.solar_mu > 0).to_numpy() if ch == "solar" else np.ones(len(d), bool)
        e = (d[f"{ch}_real"] - d[f"{ch}_mu"]).to_numpy()[m]; s = d[f"{ch}_sd"].to_numpy()[m]; h = np.asarray(loc)[m]
        big = np.abs(e) >= np.quantile(np.abs(e), 0.75)
        groups = {"all": np.ones(len(e), bool), "largest quarter": big,
                  "06-10 h": (h >= 6) & (h < 10), "10-14 h": (h >= 10) & (h < 14), "14-18 h": (h >= 14) & (h < 18),
                  "18-06 h": (h >= 18) | (h < 6)}
        for g, sel in groups.items():
            if sel.sum() < 10: continue
            for name, k in (("single", K), ("channel", ks[ch])):
                ee, ss = e[sel], k * s[sel]
                rows.append(dict(period=pn, channel=ch, group=g, scale=name, kappa=k, hours=int(sel.sum()),
                                 cov1=100 * np.mean(np.abs(ee) <= ss), cov2=100 * np.mean(np.abs(ee) <= 2 * ss)))
r = pd.DataFrame(rows); r.to_csv(f"{O}/calib_conditional.csv", index=False)
json.dump({"kappa_total": K, "kappa_channel": ks}, open(f"{O}/kappa_channel.json", "w"), indent=1)
print(ks); print(r.round(1).to_string(index=False))
