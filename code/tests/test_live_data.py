from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

import live_data


@pytest.fixture
def live_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(live_data, "LIVE_DIR", tmp_path / "live")
    return tmp_path / "live"


CFG = {"general": {"timezone": "Europe/Budapest"},
       "live": {"enabled": True, "use_measured_soc": True, "measured_soc_max_age_s": 600}}


def test_naive_timestamp_is_site_local_time():
    m = live_data.parse_measurement({"timestamp": "2026-10-16 14:30:00", "pv_w": 5}, config=CFG)
    assert m["timestamp"] == pd.Timestamp("2026-10-16 12:30:00", tz="UTC")  # CEST = UTC+2


def test_missing_timestamp_uses_arrival_time():
    now = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
    m = live_data.parse_measurement({"soc": 0.5}, now=now, config=CFG)
    assert m["timestamp"] == pd.Timestamp(now)


@pytest.mark.parametrize("record, message", [
    ({"pv": 5}, "unknown field"),
    ({"soc": 55}, "between 0 and 1"),
    ({"timestamp": "yesterday"}, "ISO 8601"),
    ({"timestamp": "2026-10-16T12:00:00Z"}, "no values"),
    ({"load_w": "high"}, "must be a number"),
])
def test_invalid_measurements_are_rejected(record, message):
    with pytest.raises(ValueError, match=message):
        live_data.parse_measurement(record, config=CFG)


def test_store_read_and_latest(live_dir):
    now = datetime.now(timezone.utc)
    live_data.append_measurements([
        {"timestamp": (now - timedelta(minutes=10)).isoformat(), "pv_w": 10, "soc": 0.5},
        {"timestamp": (now - timedelta(minutes=5)).isoformat(), "pv_w": 12},
        {"timestamp": (now - timedelta(days=3)).isoformat(), "pv_w": 99},
    ], config=CFG)
    hist = live_data.read_history(1)
    assert list(hist["pv_w"]) == [10, 12]
    latest = live_data.latest()
    assert latest["values"]["pv_w"]["value"] == 12
    assert latest["values"]["soc"]["value"] == 0.5  # the latest value of each field, even from an older message
    assert 250 < latest["age_s"] < 400


def test_measured_soc_only_when_enabled_and_fresh(live_dir):
    now = datetime.now(timezone.utc)
    live_data.append_measurements([{"timestamp": (now - timedelta(minutes=5)).isoformat(), "soc": 0.42}], config=CFG)
    assert live_data.measured_soc(CFG) == pytest.approx(0.42)
    assert live_data.measured_soc({**CFG, "live": {**CFG["live"], "enabled": False}}) is None
    assert live_data.measured_soc({**CFG, "live": {**CFG["live"], "measured_soc_max_age_s": 60}}) is None


def test_files_from_older_versions_without_new_fields_still_load(tmp_path, monkeypatch):
    import pandas as pd
    import live_data
    monkeypatch.setattr(live_data, "LIVE_DIR", tmp_path)
    now = pd.Timestamp.now(tz="UTC")
    old_columns = ["timestamp", "pv_w", "wind_w", "load_w", "grid_w", "battery_w", "soc"]
    (tmp_path / f"{now:%Y-%m-%d}.csv").write_text(
        ",".join(old_columns) + "\n" + f"{(now - pd.Timedelta(minutes=1)).isoformat()},5,1,10,4,0,0.5\n")
    df = live_data.read_history(1.0, now)
    assert list(df.columns) == live_data.COLUMNS and len(df) == 1
    latest = live_data.latest(now)
    assert latest["values"]["load_w"]["value"] == 10 and "curtailed_w" not in latest["values"]
