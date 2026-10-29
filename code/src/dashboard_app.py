"""Local control dashboard for the uncertainty-aware MPC.

Start it with:  python src/dashboard_app.py
then open http://127.0.0.1:8050 (it opens automatically).

The dashboard is a thin layer over the existing scripts: training and the MPC
run as separate Python processes, so a long run never blocks the page.
"""

import argparse
import collections
import datetime as dt
import json
import math
import os
import subprocess
import sys
import threading
import time
import webbrowser

import pandas as pd
from flask import Flask, abort, jsonify, render_template, request, send_file

import live_data
import overview
import run_log
import update_data
from settings import CONFIG_PATH, DATA_DIR, MODELS_DIR, RESULTS_DIR, ROOT_DIR, load_config

SRC_DIR = ROOT_DIR / "src"
RESULTS_CSV = RESULTS_DIR / "CostFunction_results.csv"
RESULTS_SUMMARY = RESULTS_DIR / "CostFunction_summary.json"
SVR_PLOT = RESULTS_DIR / "svr_test_prediction.png"
PRODUCTION_CSV = DATA_DIR / "production.csv"

GPR_FILES = [
    "gpr_solar_model.joblib", "scaler_X_solar.joblib", "scaler_y_solar.joblib",
    "gpr_wind_model.joblib", "scaler_X_wind.joblib", "scaler_y_wind.joblib",
]
SVR_FILES = ["svr_model_15min.pkl"]

JOBS = {
    "train_svr": {"label": "Train SVR price model", "cmd": ["svr_train_newest.py", "--no-plot"]},
    "train_gpr": {"label": "Train GPR solar & wind models", "cmd": ["scicomp_training.py"]},
    "run_mpc": {"label": "Run MPC optimisation", "cmd": ["MPC_costfunction.py"]},
    "update_data": {"label": "Update training data", "cmd": ["update_data.py"]},
}
PRODUCTION_MODES = ["STOPPED", "FIXED_COUNT", "MAX_PRODUCTION"]
LIVE_MAX_POINTS = 600
CONFIG_CHOICES = {"smoothness": ["inputs", "total"], "gti_variable": ["instant", "mean"],
                  "uncertainty_scaling": ["relative", "absolute"], "mode": ["pareto", "single"],
                  "sigma_model": ["constant", "weather", "weather_floor"],
                  "margin_window": ["horizon", "replan", "steps"], "plant": ["physical", "bms"]}
OPTIONAL_TEXT_KEYS = {"api_token"}

app = Flask(__name__, template_folder=str(SRC_DIR / "templates"), static_folder=str(SRC_DIR / "static"))
app.json.sort_keys = False  # keep the key order of dataConfig.json when it is edited and saved


# ---------------------------------------------------------------- jobs

class JobRunner:
    """Runs one script at a time in a subprocess and keeps its output."""

    def __init__(self):
        self.lock = threading.Lock()
        self.proc = None
        self.job = None
        self.lines = collections.deque(maxlen=2000)
        self.started = None
        self.finished = None
        self.returncode = None
        self.stopped = False
        self.trigger = None

    def start(self, job_id, trigger="manual"):
        with self.lock:
            if self.proc is not None and self.proc.poll() is None:
                raise RuntimeError(f"'{JOBS[self.job]['label']}' is still running")
            spec = JOBS[job_id]
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"  # the scripts print € signs; Windows pipes default to cp1252
            env["MPLBACKEND"] = "Agg"
            cmd = [sys.executable, "-u", str(SRC_DIR / spec["cmd"][0]), *spec["cmd"][1:]]
            self.lines.clear()
            self.lines.append(f"$ python src/{' '.join(spec['cmd'])}" + ("   (automatic run)" if trigger == "auto" else ""))
            self.job, self.started, self.finished, self.returncode, self.stopped = job_id, time.time(), None, None, False
            self.trigger = trigger
            self.proc = subprocess.Popen(
                cmd, cwd=str(ROOT_DIR), env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
            )
            threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()

    def _read(self, proc):
        for line in proc.stdout:
            self.lines.append(line.rstrip("\n"))
        proc.wait()
        with self.lock:
            if proc is self.proc:
                self.returncode = proc.returncode
                self.finished = time.time()

    def stop(self):
        with self.lock:
            if self.proc is not None and self.proc.poll() is None:
                self.stopped = True
                self.proc.terminate()
                self.lines.append("--- stopped by user ---")

    def state(self):
        with self.lock:
            if self.job is None:
                return {"job": None, "status": "idle", "log": []}
            running = self.proc.poll() is None or self.finished is None
            if running:
                status = "running"
            elif self.stopped:
                status = "stopped"
            else:
                status = "success" if self.returncode == 0 else "failed"
            end = time.time() if running else self.finished
            return {
                "job": self.job,
                "trigger": self.trigger,
                "label": JOBS[self.job]["label"],
                "status": status,
                "returncode": self.returncode,
                "elapsed_s": round(end - self.started, 1),
                "finished_at": _iso(self.finished) if self.finished else None,
                "log": list(self.lines),
            }


runner = JobRunner()


def mpc_precondition_error():
    """Why the MPC cannot run right now, or None."""
    missing = _model_group(GPR_FILES)["missing"] + _model_group(SVR_FILES)["missing"]
    if missing:
        return "Train the models first (missing: " + ", ".join(missing) + ")"
    if not os.environ.get("ENTSOE_API_KEY"):
        return "ENTSOE_API_KEY is not set. Add it to the .env file and restart the dashboard."
    return None


class AutoMPC:
    """Runs the MPC automatically every live.mpc_interval_min minutes while live data is enabled.

    Runs are aligned to the interval (e.g. :00, :15, :30, :45) and the first one starts as soon as
    the dashboard is running. If another job is busy, the run waits and starts when it is free
    (within the same interval).
    """

    def __init__(self, job_runner):
        self.runner = job_runner
        self.last_slot = None
        self.last_started = None
        self.next_run = None
        self.note = None

    def settings(self):
        s = live_data.live_settings()
        return s["enabled"] and s.get("auto_mpc", True), max(1.0, float(s.get("mpc_interval_min", 15))) * 60

    def tick(self, now=None):
        now = time.time() if now is None else now
        active, interval = self.settings()
        if not active:
            self.next_run = None
            return "off"
        slot = int(now // interval) * interval
        self.next_run = slot + interval
        if self.last_slot == slot:
            return "done"
        err = mpc_precondition_error()
        if err:
            self.last_slot, self.note = slot, f"skipped: {err}"
            return "skipped"
        try:
            self.runner.start("run_mpc", trigger="auto")
        except RuntimeError:
            self.note = "waiting for the running job to finish"
            return "busy"
        self.last_slot, self.last_started, self.note = slot, now, None
        return "started"

    def state(self):
        active, interval = self.settings()
        return {
            "active": active,
            "interval_min": interval / 60,
            "last_started": _iso(self.last_started) if self.last_started else None,
            "next_run": _iso(self.next_run) if active and self.next_run else None,
            "note": self.note,
        }

    def loop(self, period_s=15):
        while True:
            try:
                self.tick()
            except Exception as e:  # never let the scheduler thread die
                self.note = f"error: {e}"
            time.sleep(period_s)


auto_mpc = AutoMPC(runner)


# ---------------------------------------------------------------- helpers

def _iso(ts):
    return dt.datetime.fromtimestamp(ts).isoformat(timespec="seconds")


def _file_info(path):
    if not path.exists():
        return None
    st = path.stat()
    return {"size_mb": round(st.st_size / 1e6, 2), "modified": _iso(st.st_mtime)}


def _model_group(files):
    infos = {f: _file_info(MODELS_DIR / f) for f in files}
    present = [i for i in infos.values() if i]
    return {
        "ready": len(present) == len(files),
        "missing": [f for f, i in infos.items() if i is None],
        "modified": max((i["modified"] for i in present), default=None),
        "size_mb": round(sum(i["size_mb"] for i in present), 1),
    }


def _clean(values):
    out = []
    for v in values:
        if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
            out.append(None)
        else:
            out.append(v)
    return out


def _validate_config(new, current, path="config"):
    """The edited config must keep the same structure and numeric types."""
    if not isinstance(new, dict):
        raise ValueError(f"{path} must be an object")
    for key, cur in current.items():
        if key not in new:
            raise ValueError(f"{path}.{key} is missing")
        val = new[key]
        if isinstance(cur, dict):
            _validate_config(val, cur, f"{path}.{key}")
        elif isinstance(cur, (int, float)) and not isinstance(cur, bool):
            if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val):
                raise ValueError(f"{path}.{key} must be a number")
        elif isinstance(cur, bool):
            if not isinstance(val, bool):
                raise ValueError(f"{path}.{key} must be true or false")
        elif isinstance(cur, str):
            if not isinstance(val, str):
                raise ValueError(f"{path}.{key} must be a text")
            if key in CONFIG_CHOICES and val not in CONFIG_CHOICES[key]:
                raise ValueError(f"{path}.{key} must be one of {', '.join(CONFIG_CHOICES[key])}")
            if not val.strip() and key not in OPTIONAL_TEXT_KEYS:
                raise ValueError(f"{path}.{key} cannot be empty")
    return new


def _energy_breakdown(df, hours):
    """Energy totals (Wh) over the horizon and per hour of the plan."""
    def col(name):
        return df[name].astype(float).clip(lower=0) if name in df else pd.Series(0.0, index=df.index)

    renewable = col("Total_power (W)")
    charge = col("P_bat_ch (W)") if "P_bat_ch (W)" in df else renewable
    curtailed = col("P_curtailed (W)") if "P_curtailed (W)" in df else (renewable - charge).clip(lower=0)
    parts = {
        "demand": col("Demand_power (W)"),
        "grid": col("P_grid_opt (W)"),
        "battery": col("P_bat_dis_opt (W)"),
        "renewable": renewable,
        "charged": charge,
        "curtailed": curtailed,
    }
    energy = {k: float(v.sum() * hours) for k, v in parts.items()}
    energy["battery_share_of_demand"] = energy["battery"] / energy["demand"] if energy["demand"] > 0 else None

    hourly = {"hour": []}
    if "Date" in df:
        ts = pd.to_datetime(df["Date"].astype(str).str.slice(0, 19))
        key = ts.dt.floor("h")
        grouped = pd.DataFrame({k: v * hours for k, v in parts.items()}).groupby(key.values).sum()
        hourly["hour"] = [t.strftime("%Y-%m-%d %H:%M:00") for t in grouped.index]
        for k in parts:
            hourly[k] = [round(float(v), 3) for v in grouped[k]]
    return energy, hourly


def _live_summary():
    s = live_data.live_settings()
    latest = live_data.latest()
    if not s["enabled"]:
        state = "disabled"
    elif latest["timestamp"] is None:
        state = "waiting"
    elif latest["age_s"] > s["stale_after_s"]:
        state = "stale"
    else:
        state = "receiving"
    return {"enabled": s["enabled"], "state": state, "last": latest["timestamp"], "age_s": latest["age_s"],
            "token_required": bool(s["api_token"]), "auto_mpc": auto_mpc.state()}


# ---------------------------------------------------------------- routes

@app.get("/")
def index():
    return render_template("dashboard.html")


@app.get("/api/status")
def status():
    return jsonify({
        "models": {"svr": _model_group(SVR_FILES), "gpr": _model_group(GPR_FILES)},
        "api_key_set": bool(os.environ.get("ENTSOE_API_KEY")),
        "results": _file_info(RESULTS_CSV),
        "svr_plot": _file_info(SVR_PLOT),
        "job": runner.state(),
        "jobs": {k: v["label"] for k, v in JOBS.items()},
        "data": update_data.data_status(),
        "live": _live_summary(),
    })


@app.get("/api/config")
def get_config():
    return jsonify(load_config())


@app.put("/api/config")
def put_config():
    try:
        cfg = _validate_config(request.get_json(force=True), load_config())
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4)
        f.write("\n")
    return jsonify({"ok": True})


@app.get("/api/production")
def get_production():
    df = pd.read_csv(PRODUCTION_CSV)
    return jsonify({"rows": df.astype({"Target_Output": float}).to_dict(orient="records"), "modes": PRODUCTION_MODES})


@app.put("/api/production")
def put_production():
    rows = (request.get_json(force=True) or {}).get("rows", [])
    try:
        df = pd.DataFrame(rows, columns=["Timestamp", "Mode", "Target_Output"])
        if df.empty:
            raise ValueError("the schedule needs at least one row")
        df["Timestamp"] = pd.to_datetime(df["Timestamp"])
        if not df["Mode"].isin(PRODUCTION_MODES).all():
            raise ValueError(f"Mode must be one of {', '.join(PRODUCTION_MODES)}")
        df["Target_Output"] = pd.to_numeric(df["Target_Output"])
        if (df["Target_Output"] < 0).any():
            raise ValueError("Target_Output cannot be negative")
    except (ValueError, TypeError) as e:
        return jsonify({"error": str(e)}), 400
    df = df.sort_values("Timestamp")
    df["Timestamp"] = df["Timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S")
    df["Target_Output"] = df["Target_Output"].map(lambda v: int(v) if float(v).is_integer() else v)
    df.to_csv(PRODUCTION_CSV, index=False)
    return jsonify({"ok": True, "rows": len(df)})


@app.get("/api/results")
def results():
    if not RESULTS_CSV.exists():
        return jsonify({"available": False})
    df = pd.read_csv(RESULTS_CSV)
    cfg = load_config()
    hours = cfg["general"]["deltaT"] / 3600.0
    col = df.get

    def series(name):
        return _clean(df[name].astype(float).round(6).tolist()) if name in df else None

    price_mwh = df["Grid Price (€/Wh)"] * 1e6 if "Grid Price (€/Wh)" in df else None
    summary = {
        "steps": len(df),
        "total_cost_eur": float(col("Grid and degradation cost", pd.Series(dtype=float)).sum()),
        "grid_energy_kwh": float(col("P_grid_opt (W)", pd.Series(dtype=float)).sum() * hours / 1000),
        "battery_energy_kwh": float(col("P_bat_dis_opt (W)", pd.Series(dtype=float)).sum() * hours / 1000),
        "renewable_energy_kwh": float(col("Total_power (W)", pd.Series(dtype=float)).sum() * hours / 1000),
        "demand_energy_kwh": float(col("Demand_power (W)", pd.Series(dtype=float)).sum() * hours / 1000),
        "soc_min": float(df["SoC_opt"].min()) if "SoC_opt" in df else None,
        "soc_max": float(df["SoC_opt"].max()) if "SoC_opt" in df else None,
        "soc_end": float(df["SoC_opt"].iloc[-1]) if "SoC_opt" in df else None,
    }
    energy, hourly = _energy_breakdown(df, hours)

    solver = None
    if RESULTS_SUMMARY.exists():
        with open(RESULTS_SUMMARY, encoding="utf-8") as f:
            solver = json.load(f)

    return jsonify({
        "available": True,
        "file": _file_info(RESULTS_CSV),
        "solver": solver,
        "summary": summary,
        "energy": energy,
        "hourly": hourly,
        "limits": {
            "soc_min": cfg["SoC"].get("soc_min"),
            "soc_max": cfg["SoC"].get("soc_max"),
            "x_ref": cfg["SoC"].get("x_ref"),
        },
        "series": {
            "date": df["Date"].astype(str).tolist() if "Date" in df else list(range(len(df))),
            "solar": series("Mean_solar_power (W)"),
            "wind": series("Mean_wind_power (W)"),
            "renewable": series("Total_power (W)"),
            "sigma": _clean(overview._sigma(df).round(6).tolist()) if "Aggregated_deviation (W)" in df else None,
            "demand": series("Demand_power (W)"),
            "grid": series("P_grid_opt (W)"),
            "battery": series("P_bat_dis_opt (W)"),
            "charge": series("P_bat_ch (W)"),
            "curtailed": series("P_curtailed (W)"),
            "soc_pct": series("SoC_opt (%)"),
            "soc_min_eff": series("SoC_min_eff (%)"),
            "soc_max_eff": series("SoC_max_eff (%)"),
            "confidence": series("Forecast_confidence"),
            "price_mwh": _clean(price_mwh.round(3).tolist()) if price_mwh is not None else None,
            "step_cost": series("Grid and degradation cost"),
        },
    })


@app.get("/api/results.csv")
def results_csv():
    if not RESULTS_CSV.exists():
        abort(404)
    return send_file(RESULTS_CSV, as_attachment=True, download_name="CostFunction_results.csv")


def _clean(obj):
    """JSON-safe: NaN/inf -> None."""
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


def _results_window():
    """Period of the live results from the query: period=today|7d|30d|all (default all)."""
    cfg, tz, _ = _site()
    now = pd.Timestamp.now(tz="UTC")
    period = request.args.get("period", "all")
    if period == "today":
        start = now.tz_convert(tz).normalize().tz_convert("UTC")
    elif period in ("7d", "30d"):
        start = (now.tz_convert(tz).normalize() - pd.Timedelta(days=int(period[:-1]) - 1)).tz_convert("UTC")
    else:
        start = None
    return cfg, start, now


@app.get("/api/live-results")
def live_results():
    """Results of the live operation (run log + measurements), totals and day by day."""
    cfg, start, end = _results_window()
    res = run_log.results(start, end, cfg)
    runs = run_log.read_runs()
    res["log"] = {"runs": int(len(runs)), "first_run": runs["computed_utc"].min().isoformat() if len(runs) else None,
                  "last_run": runs["computed_utc"].max().isoformat() if len(runs) else None}
    return jsonify(_clean(res))


@app.get("/api/live-results.csv")
def live_results_csv():
    cfg, start, end = _results_window()
    res = run_log.results(start, end, cfg)
    rows = res["days"] + [{**res["total"], "day": "total"}]
    df = pd.DataFrame(rows)
    df = df[["day"] + [c for c in df.columns if c not in ("day", "start", "end")]]
    return app.response_class(df.to_csv(index=False), mimetype="text/csv",
                              headers={"Content-Disposition": "attachment; filename=kairos_live_results.csv"})


@app.get("/api/run-log.csv")
def run_log_csv():
    path = run_log.RUN_LOG if request.args.get("what") != "decisions" else run_log.DECISION_LOG
    if not path.exists():
        abort(404)
    return send_file(path, as_attachment=True, download_name=f"kairos_{path.stem}.csv", max_age=0)


@app.get("/api/svr-plot.png")
def svr_plot():
    if not SVR_PLOT.exists():
        abort(404)
    return send_file(SVR_PLOT, max_age=0)


@app.post("/api/jobs/<job_id>")
def start_job(job_id):
    if job_id not in JOBS:
        abort(404)
    if job_id == "run_mpc":
        err = mpc_precondition_error()
        if err:
            return jsonify({"error": err}), 409
    try:
        runner.start(job_id)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 409
    return jsonify(runner.state())


@app.get("/api/jobs/current")
def current_job():
    return jsonify(runner.state())


@app.post("/api/jobs/stop")
def stop_job():
    runner.stop()
    return jsonify(runner.state())


# ---------------------------------------------------------------- live measurements

@app.post("/api/live")
def post_live():
    s = live_data.live_settings()
    if not s["enabled"]:
        return jsonify({"error": "Live data is disabled. Set live.enabled to true in the configuration."}), 403
    if s["api_token"] and request.headers.get("X-API-Key") != s["api_token"]:
        return jsonify({"error": "Missing or wrong X-API-Key header"}), 401
    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"error": "The body must be JSON: one measurement object or a list of them"}), 400
    records = payload if isinstance(payload, list) else [payload]
    try:
        n = live_data.append_measurements(records)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, "stored": n})


PLANS_DIR = RESULTS_DIR / "plans"


def _plan_files():
    """Saved plans as (computed time UTC, path), oldest first. Falls back to the latest result."""
    plans = []
    if PLANS_DIR.exists():
        for p in PLANS_DIR.glob("plan_*.csv"):
            try:
                plans.append((pd.Timestamp(p.stem[5:], tz="UTC"), p))
            except ValueError:
                continue
    if not plans and RESULTS_CSV.exists():
        plans.append((pd.Timestamp(RESULTS_CSV.stat().st_mtime, unit="s", tz="UTC"), RESULTS_CSV))
    return sorted(plans)


def executed_plan(start, end):
    """The plan that was in force at each time: rows of each plan until the next plan was computed.

    With the MPC re-running every interval (receding horizon), this is what was actually decided for
    the past, and the newest plan for the future.
    """
    plans = [(t, p) for t, p in _plan_files() if t <= end]
    parts = []
    for i, (t, path) in enumerate(plans):
        t_next = plans[i + 1][0] if i + 1 < len(plans) else None
        if t_next is not None and t_next < start - pd.Timedelta(hours=1):
            continue
        df = pd.read_csv(path)
        if "Date" not in df:
            continue
        ts = pd.to_datetime(df["Date"], utc=True)
        keep = ts >= start - pd.Timedelta(minutes=15)
        if t_next is not None:
            keep &= ts < t_next
        part = df[keep.values].reset_index(drop=True)
        part["_ts"] = ts[keep].reset_index(drop=True)
        part["_plan"] = t
        parts.append(part)
    if not parts:
        return None
    out = pd.concat(parts).drop_duplicates("_ts", keep="last").sort_values("_ts")
    return out[out["_ts"] <= end]


def latest_plan():
    if not RESULTS_CSV.exists():
        return None
    df = pd.read_csv(RESULTS_CSV)
    if "Date" not in df:
        return None
    df["_ts"] = pd.to_datetime(df["Date"], utc=True)
    df["_plan"] = pd.Timestamp(RESULTS_CSV.stat().st_mtime, unit="s", tz="UTC")
    return df


def _site():
    cfg = load_config()
    return cfg, cfg.get("general", {}).get("timezone", "Europe/Budapest"), float(cfg["general"].get("deltaT", 900))


@app.get("/api/overview")
def get_overview():
    cfg, tz, step_s = _site()
    c_deg = float(cfg["degradation"]["c_deg"])
    now = pd.Timestamp.now(tz="UTC")
    midnight = now.tz_convert(tz).normalize().tz_convert("UTC")
    latest = latest_plan()
    executed = executed_plan(midnight - pd.Timedelta(hours=1), now + pd.Timedelta(hours=48))
    hist = live_data.read_history((now - midnight).total_seconds() / 3600 + 0.5)
    measured = overview.measured_steps(hist, step_s)

    energy, hourly = _energy_breakdown(latest, step_s / 3600) if latest is not None else (None, None)
    decision = overview.decision_at(executed if executed is not None else latest, now, step_s, c_deg, tz)
    live_s = live_data.live_settings(cfg)
    return jsonify({
        "now": now.tz_convert(tz).strftime("%Y-%m-%d %H:%M:%S"),
        "decision": decision,
        "measured_now": overview.compare_now(decision, live_data.latest() if live_s["enabled"] else None, live_s, c_deg),
        "costs": {
            "plan": overview.plan_costs(latest, step_s, tz),
            "today": overview.day_costs(executed, measured, midnight, now, step_s, c_deg, tz),
        },
        "summary": overview.summary_sentences(latest, step_s, tz),
        "energy": energy,
        "hourly": hourly,
        "mpc": _mpc_run_info(),
        "live": _live_summary(),
    })


@app.get("/api/timeline")
def get_timeline():
    try:
        past = min(max(float(request.args.get("past_hours", 6)), 0.0), 72.0)
    except ValueError:
        abort(400)
    cfg, tz, step_s = _site()
    now = pd.Timestamp.now(tz="UTC")
    start = now - pd.Timedelta(hours=past)
    executed = executed_plan(start, now + pd.Timedelta(hours=48))
    if executed is not None:
        executed = executed[pd.to_datetime(executed["_ts"], utc=True) >= start - pd.Timedelta(seconds=step_s)]
    measured = overview.measured_steps(live_data.read_history(past + 0.5), step_s)
    data = overview.timeline(executed, measured, now, tz, step_s)
    if data:
        data["limits"] = {"soc_min": cfg["SoC"].get("soc_min"), "soc_max": cfg["SoC"].get("soc_max"), "x_ref": cfg["SoC"].get("x_ref")}
    return jsonify({"available": data is not None, **(data or {})})


def _mpc_run_info():
    """When the current plan was computed, and from which initial SoC."""
    if not RESULTS_CSV.exists():
        return None
    info = {"computed": _iso(RESULTS_CSV.stat().st_mtime)}
    if RESULTS_SUMMARY.exists():
        with open(RESULTS_SUMMARY, encoding="utf-8") as f:
            summary = json.load(f)
        info.update({k: summary.get(k) for k in ("success", "message", "initial_soc", "initial_soc_source")})
    return info


@app.get("/api/live/latest")
def get_live_latest():
    return jsonify({**_live_summary(), "latest": live_data.latest(), "fields": live_data.MEASUREMENT_FIELDS,
                    "mpc": _mpc_run_info()})


@app.get("/api/live/history")
def get_live_history():
    try:
        hours = min(max(float(request.args.get("hours", 6)), 0.1), 24 * 7)
    except ValueError:
        abort(400)
    df = live_data.read_history(hours)
    tz = load_config().get("general", {}).get("timezone", "Europe/Budapest")
    raw_count = len(df)
    if len(df) > LIVE_MAX_POINTS:
        # average into at most LIVE_MAX_POINTS buckets so long periods stay light for the browser
        bucket = pd.Timedelta(hours=hours) / LIVE_MAX_POINTS
        df = df.set_index("timestamp").resample(bucket).mean().dropna(how="all").reset_index()
    out = {"date": [t.tz_convert(tz).strftime("%Y-%m-%d %H:%M:%S") for t in df["timestamp"]]}
    for name in live_data.MEASUREMENT_FIELDS:
        out[name] = _clean(df[name].astype(float).tolist()) if name in df else []

    # The plan for the shown period and the next few hours: the past part is compared with the
    # measurements, the future part shows what the MPC will do next
    now = pd.Timestamp.now(tz="UTC")
    ahead = pd.Timedelta(hours=min(hours, 6) / 2)
    plan = None
    start = df["timestamp"].iloc[0] if len(df) else now - pd.Timedelta(hours=hours)
    sel = executed_plan(start, now + ahead)
    if sel is not None and len(sel):
        r_ts = pd.to_datetime(sel["_ts"], utc=True)
        charge = sel["P_bat_ch (W)"] if "P_bat_ch (W)" in sel else 0.0
        plan = {
            "date": [t.tz_convert(tz).strftime("%Y-%m-%d %H:%M:%S") for t in r_ts],
            "renewable": _clean(sel["Total_power (W)"].astype(float).tolist()),
            "demand": _clean(sel["Demand_power (W)"].astype(float).tolist()),
            "grid": _clean(sel["P_grid_opt (W)"].astype(float).tolist()),
            "battery": _clean((sel["P_bat_dis_opt (W)"] - charge).astype(float).tolist()),
            "soc": _clean(sel["SoC_opt"].astype(float).tolist()),
        }
    return jsonify({"hours": hours, "measured": out, "plan": plan, "raw_count": raw_count,
                    "now": now.tz_convert(tz).strftime("%Y-%m-%d %H:%M:%S")})


def main():
    parser = argparse.ArgumentParser(description="Uncertainty-aware MPC dashboard")
    parser.add_argument("--host", default="127.0.0.1",
                        help="127.0.0.1 = this computer only; 0.0.0.0 = reachable from the network (e.g. for live data)")
    parser.add_argument("--port", type=int, default=8050)
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser automatically")
    args = parser.parse_args()

    url = f"http://{args.host}:{args.port}"
    print(f"Dashboard running at {url}  (Ctrl+C to stop)")
    if args.host not in ("127.0.0.1", "localhost") and not live_data.live_settings()["api_token"]:
        print("WARNING: the dashboard is reachable from the network and live.api_token is empty - "
              "anyone on the network can send measurements. Set a token in the configuration.")
    if not args.no_browser:
        browse = url if args.host not in ("0.0.0.0", "::") else f"http://127.0.0.1:{args.port}"
        threading.Timer(1.0, webbrowser.open, args=(browse,)).start()
    threading.Thread(target=auto_mpc.loop, daemon=True).start()
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
