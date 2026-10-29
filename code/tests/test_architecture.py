"""Battery configurations: renewable_buffer (original), self_consumption and setpoint (grid charging)."""
import copy

import numpy as np
import pandas as pd
import pytest

import replay
from MPC_costfunction import _evaluate, optimize, soc_margins
from settings import load_config
from test_cost_function import make_horizon
from test_replay import make_data


@pytest.fixture
def config():
    cfg = load_config()
    cfg["replay"]["solver_ftol"] = 1e-7
    cfg["limits"]["p_charge_max_w"] = 24.0
    return cfg


def with_arch(cfg, arch, **mpc):
    c = copy.deepcopy(cfg)
    c["mpc"].update(architecture=arch, **mpc)
    return c


def test_unknown_architecture_is_refused(config):
    with pytest.raises(ValueError):
        optimize(make_horizon(), with_arch(config, "battery_island"))


@pytest.mark.parametrize("arch", ["self_consumption", "setpoint"])
def test_grid_charging_plan_keeps_balance_and_limits(config, arch):
    cfg = with_arch(config, arch)
    h = make_horizon(n=48, renewable_w=8.0, demand_w=12.0, seed=3)
    res, _, plan = optimize(h, cfg, x_0=0.5)
    assert res.success and res.max_balance_error_w < 1e-6
    ch, cg = plan["P_bat_ch (W)"].to_numpy(), plan["P_bat_ch_grid (W)"].to_numpy()
    assert (ch <= 24.0 + 1e-2).all() and (cg >= -1e-6).all() and (cg <= ch + 1e-2).all()
    assert (plan["P_grid_opt (W)"] >= -1e-2).all() and (plan["P_curtailed (W)"] >= -1e-2).all()
    deficit = np.clip(h["Demand_power (W)"] - h["Total_power (W)"], 0, None).to_numpy()
    assert (plan["P_bat_dis_opt (W)"].to_numpy() <= deficit + 1e-2).all()   # the battery does not export


def test_cheap_hours_charge_from_the_grid(config):
    cfg = with_arch(config, "self_consumption")
    cfg["SoC"]["E_capacity"] = 72.0
    cfg["weights"].update(w_x=0.0, w_T=0.0, w_delta_u=0.0)   # only the energy cost decides
    h = make_horizon(n=32, renewable_w=0.0, demand_w=12.0)
    h["Grid Price (€/Wh)"] = np.r_[np.full(16, 20.0), np.full(16, 300.0)] / 1e6
    res, _, plan = optimize(h, cfg, x_0=0.3)
    assert res.success
    assert plan["P_bat_ch_grid (W)"].iloc[:16].sum() > 50     # buys while cheap
    assert plan["P_bat_dis_opt (W)"].iloc[16:].sum() > 50     # uses it while expensive


def test_renewable_buffer_cannot_charge_from_the_grid(config):
    cfg = with_arch(config, "renewable_buffer", charge_is_decision=True)
    h = make_horizon(n=32, renewable_w=0.0, demand_w=12.0)
    _, _, plan = optimize(h, cfg, x_0=0.3)
    assert plan["P_bat_ch (W)"].abs().sum() < 1e-6


def test_margins_by_architecture(config):
    h = make_horizon(n=24, seed=2)
    low_a, up_a = soc_margins(h, with_arch(config, "renewable_buffer", soc_chance_z=1.5, charge_is_decision=True))
    low_b, up_b = soc_margins(h, with_arch(config, "self_consumption", soc_chance_z=1.5))
    low_c, up_c = soc_margins(h, with_arch(config, "setpoint", soc_chance_z=1.5, soc_backoff=0.05))
    assert (low_a > 0).all() and (up_a == 0).all()       # surplus is curtailed: only the lower limit
    assert np.allclose(low_b, low_a) and np.allclose(up_b, low_b)
    assert (low_c == 0).all() and (up_c == 0).all()     # the SoC follows the plan


def test_charge_gradient_matches_finite_differences(config):
    cfg = with_arch(config, "self_consumption")
    cfg["weights"]["w_ff"] = 1e-3
    h = make_horizon(n=10, renewable_w=20.0, demand_w=12.0, seed=4)
    rng = np.random.default_rng(1)
    u = np.column_stack([rng.uniform(0, 20, 10), rng.uniform(0, 10, 10)]).ravel()
    cr, cg = rng.uniform(0, 8, 10), rng.uniform(0, 8, 10)
    r = _evaluate(u, h, 0.5, cfg, with_grad=True, charge=cr + cg, charge_ren=cr)
    eps = 1e-6
    for j in (0, 4, 9):
        e = np.zeros(10)
        e[j] = eps
        f_cr = (_evaluate(u, h, 0.5, cfg, charge=cr + cg + e, charge_ren=cr + e)["cost"]
                - _evaluate(u, h, 0.5, cfg, charge=cr + cg - e, charge_ren=cr - e)["cost"]) / (2 * eps)
        assert f_cr == pytest.approx(r["grad_charge"][j] + r["grad_charge_ren"][j], rel=1e-4, abs=1e-9)


@pytest.mark.parametrize("arch", ["self_consumption", "setpoint"])
@pytest.mark.parametrize("controller", ["grid_only", "rule", "det", "ua"])
def test_replay_balance_with_grid_charging(config, arch, controller):
    cfg = with_arch(config, arch)
    data = make_data(cfg, days=1.0, ren=20.0)
    start = data.index[0]
    df, _ = replay.simulate(data, cfg, controller, start, start + pd.Timedelta(days=1), replan_steps=8)
    bal = df["grid_w"] + df["discharge_w"] + df["renewable_w"] - df["curtailed_w"] - df["charge_w"] - df["demand_w"]
    assert np.allclose(bal, 0, atol=1e-6)
    assert (df["grid_w"] >= -1e-9).all() and (df["curtailed_w"] >= -1e-9).all()
    assert (df["curtailed_w"] <= df["renewable_w"] + 1e-9).all()
    assert (df["discharge_w"] <= df["demand_w"] + 1e-9).all() and df["soc"].between(0, 1).all()


def test_with_a_perfect_forecast_self_consumption_equals_setpoint(config):
    data = make_data(config, days=0.5, ren=20.0)
    start = data.index[0]
    out = {a: replay.simulate(data, with_arch(config, a), "oracle", start, start + pd.Timedelta(hours=12),
                              replan_steps=8)[0] for a in ("self_consumption", "setpoint")}
    assert np.allclose(out["self_consumption"]["soc"], out["setpoint"]["soc"])
    assert np.allclose(out["self_consumption"]["grid_w"], out["setpoint"]["grid_w"])


def test_setpoint_soc_follows_the_plan_and_errors_reach_the_grid(config):
    data = make_data(config, days=0.5, ren=20.0, seed=1)
    start = data.index[0]
    end = start + pd.Timedelta(hours=12)
    sc, _ = replay.simulate(data, with_arch(config, "self_consumption"), "det", start, end, replan_steps=8)
    sp, _ = replay.simulate(data, with_arch(config, "setpoint"), "det", start, end, replan_steps=8)
    # the same plans; in setpoint operation the forecast error moves the grid power, not the SoC
    assert not np.allclose(sc["soc"], sp["soc"])
    assert not np.allclose(sc["grid_w"], sp["grid_w"])


def test_load_profile_cleaning_fills_gaps_and_normalises():
    import prepare_load_profile as plp
    idx = pd.date_range("2026-03-01", "2026-04-15", freq="h")       # spans the spring clock change
    local = pd.Series(1000.0 + 500.0 * (idx.hour >= 6) * (idx.hour < 14), index=idx)
    local.iloc[200] = 0.0                                             # a zero = missing
    local.iloc[300] = np.nan
    hourly, missing = plp.fill(plp.to_utc(local))
    assert hourly.notna().all() and hourly.index.tz is not None
    assert missing.sum() == 2           # the zero and the gap; the spring local hour does not exist in UTC
    assert hourly[missing].between(1000.0, 1500.0).all()
    pu = hourly / hourly.mean()
    assert pu.mean() == pytest.approx(1.0)


def test_measured_load_profile_settings_and_alignment(config, monkeypatch, tmp_path):
    data = make_data(config, days=1.0)
    prof = pd.DataFrame({"load_pu": np.linspace(0.5, 1.5, len(data))}, index=data.index.rename("timestamp_utc"))
    prof.iloc[:-10].to_csv(tmp_path / "load.csv")                     # the profile ends 10 steps early
    monkeypatch.setattr(replay, "DATA_DIR", tmp_path)
    cfg = copy.deepcopy(config)
    cfg["replay"].update(demand_profile_csv="load.csv", demand_mean_w=20.0)
    s = replay.replay_settings(cfg)
    assert s["demand_profile_csv"] == "load.csv" and s["demand_mean_w"] == 20.0
    p = pd.read_csv(tmp_path / "load.csv", index_col=0, parse_dates=True)["load_pu"]
    p.index = pd.to_datetime(p.index, utc=True)
    assert np.allclose(p.reindex(data.index).dropna().to_numpy() * 20.0, prof.iloc[:-10].load_pu * 20.0)
