"""Walk-forward month m: forecasts of the GPR trained before CUTOFF (models in <root>/models), the sigma
calibration on that model's own held-out tail before CUTOFF, and the replay data of the test month.

    python prep_month.py <root> <cutoff YYYY-MM-DD> <test end YYYY-MM-DD>

Writes <root>/results/rewind/{calibration.json, data.pkl}. Nothing after CUTOFF is used for the model or the
calibration; the test month only receives the forecasts.
"""
import json
import os
import sys

import pandas as pd

root, cutoff_s, end_s = sys.argv[1:4]
sys.path.insert(0, f"{root}/src")
import replay  # noqa: E402
from calibrate_uncertainty import calibrate, coverage, _hourly  # noqa: E402
from replay import build_data, load_config, with_overrides  # noqa: E402

TZ = "Europe/Budapest"
OUT = f"{root}/results/rewind"
os.makedirs(OUT, exist_ok=True)
cut = pd.Timestamp(cutoff_s, tz=TZ).tz_convert("UTC")
end = pd.Timestamp(end_s, tz=TZ).tz_convert("UTC")
first = pd.Timestamp("2025-06-01", tz=TZ).tz_convert("UTC")
# scicomp_training.py keeps the last 20 % (chronological) of the rows before the cutoff out of training:
# the calibration window lies inside that held-out tail, at most 30 days
tail_start = first + 0.8 * (cut - first) + pd.Timedelta(days=1)
cal_start = max(tail_start, cut - pd.Timedelta(days=30))

base = load_config()
# only the weather up to the end of the test month (+ 3 days for the last horizons) is predicted: the GPR
# prediction of a whole year at once does not fit in memory
_weather = replay._weather
replay._weather = lambda: (lambda w: w[w["date"] < end + pd.Timedelta(days=3)])(_weather())
start_local = (cal_start - pd.Timedelta(days=2)).tz_convert(TZ).date().isoformat()

# 1. calibration: weather-only data (no prices or load needed); the price file starts later, so the
#    calibration build gets a flat placeholder price before its first row (prices are not used here)
_read = pd.read_csv


def read_with_price_padding(path, *a, **k):
    df = _read(path, *a, **k)
    if str(path).endswith("ENTSOE_HU_Dataset_2025.csv"):
        t = pd.to_datetime(df["Timestamp"])
        pad_idx = pd.date_range(pd.Timestamp("2025-05-01"), t.min() - pd.Timedelta(minutes=15), freq="15min")
        pad = pd.DataFrame({"Timestamp": pad_idx.astype(str), "price": 100.0})
        df = pd.concat([pad, df], ignore_index=True)
    return df


replay.pd.read_csv = read_with_price_padding
cfg_cal = with_overrides(base, {"replay": {"heldout_start": start_local}})
cal_data = build_data(cfg_cal, refresh=True)
cal = calibrate(cal_data, cfg_cal, cal_start, cut)
replay.pd.read_csv = _read

# 2. test month: industrial load, real prices, forecasts of this month's model
cfg_test = with_overrides(base, {"replay": {"heldout_start": start_local,
                                            "demand_profile_csv": "industrial_load_profile.csv",
                                            "demand_mean_w": 10.788}})
data = build_data(cfg_test, refresh=True)
horizon = int(base["general"]["horizon"])
i1 = data.index.searchsorted(end) + horizon
data = data.iloc[: i1 + 1]
data = data[data.index >= cal_start]
data.to_pickle(f"{OUT}/data.pkl")

# out-of-sample quality of the forecast on the test month (reported, never fitted)
_, err, sd, _ = _hourly(data, max(cut, data.index[0]), end)
ok = sd > 0
test_q = {"rmse_w": float((err ** 2).mean() ** 0.5),
          "coverage_1sigma_calibrated_pct": coverage(err, cal["sigma_scale"] * sd, 1),
          "coverage_2sigma_calibrated_pct": coverage(err, cal["sigma_scale"] * sd, 2),
          "hours": int(ok.sum())}
rec = {"cutoff": cut.isoformat(), "test_end": end.isoformat(), "calibration_window": [cal_start.isoformat(), cut.isoformat()],
       "sigma_scale": cal["sigma_scale"], "error_autocorr_step": cal["error_autocorr_step"],
       "calibration": {k: v for k, v in cal.items() if k != "sigma_weather_coef"}, "test_month_forecast": test_q,
       "test_start": str(data.index[data.index >= cut][0])}
json.dump(rec, open(f"{OUT}/calibration.json", "w"), indent=1, default=float)
print(json.dumps(rec, indent=1, default=float))
