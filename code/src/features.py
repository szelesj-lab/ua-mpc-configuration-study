"""Feature construction shared by model training and prediction.

Both the training scripts and the live forecast use these functions, so the
models always see features built the same way.

The GPR models are trained on hourly data but applied to the 15-minute Open-Meteo
forecast. Differences and rolling statistics are therefore defined over fixed time
spans (1 hour, 3 hours) instead of a fixed number of rows: on hourly data they are
identical to the original row-based features, and on 15-minute data they still
describe the same physical time span.
"""

import numpy as np
import pandas as pd

SOLAR_FEATURES = [
    "global_tilted_irradiance", "gti_diff", "gti_roll_mean3", "solar_base",
    "hour_sin", "hour_cos", "year_sin", "year_cos",
]

WIND_FEATURES = [
    "wind_speed_10m", "wind_cube", "wind_diff", "wind_roll_mean3", "wind_roll_std3", "wind_base",
    "hour_sin", "hour_cos", "wind_speed_100m", "wind_gusts_10m", "temperature_2m",
    "wind_dir_sin", "wind_dir_cos",
]

DIFF_SPAN = "1h"     # gti_diff / wind_diff: change compared to one hour earlier
ROLLING_SPAN = "3h"  # *_roll_mean3 / wind_roll_std3: statistics over the last three hours


def _utc_index(dates) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(pd.to_datetime(pd.Series(dates).to_numpy(), utc=True)).tz_localize(None)


def _on_sorted(values, idx, func):
    """Apply a time-indexed function even if the input is not in time order."""
    s = pd.Series(np.asarray(values, dtype=float), index=idx)
    if s.index.is_monotonic_increasing:
        return func(s).to_numpy()
    order = np.argsort(idx.to_numpy(), kind="stable")
    out = np.empty(len(s))
    out[order] = func(s.iloc[order]).to_numpy()
    return out


def time_diff(values, idx, span=DIFF_SPAN):
    """value(t) - value(t - span); 0 where the earlier value is not available."""
    def f(s):
        earlier = s.shift(freq=span).reindex(s.index)
        return (s - earlier).fillna(0.0)
    return _on_sorted(values, idx, f)


def time_rolling(values, idx, stat, span=ROLLING_SPAN):
    """Rolling mean/std over the time window (t - span, t]."""
    def f(s):
        r = getattr(s.rolling(span), stat)()
        return r.fillna(0.0)
    return _on_sorted(values, idx, f)


def _hour_of_day(idx):
    # Fractional hour, so 15-minute steps within the same hour get different values
    return idx.hour + idx.minute / 60.0


def solar_features(gti, dates, solar_base, index=None) -> pd.DataFrame:
    idx = _utc_index(dates)
    gti = np.asarray(gti, dtype=float)
    hours = _hour_of_day(idx)
    day_of_year = idx.dayofyear

    X = pd.DataFrame(index=index if index is not None else pd.RangeIndex(len(gti)))
    X["global_tilted_irradiance"] = gti
    X["gti_diff"] = time_diff(gti, idx)
    X["gti_roll_mean3"] = time_rolling(gti, idx, "mean")
    X["solar_base"] = np.asarray(solar_base, dtype=float)
    X["hour_sin"] = np.sin(2 * np.pi * hours / 24.0)
    X["hour_cos"] = np.cos(2 * np.pi * hours / 24.0)
    X["year_sin"] = np.sin(2 * np.pi * day_of_year / 365.25)
    X["year_cos"] = np.cos(2 * np.pi * day_of_year / 365.25)
    return X[SOLAR_FEATURES]


def wind_features(weather: pd.DataFrame, dates, wind_base, index=None) -> pd.DataFrame:
    """weather needs the columns wind_speed_10m, wind_speed_100m, wind_gusts_10m,
    temperature_2m and wind_direction_10m (forecast values)."""
    idx = _utc_index(dates)
    ws = weather["wind_speed_10m"].to_numpy(dtype=float)
    hours = _hour_of_day(idx)
    wind_dir_rad = np.radians(weather["wind_direction_10m"].to_numpy(dtype=float))

    X = pd.DataFrame(index=index if index is not None else pd.RangeIndex(len(ws)))
    X["wind_speed_10m"] = ws
    X["wind_cube"] = ws ** 3
    X["wind_diff"] = time_diff(ws, idx)
    X["wind_roll_mean3"] = time_rolling(ws, idx, "mean")
    X["wind_roll_std3"] = time_rolling(ws, idx, "std")
    X["wind_base"] = np.asarray(wind_base, dtype=float)
    X["hour_sin"] = np.sin(2 * np.pi * hours / 24.0)
    X["hour_cos"] = np.cos(2 * np.pi * hours / 24.0)
    X["wind_speed_100m"] = weather["wind_speed_100m"].to_numpy(dtype=float)
    X["wind_gusts_10m"] = weather["wind_gusts_10m"].to_numpy(dtype=float)
    X["temperature_2m"] = weather["temperature_2m"].to_numpy(dtype=float)
    X["wind_dir_sin"] = np.sin(wind_dir_rad)
    X["wind_dir_cos"] = np.cos(wind_dir_rad)
    return X[WIND_FEATURES]


def create_price_feature_vector(ts, current_lags, load_fc, solar_fc, wind_fc, features):
    """One feature row for the SVR grid price model (15-minute steps)."""
    net_load = load_fc - (solar_fc + wind_fc)
    minute_of_day = ts.hour * 60 + ts.minute

    row = {
        'load_forecast': load_fc,
        'solar_forecast': solar_fc,
        'wind_forecast': wind_fc,
        'net_load': net_load,
        'sin_day': np.sin(2 * np.pi * minute_of_day / 1440.0),
        'cos_day': np.cos(2 * np.pi * minute_of_day / 1440.0),
        'is_weekend': int(ts.dayofweek in [5, 6]),
        'sin_dayofweek': np.sin(2 * np.pi * ts.dayofweek / 7.0),
        'cos_dayofweek': np.cos(2 * np.pi * ts.dayofweek / 7.0),
        'sin_month': np.sin(2 * np.pi * ts.month / 12.0),
        'cos_month': np.cos(2 * np.pi * ts.month / 12.0),
    }

    row['lag_15min'] = current_lags[-1]
    row['lag_24h'] = current_lags[-96]
    row['lag_48h'] = current_lags[-192]
    row['lag_168h'] = current_lags[-672]

    return pd.DataFrame([row])[features]


# Forecast-time indicators of weather that is hard to forecast (storms, fronts, broken clouds).
# They scale the forecast std in the MPC when mpc.sigma_model = "weather" (see calibrate_uncertainty.py).
DIFFICULTY_FEATURES = ["Gust_excess (m/s)", "GTI_var3h (W/m2)"]


def difficulty_features(weather: pd.DataFrame, dates) -> pd.DataFrame:
    """weather needs the forecast columns wind_speed_10m, wind_gusts_10m and global_tilted_irradiance.

    Gust_excess: forecast gusts above the mean wind (convective, frontal weather).
    GTI_var3h:   standard deviation of the irradiance forecast over the last three hours
                 (broken clouds, passing showers).
    """
    idx = _utc_index(dates)
    gust = np.clip(weather["wind_gusts_10m"].to_numpy(dtype=float) - weather["wind_speed_10m"].to_numpy(dtype=float), 0.0, None)
    gti = weather["global_tilted_irradiance"].to_numpy(dtype=float)
    return pd.DataFrame({
        DIFFICULTY_FEATURES[0]: gust,
        DIFFICULTY_FEATURES[1]: time_rolling(gti, idx, "std"),
    })
