import numpy as np
import pandas as pd
import pytest

from referencemodel_functions import (
    BatteryDegradationModel,
    SoCModel,
    aggregated_deviation,
    config,
    demand_model,
    get_solar_power,
    get_wind_power_vectorized,
)


def test_soc_model_charging_increases_soc():
    eta = config["SoC"]["eta_batt"]
    e_cap = config["SoC"]["E_capacity"]
    df = SoCModel(current_soc=0.5, p_charge=10.0, p_discharge=0.0, deltaT=3600)
    expected = 0.5 + 10.0 * eta / e_cap
    assert df["SoC"].iloc[0] == pytest.approx(expected)
    assert df["SoC_%"].iloc[0] == pytest.approx(expected * 100)


def test_soc_model_is_clipped_to_physical_range():
    df = SoCModel(current_soc=0.99, p_charge=1e6, p_discharge=0.0, deltaT=3600)
    assert df["SoC"].iloc[0] == 1.0
    df = SoCModel(current_soc=0.01, p_charge=0.0, p_discharge=1e6, deltaT=3600)
    assert df["SoC"].iloc[0] == 0.0


def test_degradation_cost_is_proportional_to_throughput():
    c_deg = config["degradation"]["c_deg"]
    df = BatteryDegradationModel(p_charge=100.0, p_discharge=50.0, deltaT=3600)
    assert df["Degradation_Cost"].iloc[0] == pytest.approx(150.0 * c_deg)  # not rounded


def test_aggregated_deviation_is_root_sum_square():
    df = aggregated_deviation([3.0, 0.0], [4.0, 2.0])
    assert list(df["Aggregated_Deviation_W"]) == [5.0, 2.0]


def test_wind_power_cut_in_and_cut_out():
    power = get_wind_power_vectorized(np.array([0.5, 5.0, 10.0, 13.0]))
    assert power[0] == 0.0
    assert power[1] > 0.0
    assert power[2] > power[1]  # capped at 8 m/s, still above the 5 m/s value
    assert power[3] == 0.0


def test_solar_power_scales_with_irradiance():
    assert get_solar_power(0.0) == 0.0
    assert get_solar_power(1000.0) == pytest.approx(30 * 0.92)


def test_demand_model_follows_schedule(tmp_path):
    schedule = tmp_path / "production.csv"
    pd.DataFrame({
        "Timestamp": ["2026-01-01 00:00:00", "2026-01-01 01:00:00", "2026-01-01 02:00:00"],
        "Mode": ["STOPPED", "MAX_PRODUCTION", "FIXED_COUNT"],
        "Target_Output": [0, 0, 60],
    }).to_csv(schedule, index=False)

    df = demand_model(
        schedule_csv_path=schedule,
        start_time="2026-01-01 00:00:00",
        horizon_steps=12,
        step_duration_s=900,
    )

    assert len(df) == 12
    power = df["P_demand_W"].to_numpy()
    assert (power[:4] == 0).all()  # STOPPED
    assert (power[4:8] == 24.0).all()  # MAX_PRODUCTION: 30 units x 30 s per 15 min at 24 W
    assert power[8:] == pytest.approx([12.0] * 4)  # FIXED_COUNT 60/h -> 15 units per step


class _FakeGPR:
    """Returns zero mean and a constant std of 1 in the scaled target space."""

    def predict(self, X, return_std=False):
        n = len(X)
        return (np.zeros(n), np.ones(n)) if return_std else np.zeros(n)


class _IdentityScalerX:
    def transform(self, X):
        return np.asarray(X, dtype=float)


class _ScalerY:
    scale_ = np.array([2.5])

    def inverse_transform(self, y):
        return np.asarray(y) * self.scale_[0]


def _fake_models():
    return {
        "solar_gpr": _FakeGPR(), "scaler_X_solar": _IdentityScalerX(), "scaler_y_solar": _ScalerY(),
        "wind_gpr": _FakeGPR(), "scaler_X_wind": _IdentityScalerX(), "scaler_y_wind": _ScalerY(),
    }


def _forecast(gti):
    n = len(gti)
    return pd.DataFrame({
        "date": pd.date_range("2026-06-01 00:00", periods=n, freq="15min", tz="Europe/Budapest"),
        "global_tilted_irradiance": gti,
        "wind_speed_10m": np.full(n, 5.0),
        "wind_speed_100m": np.full(n, 7.0),
        "wind_gusts_10m": np.full(n, 8.0),
        "temperature_2m": np.full(n, 15.0),
        "wind_direction_10m": np.full(n, 180.0),
    })


def test_gpr_std_is_returned_in_watts(monkeypatch):
    import referencemodel_functions as rm

    monkeypatch.setattr(rm, "_load_gpr_models", _fake_models)
    _, wind_std = rm.predict_windPower(_forecast([500.0] * 4))
    _, solar_std = rm.predict_solarPower(_forecast([500.0] * 4))
    assert np.allclose(wind_std, 2.5)  # 1 (scaled) x scale_ 2.5
    assert np.allclose(solar_std, 2.5)


def test_solar_is_zero_at_night(monkeypatch):
    import referencemodel_functions as rm

    monkeypatch.setattr(rm, "_load_gpr_models", _fake_models)
    power, std = rm.predict_solarPower(_forecast([0.0, 0.0, 300.0, 600.0]))
    assert power[0] == power[1] == 0.0
    assert std[0] == std[1] == 0.0
    assert power[3] > power[2] > 0.0
    assert std[2] > 0.0


def test_physical_models_follow_config():
    cfg = {"pv": {"rated_power_w": 300.0, "efficiency": 1.0}, "wind": {"rotor_area_m2": 0.64, "cut_out_speed_ms": 6.0}}
    assert get_solar_power(1000.0, cfg) == pytest.approx(300.0)
    base = get_wind_power_vectorized(np.array([5.0]))[0]
    bigger = get_wind_power_vectorized(np.array([5.0]), cfg)[0]
    assert bigger == pytest.approx(2 * base)  # double rotor area -> double power
    assert get_wind_power_vectorized(np.array([7.0]), cfg)[0] == 0.0  # above the configured cut-out


def test_demand_model_uses_site_local_time(tmp_path):
    schedule = tmp_path / "production.csv"
    pd.DataFrame({
        "Timestamp": ["2026-10-17 11:00:00", "2026-10-17 12:00:00"],
        "Mode": ["STOPPED", "MAX_PRODUCTION"],
        "Target_Output": [0, 0],
    }).to_csv(schedule, index=False)

    # 10:00 UTC is 12:00 in Budapest (CEST) -> production is running
    start = pd.Timestamp("2026-10-17 10:00", tz="UTC")
    df = demand_model(schedule_csv_path=schedule, start_time=start, horizon_steps=2, step_duration_s=900)
    assert df["Timestamp"].iloc[0] == pd.Timestamp("2026-10-17 12:00")
    assert (df["P_demand_W"] == 24.0).all()
