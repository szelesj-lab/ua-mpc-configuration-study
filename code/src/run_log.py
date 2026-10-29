"""Log of the live MPC runs and the results of the live operation.

Every MPC run (automatic every live.mpc_interval_min minutes, or started by hand) appends

* one row to results/runs/mpc_runs.csv: when it ran, from which SoC, whether the solver converged,
  how long it took, the cost terms, the settings that matter for the evaluation, and the decision
  for the first step;
* the plan rows that are in force until the next scheduled run to results/runs/decisions.csv.

The decision log is what the controller actually decided for each step, so it is kept for the
whole experiment (unlike results/plans/, which keeps 7 days). Together with the live measurements
(data/live/) it gives the results of the live operation with the same KPIs as the replay
(replay.metrics): cost and saving, the three levels of a constraint violation (leaving the planned
SoC band, the cell's protection acting, the energy the battery could not deliver and its cost),
and the MPC's own statistics - for any period and day by day.
"""

import csv
import math

import numpy as np
import pandas as pd

import live_data
from settings import RESULTS_DIR

RUNS_DIR = RESULTS_DIR / "runs"
RUN_LOG = RUNS_DIR / "mpc_runs.csv"
DECISION_LOG = RUNS_DIR / "decisions.csv"

RUN_FIELDS = [
    "computed_utc", "success", "iterations", "solve_s", "initial_soc", "initial_soc_source", "total_cost",
    "J_x", "J_u", "J_du", "J_ff", "J_T", "min_soc_margin",
    # settings that define the controller being evaluated
    "E_capacity", "soc_min", "soc_max", "soc_chance_z", "soc_backoff", "margin_window", "sigma_scale",
    "lambda", "w_ff", "w_x",
    # decision for the first step
    "step_utc", "grid_w", "discharge_w", "charge_w", "curtailed_w", "renewable_fc_w", "sigma_eff_w",
    "soc_min_eff", "price_eur_wh",
]
DECISION_FIELDS = [
    "step_utc", "computed_utc", "grid_w", "discharge_w", "charge_w", "curtailed_w", "soc", "soc_min_eff",
    "soc_max_eff", "renewable_fc_w", "sigma_eff_w", "demand_w", "price_eur_wh",
]
PLAN_COLS = {
    "grid_w": "P_grid_opt (W)", "discharge_w": "P_bat_dis_opt (W)", "charge_w": "P_bat_ch (W)",
    "curtailed_w": "P_curtailed (W)", "soc": "SoC_opt", "renewable_fc_w": "Total_power (W)",
    "sigma_eff_w": "Sigma_eff (W)", "demand_w": "Demand_power (W)", "price_eur_wh": "Grid Price (€/Wh)",
}


def _append(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if new:
            w.writeheader()
        for row in rows:
            w.writerow(row)


def _num(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else ""
    except (TypeError, ValueError):
        return ""


def _plan_rows(plan, computed, interval_s, step_s):
    """Plan rows in force from this run until the next scheduled run (at least one step)."""
    ts = pd.to_datetime(plan["Date"], utc=True)
    first = computed.floor(f"{int(step_s)}s")
    n = max(1, int(round(interval_s / step_s)))
    keep = (ts >= first) & (ts < first + pd.Timedelta(seconds=n * step_s))
    if not keep.any():
        keep = ts.index == ts.index[0]
    out = []
    for i in np.flatnonzero(keep.to_numpy()):
        row = {"step_utc": ts.iloc[i].isoformat(), "computed_utc": computed.isoformat()}
        for k, col in PLAN_COLS.items():
            row[k] = _num(plan[col].iloc[i]) if col in plan else ""
        row["soc_min_eff"] = _num(plan["SoC_min_eff (%)"].iloc[i] / 100) if "SoC_min_eff (%)" in plan else ""
        row["soc_max_eff"] = _num(plan["SoC_max_eff (%)"].iloc[i] / 100) if "SoC_max_eff (%)" in plan else ""
        out.append(row)
    return out


def log_run(res, plan, config, initial_soc_source, solve_s, total_cost=None, computed=None):
    """Append one MPC run to the run log and its decisions to the decision log."""
    computed = pd.Timestamp.now(tz="UTC") if computed is None else pd.Timestamp(computed).tz_convert("UTC")
    step_s = float(config["general"]["deltaT"])
    interval_s = live_data.live_settings(config)["mpc_interval_min"] * 60
    decisions = _plan_rows(plan, computed, interval_s, step_s)
    mpc, soc, w = config.get("mpc", {}), config["SoC"], config["weights"]
    terms = getattr(res, "cost_terms", {}) or {}
    first = decisions[0] if decisions else {}
    run = {
        "computed_utc": computed.isoformat(), "success": bool(res.success), "iterations": int(res.nit),
        "solve_s": round(float(solve_s), 3), "initial_soc": _num(getattr(res, "initial_soc", "")),
        "initial_soc_source": initial_soc_source, "total_cost": _num(total_cost),
        **{k: _num(terms.get(k, "")) for k in ("J_x", "J_u", "J_du", "J_ff", "J_T")},
        "min_soc_margin": _num(getattr(res, "min_soc_margin", "")),
        "E_capacity": soc.get("E_capacity"), "soc_min": soc.get("soc_min", 0.1), "soc_max": soc.get("soc_max", 0.9),
        "soc_chance_z": mpc.get("soc_chance_z", 0), "soc_backoff": mpc.get("soc_backoff", 0),
        "margin_window": mpc.get("margin_window", "horizon"), "sigma_scale": mpc.get("sigma_scale", 1.0),
        "lambda": w.get("lambda"), "w_ff": w.get("w_ff"), "w_x": w.get("w_x"),
        **{k: first.get(k, "") for k in ("step_utc", "grid_w", "discharge_w", "charge_w", "curtailed_w",
                                          "renewable_fc_w", "sigma_eff_w", "soc_min_eff", "price_eur_wh")},
    }
    _append(RUN_LOG, RUN_FIELDS, [run])
    _append(DECISION_LOG, DECISION_FIELDS, decisions)
    return run


# ---------------------------------------------------------------- reading and results

def read_runs():
    if not RUN_LOG.exists():
        return pd.DataFrame(columns=RUN_FIELDS)
    df = pd.read_csv(RUN_LOG)
    df["computed_utc"] = pd.to_datetime(df["computed_utc"], utc=True, format="ISO8601")
    return df


def read_decisions():
    """The decision in force for every step: the newest plan row computed for it."""
    if not DECISION_LOG.exists():
        return pd.DataFrame(columns=DECISION_FIELDS)
    df = pd.read_csv(DECISION_LOG)
    df["step_utc"] = pd.to_datetime(df["step_utc"], utc=True, format="ISO8601")
    df["computed_utc"] = pd.to_datetime(df["computed_utc"], utc=True, format="ISO8601")
    return df.sort_values(["step_utc", "computed_utc"]).drop_duplicates("step_utc", keep="last").set_index("step_utc")


def first_measurement():
    files = sorted(live_data.LIVE_DIR.glob("*.csv")) if live_data.LIVE_DIR.exists() else []
    return pd.Timestamp(files[0].stem, tz="UTC") if files else None


def _measured_steps(start, end, step_s):
    hours = (end - start).total_seconds() / 3600 + step_s / 3600
    hist = live_data.read_history(hours, end)
    if hist.empty:
        return pd.DataFrame()
    m = hist.set_index("timestamp").sort_index().apply(pd.to_numeric, errors="coerce")
    rule = pd.Timedelta(seconds=step_s)
    mean = m.resample(rule, origin="epoch", label="left", closed="left").mean()
    if "soc" in m:
        mean["soc_min_step"] = m["soc"].resample(rule, origin="epoch", label="left", closed="left").min()
    mean = mean.dropna(how="all")
    return mean[(mean.index >= start) & (mean.index < end)]


def _kpis(ms, dec, runs, cfg, step_s):
    """KPIs of one period from measured steps (ms), the decisions in force (dec) and the run log."""
    dt = step_s / 3600
    soc_cfg = cfg["SoC"]
    lo, hi = float(soc_cfg.get("soc_min", 0.1)), float(soc_cfg.get("soc_max", 0.9))
    c_deg = float(cfg["degradation"]["c_deg"])
    out = {"measured_hours": len(ms) * dt}
    if ms.empty:
        return out
    d = dec.reindex(ms.index) if not dec.empty else pd.DataFrame(index=ms.index, columns=DECISION_FIELDS)
    price = pd.to_numeric(d.get("price_eur_wh"), errors="coerce")
    out["planned_hours"] = float(price.notna().sum() * dt)

    # cost: measured where a price is known (from the decision in force)
    if "grid_w" in ms:
        ok = price.notna() & ms["grid_w"].notna()
        grid_eur = float((ms["grid_w"] * price)[ok].sum() * dt)
        wear_eur = float((c_deg * ms.get("battery_w", pd.Series(0.0, index=ms.index)).abs())[ok].sum() * dt)
        out.update(grid_eur=grid_eur, wear_eur=wear_eur, total_eur=grid_eur + wear_eur,
                   grid_kwh=float(ms["grid_w"][ok].sum() * dt / 1000))
        if "load_w" in ms:
            ref = float((ms["load_w"] * price)[ok & ms["load_w"].notna()].sum() * dt)
            out["grid_only_eur"] = ref
            out["saving_vs_grid_only_pct"] = 100 * (ref - out["total_eur"]) / ref if ref > 0 else None
        planned = pd.to_numeric(d.get("grid_w"), errors="coerce")
        p_wear = c_deg * (pd.to_numeric(d.get("discharge_w"), errors="coerce").abs()
                          + pd.to_numeric(d.get("charge_w"), errors="coerce").abs())
        out["planned_eur"] = float(((planned * price) + p_wear.fillna(0))[ok].sum() * dt)  # grid + wear, like for like

    # level 1: leaving the planned SoC band
    if "soc" in ms and ms["soc"].notna().any():
        soc = ms["soc"].dropna()
        smin = ms.get("soc_min_step", soc).reindex(soc.index).fillna(soc)
        excess = (lo - soc).clip(lower=0) + (soc - hi).clip(lower=0)
        out.update(
            violation_pp_h=float(100 * excess.sum() * dt),
            max_violation_pp=float(100 * ((lo - smin).clip(lower=0).max())),
            h_below_soc_min=float((smin < lo - 1e-4).sum() * dt),
            steps_below_soc_min_pct=100 * float((smin < lo - 1e-4).mean()),
            min_soc=float(smin.min()), max_soc=float(soc.max()),
        )
        z = pd.to_numeric(runs.get("soc_chance_z"), errors="coerce").dropna() if not runs.empty else pd.Series(dtype=float)
        if len(z) and z.iloc[-1] > 0:
            out["chance_target_below_pct"] = 100 * 0.5 * (1 - math.erf(float(z.median()) / math.sqrt(2)))

    # levels 2 and 3: the protection acting and what it cost
    if "battery_w" in ms and "soc" in ms and not dec.empty:
        meas_dis = ms["battery_w"].clip(lower=0)
        meas_ch = (-ms["battery_w"]).clip(lower=0)
        plan_dis = pd.to_numeric(d.get("discharge_w"), errors="coerce")
        plan_ch = pd.to_numeric(d.get("charge_w"), errors="coerce")
        smin = ms.get("soc_min_step", ms["soc"])
        tol = np.maximum(0.5, 0.1 * plan_dis.fillna(0))
        cut_d = ((plan_dis - meas_dis) > tol) & (smin <= lo + 0.01)
        cut_c = ((plan_ch - meas_ch) > np.maximum(0.5, 0.1 * plan_ch.fillna(0))) & (ms["soc"] >= hi - 0.01)
        und = (plan_dis - meas_dis).clip(lower=0)[cut_d]
        out.update(
            protection_steps=int((cut_d | cut_c).sum()), protection_h=float((cut_d | cut_c).sum() * dt),
            protection_discharge_steps=int(cut_d.sum()), protection_charge_steps=int(cut_c.sum()),
            undelivered_discharge_kwh=float(und.sum() * dt / 1000),
            extra_grid_eur=float((und * price.reindex(und.index)).sum() * dt),
            tracking_error_w=float((plan_dis - meas_dis).abs().mean()) if plan_dis.notna().any() else None,
        )

    # the MPC itself
    if not runs.empty:
        out.update(
            mpc_runs=int(len(runs)),
            solver_success_pct=100 * float(runs["success"].astype(str).str.lower().eq("true").mean()),
            solve_median_s=float(pd.to_numeric(runs["solve_s"], errors="coerce").median()),
            measured_soc_start_pct=100 * float(runs["initial_soc_source"].astype(str).str.startswith("live").mean()),
        )
    return out


def results(start=None, end=None, config=None):
    """Totals over [start, end) and one row per local day. Times are UTC; defaults: whole experiment."""
    from settings import load_config

    cfg = config or load_config()
    step_s = float(cfg["general"]["deltaT"])
    tz = cfg["general"].get("timezone", "Europe/Budapest")
    end = pd.Timestamp.now(tz="UTC") if end is None else pd.Timestamp(end).tz_convert("UTC")
    runs, dec = read_runs(), read_decisions()
    if start is None:
        cands = [t for t in (first_measurement(), runs["computed_utc"].min() if not runs.empty else None) if t is not None]
        start = min(cands) if cands else end - pd.Timedelta(days=1)
    start = pd.Timestamp(start).tz_convert("UTC")
    ms = _measured_steps(start, end, step_s)
    r = runs[(runs["computed_utc"] >= start) & (runs["computed_utc"] < end)] if not runs.empty else runs
    total = _kpis(ms, dec, r, cfg, step_s)
    total.update(start=start.tz_convert(tz).strftime("%Y-%m-%d %H:%M"), end=end.tz_convert(tz).strftime("%Y-%m-%d %H:%M"))

    days = []
    if not ms.empty:
        local_day = ms.index.tz_convert(tz).normalize()
        for day in sorted(set(local_day)):
            sel = ms[local_day == day]
            d0, d1 = day.tz_convert("UTC"), (day + pd.Timedelta(days=1)).tz_convert("UTC")
            rd = r[(r["computed_utc"] >= d0) & (r["computed_utc"] < d1)] if not r.empty else r
            row = _kpis(sel, dec, rd, cfg, step_s)
            row["day"] = day.strftime("%Y-%m-%d")
            days.append(row)
    return {"total": total, "days": days}
