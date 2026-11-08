"""Γ check: routing of the forecast error and the classification of the re-planning hours."""
import pandas as pd

from app.own.gammacheck import routed, summary, windows


def test_routing():
    mu = pd.Series([10.0, 10.0, 0.0]); real = pd.Series([4.0, 14.0, 5.0])
    assert list(routed(mu, real, "self_consumption", 24, 24)) == [6.0, -4.0, -5.0]
    assert list(routed(mu, real, "renewable_buffer", 24, 24)) == [6.0, 0.0, 0.0]
    assert list(routed(mu, real, "set_point", 24, 24)) == [0.0, 0.0, 0.0]


def test_classes():
    t = pd.date_range("2025-11-01", periods=12, freq="15min", tz="UTC")
    # hour 1 starts inside (0.5), small deficit; hour 2 starts inside, deficit above the reserve, ends at the limit;
    # hour 3 starts with the reserve used (0.11 < 0.1 + 0.03)
    soc = [0.5] * 3 + [0.5] + [0.3, 0.2, 0.15, 0.11] + [0.1] * 4
    cut = [0.0] * 8 + [1.0, 0, 0, 0]
    steps = pd.DataFrame({"t": t, "soc": soc, "cut_discharge_w": cut, "cut_charge_w": 0.0})
    err = pd.Series([1.0] * 4 + [8.0] * 4 + [0.0] * 4, index=t)        # W; reserve = 0.03 * 72 * 0.95 = 2.05 Wh
    w = windows(steps, err, 0.03, 72.0, 0.5)
    assert list(w["class"]) == ["inside_within", "inside_above", "reserve_used"]
    assert abs(w.deficit_wh.iloc[1] - 8.0) < 1e-12 and list(w.lower_edge) == [False, False, True]
    s = summary(w)
    assert s["inside_within_lower_edge"] == 0 and s["reserve_used_lower_edge"] == 1 and s["lower_edge_hours"] == 1
    assert pd.isna(windows(steps, err, 0.03, 72.0, None)["class"].iloc[0])   # unknown start SoC: not classified


def test_archive_rules():
    from app.own.gammacheck import archive_rules
    t = pd.date_range("2025-11-01", periods=8, freq="15min", tz="UTC")
    err = pd.Series([4.0, 4.0, 4.0, 4.0, -8.0, 0.0, 12.0, 0.0], index=t)        # W
    r = archive_rules(err, 72.0)
    # 1-h windows (Wh): 4, 1, 0, 2, 1 -> max 4 Wh; largest step deficit 12 W -> 12 Wh over one hour
    assert abs(r["scenario"] - 4.0 / 72) < 1e-12 and abs(r["robust"] - 12.0 / 72) < 1e-12 and r["windows"] == 5
    q = float(pd.Series([4.0, 1.0, 0.0, 2.0, 1.0]).quantile(0.95))
    assert abs(r["resized"] - q / (72 * 0.75)) < 1e-12
