import numpy as np
import pandas as pd
import pytest

import features as F


def _hourly(values, start="2026-06-01 00:00"):
    dates = pd.date_range(start, periods=len(values), freq="1h", tz="UTC")
    return np.asarray(values, dtype=float), dates


def test_time_features_equal_row_features_on_hourly_data():
    rng = np.random.default_rng(0)
    values, dates = _hourly(rng.uniform(0, 10, 50))
    idx = F._utc_index(dates)
    s = pd.Series(values)
    assert np.allclose(F.time_diff(values, idx), s.diff().fillna(0))
    assert np.allclose(F.time_rolling(values, idx, "mean"), s.rolling(3, min_periods=1).mean())
    assert np.allclose(F.time_rolling(values, idx, "std"), s.rolling(3, min_periods=1).std().fillna(0))


def test_time_features_keep_their_time_span_on_15_minute_data():
    # a linear ramp of 4 units per hour
    dates = pd.date_range("2026-06-01 00:00", periods=40, freq="15min", tz="UTC")
    values = np.arange(40, dtype=float)  # +1 per 15 min = +4 per hour
    idx = F._utc_index(dates)
    diff = F.time_diff(values, idx)
    assert np.allclose(diff[4:], 4.0)  # change over one hour, not over one row
    mean = F.time_rolling(values, idx, "mean")
    assert mean[20] == pytest.approx(np.mean(values[9:21]))  # last 3 hours = 12 rows


def test_unsorted_input_gives_same_features():
    rng = np.random.default_rng(1)
    values, dates = _hourly(rng.uniform(0, 10, 30))
    order = rng.permutation(30)
    idx = F._utc_index(dates)
    idx_shuffled = F._utc_index(dates[order])
    assert np.allclose(F.time_diff(values[order], idx_shuffled), F.time_diff(values, idx)[order])


def test_feature_columns_match_the_trained_models():
    values, dates = _hourly(np.linspace(0, 800, 24))
    X = F.solar_features(values, dates, values * 0.03)
    assert list(X.columns) == F.SOLAR_FEATURES
    weather = pd.DataFrame({c: np.ones(24) for c in ["wind_speed_10m", "wind_speed_100m", "wind_gusts_10m", "temperature_2m", "wind_direction_10m"]})
    W = F.wind_features(weather, dates, np.ones(24))
    assert list(W.columns) == F.WIND_FEATURES


def test_hour_of_day_distinguishes_quarter_hours():
    dates = pd.date_range("2026-06-01 12:00", periods=4, freq="15min", tz="UTC")
    X = F.solar_features(np.ones(4), dates, np.ones(4))
    assert X["hour_sin"].nunique() == 4
