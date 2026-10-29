import numpy as np
import pandas as pd
import pytest

import overview

STEP = 900
TZ = "Europe/Budapest"


def make_plan(start="2026-10-16 10:00", n=8, demand=20.0, grid=15.0, dis=5.0, charge=4.0, ren=6.0, price_mwh=100.0, plan_time=None):
    ts = pd.date_range(start, periods=n, freq="15min", tz="UTC")
    df = pd.DataFrame({
        "Date": ts.tz_convert(TZ).astype(str),
        "Demand_power (W)": demand, "P_grid_opt (W)": grid, "P_bat_dis_opt (W)": dis,
        "P_bat_ch (W)": charge, "Total_power (W)": ren, "P_curtailed (W)": ren - charge,
        "Aggregated_deviation (W)": 1.0, "Grid Price (€/Wh)": price_mwh / 1e6,
        "SoC_opt": np.linspace(0.5, 0.4, n), "SoC_min_eff (%)": 10.0, "SoC_max_eff (%)": 90.0,
    })
    df["Grid and degradation cost"] = (df["P_grid_opt (W)"] * df["Grid Price (€/Wh)"] + 6.87e-5 * (charge + dis)) * 0.25
    df["_ts"] = ts
    df["_plan"] = pd.Timestamp(plan_time or start, tz="UTC")
    return df


def test_decision_in_force_and_cost_rate():
    plan = make_plan()
    d = overview.decision_at(plan, pd.Timestamp("2026-10-16 10:20", tz="UTC"), STEP, 6.87e-5, TZ)
    assert (d["from"], d["to"]) == ("2026-10-16 12:15", "2026-10-16 12:30")  # local time
    assert d["grid_share"] == pytest.approx(0.75) and d["battery_share"] == pytest.approx(0.25)
    assert d["price_eur_mwh"] == pytest.approx(100.0)
    assert d["grid_cost_rate_eur_h"] == pytest.approx(15.0 * 1e-4)          # W x EUR/Wh = EUR/h
    assert d["cost_rate_eur_h"] == pytest.approx(15.0 * 1e-4 + 6.87e-5 * 9)
    assert not d["upcoming"]


def test_decision_before_plan_start_is_marked_upcoming():
    d = overview.decision_at(make_plan(), pd.Timestamp("2026-10-16 09:50", tz="UTC"), STEP, 0.0, TZ)
    assert d["upcoming"] and d["from"] == "2026-10-16 12:00"
    assert overview.decision_at(make_plan(), pd.Timestamp("2026-10-17 09:50", tz="UTC"), STEP, 0.0, TZ) is None


def test_plan_costs_against_grid_only():
    c = overview.plan_costs(make_plan(), STEP)
    assert c["grid_eur"] == pytest.approx(8 * 15.0 * 1e-4 * 0.25)
    assert c["grid_only_eur"] == pytest.approx(8 * 20.0 * 1e-4 * 0.25)
    assert c["savings_eur"] == pytest.approx(c["grid_only_eur"] - c["total_eur"])
    assert c["wear_eur"] == pytest.approx(8 * 6.87e-5 * 9 * 0.25)


def test_costs_between_planned_and_measured():
    plan = make_plan()
    start, end = pd.Timestamp("2026-10-16 10:00", tz="UTC"), pd.Timestamp("2026-10-16 10:37:30", tz="UTC")
    measured = pd.DataFrame({"grid_w": [10.0, 10.0]}, index=pd.date_range("2026-10-16 10:00", periods=2, freq="15min", tz="UTC"))
    c = overview.costs_between(plan, measured, start, end, STEP, 0.0)
    assert c["planned_eur"] == pytest.approx(15.0 * 1e-4 * 2.5 / 4)  # 2.5 steps of 15 min
    assert c["measured_eur"] == pytest.approx(10.0 * 1e-4 * 0.5)
    assert c["measured_hours"] == pytest.approx(0.5)


def test_plan_costs_energy_and_range():
    c = overview.plan_costs(make_plan(), STEP, TZ)
    assert c["grid_kwh"] == pytest.approx(8 * 15.0 * 0.25 / 1000)
    assert c["avg_price_eur_kwh"] == pytest.approx(0.1)  # 100 EUR/MWh
    assert (c["from"], c["to"]) == ("2026-10-16 12:00", "2026-10-16 14:00")


def test_day_costs_measured_past_and_planned_rest():
    # plan 10:00-12:00 UTC (12:00-14:00 local); now 10:30 UTC; 10:00-10:15 measured
    plan = make_plan()
    midnight = pd.Timestamp("2026-10-16", tz=TZ).tz_convert("UTC")
    now = pd.Timestamp("2026-10-16 10:30", tz="UTC")
    measured = pd.DataFrame({"grid_w": [10.0]}, index=pd.date_range("2026-10-16 10:00", periods=1, freq="15min", tz="UTC"))
    d = overview.day_costs(plan, measured, midnight, now, STEP, 0.0, TZ)
    step_plan = 15.0 * 1e-4 * 0.25
    assert d["measured_eur"] == pytest.approx(10.0 * 1e-4 * 0.25)
    assert d["plan_same_eur"] == pytest.approx(step_plan)
    assert d["past_unmeasured_eur"] == pytest.approx(step_plan)          # 10:15-10:30
    assert d["rest_planned_eur"] == pytest.approx(6 * step_plan)         # 10:30-12:00
    assert d["expected_eur"] == pytest.approx(d["measured_eur"] + 7 * step_plan)
    assert d["uncovered_hours"] == pytest.approx(24 - 2)
    assert (d["from"], d["now"]) == ("2026-10-16 00:00", "12:30")


def test_summary_mentions_the_key_facts():
    plan = make_plan(n=16)
    text = " ".join(overview.summary_sentences(plan, STEP, TZ))
    assert "The plan covers" in text
    assert "the battery supplies 25 %" in text
    assert "Renewables are expected to produce" in text
    assert "than taking all energy from the grid" in text


def test_measured_steps_and_timeline_alignment():
    t = pd.date_range("2026-10-16 10:00", periods=30, freq="1min", tz="UTC")
    hist = pd.DataFrame({"timestamp": t, "pv_w": 2.0, "wind_w": 1.0, "load_w": np.r_[np.full(15, 10.0), np.full(15, 20.0)], "grid_w": np.nan, "battery_w": np.nan, "soc": 0.5})
    steps = overview.measured_steps(hist, STEP)
    assert list(steps.index) == list(pd.date_range("2026-10-16 10:00", periods=2, freq="15min", tz="UTC"))
    assert steps["load_w"].tolist() == [10.0, 20.0]
    assert steps["renewable_w"].tolist() == [3.0, 3.0]

    tl = overview.timeline(make_plan(), steps, pd.Timestamp("2026-10-16 10:20", tz="UTC"), TZ, STEP)
    assert tl["date"][0] == "2026-10-16 12:00:00" and tl["now"] == "2026-10-16 12:20:00"
    assert tl["measured"]["load"][:3] == [10.0, 20.0, None]
    assert tl["measured"]["soc"][0] == 50.0
    assert tl["plan"]["price"][0] == pytest.approx(100.0)


def test_compare_now_flags_deviations_and_measured_cost():
    d = overview.decision_at(make_plan(), pd.Timestamp("2026-10-16 10:20", tz="UTC"), STEP, 0.0, TZ)
    v = lambda x, age=10: {"value": x, "age_s": age, "timestamp": ""}
    latest = {"values": {"load_w": v(20.5), "grid_w": v(25.0), "battery_w": v(-4.0), "pv_w": v(5.0), "wind_w": v(1.0),
                         "soc": v(0.30, age=999)}}
    s = {"stale_after_s": 120, "deviation_abs_w": 2.0, "deviation_rel": 0.25, "deviation_soc": 0.05}
    c = overview.compare_now(d, latest, s, 1e-4)
    rows = {r["key"]: r for r in c["rows"]}
    assert not rows["load"]["flag"]                      # 0.5 W < max(2, 5)
    assert rows["grid"]["flag"] and rows["grid"]["diff"] == pytest.approx(10.0)
    assert rows["battery"]["plan"] == pytest.approx(5.0 - 4.0) and rows["battery"]["flag"]
    assert rows["renewables"]["measured"] == pytest.approx(6.0)
    assert rows["soc"]["measured"] is None               # too old, ignored
    assert c["deviation"]
    assert c["cost_rate_eur_h"] == pytest.approx(25.0 * 1e-4 + 1e-4 * 4.0)
    assert overview.compare_now(d, {"values": {"grid_w": v(1.0, age=500)}}, s, 0.0) is None


def test_planned_soc_now_is_interpolated_within_the_slot():
    plan = make_plan()
    d = overview.decision_at(plan, pd.Timestamp("2026-10-16 10:22:30", tz="UTC"), STEP, 0.0, TZ)
    assert d["slot_fraction"] == pytest.approx(0.5)
    assert overview._soc_now(d) == pytest.approx((plan["SoC_opt"].iloc[0] + plan["SoC_opt"].iloc[1]) / 2)


def test_live_simulator_closes_the_power_balance_and_keeps_soc_limits():
    import random
    import live_data
    import live_simulator
    from settings import load_config

    cfg = load_config()
    cfg["SoC"]["initial_soc"] = 0.12
    plan = make_plan(n=16, demand=20.0, grid=0.0, dis=20.0, charge=6.0, ren=6.0)
    plan.index = pd.to_datetime(plan["_ts"], utc=True)
    plan["Mean_solar_power (W)"], plan["Mean_wind_power (W)"] = 4.0, 2.0
    cell = live_simulator.Cell(cfg, rng=random.Random(0))
    ts = plan.index[0]
    socs = []
    while ts < plan.index[-1]:
        row = plan.iloc[plan.index.searchsorted(ts, side="right") - 1]
        m = cell.measure(row, ts, 60.0)
        assert abs(live_data.balance_residual(m)) < 1e-6
        assert m["curtailed_w"] >= -1e-9 and m["grid_w"] >= -1e-9
        socs.append(m["soc"])
        ts += pd.Timedelta(minutes=1)
    assert min(socs) >= cfg["SoC"]["soc_min"] - 1e-6   # the BMS stops discharging at soc_min


def test_plan_book_uses_the_step_that_contains_the_time(tmp_path, monkeypatch):
    import live_simulator
    plans = tmp_path / "plans"
    plans.mkdir()
    p = make_plan(n=8)
    p.to_csv(plans / "plan_20261016T095000.csv", index=False)
    monkeypatch.setattr(live_simulator, "RESULTS_DIR", tmp_path)
    book = live_simulator.PlanBook()
    book.reload()
    row = book.step(pd.Timestamp("2026-10-16 10:40", tz="UTC"), STEP)   # step 10:30-10:45
    assert pd.Timestamp(row["Date"]).tz_convert("UTC") == pd.Timestamp("2026-10-16 10:30", tz="UTC")
    assert book.step(pd.Timestamp("2026-10-16 13:00", tz="UTC"), STEP) is None
