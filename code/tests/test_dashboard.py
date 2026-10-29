import json

import pandas as pd
import pytest

import dashboard_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Work on copies so the tests never touch the real config, schedule or results
    config_copy = tmp_path / "dataConfig.json"
    config_copy.write_text(dashboard_app.CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    schedule_copy = tmp_path / "production.csv"
    schedule_copy.write_text(dashboard_app.PRODUCTION_CSV.read_text(encoding="utf-8"), encoding="utf-8")

    monkeypatch.setattr(dashboard_app, "CONFIG_PATH", config_copy)
    monkeypatch.setattr(dashboard_app, "PRODUCTION_CSV", schedule_copy)
    monkeypatch.setattr(dashboard_app, "RESULTS_CSV", tmp_path / "CostFunction_results.csv")
    monkeypatch.setattr(dashboard_app, "load_config", lambda: json.loads(config_copy.read_text(encoding="utf-8")))
    return dashboard_app.app.test_client()


def test_index_page(client):
    res = client.get("/")
    assert res.status_code == 200
    assert b"Kairos" in res.data
    assert "József Szeles and Laura Deszát".encode() in res.data


def test_status(client):
    data = client.get("/api/status").get_json()
    assert set(data["models"]) == {"svr", "gpr"}
    assert data["job"]["status"] in {"idle", "running", "success", "failed", "stopped"}


def test_config_roundtrip_and_validation(client):
    cfg = client.get("/api/config").get_json()
    cfg["weights"]["w_x"] = 0.3
    assert client.put("/api/config", json=cfg).status_code == 200
    assert client.get("/api/config").get_json()["weights"]["w_x"] == 0.3

    cfg["SoC"]["E_capacity"] = "large"
    res = client.put("/api/config", json=cfg)
    assert res.status_code == 400
    assert "E_capacity" in res.get_json()["error"]


def test_production_schedule(client):
    data = client.get("/api/production").get_json()
    rows = data["rows"]
    assert "MAX_PRODUCTION" in data["modes"]

    rows[0]["Mode"] = "MAX_PRODUCTION"
    assert client.put("/api/production", json={"rows": rows}).status_code == 200
    saved = pd.read_csv(dashboard_app.PRODUCTION_CSV)
    assert saved.loc[0, "Mode"] == "MAX_PRODUCTION"

    rows[0]["Mode"] = "TURBO"
    assert client.put("/api/production", json={"rows": rows}).status_code == 400


def test_results(client):
    assert client.get("/api/results").get_json() == {"available": False}

    n = 4
    pd.DataFrame({
        "Date": pd.date_range("2026-01-01", periods=n, freq="15min").astype(str),
        "Mean_wind_power (W)": [1.0] * n,
        "Mean_solar_power (W)": [2.0] * n,
        "Total_power (W)": [3.0] * n,
        "Aggregated_deviation (W)": [0.5] * n,
        "Demand_power (W)": [8.0] * n,
        "Grid Price (€/Wh)": [1e-4] * n,
        "P_grid_opt (W)": [8.0] * n,
        "P_bat_dis_opt (W)": [0.0] * n,
        "SoC_opt": [0.50, 0.51, 0.52, 0.53],
        "SoC_opt (%)": [50.0, 51.0, 52.0, 53.0],
        "Grid and degradation cost": [0.001] * n,
    }).to_csv(dashboard_app.RESULTS_CSV, index=False)

    data = client.get("/api/results").get_json()
    assert data["available"]
    assert data["summary"]["grid_energy_kwh"] == pytest.approx(4 * 8.0 * 0.25 / 1000)
    assert data["summary"]["soc_end"] == pytest.approx(0.53)
    assert data["series"]["price_mwh"] == pytest.approx([100.0] * n)


def test_mpc_refuses_without_api_key(client, monkeypatch):
    monkeypatch.delenv("ENTSOE_API_KEY", raising=False)
    monkeypatch.setattr(dashboard_app, "_model_group", lambda files: {"ready": True, "missing": []})
    res = client.post("/api/jobs/run_mpc")
    assert res.status_code == 409
    assert "ENTSOE_API_KEY" in res.get_json()["error"]


@pytest.fixture
def live_client(client, tmp_path, monkeypatch):
    import live_data

    monkeypatch.setattr(live_data, "LIVE_DIR", tmp_path / "live")
    settings = {"enabled": True, "api_token": "secret", "stale_after_s": 120, "use_measured_soc": True, "measured_soc_max_age_s": 900}
    monkeypatch.setattr(live_data, "live_settings", lambda config=None: dict(settings))
    return client, settings


def test_live_post_requires_enabled_and_token(live_client):
    client, settings = live_client
    assert client.post("/api/live", json={"pv_w": 1.0}).status_code == 401
    assert client.post("/api/live", json={"pv_w": 1.0}, headers={"X-API-Key": "wrong"}).status_code == 401
    res = client.post("/api/live", json=[{"pv_w": 1.0}, {"soc": 0.5}], headers={"X-API-Key": "secret"})
    assert res.status_code == 200 and res.get_json()["stored"] == 2
    bad = client.post("/api/live", json={"soc": 5}, headers={"X-API-Key": "secret"})
    assert bad.status_code == 400
    settings["enabled"] = False
    assert client.post("/api/live", json={"pv_w": 1.0}, headers={"X-API-Key": "secret"}).status_code == 403


def test_live_latest_and_history(live_client):
    client, _ = live_client
    client.post("/api/live", json={"pv_w": 7.5, "load_w": 24.0, "soc": 0.6}, headers={"X-API-Key": "secret"})
    latest = client.get("/api/live/latest").get_json()
    assert latest["state"] == "receiving"
    assert latest["latest"]["values"]["pv_w"]["value"] == 7.5
    hist = client.get("/api/live/history?hours=1").get_json()
    assert hist["measured"]["load_w"] == [24.0]


def test_results_include_energy_breakdown(client):
    n = 8
    pd.DataFrame({
        "Date": pd.date_range("2026-01-01 10:00", periods=n, freq="15min", tz="Europe/Budapest").astype(str),
        "Total_power (W)": [10.0] * n, "Aggregated_deviation (W)": [1.0] * n, "Demand_power (W)": [20.0] * n,
        "Grid Price (€/Wh)": [1e-4] * n, "P_grid_opt (W)": [15.0] * n, "P_bat_dis_opt (W)": [5.0] * n,
        "P_bat_ch (W)": [8.0] * n, "P_curtailed (W)": [2.0] * n, "SoC_opt": [0.5] * n, "SoC_opt (%)": [50.0] * n,
        "Grid and degradation cost": [0.001] * n,
    }).to_csv(dashboard_app.RESULTS_CSV, index=False)
    data = client.get("/api/results").get_json()
    e = data["energy"]
    assert e["demand"] == pytest.approx(40.0)  # 20 W x 2 h
    assert e["curtailed"] == pytest.approx(4.0)
    assert e["battery_share_of_demand"] == pytest.approx(0.25)
    assert data["hourly"]["hour"] == ["2026-01-01 10:00:00", "2026-01-01 11:00:00"]
    assert data["hourly"]["grid"] == [15.0, 15.0]


class _FakeRunner:
    def __init__(self, busy=False):
        self.busy, self.started = busy, []

    def start(self, job_id, trigger="manual"):
        if self.busy:
            raise RuntimeError("busy")
        self.started.append((job_id, trigger))


def _auto(monkeypatch, runner, enabled=True, auto=True, interval=15, precondition=None):
    import live_data

    monkeypatch.setattr(live_data, "live_settings", lambda config=None: {
        "enabled": enabled, "auto_mpc": auto, "mpc_interval_min": interval, "api_token": "",
        "stale_after_s": 120, "use_measured_soc": True, "measured_soc_max_age_s": 900})
    monkeypatch.setattr(dashboard_app, "mpc_precondition_error", lambda: precondition)
    return dashboard_app.AutoMPC(runner)


def test_auto_mpc_runs_once_per_interval(monkeypatch):
    runner = _FakeRunner()
    auto = _auto(monkeypatch, runner)
    t0 = 1_800_000_000  # aligned to 15 min
    assert auto.tick(t0 + 10) == "started"
    assert auto.tick(t0 + 300) == "done"           # same interval
    assert auto.tick(t0 + 900 + 5) == "started"    # next interval
    assert runner.started == [("run_mpc", "auto"), ("run_mpc", "auto")]
    assert auto.next_run == t0 + 1800


def test_auto_mpc_off_waits_or_skips(monkeypatch):
    assert _auto(monkeypatch, _FakeRunner(), enabled=False).tick(1_800_000_000) == "off"
    assert _auto(monkeypatch, _FakeRunner(), auto=False).tick(1_800_000_000) == "off"

    busy = _FakeRunner(busy=True)
    auto = _auto(monkeypatch, busy)
    assert auto.tick(1_800_000_000) == "busy"
    busy.busy = False
    assert auto.tick(1_800_000_030) == "started"   # starts when the other job is done

    auto = _auto(monkeypatch, _FakeRunner(), precondition="Train the models first")
    assert auto.tick(1_800_000_000) == "skipped"
    assert "Train the models" in auto.note


def test_executed_plan_uses_the_plan_in_force(tmp_path, monkeypatch):
    plans = tmp_path / "plans"
    plans.mkdir()
    monkeypatch.setattr(dashboard_app, "PLANS_DIR", plans)
    for computed, value in [("2026-10-16T10:00:00", 1.0), ("2026-10-16T10:30:00", 2.0)]:
        start = pd.Timestamp(computed, tz="UTC")
        pd.DataFrame({
            "Date": pd.date_range(start, periods=8, freq="15min").astype(str),
            "Total_power (W)": value, "Demand_power (W)": value, "P_grid_opt (W)": value,
            "P_bat_dis_opt (W)": 0.0, "SoC_opt": 0.5,
        }).to_csv(plans / f"plan_{computed.replace('-', '').replace(':', '')}.csv", index=False)
    out = dashboard_app.executed_plan(pd.Timestamp("2026-10-16T10:00", tz="UTC"), pd.Timestamp("2026-10-16T11:30", tz="UTC"))
    by_time = dict(zip(out["_ts"].dt.strftime("%H:%M"), out["Total_power (W)"]))
    assert by_time["10:15"] == 1.0   # first plan until the second one was computed
    assert by_time["10:30"] == 2.0   # then the newer plan
    assert by_time["11:30"] == 2.0


def test_overview_and_timeline_endpoints(client, tmp_path, monkeypatch):
    import live_data

    monkeypatch.setattr(live_data, "LIVE_DIR", tmp_path / "live")
    monkeypatch.setattr(dashboard_app, "PLANS_DIR", tmp_path / "plans")
    start = pd.Timestamp.now(tz="UTC").floor("15min") - pd.Timedelta(hours=1)
    n = 24
    pd.DataFrame({
        "Date": pd.date_range(start, periods=n, freq="15min").tz_convert("Europe/Budapest").astype(str),
        "Total_power (W)": 6.0, "Aggregated_deviation (W)": 1.0, "Demand_power (W)": 20.0, "Grid Price (€/Wh)": 1e-4,
        "P_grid_opt (W)": 15.0, "P_bat_dis_opt (W)": 5.0, "P_bat_ch (W)": 6.0, "P_curtailed (W)": 0.0,
        "SoC_opt": 0.5, "SoC_opt (%)": 50.0, "Grid and degradation cost": 0.0004,
    }).to_csv(dashboard_app.RESULTS_CSV, index=False)

    ov = client.get("/api/overview").get_json()
    assert ov["decision"]["grid_w"] == 15.0
    assert ov["costs"]["plan"]["grid_only_eur"] == pytest.approx(n * 20.0 * 1e-4 * 0.25)
    assert ov["summary"] and ov["energy"]["demand"] == pytest.approx(n * 20.0 * 0.25)

    tl = client.get("/api/timeline?past_hours=2").get_json()
    assert tl["available"] and len(tl["date"]) == n
    assert tl["plan"]["grid"][0] == 15.0


def test_live_results_endpoints(client, tmp_path, monkeypatch):
    import run_log
    monkeypatch.setattr(run_log, "RUN_LOG", tmp_path / "runs" / "mpc_runs.csv")
    monkeypatch.setattr(run_log, "DECISION_LOG", tmp_path / "runs" / "decisions.csv")
    data = client.get("/api/live-results?period=7d").get_json()
    assert "total" in data and "days" in data and data["log"]["runs"] == 0
    res = client.get("/api/live-results.csv?period=all")
    assert res.status_code == 200 and b"day" in res.data
    assert client.get("/api/run-log.csv").status_code == 404     # nothing logged yet
