"""Checks of the own-data upload: parsing, units, time zones, gaps, profile mapping and size scaling."""
import numpy as np
import pandas as pd
import pytest

from app.own import profile as P


def _csv(idx, vals, fmt="%Y-%m-%d %H:%M", sep=",", dec="."):
    rows = [f"{t.strftime(fmt)}{sep}{str(round(v, 3)).replace('.', dec)}" for t, v in zip(idx, vals)]
    return f"time{sep}load\n" + "\n".join(rows)


def week_profile(idx):
    loc = idx.tz_convert(P.TZ) if idx.tz is not None else idx
    return 100 + 50 * (loc.dayofweek < 5) + 10 * np.sin(2 * np.pi * loc.hour / 24)


def test_hourly_local_decimal_comma_and_gap():
    idx = pd.date_range("2026-01-05", "2026-01-25 23:00", freq="1h")
    v = np.asarray(week_profile(idx), float)
    keep = np.ones(len(idx), bool); keep[50:52] = False; keep[200:220] = False   # 2 h and 20 h gaps
    s, rep = P.read(_csv(idx[keep], v[keep], sep=";", dec=","), "kw", P.TZ)
    assert not rep["errors"] and rep["interval_min"] == 60
    assert rep["gaps"] == 2 and rep["longest_gap_h"] == 20
    assert rep["missing_steps"] == 80          # the 20-h gap stays open, the 2-h gap is interpolated
    assert abs(rep["mean_kw"] - v[keep].mean()) / v.mean() < 0.01
    assert s.index.tz is not None and (s.index.minute % 15 == 0).all()


def test_units_and_utc_offsets():
    idx = pd.date_range("2026-02-02", periods=96 * 8, freq="15min", tz="UTC")
    kw = np.full(len(idx), 40.0)
    txt = "ts,e\n" + "\n".join(f"{t.isoformat()},{10.0}" for t in idx)   # 10 kWh per 15 min = 40 kW
    s, rep = P.read(txt, "kwh")
    assert rep["timezone"].startswith("from the file") and abs(rep["mean_kw"] - 40) < 1e-9
    assert np.allclose(s.reindex(idx).to_numpy(), kw)


@pytest.mark.parametrize("bad,msg", [("short", "full week"), ("res", "resolution"), ("neg", "negative")])
def test_errors(bad, msg):
    if bad == "short":
        idx = pd.date_range("2026-01-05", periods=24 * 3, freq="1h"); v = np.ones(len(idx))
    elif bad == "res":
        idx = pd.date_range("2026-01-05", periods=24 * 10, freq="45min"); v = np.ones(len(idx))
    else:
        idx = pd.date_range("2026-01-05", periods=24 * 10, freq="1h"); v = -np.ones(len(idx))
    _, rep = P.read(_csv(idx, v), "kw")
    assert any(msg in e for e in rep["errors"])


def test_profile_mapping_keeps_weekday_and_time():
    idx = pd.date_range("2026-03-02", periods=96 * 14, freq="15min", tz=P.TZ).tz_convert("UTC")
    s = pd.Series(np.asarray(week_profile(idx), float), index=idx)
    target = pd.date_range("2025-11-01", periods=96 * 30, freq="15min", tz=P.TZ).tz_convert("UTC")
    v, share = P.map_to(target, s)
    assert share == 0.0 and not np.isnan(v).any()
    assert np.allclose(v, week_profile(target))
    v2, share2 = P.map_to(idx, s)
    assert share2 == 1.0 and np.allclose(v2, s.to_numpy())


def test_sizes_round_trip_to_the_cell():
    mean = 250.0
    d = P.default_sizes(mean)
    c = P.sizes_to_cell({k: d[k] for k in d}, mean)
    assert abs(c["E_capacity"] - 72) < 0.1 and abs(c["p_max_w"] - 24) < 0.1
    assert abs(c["pv_scale"] - 1) < 0.01 and abs(c["wind_scale"] - 1) < 0.01
    assert abs(c["factor"] * mean * 1000 - P.CELL_MEAN_W) < 1e-9


@pytest.mark.parametrize("span,res,unit", [("week", 60, "kw"), ("month", 15, "kwh"), ("year", 30, "kw")])
def test_sample_export_round_trip(span, res, unit):
    """The exported sample (local time with summer and winter UTC offsets) is accepted as it is."""
    from app.own.runner import sample_csv
    body, name = sample_csv(span, res, unit, 250.0)
    s, rep = P.read(body.decode(), "kwh" if unit == "kwh" else "kw", P.TZ)
    assert not rep["errors"] and rep["interval_min"] == res and rep["gaps"] == 0
    assert abs(rep["mean_kw"] - 250.0) < 0.01 and name.endswith(".csv")  # values are written with 3 decimals
