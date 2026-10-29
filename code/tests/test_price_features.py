import numpy as np
import pandas as pd

from svr_function_grid import create_feature_vector, to_local_timestamps

FEATURES = [
    "load_forecast", "solar_forecast", "wind_forecast", "net_load",
    "lag_15min", "lag_24h", "lag_48h", "lag_168h",
    "sin_day", "cos_day", "is_weekend",
    "sin_dayofweek", "cos_dayofweek", "sin_month", "cos_month",
]


def test_feature_vector_uses_correct_lags():
    lags = list(np.arange(700, dtype=float))  # value == position
    ts = pd.Timestamp("2026-10-17 12:00", tz="Europe/Budapest")  # Saturday

    row = create_feature_vector(ts, lags, 5000.0, 800.0, 200.0, FEATURES)

    assert list(row.columns) == FEATURES
    r = row.iloc[0]
    assert r["net_load"] == 4000.0
    assert r["lag_15min"] == 699
    assert r["lag_24h"] == 700 - 96
    assert r["lag_48h"] == 700 - 192
    assert r["lag_168h"] == 700 - 672
    assert r["is_weekend"] == 1
    assert np.isclose(r["cos_day"], -1.0)  # noon


def test_horizon_timestamps_keep_the_right_time():
    local = pd.date_range("2026-10-17 12:00", periods=3, freq="15min", tz="Europe/Budapest")
    # tz-aware input stays the same instant
    assert (to_local_timestamps(local, "Europe/Budapest") == local).all()
    # numpy values from a tz-aware column are UTC and must not be relabelled as local time
    from_values = to_local_timestamps(pd.Series(local).values, "Europe/Budapest")
    assert (from_values == local).all()
    assert from_values[0].hour == 12


def test_entsoe_cache_reuses_recent_downloads(tmp_path, monkeypatch):
    import svr_function_grid as sfg

    monkeypatch.setattr(sfg, "CACHE_DIR", tmp_path)
    calls = []

    def fake_query(country, start, end):
        calls.append(country)
        return pd.Series([1.0, 2.0], index=pd.date_range(start, periods=2, freq="15min"))

    start, end = pd.Timestamp("2026-10-07", tz="Europe/Budapest"), pd.Timestamp("2026-10-19", tz="Europe/Budapest")
    first = sfg._cached_query("prices", fake_query, "HU", start, end, cache_minutes=60)
    second = sfg._cached_query("prices", fake_query, "HU", start, end, cache_minutes=60)
    assert len(calls) == 1
    assert second.equals(first)

    sfg._cached_query("prices", fake_query, "HU", start, end, cache_minutes=0)  # cache disabled
    assert len(calls) == 2
