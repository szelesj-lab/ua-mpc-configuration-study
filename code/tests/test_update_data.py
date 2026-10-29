import numpy as np
import pandas as pd
import pytest

import update_data


def _entsoe_frames(start, end):
    idx_h = pd.date_range(start, end, freq="1h", tz="Europe/Budapest", inclusive="left")
    prices = pd.Series(np.arange(len(idx_h), dtype=float) + 100, index=idx_h)
    idx_q = pd.date_range(start, end, freq="15min", tz="Europe/Budapest", inclusive="left")
    load = pd.DataFrame({"Forecasted Load": 5000.0}, index=idx_q)
    ws = pd.DataFrame({"Solar": 300.0, "Wind Onshore": 50.0}, index=idx_q)
    return prices, load, ws


def test_combine_entsoe_matches_the_training_file_format():
    prices, load, ws = _entsoe_frames("2026-10-01", "2026-10-02")
    rows = update_data.combine_entsoe(prices, load, ws)
    assert list(rows.columns) == ["Timestamp", "price", "load_forecast", "Solar", "Wind Onshore"]
    assert rows["Timestamp"].iloc[0] == pd.Timestamp("2026-09-30 22:00")  # UTC, naive
    assert (pd.Series(rows["Timestamp"]).diff().dropna() == pd.Timedelta("15min")).all()
    assert rows["price"].iloc[:4].tolist() == [100.0] * 4  # hourly price held for the 15-min steps


def test_append_rows_adds_new_and_replaces_existing(tmp_path):
    path = tmp_path / "x.csv"
    pd.DataFrame({"Timestamp": ["2026-01-01 00:00:00", "2026-01-01 00:15:00"], "v": [1, 2]}).to_csv(path, index=False)
    added = update_data.append_rows(path, pd.DataFrame({"Timestamp": ["2026-01-01 00:15:00", "2026-01-01 00:30:00"], "v": [20, 3]}), "Timestamp")
    df = pd.read_csv(path)
    assert added == 1
    assert df["v"].tolist() == [1, 20, 3]


class _FakeClient:
    def __init__(self):
        self.calls = 0

    def query_day_ahead_prices(self, country, start, end):
        self.calls += 1
        return _entsoe_frames(start, end)[0]

    def query_load_forecast(self, country, start, end):
        return _entsoe_frames(start, end)[1]

    def query_wind_and_solar_forecast(self, country, start, end):
        return _entsoe_frames(start, end)[2]


def test_update_entsoe_appends_only_new_rows(tmp_path, monkeypatch):
    path = tmp_path / "entsoe.csv"
    last = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=3)).floor("h").tz_localize(None)
    pd.DataFrame({"Timestamp": [last.strftime("%Y-%m-%d %H:%M:%S")], "price": [1.0], "load_forecast": [1.0],
                  "Solar": [0.0], "Wind Onshore": [0.0]}).to_csv(path, index=False)
    monkeypatch.setattr(update_data, "ENTSOE_CSV", path)
    added = update_data.update_entsoe({"entsoe": {"country_code": "HU"}, "general": {"timezone": "Europe/Budapest"}}, client=_FakeClient())
    df = pd.read_csv(path)
    ts = pd.to_datetime(df["Timestamp"])
    assert added > 4 * 24 * 3
    assert ts.is_monotonic_increasing and ts.is_unique
    assert df["price"].iloc[0] == 1.0  # the existing row is kept


class _Var:
    def __init__(self, values):
        self.values = np.asarray(values, dtype=np.float32)

    def ValuesAsNumpy(self):
        return self.values


class _Hourly:
    def __init__(self, start, n, cols):
        self.start, self.n, self.cols = start, n, cols

    def Time(self):
        return self.start

    def TimeEnd(self):
        return self.start + 3600 * self.n

    def Interval(self):
        return 3600

    def Variables(self, i):
        return _Var(self.cols[i])


class _Response:
    def __init__(self, hourly):
        self.hourly = hourly

    def Hourly(self):
        return self.hourly

    def Timezone(self):
        return b"Europe/Budapest"


def test_hourly_frame_parses_open_meteo_response():
    start = int(pd.Timestamp("2026-06-01 00:00", tz="UTC").timestamp())
    cols = [np.arange(3) + i for i in range(6)]
    df = update_data.hourly_frame(_Response(_Hourly(start, 3, cols)), ["a", "b", "c", "d", "e", "f"])
    assert list(df.columns) == ["date", "a", "b", "c", "d", "e", "f"]
    assert str(df["date"].iloc[0]) == "2026-06-01 02:00:00+02:00"
    assert df["f"].tolist() == [5, 6, 7]


def test_gti_variable_follows_config():
    assert update_data._gti_variable({"openmeteo": {"gti_variable": "instant"}}) == "global_tilted_irradiance_instant"
    assert update_data._gti_variable({"openmeteo": {"gti_variable": "mean"}}) == "global_tilted_irradiance"


def test_data_status_reports_age(tmp_path, monkeypatch):
    # own small files, so the test does not depend on how far the real data has been updated
    weather = tmp_path / "weather.csv"
    weather.write_text("date,x\n2026-08-31 23:00:00+02:00,1\n2026-09-01 23:00:00+02:00,2\n")
    prices = tmp_path / "prices.csv"
    prices.write_text("Timestamp,price\n2026-09-10 23:45:00,90\n")
    monkeypatch.setattr(update_data, "FORECAST_CSV", weather)
    monkeypatch.setattr(update_data, "REAL_CSV", weather)
    monkeypatch.setattr(update_data, "ENTSOE_CSV", prices)
    status = update_data.data_status(now=pd.Timestamp("2026-10-16", tz="UTC"))
    assert set(status) == {"prices", "weather_forecast", "weather_real"}
    assert status["weather_real"]["age_days"] == pytest.approx(44.1, abs=0.2)  # data ends 2026-09-01 21:00 UTC
