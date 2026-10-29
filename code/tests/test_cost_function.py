import copy

import numpy as np
import pandas as pd
import pytest

from MPC_costfunction import (
    _evaluate,
    charge_profile,
    cost_function,
    cost_gradient,
    get_only_cost,
    optimize,
    power_balance_constraint,
    soc_bounds_constraint,
    soc_bounds_jacobian,
    soc_margins,
)
from settings import load_config


@pytest.fixture
def config():
    return load_config()


def make_horizon(n=8, renewable_w=5.0, demand_w=12.0, price_eur_mwh=100.0, seed=None):
    rng = np.random.default_rng(seed)
    ren = np.full(n, renewable_w) if seed is None else rng.uniform(0, 2 * renewable_w, n)
    dem = np.full(n, demand_w) if seed is None else rng.choice([0.0, demand_w / 2, demand_w], n)
    price = np.full(n, price_eur_mwh) if seed is None else rng.uniform(0.5, 1.5, n) * price_eur_mwh
    return pd.DataFrame({
        "Date": pd.date_range("2026-01-01", periods=n, freq="15min"),
        "Total_power (W)": ren,
        "Aggregated_deviation (W)": np.full(n, 1.0),
        "Demand_power (W)": dem,
        "Grid Price (€/Wh)": price / 1e6,
    })


def test_cost_function_outputs(config):
    horizon = make_horizon()
    n = len(horizon)
    u = np.zeros(2 * n)
    u[0::2] = horizon["Demand_power (W)"].to_numpy()

    cost, soc, deg, eu = cost_function(u, horizon, 0.5, config)

    assert np.isfinite(cost) and cost >= 0
    assert len(soc) == n + 1 and soc[0] == 0.5
    assert len(deg) == n and len(eu) == n
    assert soc[-1] > soc[0]  # renewable energy charges the battery, nothing is discharged


def test_gradient_matches_finite_differences(config):
    horizon = make_horizon(n=12, seed=1)
    rng = np.random.default_rng(2)
    u = np.column_stack([rng.uniform(0, 20, 12), rng.uniform(0, 10, 12)]).ravel()

    grad = cost_gradient(u, horizon, 0.5, config)
    eps = 1e-5
    fd = np.array([
        (get_only_cost(u + eps * e, horizon, 0.5, config) - get_only_cost(u - eps * e, horizon, 0.5, config)) / (2 * eps)
        for e in np.eye(len(u))
    ])
    assert np.allclose(grad, fd, atol=1e-9)


def test_soc_jacobian_matches_finite_differences(config):
    horizon = make_horizon(n=10, seed=3)
    u = np.full(20, 3.0)
    J = soc_bounds_jacobian(u, horizon, 0.5, config)
    eps = 1e-5
    fd = np.column_stack([
        (soc_bounds_constraint(u + eps * e, horizon, 0.5, config) - soc_bounds_constraint(u - eps * e, horizon, 0.5, config)) / (2 * eps)
        for e in np.eye(len(u))
    ])
    assert np.allclose(J, fd, atol=1e-10)


def test_charging_is_capped_by_config(config):
    cfg = copy.deepcopy(config)
    cfg["limits"]["p_charge_max_w"] = 4.0
    horizon = make_horizon(renewable_w=10.0)
    assert np.allclose(charge_profile(horizon, cfg), 4.0)


def test_power_balance_is_zero_when_grid_covers_demand():
    horizon = make_horizon()
    u = np.zeros(2 * len(horizon))
    u[0::2] = horizon["Demand_power (W)"].to_numpy()
    assert np.allclose(power_balance_constraint(u, horizon), 0.0)


def test_soc_bounds_constraint_shape(config):
    horizon = make_horizon()
    u = np.zeros(2 * len(horizon))
    c = soc_bounds_constraint(u, horizon, 0.5, config)
    assert c.shape == (2 * len(horizon),)
    assert (c >= 0).all()


def test_optimizer_respects_constraints_and_limits(config):
    horizon = make_horizon(n=96, renewable_w=8.0, demand_w=24.0, seed=4)
    res, best_cost, result_df = optimize(horizon, config)

    assert res.success
    assert np.isfinite(best_cost)
    balance = result_df["P_grid_opt (W)"] + result_df["P_bat_dis_opt (W)"] - result_df["Demand_power (W)"]
    assert np.allclose(balance, 0.0, atol=0.02)
    assert (result_df["P_bat_dis_opt (W)"] <= config["limits"]["p_discharge_max_w"] + 1e-6).all()
    assert (result_df["P_grid_opt (W)"] >= -1e-6).all()
    soc = result_df["SoC_opt"]
    assert soc.between(config["SoC"]["soc_min"] - 1e-3, config["SoC"]["soc_max"] + 1e-3).all()


def test_optimizer_improves_on_grid_only_plan(config):
    horizon = make_horizon(n=48, renewable_w=8.0, demand_w=24.0, seed=5)
    res, best_cost, result_df = optimize(horizon, config)

    grid_only = np.zeros(2 * len(horizon))
    grid_only[0::2] = horizon["Demand_power (W)"].to_numpy()
    assert best_cost < get_only_cost(grid_only, horizon, config["SoC"]["initial_soc"], config) - 1e-6
    assert result_df["P_bat_dis_opt (W)"].sum() > 0  # the battery is actually used


def _variant(config, **mpc):
    cfg = copy.deepcopy(config)
    cfg["mpc"] = {"charge_is_decision": False, "smoothness": "total", "soc_chance_z": 0.0, **mpc}
    return cfg


@pytest.mark.parametrize("charge_is_decision", [False, True])
@pytest.mark.parametrize("smoothness", ["total", "inputs"])
def test_gradients_of_all_model_variants(config, charge_is_decision, smoothness):
    cfg = _variant(config, charge_is_decision=charge_is_decision, smoothness=smoothness, soc_chance_z=1.0)
    horizon = make_horizon(n=10, seed=6)
    rng = np.random.default_rng(7)
    u = np.column_stack([rng.uniform(0, 20, 10), rng.uniform(0, 10, 10)]).ravel()
    c = rng.uniform(0, 8, 10)
    r = _evaluate(u, horizon, 0.5, cfg, with_grad=True, charge=c)
    eps = 1e-5
    f = lambda uu, cc: _evaluate(uu, horizon, 0.5, cfg, charge=cc)["cost"]  # noqa: E731
    fd_u = np.array([(f(u + eps * e, c) - f(u - eps * e, c)) / (2 * eps) for e in np.eye(20)])
    fd_c = np.array([(f(u, c + eps * e) - f(u, c - eps * e)) / (2 * eps) for e in np.eye(10)])
    assert np.allclose(r["grad"], fd_u, atol=1e-7)
    assert np.allclose(r["grad_charge"], fd_c, atol=1e-7)


def test_original_smoothness_term_is_constant_but_inputs_term_is_not(config):
    horizon = make_horizon(n=8, seed=8)
    demand = horizon["Demand_power (W)"].to_numpy()
    u1, u2 = np.zeros(16), np.zeros(16)
    u1[0::2] = demand                     # grid only
    u2[1::2] = np.minimum(demand, 5.0)    # some battery
    u2[0::2] = demand - u2[1::2]
    total = _variant(config, smoothness="total")
    inputs = _variant(config, smoothness="inputs")
    assert _evaluate(u1, horizon, 0.5, total)["terms"]["J_du"] == pytest.approx(_evaluate(u2, horizon, 0.5, total)["terms"]["J_du"])
    assert _evaluate(u1, horizon, 0.5, inputs)["terms"]["J_du"] != pytest.approx(_evaluate(u2, horizon, 0.5, inputs)["terms"]["J_du"])


def test_charge_decision_curtails_instead_of_overfilling(config):
    horizon = make_horizon(n=48, renewable_w=20.0, demand_w=0.0)  # lots of sun, nothing to supply
    original = _variant(config)
    res_o, _, out_o = optimize(horizon, original, x_0=0.7)
    assert out_o["SoC_opt"].max() > config["SoC"]["soc_max"] + 0.01  # infeasible without curtailment

    extended = _variant(config, charge_is_decision=True, smoothness="inputs")
    res, _, out = optimize(horizon, extended, x_0=0.7)
    assert res.success
    assert out["SoC_opt"].max() <= config["SoC"]["soc_max"] + 1e-3
    assert out["P_curtailed (W)"].sum() > 0
    assert (out["P_bat_ch (W)"] <= horizon["Total_power (W)"] + 1e-6).all()


def test_chance_constraint_tightens_with_uncertainty(config):
    horizon = make_horizon(n=24, seed=9)
    no_chance = _variant(config, charge_is_decision=True)
    with_chance = _variant(config, charge_is_decision=True, soc_chance_z=2.0)
    m0, _ = soc_margins(horizon, no_chance)
    m2, up2 = soc_margins(horizon, with_chance)
    assert np.allclose(m0, 0.0)
    assert (np.diff(m2) >= 0).all() and m2[-1] > 0  # grows over the horizon
    assert np.allclose(up2, 0.0)  # upper limit not tightened when charging is a decision
    _, up_follow = soc_margins(horizon, _variant(config, soc_chance_z=2.0))
    assert up_follow[-1] > 0


def test_objective_scale_does_not_break_convergence(config):
    cfg = _variant(config, charge_is_decision=True, smoothness="inputs")
    cfg["weights"]["w_ff"] = 50.0  # makes the total cost very large
    horizon = make_horizon(n=48, renewable_w=20.0, demand_w=0.0)
    res, _, _ = optimize(horizon, cfg, x_0=0.7)
    assert res.success


def test_plan_history_is_saved_and_pruned(tmp_path, monkeypatch):
    import MPC_costfunction as m

    monkeypatch.setattr(m, "RESULTS_DIR", tmp_path)
    df = pd.DataFrame({"Date": ["2026-10-16 12:00:00+02:00"], "P_grid_opt (W)": [1.0]})
    m.save_plan_history(df, now=pd.Timestamp("2026-10-01T10:00:00", tz="UTC"))
    m.save_plan_history(df, now=pd.Timestamp("2026-10-16T10:00:00", tz="UTC"))
    names = sorted(p.name for p in (tmp_path / "plans").glob("*.csv"))
    assert names == ["plan_20261016T100000.csv"]  # the 15-day-old plan was removed


# ---------------------------------------------------------------- uncertainty scaling

def test_cumulative_std_independent_and_ar1():
    from MPC_costfunction import cumulative_std
    sigma = np.array([1.0, 2.0, 0.5, 1.5])
    assert np.allclose(cumulative_std(sigma, 0.0), np.sqrt(np.cumsum(sigma ** 2)))
    phi = 0.7
    n = len(sigma)
    cov = np.array([[sigma[i] * sigma[j] * phi ** abs(i - j) for j in range(n)] for i in range(n)])
    brute = np.sqrt([cov[:k + 1, :k + 1].sum() for k in range(n)])
    assert np.allclose(cumulative_std(sigma, phi), brute)


def test_relative_confidence_is_independent_of_system_size(config):
    from MPC_costfunction import _mpc_params, forecast_confidence, installed_renewable_w
    cfg = copy.deepcopy(config)
    cfg["mpc"].update(uncertainty_scaling="relative", sigma_ref_w=0.0)
    cfg["weights"]["lambda"] = 100.0
    p_small = _mpc_params(cfg)
    assert p_small["sigma_ref"] == pytest.approx(installed_renewable_w(cfg))
    big = copy.deepcopy(cfg)
    big["pv"]["rated_power_w"] *= 1000
    big["wind"]["rotor_area_m2"] *= 1000
    p_big = _mpc_params(big)
    sigma = np.array([0.5, 1.0, 2.0])
    assert np.allclose(forecast_confidence(sigma, p_small), forecast_confidence(sigma * 1000, p_big))
    absolute = copy.deepcopy(cfg)
    absolute["mpc"]["uncertainty_scaling"] = "absolute"
    assert np.allclose(forecast_confidence(sigma, _mpc_params(absolute)), 1 / (1 + 100.0 * sigma ** 2))


def test_sigma_scale_and_autocorr_widen_the_soc_margin(config):
    cfg = copy.deepcopy(config)
    cfg["mpc"].update(soc_chance_z=1.0, sigma_scale=1.0, error_autocorr=0.0, margin_window="horizon")
    h = make_horizon(n=16)
    base, _ = soc_margins(h, cfg)
    cfg["mpc"]["sigma_scale"] = 2.0
    scaled, _ = soc_margins(h, cfg)
    assert np.allclose(scaled, 2 * base)
    cfg["mpc"].update(sigma_scale=1.0, error_autocorr=0.8)
    corr, _ = soc_margins(h, cfg)
    assert np.all(corr[1:] > base[1:]) and corr[0] == pytest.approx(base[0])


def test_warm_start_reaches_the_same_plan(config):
    h = make_horizon(n=24, seed=3)
    res_cold, cost_cold, plan = optimize(h, config, x_0=0.5)
    init = {"dis": plan["P_bat_dis_opt (W)"].to_numpy(), "charge": plan["P_bat_ch (W)"].to_numpy()}
    res_warm, cost_warm, _ = optimize(h, config, x_0=0.5, init=init)
    assert cost_warm == pytest.approx(cost_cold, rel=1e-4, abs=1e-9)
    assert res_warm.nit <= res_cold.nit


def test_old_configs_get_the_new_sections_with_the_original_behaviour():
    from settings import with_defaults
    cfg = with_defaults({"mpc": {"soc_chance_z": 1.0}, "live": {"enabled": True}})
    assert cfg["mpc"]["uncertainty_scaling"] == "absolute" and cfg["mpc"]["sigma_scale"] == 1.0
    assert cfg["mpc"]["soc_chance_z"] == 1.0 and cfg["live"]["enabled"] is True
    assert "params_ua" in cfg["tuning"] and cfg["replay"]["replan_steps"] == 4


def test_weather_sigma_model_scales_with_forecast_difficulty(config):
    from features import DIFFICULTY_FEATURES
    from MPC_costfunction import effective_sigma, sigma_multiplier
    cfg = copy.deepcopy(config)
    h = make_horizon(n=4)
    cfg["mpc"].update(sigma_model="constant", sigma_scale=1.3)
    assert np.allclose(sigma_multiplier(h, cfg), 1.3)
    cfg["mpc"].update(sigma_model="weather", sigma_weather_coef=[0.0, 0.2, 0.5])
    assert np.allclose(sigma_multiplier(h, cfg), 1.3)          # no feature columns: constant factor
    h[DIFFICULTY_FEATURES[0]] = [0.0, 5.0, 0.0, 5.0]
    h[DIFFICULTY_FEATURES[1]] = [0.0, 0.0, 200.0, 200.0]
    m = sigma_multiplier(h, cfg)
    assert np.allclose(m, np.exp([0.0, 1.0, 1.0, 2.0]))
    assert np.allclose(effective_sigma(h, cfg), m * 1.0)
    cfg["mpc"]["sigma_model"] = "weather_floor"
    assert np.allclose(sigma_multiplier(h, cfg), np.maximum(1.3, np.exp([0.0, 1.0, 1.0, 2.0])))


def test_difficulty_features():
    from features import DIFFICULTY_FEATURES, difficulty_features
    dates = pd.date_range("2026-07-15 10:00", periods=13, freq="15min", tz="Europe/Budapest")
    w = pd.DataFrame({"wind_speed_10m": 3.0, "wind_gusts_10m": [2.0] + [9.0] * 12,
                      "global_tilted_irradiance": [500.0] * 6 + [100.0, 700.0] * 3 + [500.0]})
    f = difficulty_features(w, dates)
    assert list(f.columns) == DIFFICULTY_FEATURES
    assert f.iloc[0, 0] == 0.0 and f.iloc[1, 0] == 6.0           # gust excess, never negative
    assert f.iloc[3, 1] == 0.0 and f.iloc[-1, 1] > 200          # calm, then broken-cloud irradiance


def test_weather_calibration_recovers_the_coefficients():
    from calibrate_uncertainty import fit_weather
    rng = np.random.default_rng(0)
    n = 4000
    feats = np.column_stack([rng.uniform(0, 6, n), rng.uniform(0, 300, n)])
    sd = rng.uniform(0.5, 2.0, n)
    true = [-0.3, 0.15, 0.4]
    err = rng.normal(0, sd * np.exp(true[0] + true[1] * feats[:, 0] + true[2] * feats[:, 1] / 100))
    assert np.allclose(fit_weather(err, sd, feats), true, atol=0.05)


def test_window_std_matches_brute_force_and_saturates():
    from MPC_costfunction import cumulative_std, window_std
    rng = np.random.default_rng(1)
    sigma = rng.uniform(0.5, 2.0, 12)
    phi, w = 0.8, 4
    n = len(sigma)
    cov = np.array([[sigma[i] * sigma[j] * phi ** abs(i - j) for j in range(n)] for i in range(n)])
    brute = np.sqrt([cov[max(k - w + 1, 0):k + 1, max(k - w + 1, 0):k + 1].sum() for k in range(n)])
    assert np.allclose(window_std(sigma, phi, w), brute)
    assert np.allclose(window_std(sigma, phi, 0), cumulative_std(sigma, phi))
    assert np.allclose(window_std(sigma, 0.0, 1), sigma)                       # next step only
    const = window_std(np.ones(20), phi, w)
    assert np.allclose(const[w - 1:], const[w - 1])                            # no growth after the window


def test_margin_window_shrinks_the_late_soc_margin(config):
    cfg = copy.deepcopy(config)
    cfg["mpc"].update(soc_chance_z=1.0, error_autocorr=0.5, margin_window="horizon")
    h = make_horizon(n=24)
    full, _ = soc_margins(h, cfg)
    cfg["mpc"].update(margin_window="steps", margin_steps=4)
    win, _ = soc_margins(h, cfg)
    assert np.allclose(win[:4], full[:4]) and np.all(win[6:] < full[6:])


def test_margin_window_follows_the_replanning_interval(config):
    from MPC_costfunction import margin_window_steps
    cfg = copy.deepcopy(config)
    cfg["mpc"].pop("margin_steps", None)
    cfg["mpc"]["margin_window"] = "horizon"
    assert margin_window_steps(cfg) == 0
    cfg["mpc"]["margin_window"] = "replan"
    cfg.setdefault("live", {})["mpc_interval_min"] = 15
    step_min = cfg["general"]["deltaT"] / 60
    assert margin_window_steps(cfg) == max(1, round(15 / step_min))     # live: one MPC interval
    cfg["mpc"]["replan_steps"] = 4                                       # set by the replay
    assert margin_window_steps(cfg) == 4
    cfg["mpc"].update(margin_window="steps", margin_steps=6)
    assert margin_window_steps(cfg) == 6
    del cfg["mpc"]["margin_window"]                                      # older configs
    assert margin_window_steps(cfg) == 6


@pytest.mark.parametrize("charge_is_decision", [True, False])
def test_start_below_the_band_recovers_instead_of_failing(config, charge_is_decision):
    cfg = copy.deepcopy(config)
    cfg["SoC"]["E_capacity"] = 72.0
    cfg["mpc"].update(charge_is_decision=charge_is_decision, soc_chance_z=1.0)
    # little renewable power: getting back into the band takes several steps
    horizon = make_horizon(n=96, renewable_w=1.0, demand_w=24.0)
    x0 = cfg["SoC"]["soc_min"] - 0.03
    res, _, plan = optimize(horizon, cfg, x_0=x0)
    assert res.success and res.recovery_steps > 0
    soc = plan["SoC_opt"].to_numpy()
    lo = plan["SoC_min_eff (%)"].to_numpy() / 100
    back = int(np.argmax(soc >= lo - 1e-3))
    assert back > 0 and (soc[back:] >= lo[back:] - 1e-3).all()      # back in the band, and it stays there
    assert (plan["P_bat_dis_opt (W)"].to_numpy()[:back] <= 1e-2).all()  # no discharge while below it
    # without the relaxation the same start is infeasible
    cfg["mpc"]["soc_recovery"] = False
    res_off, _, _ = optimize(horizon, cfg, x_0=x0)
    assert not res_off.success


def test_recovery_is_inactive_inside_the_band(config):
    horizon = make_horizon(n=48, renewable_w=8.0, demand_w=24.0, seed=5)
    res_on, cost_on, _ = optimize(horizon, config, x_0=0.5)
    cfg = copy.deepcopy(config)
    cfg["mpc"]["soc_recovery"] = False
    res_off, cost_off, _ = optimize(horizon, cfg, x_0=0.5)
    assert res_on.recovery_steps == 0 and cost_on == pytest.approx(cost_off)
