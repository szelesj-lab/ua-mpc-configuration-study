"""Forecast bundle of one simulated month, for any load (run inside the bundle's own copy of the controller code).

    python prep_bundle.py <root> month <cutoff YYYY-MM-DD> <test end YYYY-MM-DD>
    python prep_bundle.py <root> final_test <stored run .json>

month: the walk-forward step of experiments/winter/prep_month.py - the GP of <root>/models was trained on the
weather before the cutoff (train_rewind.py), sigma is calibrated on that model's own held-out tail before the
cutoff, and the replay data of the test month get this model's forecasts. Differences from prep_month.py: the
demand column is a placeholder (the job puts the user's load in), and the data keep the calibration window
before the cutoff, where the "past" r_E for sizing the back-off is read.

final_test: the September 2026 test period with the summer GP models (<root> = work copy B), its period from the
stored run, the summer calibration (code/config), and the tuning period as the "past" window.

Writes <root>/results/rewind/{calibration.json, data.pkl}.
"""
import json
import os
import sys

import pandas as pd

root, kind = sys.argv[1], sys.argv[2]
sys.path.insert(0, f"{root}/src")
import replay  # noqa: E402
from calibrate_uncertainty import _hourly, calibrate, coverage  # noqa: E402
from replay import build_data, load_config, named_periods, with_overrides  # noqa: E402

TZ = "Europe/Budapest"
OUT = f"{root}/results/rewind"
os.makedirs(OUT, exist_ok=True)
base = load_config()
horizon = int(base["general"]["horizon"])
PLACEHOLDER = {"replay": {"demand_profile_csv": "", "demand_mean_w": 0.0}}
# the reference load profile ends here; the reference replay data end with it (kept for identical horizons)
PROFILE_END = pd.Timestamp("2026-09-30 03:45", tz="UTC")

if kind == "final_test":
    meta = json.load(open(sys.argv[3]))
    cfg = with_overrides(base, PLACEHOLDER)
    data = build_data(cfg, refresh=True)
    data = data[data.index <= PROFILE_END]
    per = named_periods(data, cfg)
    t0, t1 = pd.Timestamp(meta["start"]), pd.Timestamp(meta["end"])
    data = data[data.index >= per["tuning"][0]]
    data.to_pickle(f"{OUT}/data.pkl")
    rec = {"kind": "final_test", "test_start": str(t0), "test_end": str(t1),
           "sigma_scale": float(base["mpc"]["sigma_scale"]), "error_autocorr_step": float(base["mpc"]["error_autocorr"]),
           "past_window": [str(per["tuning"][0]), str(per["tuning"][1])], "past_label": "tuning period (summer)"}
    json.dump(rec, open(f"{OUT}/calibration.json", "w"), indent=1, default=float)
    print(json.dumps(rec, indent=1))
    sys.exit(0)

cutoff_s, end_s = sys.argv[3:5]
cut = pd.Timestamp(cutoff_s, tz=TZ).tz_convert("UTC")
end = pd.Timestamp(end_s, tz=TZ).tz_convert("UTC")
first = pd.Timestamp("2025-06-01", tz=TZ).tz_convert("UTC")
tail_start = first + 0.8 * (cut - first) + pd.Timedelta(days=1)
cal_start = max(tail_start, cut - pd.Timedelta(days=30))
_weather = replay._weather
replay._weather = lambda: (lambda w: w[w["date"] < end + pd.Timedelta(days=3)])(_weather())
start_local = (cal_start - pd.Timedelta(days=2)).tz_convert(TZ).date().isoformat()

# 1. calibration, exactly as prep_month.py (flat placeholder price before the first price row)
_read = pd.read_csv


def read_with_price_padding(path, *a, **k):
    df = _read(path, *a, **k)
    if str(path).endswith("ENTSOE_HU_Dataset_2025.csv"):
        t = pd.to_datetime(df["Timestamp"])
        pad_idx = pd.date_range(pd.Timestamp("2025-05-01"), t.min() - pd.Timedelta(minutes=15), freq="15min")
        df = pd.concat([pd.DataFrame({"Timestamp": pad_idx.astype(str), "price": 100.0}), df], ignore_index=True)
    return df


replay.pd.read_csv = read_with_price_padding
cfg_cal = with_overrides(base, {"replay": {"heldout_start": start_local}})
cal = calibrate(build_data(cfg_cal, refresh=True), cfg_cal, cal_start, cut)
replay.pd.read_csv = _read

# 2. replay data of the month (real prices, this month's forecasts), demand as a placeholder
cfg_test = with_overrides(base, {"replay": {"heldout_start": start_local, **PLACEHOLDER["replay"]}})
data = build_data(cfg_test, refresh=True)
i1 = data.index.searchsorted(end) + horizon
data = data.iloc[: i1 + 1]
data = data[data.index >= cal_start]
data.to_pickle(f"{OUT}/data.pkl")
_, err, sd, _ = _hourly(data, max(cut, data.index[0]), end)
test_q = {"rmse_w": float((err ** 2).mean() ** 0.5),
          "coverage_1sigma_calibrated_pct": coverage(err, cal["sigma_scale"] * sd, 1),
          "coverage_2sigma_calibrated_pct": coverage(err, cal["sigma_scale"] * sd, 2), "hours": int((sd > 0).sum())}
rec = {"kind": "month", "cutoff": cut.isoformat(), "test_end": end.isoformat(),
       "calibration_window": [cal_start.isoformat(), cut.isoformat()],
       "sigma_scale": cal["sigma_scale"], "error_autocorr_step": cal["error_autocorr_step"],
       "calibration": {k: v for k, v in cal.items() if k != "sigma_weather_coef"}, "test_month_forecast": test_q,
       "test_start": str(data.index[data.index >= cut][0]),
       "past_window": [str(data.index[0]), cut.isoformat()], "past_label": "held-out tail before the month"}
json.dump(rec, open(f"{OUT}/calibration.json", "w"), indent=1, default=float)
print(json.dumps(rec, indent=1, default=float))
