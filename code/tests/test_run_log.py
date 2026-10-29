from types import SimpleNamespace

import pandas as pd
import pytest

import live_data
import run_log
from settings import load_config


@pytest.fixture
def logs(tmp_path, monkeypatch):
    monkeypatch.setattr(run_log, "RUN_LOG", tmp_path / "runs" / "mpc_runs.csv")
    monkeypatch.setattr(run_log, "DECISION_LOG", tmp_path / "runs" / "decisions.csv")
    monkeypatch.setattr(live_data, "LIVE_DIR", tmp_path / "live")
    cfg = load_config()
    cfg["live"]["mpc_interval_min"] = 15
    cfg["mpc"]["soc_chance_z"] = 2.0
    return cfg


def make_plan(start, n=8, dis=10.0, price=1e-4):
    ts = pd.date_range(start, periods=n, freq="15min", tz="UTC")
    return pd.DataFrame({
        "Date": ts, "P_grid_opt (W)": 14.0, "P_bat_dis_opt (W)": dis, "P_bat_ch (W)": 5.0, "P_curtailed (W)": 0.0,
        "SoC_opt": 0.5, "SoC_min_eff (%)": 12.0, "SoC_max_eff (%)": 90.0, "Total_power (W)": 5.0,
        "Sigma_eff (W)": 1.0, "Demand_power (W)": 24.0, "Grid Price (€/Wh)": price,
    })


def fake_res(success=True):
    return SimpleNamespace(success=success, nit=12, initial_soc=0.5, min_soc_margin=0.01,
                           cost_terms={"J_x": 0.1, "J_u": 0.2, "J_du": 0.0, "J_ff": 0.0, "J_T": 0.0})


def test_each_run_logs_one_row_and_the_decisions_until_the_next_run(logs):
    t0 = pd.Timestamp("2026-10-22 10:00", tz="UTC")
    for k in range(3):
        computed = t0 + pd.Timedelta(minutes=15 * k, seconds=20)
        run_log.log_run(fake_res(), make_plan(t0 + pd.Timedelta(minutes=15 * k)), logs, "live measurement", 1.2,
                        total_cost=0.5, computed=computed)
    runs = run_log.read_runs()
    dec = run_log.read_decisions()
    assert len(runs) == 3 and runs["success"].all()
    assert runs["soc_chance_z"].iloc[0] == 2.0 and runs["grid_w"].iloc[0] == 14.0
    # 15-minute interval = one 15-minute step per run, one decision per step
    assert len(dec) == 3 and list(dec.index) == list(pd.date_range(t0, periods=3, freq="15min", tz="UTC"))
    assert dec["soc_min_eff"].iloc[0] == pytest.approx(0.12)


def test_results_measure_the_three_levels_of_a_violation(logs):
    t0 = pd.Timestamp("2026-10-22 10:00", tz="UTC")
    for k in range(4):
        run_log.log_run(fake_res(), make_plan(t0 + pd.Timedelta(minutes=15 * k)), logs, "live measurement", 1.0,
                        total_cost=0.5, computed=t0 + pd.Timedelta(minutes=15 * k, seconds=5))
    # measurements: SoC falls below soc_min in the last step and the BMS stops the discharge there
    recs = []
    for k in range(4):
        for s in range(0, 900, 60):
            ts = t0 + pd.Timedelta(minutes=15 * k, seconds=s)
            soc = [0.30, 0.20, 0.12, 0.095][k]
            bat = 10.0 if k < 3 else 0.0
            recs.append({"timestamp": ts.isoformat(), "load_w": 24.0, "grid_w": 24.0 - bat, "battery_w": bat,
                         "pv_w": 0.0, "wind_w": 0.0, "curtailed_w": 0.0, "soc": soc})
    live_data.append_measurements(recs, config=logs)
    res = run_log.results(t0, t0 + pd.Timedelta(hours=1), logs)
    t = res["total"]
    assert t["measured_hours"] == pytest.approx(1.0)
    assert t["mpc_runs"] == 4 and t["solver_success_pct"] == 100
    assert t["steps_below_soc_min_pct"] == pytest.approx(25.0)
    assert t["violation_pp_h"] == pytest.approx(100 * 0.005 * 0.25)
    assert t["chance_target_below_pct"] == pytest.approx(2.275, abs=0.01)
    assert t["protection_steps"] == 1
    assert t["undelivered_discharge_kwh"] == pytest.approx(10.0 * 0.25 / 1000)
    assert t["extra_grid_eur"] == pytest.approx(10.0 * 1e-4 * 0.25)
    # cost: grid power times price, compared with supplying the load from the grid only
    assert t["grid_only_eur"] == pytest.approx(24.0 * 1e-4 * 1.0)
    assert 0 < t["saving_vs_grid_only_pct"] < 100
    assert len(res["days"]) == 1


def test_results_without_any_log_are_empty_but_valid(logs):
    res = run_log.results(None, None, logs)
    assert res["total"]["measured_hours"] == 0 and res["days"] == []
