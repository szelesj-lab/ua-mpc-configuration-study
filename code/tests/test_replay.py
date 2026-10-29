import copy

import numpy as np
import pandas as pd
import pytest

import replay
from settings import load_config


@pytest.fixture
def config():
    cfg = load_config()
    cfg["replay"]["solver_ftol"] = 1e-7
    return cfg


def make_data(cfg, days=1.0, ren=6.0, demand=12.0, price=100.0, seed=0):
    rng = np.random.default_rng(seed)
    n = int(days * 96) + int(cfg["general"]["horizon"])
    idx = pd.date_range("2026-07-06 22:00", periods=n, freq="15min", tz="UTC")
    hours = idx.tz_convert("Europe/Budapest").hour
    sun = np.where((hours >= 7) & (hours < 19), ren, 0.0)
    return pd.DataFrame({
        "solar_base": sun, "wind_base": 0.5, "solar_mu": sun, "wind_mu": 0.5,
        "solar_sd": np.where(sun > 0, 1.0, 0.0), "wind_sd": 0.6,
        "solar_real": np.clip(sun + rng.normal(0, 1.0, n) * (sun > 0), 0, None), "wind_real": 0.5,
        "price_eur_mwh": price * (1 + 0.5 * np.sin(np.arange(n) / 10)),
        "demand_w": np.where((hours >= 6) & (hours < 22), demand, 0.0),
    }, index=idx)


def test_weekly_schedule_wraps_around_the_week(config, tmp_path):
    path = tmp_path / "week.csv"
    path.write_text("Weekday,Time,Mode,Target_Output\nMon,06:00,MAX_PRODUCTION,0\nFri,22:00,STOPPED,0\n")
    idx = pd.DatetimeIndex(["2026-10-12 05:00", "2026-10-12 06:00", "2026-10-16 21:45", "2026-10-17 12:00"]).tz_localize("Europe/Budapest")
    p = replay.weekly_demand(idx, config, path)
    full = config["production"]["cat_power_w"]
    assert list(p) == [0.0, full, full, 0.0]   # Monday 05:00 still belongs to last week's STOPPED row


@pytest.mark.parametrize("controller", ["grid_only", "rule", "det", "ua", "oracle"])
def test_replay_keeps_the_power_balance_and_physics(config, controller):
    data = make_data(config, days=1.0)
    start = data.index[0]
    df, stats = replay.simulate(data, config, controller, start, start + pd.Timedelta(days=1), replan_steps=8)
    assert len(df) == 96
    assert np.allclose(df["grid_w"] + df["discharge_w"], df["demand_w"])
    assert (df["charge_w"] <= df["renewable_w"] + 1e-9).all() and (df["curtailed_w"] >= -1e-9).all()
    assert df["soc"].between(0, 1).all()
    if controller == "grid_only":
        assert df["discharge_w"].sum() == 0 and df["charge_w"].sum() == 0
    if controller == "rule":
        assert df["soc"].between(config["SoC"]["soc_min"] - 1e-9, config["SoC"]["soc_max"] + 1e-9).all()
    m = replay.metrics(df, stats, config)
    assert m["total_eur"] == pytest.approx(df["grid_eur"].sum() + df["wear_eur"].sum())
    assert (m.get("solves", 0) == 12) == (controller not in ("grid_only", "rule"))


def test_uncertainty_aware_mpc_differs_from_deterministic_when_uncertainty_matters(config):
    cfg = copy.deepcopy(config)
    cfg["SoC"]["E_capacity"] = 20.0       # small battery: the SoC margin binds
    cfg["mpc"].update(soc_chance_z=2.0, uncertainty_scaling="relative", sigma_scale=1.345, error_autocorr=0.857)
    cfg["weights"]["lambda"] = 1000.0
    data = make_data(cfg, days=0.5)
    start = data.index[0]
    h = replay.horizon_frame(data, 0, 96, "gpr")
    assert h["Aggregated_deviation (W)"].max() > 0
    assert replay.horizon_frame(data, 0, 96, "gpr_mean")["Aggregated_deviation (W)"].max() == 0
    det, _ = replay.simulate(data, cfg, "det", start, start + pd.Timedelta(hours=12), replan_steps=8)
    ua, _ = replay.simulate(data, cfg, "ua", start, start + pd.Timedelta(hours=12), replan_steps=8)
    assert not np.allclose(det["discharge_w"], ua["discharge_w"])


def test_search_space_round_trip():
    import tune_mpc
    space = tune_mpc.Space({"weights.w_x": [1e-4, 1.0, "log"], "mpc.soc_chance_z": [0.0, 2.0, "lin"]})
    v = np.array([0.01, 1.5])
    assert np.allclose(space.to_values(space.to_unit(v)), v)
    assert np.allclose(space.to_values(np.array([0.5, 0.5])), [1e-2, 1.0])
    assert space.overrides(v) == {"weights": {"w_x": 0.01}, "mpc": {"soc_chance_z": 1.5}}


def test_pareto_helpers():
    import tune_mpc
    F = np.array([[1.0, 5.0, 2.0], [2.0, 1.0, 2.0], [3.0, 6.0, 3.0], [1.0, 5.0, 1.0]])
    assert list(tune_mpc.pareto_mask(F)) == [False, True, False, True]   # row 0 is dominated by row 3
    Fn = tune_mpc.normalise(F)
    assert Fn.min() == 0 and Fn.max() == 1
    assert tune_mpc.normalise(np.array([[1.0, 2.0], [1.0, 3.0]]))[:, 0].tolist() == [0.0, 0.0]  # constant objective
    w = np.array([0.5, 0.5, 0.0])
    s = tune_mpc.chebyshev(Fn, w)
    assert s.argmin() in (1, 3)
    p = tune_mpc.presets(F, ["net_cost_eur", "violation_pp_h", "curtailed_pct"])
    assert p["cheapest"] == 3 and p["safest"] == 1 and p["greenest"] == 3
    assert p["balanced"] in (1, 3)


def test_parse_sets():
    assert replay.parse_sets(["SoC.E_capacity=72", "mpc.uncertainty_scaling=relative", "live.enabled=true"]) == {
        "SoC": {"E_capacity": 72}, "mpc": {"uncertainty_scaling": "relative"}, "live": {"enabled": True}}
    with pytest.raises(SystemExit):
        replay.parse_sets(["E_capacity=72"])


def test_metrics_report_the_extra_kpis(config):
    data = make_data(config, days=1.0)
    start = data.index[0]
    df, stats = replay.simulate(data, config, "rule", start, start + pd.Timedelta(days=1))
    m = replay.metrics(df, stats, config)
    assert m["renewable_use_pct"] + m["curtailed_pct"] == pytest.approx(100)
    assert m["equivalent_full_cycles"] == pytest.approx(m["throughput_kwh"] * 1000 / (2 * config["SoC"]["E_capacity"]))
    assert m["net_cost_eur"] == pytest.approx(m["total_eur"] - m["stored_value_eur"])
    assert m["peak_grid_w"] == pytest.approx(df["grid_w"].max())


def test_violation_noise_counts_as_zero():
    import tune_mpc
    res = {"net_cost_eur": 1.0, "violation_pp_h": 0.0008, "curtailed_pct": 20.0}
    assert tune_mpc.objective_vector(res, ["net_cost_eur", "violation_pp_h", "curtailed_pct"]) == [1.0, 0.0, 20.0]
    res["violation_pp_h"] = 0.5
    assert tune_mpc.objective_vector(res, ["violation_pp_h"]) == [0.5]


def test_bms_plant_keeps_the_band_and_reports_what_it_cut(config):
    cfg = copy.deepcopy(config)
    cfg["SoC"].update(E_capacity=10.0, initial_soc=0.15)   # tiny battery that runs empty
    cfg["replay"]["plant"] = "bms"
    data = make_data(cfg, days=1.0, ren=1.0, demand=20.0)
    start = data.index[0]
    df, stats = replay.simulate(data, cfg, "rule", start, start + pd.Timedelta(days=1))
    lo, hi = cfg["SoC"]["soc_min"], cfg["SoC"]["soc_max"]
    assert df["soc"].between(lo - 1e-9, hi + 1e-9).all()
    assert np.allclose(df["grid_w"] + df["discharge_w"], df["demand_w"])
    m = replay.metrics(df, stats, cfg)
    assert m["plant"] == "bms" and m["violation_pp_h"] == 0 and m["steps_below_soc_min_pct"] == 0
    # what the BMS withheld is covered by the grid and priced
    assert m["undelivered_discharge_kwh"] >= 0 and m["extra_grid_eur"] >= 0


def test_bms_cut_shows_up_as_undelivered_energy_and_extra_cost(config):
    cfg = copy.deepcopy(config)
    cfg["SoC"].update(E_capacity=10.0, initial_soc=0.12)
    cfg["mpc"].update(soc_chance_z=0.0, soc_backoff=0.0)
    data = make_data(cfg, days=0.5, ren=0.0, demand=20.0)
    data["solar_mu"] = 15.0     # the forecast promises renewables that never come
    start = data.index[0]
    end = start + pd.Timedelta(hours=12)
    phys, _ = replay.simulate(data, cfg, "det", start, end, replan_steps=8)
    cfg["replay"]["plant"] = "bms"
    bms, st = replay.simulate(data, cfg, "det", start, end, replan_steps=8)
    assert bms["soc"].min() >= cfg["SoC"]["soc_min"] - 1e-9
    m = replay.metrics(bms, st, cfg)
    assert phys["soc"].min() < cfg["SoC"]["soc_min"] - 1e-4      # without a BMS the plan leaves the band
    assert m["protection_steps"] > 0 and m["undelivered_discharge_kwh"] > 0 and m["extra_grid_eur"] > 0


def test_constant_backoff_tightens_the_soc_limits(config):
    from MPC_costfunction import soc_margins
    cfg = copy.deepcopy(config)
    cfg["mpc"].update(soc_chance_z=0.0, soc_backoff=0.05)
    data = make_data(cfg, days=0.5)
    h = replay.horizon_frame(data, 0, 96, "gpr_mean")
    low, up = soc_margins(h, cfg)
    assert np.allclose(low, 0.05)
    cfg["mpc"].update(soc_chance_z=1.0)
    hu = replay.horizon_frame(data, 0, 96, "gpr")
    low2, _ = soc_margins(hu, cfg)
    assert np.all(low2 >= 0.05 - 1e-12) and np.any(low2 > 0.05)    # back-off + chance margin


def test_backoff_baseline_is_a_tunable_controller(config):
    assert replay.CONTROLLERS["det_bo"]["forecast"] == "gpr_mean"
    assert "mpc.soc_backoff" in config["tuning"]["params_det_bo"]


def test_four_separate_periods_and_the_older_split(config):
    cfg = copy.deepcopy(config)
    data = make_data(cfg, days=20.0)
    cfg["replay"].update(calibration_days=3, tuning_days=5, development_days=4)
    p = replay.named_periods(data, cfg)
    assert list(p) == ["calibration", "tuning", "development", "final_test"]
    # back to back, no overlap, final test = the rest
    assert p["calibration"][1] == p["tuning"][0] and p["tuning"][1] == p["development"][0]
    assert p["final_test"][0] == p["development"][1]
    assert p["tuning"][1] - p["tuning"][0] == pd.Timedelta(days=5)
    # older split: calibration = tuning = validation days, development = the rest, no final test
    cfg["replay"].update(calibration_days=0, validation_days=4, test_days=0)
    q = replay.named_periods(data, cfg)
    assert q["calibration"] == q["tuning"] and q["final_test"] is None
    assert replay.periods(data, cfg) == (*q["tuning"], *q["development"])


def test_heldout_start_can_be_pinned(config):
    cfg = copy.deepcopy(config)
    cfg["replay"]["heldout_start"] = "2026-06-16"
    assert replay.heldout_start(cfg) == pd.Timestamp("2026-06-16", tz="Europe/Budapest").tz_convert("UTC")
