"""Closed-loop replay: how the controllers would have performed on past data.

The replay walks through a past period in MPC steps. At every re-planning time the controller
gets the information it would have had then (renewable forecast with GPR mean and std, grid
prices, production schedule), the plan's first steps are applied to a simulated cell driven by
the *real* weather, and the resulting battery state is carried to the next re-planning.

Data (all already used for training):
  * data/Past_Forecast_Values.csv / Past_Real_Values.csv   hourly weather forecast and actuals
  * data/ENTSOE_HU_Dataset_2025.csv                         day-ahead prices (UTC)
  * data/production_weekly.csv                              weekly production schedule (local time)
The "real" PV and wind power is the physical model applied to the real weather, which is the same
target the GPR models were trained on.

Periods: the replay uses the held-out part of the weather data (the last 20 % that the GPR models
did not see in training, or from replay.heldout_start). It is split into four separate periods
(named_periods): calibration, tuning, development and a final test that is run once at the end.

    python src/replay.py                                   # all controllers on the development period
    python src/replay.py --period final_test --final       # the final test, once, at the very end
    python src/replay.py --controllers det,ua --days 7     # a quick run
    python src/replay.py --tuned                           # use the weights found by tune_mpc.py

Results: results/replay/summary_<period>.csv and one CSV per controller with every step.
"""

import argparse
import copy
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from settings import DATA_DIR, MODELS_DIR, RESULTS_DIR, load_config

REPLAY_DIR = RESULTS_DIR / "replay"
TUNING_DIR = RESULTS_DIR / "tuning"
DATA_VERSION = 2  # bump when build_data adds or changes columns
WEEKDAYS = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}

# Controllers. "mpc" controllers differ only in the forecast they get:
#   gpr      GPR mean and std (uncertainty-aware MPC, the full method)
#   gpr_mean GPR mean, std set to 0 (deterministic MPC with the same forecast)
#   base     physical model on the weather forecast, no GPR (deterministic)
#   oracle   the real renewable power (perfect forecast, an upper bound)
# det_bo is the deterministic MPC whose SoC limits are tightened by a constant, tuned back-off
# (mpc.soc_backoff): it shows whether knowing the time-varying uncertainty beats simply being cautious.
CONTROLLERS = {
    "grid_only": {"kind": "grid_only", "label": "Grid only (no battery)"},
    "rule": {"kind": "rule", "label": "Rule-based (store all, discharge to the load)"},
    "det_base": {"kind": "mpc", "forecast": "base", "label": "Deterministic MPC, physical forecast"},
    "det": {"kind": "mpc", "forecast": "gpr_mean", "label": "Deterministic MPC, GPR mean"},
    "det_bo": {"kind": "mpc", "forecast": "gpr_mean",
               "label": "Deterministic MPC, GPR mean, constant SoC back-off (mpc.soc_backoff)"},
    "ua": {"kind": "mpc", "forecast": "gpr", "label": "Uncertainty-aware MPC"},
    "oracle": {"kind": "mpc", "forecast": "oracle", "label": "MPC with perfect forecast"},
}


def replay_settings(cfg):
    r = cfg.get("replay", {})
    return {
        "schedule_csv": r.get("schedule_csv", "production_weekly.csv"),
        "replan_steps": int(r.get("replan_steps", 4)),
        "validation_days": float(r.get("validation_days", 21)),
        "test_days": float(r.get("test_days", 0)),
        "heldout_start": str(r.get("heldout_start", "") or ""),
        "calibration_days": float(r.get("calibration_days", 0) or 0),
        "tuning_days": float(r.get("tuning_days", 0) or 0),
        "development_days": float(r.get("development_days", 0) or 0),
        "workers": int(r.get("workers", 0)) or (os.cpu_count() or 1),
        "violation_penalty_eur_per_pp_h": float(r.get("violation_penalty_eur_per_pp_h", 0.05)),
        # looser than the live optimizer.ftol: same closed-loop result, about half the solve time
        "solver_ftol": float(r.get("solver_ftol", 1e-7)),
        # simulated cell: "physical" keeps only the physical SoC range 0..1 (leaving the planned band
        # [soc_min, soc_max] is measured as a violation); "bms" adds a battery management system that
        # stops discharging at soc_min and charging at soc_max - the grid then covers the shortfall
        "plant": str(r.get("plant", "physical")),
        # measured load instead of the weekly schedule: data/<demand_profile_csv> (timestamp_utc, load_pu) scaled
        # to demand_mean_w (W); "" = the weekly production schedule
        "demand_profile_csv": str(r.get("demand_profile_csv", "") or ""),
        "demand_mean_w": float(r.get("demand_mean_w", 0.0) or 0.0),
    }


# ---------------------------------------------------------------- data

def _weather():
    fc = pd.read_csv(DATA_DIR / "Past_Forecast_Values.csv")
    rl = pd.read_csv(DATA_DIR / "Past_Real_Values.csv")
    m = pd.merge(fc, rl, on="date", suffixes=("_forecast", "_real"))
    m["date"] = pd.to_datetime(m["date"], utc=True)
    return m.sort_values("date").reset_index(drop=True)


def heldout_start(cfg, weather=None):
    """First local midnight after the GPR training data (same split as scicomp_training.py).

    replay.heldout_start (a local date) pins it: the 80/20 split would move when new data is added,
    although the trained models stay the same.
    """
    tz = cfg["general"].get("timezone", "Europe/Budapest")
    pinned = replay_settings(cfg)["heldout_start"]
    if pinned:
        return pd.Timestamp(pinned).tz_localize(tz).tz_convert("UTC")
    m = _weather() if weather is None else weather
    months = cfg.get("gpr", {}).get("train_months", 0)
    if months:
        m = m[m["date"] >= m["date"].max() - pd.DateOffset(months=int(months))].reset_index(drop=True)
    n = len(m)
    wind_start = m["date"].iloc[n - int(np.ceil(0.2 * n))]
    day = m[m["global_tilted_irradiance_forecast"] > 0]
    nd = len(day)
    solar_start = day["date"].iloc[nd - int(np.ceil(0.2 * nd))]
    return max(wind_start, solar_start).tz_convert(tz).ceil("D").tz_convert("UTC")


def weekly_demand(index, cfg, path=None):
    """Demand (W) from a weekly schedule: Weekday (Mon..Sun), Time (HH:MM, local), Mode, Target_Output.
    Each row holds until the next row of the week; the week wraps around."""
    from referencemodel_functions import mode_power

    path = path or DATA_DIR / replay_settings(cfg)["schedule_csv"]
    sch = pd.read_csv(path)
    hm = sch["Time"].str.split(":", expand=True).astype(int)
    sch["minute"] = sch["Weekday"].map(WEEKDAYS) * 1440 + hm[0] * 60 + hm[1]
    sch = sch.sort_values("minute").reset_index(drop=True)
    step_s = float(cfg["general"]["deltaT"])
    power = np.array([mode_power(m, t, step_s, cfg) for m, t in zip(sch["Mode"], sch["Target_Output"])])
    loc = index.tz_convert(cfg["general"].get("timezone", "Europe/Budapest"))
    minute = loc.dayofweek * 1440 + loc.hour * 60 + loc.minute
    pos = np.searchsorted(sch["minute"].to_numpy(), minute, side="right") - 1  # -1 wraps to the last row
    return power[pos]


def _to_steps(df, cols, step_s):
    d = df.set_index("date")[cols].astype(float)
    idx = pd.date_range(d.index[0], d.index[-1], freq=pd.Timedelta(seconds=step_s))
    return d.reindex(d.index.union(idx)).interpolate(method="time").reindex(idx)


def _cache_key(cfg):
    files = [DATA_DIR / f for f in ("Past_Forecast_Values.csv", "Past_Real_Values.csv", "ENTSOE_HU_Dataset_2025.csv",
                                    replay_settings(cfg)["schedule_csv"])]
    if replay_settings(cfg)["demand_profile_csv"]:
        files.append(DATA_DIR / replay_settings(cfg)["demand_profile_csv"])
    files += sorted(MODELS_DIR.glob("*.joblib"))
    parts = {str(f.name): f.stat().st_mtime for f in files if f.exists()}
    parts["cfg"] = {k: cfg.get(k) for k in ("pv", "wind", "production", "general", "gpr")}
    parts["heldout_start"] = replay_settings(cfg)["heldout_start"]
    if replay_settings(cfg)["demand_profile_csv"]:
        parts["demand"] = [replay_settings(cfg)["demand_profile_csv"], replay_settings(cfg)["demand_mean_w"]]
    parts["version"] = DATA_VERSION
    return json.dumps(parts, sort_keys=True, default=str)


def build_data(cfg, refresh=False):
    """15-minute replay data for the held-out period (cached in results/replay/data.pkl)."""
    from features import DIFFICULTY_FEATURES, difficulty_features
    from referencemodel_functions import (
        get_solar_power, get_wind_power_vectorized, predict_solarPower, predict_windPower,
    )

    REPLAY_DIR.mkdir(parents=True, exist_ok=True)
    cache = REPLAY_DIR / "data.pkl"
    key = _cache_key(cfg)
    if cache.exists() and not refresh:
        stored = pd.read_pickle(cache)
        if stored.attrs.get("key") == key:
            return stored

    step_s = float(cfg["general"]["deltaT"])
    tz = cfg["general"].get("timezone", "Europe/Budapest")
    w = _weather()
    start = heldout_start(cfg, w)
    cols = ["wind_speed_10m", "global_tilted_irradiance", "wind_speed_100m", "wind_gusts_10m",
            "wind_direction_10m", "temperature_2m"]
    # 4 h of history before the start for the rolling features
    w = w[w["date"] >= start - pd.Timedelta(hours=4)]
    fc = _to_steps(w.rename(columns=lambda c: c[:-9] if c.endswith("_forecast") else c), cols, step_s)
    rl = _to_steps(w.rename(columns=lambda c: c[:-5] if c.endswith("_real") else c), cols, step_s)

    f_df = fc.reset_index().rename(columns={"index": "date"})
    f_df["date"] = f_df["date"].dt.tz_convert(tz)
    wind_mu, wind_sd = predict_windPower(f_df)
    solar_mu, solar_sd = predict_solarPower(f_df)

    out = pd.DataFrame(index=fc.index)
    out["solar_base"] = get_solar_power(fc["global_tilted_irradiance"].to_numpy(), cfg)
    out["wind_base"] = get_wind_power_vectorized(fc["wind_speed_10m"].to_numpy(), cfg)
    out["solar_mu"], out["solar_sd"] = solar_mu, solar_sd
    out["wind_mu"], out["wind_sd"] = wind_mu, wind_sd
    feats = difficulty_features(f_df, f_df["date"])
    for col in DIFFICULTY_FEATURES:
        out[col] = feats[col].to_numpy()
    out["solar_real"] = get_solar_power(rl["global_tilted_irradiance"].to_numpy(), cfg)
    out["wind_real"] = get_wind_power_vectorized(rl["wind_speed_10m"].to_numpy(), cfg)

    prices = pd.read_csv(DATA_DIR / "ENTSOE_HU_Dataset_2025.csv")
    p_idx = pd.to_datetime(prices["Timestamp"])
    p_idx = p_idx.dt.tz_localize("UTC") if p_idx.dt.tz is None else p_idx.dt.tz_convert("UTC")
    price = pd.Series(prices["price"].to_numpy(dtype=float), index=p_idx).sort_index()
    price = price[~price.index.duplicated()]
    out["price_eur_mwh"] = price.reindex(price.index.union(out.index)).interpolate(method="time", limit=8).reindex(out.index)

    out = out[out.index >= start]
    out = out[out["price_eur_mwh"].notna()]
    s = replay_settings(cfg)
    if s["demand_profile_csv"]:
        # measured (anonymised) load profile: load_pu x demand_mean_w; steps outside the profile are dropped
        prof = pd.read_csv(DATA_DIR / s["demand_profile_csv"], index_col=0, parse_dates=True)["load_pu"]
        prof.index = pd.to_datetime(prof.index, utc=True)
        out["demand_w"] = prof.reindex(out.index).to_numpy() * s["demand_mean_w"]
        out = out[out["demand_w"].notna()]
    else:
        out["demand_w"] = weekly_demand(out.index, cfg)
    out.attrs["key"] = key
    out.to_pickle(cache)
    return out


PERIOD_NAMES = ("calibration", "tuning", "development", "final_test")


def named_periods(data, cfg):
    """{name: (start, end)} in UTC for the four separate periods of the held-out data.

    calibration  sigma scale and error autocorrelation (calibrate_uncertainty.py)
    tuning       Bayesian tuning of every controller with the same budget (tune_mpc.py)
    development  ablations, the decision-rule map and every design decision
    final_test   run once at the very end; replay.py only runs it with --final
    With replay.calibration_days = 0 the older split is used: calibration = tuning = the validation
    days, development = the rest, no final test.
    """
    s = replay_settings(cfg)
    step = pd.Timedelta(seconds=float(cfg["general"]["deltaT"]))
    last = data.index[-1] - int(cfg["general"]["horizon"]) * step  # every plan needs a full horizon
    d0 = data.index[0]
    if s["calibration_days"] <= 0:
        v1 = d0 + pd.Timedelta(days=s["validation_days"])
        t1 = min(last, v1 + pd.Timedelta(days=s["test_days"])) if s["test_days"] > 0 else last
        return {"calibration": (d0, v1), "tuning": (d0, v1), "development": (v1, t1), "final_test": None}
    out, a = {}, d0
    for name in PERIOD_NAMES[:3]:
        b = min(a + pd.Timedelta(days=s[f"{name}_days"]), last)
        out[name] = (a, b)
        a = b
    out["final_test"] = (a, last) if last - a >= pd.Timedelta(days=1) else None
    return out


def periods(data, cfg):
    """(tuning start, tuning end, development start, development end) as UTC timestamps.

    Kept for scripts written for the older two-period split (validation / test).
    """
    p = named_periods(data, cfg)
    return (*p["tuning"], *p["development"])


# ---------------------------------------------------------------- simulation

def with_overrides(cfg, overrides):
    cfg = copy.deepcopy(cfg)
    for section, values in (overrides or {}).items():
        cfg.setdefault(section, {}).update(values)
    return cfg


def horizon_frame(data, i, n, forecast):
    sl = data.iloc[i:i + n]
    if forecast == "oracle":
        mu, sd = sl["solar_real"] + sl["wind_real"], 0.0
    elif forecast == "base":
        mu, sd = sl["solar_base"] + sl["wind_base"], 0.0
    elif forecast == "gpr_mean":
        mu, sd = sl["solar_mu"] + sl["wind_mu"], 0.0
    else:
        mu = sl["solar_mu"] + sl["wind_mu"]
        sd = np.sqrt(sl["solar_sd"] ** 2 + sl["wind_sd"] ** 2)
    frame = pd.DataFrame({
        "Date": sl.index,
        "Total_power (W)": np.asarray(mu, dtype=float),
        "Aggregated_deviation (W)": np.broadcast_to(np.asarray(sd, dtype=float), len(sl)).copy(),
        "Demand_power (W)": sl["demand_w"].to_numpy(dtype=float),
        "Grid Price (€/Wh)": sl["price_eur_mwh"].to_numpy(dtype=float) / 1e6,
    })
    from features import DIFFICULTY_FEATURES
    for col in DIFFICULTY_FEATURES:
        if col in sl:
            frame[col] = sl[col].to_numpy(dtype=float)
    return frame


def simulate(data, cfg, controller, start, end, soc0=None, replan_steps=None):
    """Run one controller from start to end (UTC). Returns (per-step DataFrame, solver stats)."""
    from MPC_costfunction import _mpc_params, optimize

    spec = CONTROLLERS[controller] if isinstance(controller, str) else controller
    if replay_settings(cfg)["solver_ftol"] > 0:
        cfg = with_overrides(cfg, {"optimizer": {"ftol": replay_settings(cfg)["solver_ftol"]}})
    p = _mpc_params(cfg)
    k_ch = p["eta"] * p["dt_h"] / p["e_cap"]
    k_dis = p["dt_h"] / (p["eta"] * p["e_cap"])
    horizon = int(cfg["general"]["horizon"])
    replan = replan_steps or replay_settings(cfg)["replan_steps"]
    # the MPC sees the replay's re-planning interval (used by mpc.margin_window = "replan")
    cfg = with_overrides(cfg, {"mpc": {"replan_steps": int(replan)}})
    i0, i1 = data.index.searchsorted(start), data.index.searchsorted(end)
    soc = cfg["SoC"].get("initial_soc", 0.5) if soc0 is None else soc0
    bms = replay_settings(cfg)["plant"] == "bms"
    arch = p["architecture"]

    ren = (data["solar_real"] + data["wind_real"]).to_numpy()
    dem = data["demand_w"].to_numpy()
    price = data["price_eur_mwh"].to_numpy() / 1e6
    rows, stats = [], []
    plan_dis = plan_ch = plan_cg = plan_mu = None
    k = 0
    for i in range(i0, i1):
        R, D = max(ren[i], 0.0), dem[i]
        dev = 0.0  # renewable power minus its forecast, taken up by the battery (self_consumption)
        if spec["kind"] == "grid_only":
            dis, ch_target = 0.0, 0.0
        elif spec["kind"] == "rule" and arch != "renewable_buffer":
            # self-consumption rule: charge the actual surplus, discharge into the actual deficit
            ch_target = min(max(R - D, 0.0), p["p_ch_max"], max(p["soc_max"] - soc, 0.0) / k_ch)
            dis = min(max(D - R, 0.0), p["p_dis_max"], max(soc - p["soc_min"], 0.0) / k_dis)
        elif spec["kind"] == "rule":
            ch_target = min(R, p["p_ch_max"], max(p["soc_max"] - soc, 0.0) / k_ch)
            dis = min(D, p["p_dis_max"], max(soc - p["soc_min"], 0.0) / k_dis)
        else:
            if plan_dis is None or k >= replan or k >= len(plan_dis):
                h = horizon_frame(data, i, min(horizon, len(data) - i), spec["forecast"])
                init = None
                if plan_dis is not None and k < len(plan_dis):
                    init = {"dis": np.r_[plan_dis[k:], np.repeat(plan_dis[-1], k)],
                            "charge": np.r_[plan_ch[k:], np.repeat(plan_ch[-1], k)]}
                    if plan_cg is not None:
                        init["charge_grid"] = np.r_[plan_cg[k:], np.repeat(plan_cg[-1], k)]
                t0 = time.perf_counter()
                res, _, plan = optimize(h, cfg, x_0=soc, init=init)
                stats.append({"t": data.index[i], "seconds": time.perf_counter() - t0,
                              "success": bool(res.success), "iterations": int(res.nit)})
                plan_dis = plan["P_bat_dis_opt (W)"].to_numpy(dtype=float)
                plan_ch = plan["P_bat_ch (W)"].to_numpy(dtype=float)
                if arch != "renewable_buffer":
                    plan_cg = plan["P_bat_ch_grid (W)"].to_numpy(dtype=float)
                    plan_mu = np.clip(plan["Total_power (W)"].to_numpy(dtype=float), 0.0, None)
                k = 0
            dis = plan_dis[k]
            if arch == "renewable_buffer":
                ch_target = min(R, plan_ch[k]) if p["charge_is_decision"] else min(R, p["p_ch_max"])
            else:
                ch_target = plan_ch[k]
                if arch == "self_consumption":
                    dev = R - plan_mu[k]
            k += 1
        # physical SoC range 0..1, or the BMS band [soc_min, soc_max]
        lo, hi = (p["soc_min"], p["soc_max"]) if bms else (0.0, 1.0)
        if arch == "renewable_buffer":
            # the cell: what the controller asks for, within the power limits and the available power
            ch_req = max(0.0, min(ch_target, R, p["p_ch_max"]))
            dis_req = max(0.0, min(dis, D, p["p_dis_max"]))
            ch = max(0.0, min(ch_req, (hi - soc) / k_ch))
            dis = max(0.0, min(dis_req, (soc - lo) / k_dis))
            soc = soc + k_ch * ch - k_dis * dis
            grid = D - dis
            curtailed = R - ch
        else:
            # battery power = plan (+ the forecast deviation in self_consumption), within the power limits;
            # the battery never discharges more than the demand; the grid or curtailment balances the rest
            p_bat = ch_target - dis + dev
            ch_req = max(0.0, min(p_bat, p["p_ch_max"]))
            dis_req = max(0.0, min(-p_bat, D, p["p_dis_max"]))
            ch = max(0.0, min(ch_req, (hi - soc) / k_ch))
            dis = max(0.0, min(dis_req, (soc - lo) / k_dis))
            soc = soc + k_ch * ch - k_dis * dis
            net = D - R - dis + ch
            grid, curtailed = max(net, 0.0), max(-net, 0.0)
        rows.append((data.index[i], soc, D, grid, dis, ch, R, curtailed, price[i],
                     grid * price[i] * p["dt_h"], p["c_deg"] * (ch + dis) * p["dt_h"],
                     dis_req - dis, ch_req - ch))
    df = pd.DataFrame(rows, columns=["t", "soc", "demand_w", "grid_w", "discharge_w", "charge_w", "renewable_w",
                                     "curtailed_w", "price_eur_wh", "grid_eur", "wear_eur",
                                     "cut_discharge_w", "cut_charge_w"]).set_index("t")
    df.attrs["soc0"] = cfg["SoC"].get("initial_soc", 0.5) if soc0 is None else soc0
    df.attrs["plant"] = "bms" if bms else "physical"
    df.attrs["uncertainty_aware"] = spec.get("forecast") == "gpr"
    return df, stats


def metrics(df, stats, cfg):
    """Summary of one run (all costs are financial; the penalty only enters "objective").

    objective = cost - value of the energy left in the battery
                + replay.violation_penalty_eur_per_pp_h x violation depth (SoC %-points) x hours
    """
    from MPC_costfunction import _mpc_params

    p = _mpc_params(cfg)
    dt = p["dt_h"]
    soc0 = df.attrs.get("soc0", cfg["SoC"].get("initial_soc", 0.5))
    # tolerance 0.01 %-point: plans are stored with 0.01 W resolution, which can end a hair above a limit
    tol = 1e-4
    below = (df["soc"] < p["soc_min"] - tol).sum() * dt
    above = (df["soc"] > p["soc_max"] + tol).sum() * dt
    # depth x duration of the violations in SoC percentage points x hours
    excess = (p["soc_min"] - df["soc"]).clip(lower=0) + (df["soc"] - p["soc_max"]).clip(lower=0)
    violation_pp_h = float(100 * excess.sum() * dt)
    total = df["grid_eur"].sum() + df["wear_eur"].sum()
    stored_value = (df["soc"].iloc[-1] - soc0) * p["e_cap"] * df["price_eur_wh"].mean() * p["eta"]
    penalty = replay_settings(cfg)["violation_penalty_eur_per_pp_h"] * violation_pp_h
    out = {
        "days": len(df) * dt / 24,
        "total_eur": total, "grid_eur": df["grid_eur"].sum(), "wear_eur": df["wear_eur"].sum(),
        "stored_value_eur": stored_value, "objective": total - stored_value + penalty,
        "grid_kwh": df["grid_w"].sum() * dt / 1000, "demand_kwh": df["demand_w"].sum() * dt / 1000,
        "renewable_kwh": df["renewable_w"].sum() * dt / 1000, "curtailed_kwh": df["curtailed_w"].sum() * dt / 1000,
        "throughput_kwh": (df["charge_w"] + df["discharge_w"]).sum() * dt / 1000,
        "h_below_soc_min": below, "h_above_soc_max": above, "violation_pp_h": violation_pp_h,
        "max_violation_pp": float(100 * excess.max()),
        "min_soc": df["soc"].min(), "max_soc": df["soc"].max(), "end_soc": df["soc"].iloc[-1],
    }
    # further KPIs: net cost, renewable use, battery cycling, grid peak
    out["net_cost_eur"] = total - stored_value
    out["curtailed_pct"] = 100 * out["curtailed_kwh"] / out["renewable_kwh"] if out["renewable_kwh"] > 0 else 0.0
    out["renewable_use_pct"] = 100 - out["curtailed_pct"]
    out["equivalent_full_cycles"] = out["throughput_kwh"] * 1000 / (2 * p["e_cap"])
    out["cycles_per_day"] = out["equivalent_full_cycles"] / out["days"] if out["days"] > 0 else 0.0
    out["peak_grid_w"] = float(df["grid_w"].max())
    # three levels of a constraint violation (they mean different things):
    #  1. leaving the planned band [soc_min, soc_max]: violation_pp_h, h_below_soc_min, max_violation_pp,
    #     and how often (share of steps) compared with what the chance constraint promises
    #  2. the cell's own protection acting: steps where the BMS (plant "bms") or the physical
    #     limit (plant "physical") cut the discharge or the charge the controller asked for
    #  3. the consequence: battery energy that was planned but not delivered (covered by the grid)
    #     and the extra grid cost of it
    n = max(len(df), 1)
    out["steps_below_soc_min_pct"] = 100 * float((df["soc"] < p["soc_min"] - tol).sum()) / n
    z = p["soc_chance_z"]
    if z > 0 and df.attrs.get("uncertainty_aware", False):
        from math import erf, sqrt
        out["chance_target_below_pct"] = 100 * 0.5 * (1 - erf(z / sqrt(2)))  # one-sided normal tail
    if "cut_discharge_w" in df:
        cut_d, cut_c = df["cut_discharge_w"].clip(lower=0), df["cut_charge_w"].clip(lower=0)
        out["plant"] = df.attrs.get("plant", "physical")
        out["protection_steps"] = int(((cut_d > 1e-3) | (cut_c > 1e-3)).sum())
        out["protection_h"] = out["protection_steps"] * dt
        out["undelivered_discharge_kwh"] = float(cut_d.sum() * dt / 1000)
        out["undelivered_charge_kwh"] = float(cut_c.sum() * dt / 1000)
        out["extra_grid_eur"] = float((cut_d * df["price_eur_wh"]).sum() * dt)
    if stats:
        s = np.array([x["seconds"] for x in stats])
        out.update(solves=len(stats), solve_median_s=float(np.median(s)), solve_p95_s=float(np.percentile(s, 95)),
                   solver_success_pct=100 * float(np.mean([x["success"] for x in stats])))
    return out


def _run_one(args):
    data, cfg, controller, start, end = args
    df, stats = simulate(data, cfg, controller, start, end)
    return controller if isinstance(controller, str) else controller.get("name"), df, stats


def run_many(jobs, workers):
    """jobs: list of (data, cfg, controller, start, end); runs them in parallel processes."""
    if workers <= 1 or len(jobs) == 1:
        return [_run_one(j) for j in jobs]
    with ProcessPoolExecutor(max_workers=min(workers, len(jobs))) as ex:
        return list(ex.map(_run_one, jobs))


def parse_sets(items):
    """["SoC.E_capacity=72", ...] -> {"SoC": {"E_capacity": 72.0}} (numbers, true/false or text)."""
    out = {}
    for item in items or []:
        key, _, raw = item.partition("=")
        if "." not in key or not raw:
            raise SystemExit(f"--set needs SECTION.KEY=VALUE, got '{item}'")
        section, name = key.split(".", 1)
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = raw
        out.setdefault(section, {})[name] = value
    return out


def tuned_overrides(controller):
    path = TUNING_DIR / f"{controller}_best.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))["overrides"]


def main():
    parser = argparse.ArgumentParser(description="Closed-loop replay of the controllers on past data.")
    parser.add_argument("--controllers", default=",".join(CONTROLLERS), help=f"comma separated: {', '.join(CONTROLLERS)}")
    parser.add_argument("--period", choices=[*PERIOD_NAMES, "validation", "test"], default="development",
                        help="validation = tuning, test = development (older names)")
    parser.add_argument("--final", action="store_true", help="allow the final test period (run it once, at the end)")
    parser.add_argument("--days", type=float, default=0, help="only the first N days of the period")
    parser.add_argument("--tuned", action="store_true", help="use results/tuning/<controller>_best.json where available")
    parser.add_argument("--workers", type=int, default=0)
    parser.add_argument("--refresh-data", action="store_true")
    parser.add_argument("--set", action="append", default=[], metavar="SECTION.KEY=VALUE",
                        help="override a config value for this run, e.g. --set SoC.E_capacity=72")
    args = parser.parse_args()

    cfg = with_overrides(load_config(), parse_sets(args.set))
    s = replay_settings(cfg)
    data = build_data(cfg, refresh=args.refresh_data)
    name = {"validation": "tuning", "test": "development"}.get(args.period, args.period)
    period = named_periods(data, cfg)[name]
    if period is None:
        raise SystemExit(f"There is no '{name}' period in the data (see replay.*_days in the configuration).")
    if name == "final_test":
        if not args.final:
            raise SystemExit("The final test period is only run once, at the very end: add --final to run it.")
        REPLAY_DIR.mkdir(parents=True, exist_ok=True)
        with open(REPLAY_DIR / "final_test_runs.log", "a", encoding="utf-8") as f:  # every look is recorded
            f.write(f"{pd.Timestamp.now(tz='UTC').isoformat()} controllers={args.controllers} set={args.set}\n")
    start, end = period
    args.period = name
    if args.days:
        end = min(end, start + pd.Timedelta(days=args.days))
    tz = cfg["general"].get("timezone", "Europe/Budapest")
    print(f"Replay period ({args.period}): {start.tz_convert(tz):%Y-%m-%d %H:%M} – {end.tz_convert(tz):%Y-%m-%d %H:%M}, "
          f"re-planning every {s['replan_steps']} steps", flush=True)

    names = [c.strip() for c in args.controllers.split(",") if c.strip()]
    jobs, used = [], {}
    for name in names:
        if name not in CONTROLLERS:
            raise SystemExit(f"unknown controller {name}; choose from {', '.join(CONTROLLERS)}")
        over = tuned_overrides(name) if args.tuned else None
        used[name] = over
        jobs.append((data, with_overrides(cfg, over), name, start, end))

    t_start = time.perf_counter()
    results = run_many(jobs, args.workers or s["workers"])
    REPLAY_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for (name, df, stats), job in zip(results, jobs):
        m = metrics(df, stats, job[1])
        rows.append({"controller": name, "label": CONTROLLERS[name]["label"], "tuned": used[name] is not None, **m})
        df.to_csv(REPLAY_DIR / f"steps_{args.period}_{name}.csv")
    summary = pd.DataFrame(rows).set_index("controller")
    if "grid_only" in summary.index:
        base = summary.loc["grid_only", "objective"]
        summary["saving_vs_grid_only_pct"] = 100 * (base - summary["objective"]) / base
    summary.to_csv(REPLAY_DIR / f"summary_{args.period}.csv")
    pd.set_option("display.width", 200)
    cols = ["total_eur", "stored_value_eur", "objective", "grid_kwh", "curtailed_kwh", "throughput_kwh",
            "violation_pp_h", "steps_below_soc_min_pct", "chance_target_below_pct", "max_violation_pp",
            "protection_steps", "undelivered_discharge_kwh", "extra_grid_eur", "min_soc", "solve_median_s",
            "solver_success_pct"]
    print(summary[[c for c in cols if c in summary]].round(4).to_string())
    print(f"Done in {time.perf_counter() - t_start:.0f} s. Results in {REPLAY_DIR}")


if __name__ == "__main__":
    main()
