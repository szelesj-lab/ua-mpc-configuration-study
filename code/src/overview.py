"""Calculations behind the dashboard Overview: the decision in force, costs, a plain-language
summary of the plan and one time grid that joins the executed plan with the measurements.

Plans come as DataFrames with the columns of results/CostFunction_results.csv plus `_ts`
(UTC step start) and, for executed plans, `_plan` (UTC time the plan was computed).
"""

import numpy as np
import pandas as pd

PRICE_COL = "Grid Price (€/Wh)"


def _col(df, name, default=0.0):
    return df[name].astype(float) if name in df else pd.Series(default, index=df.index, dtype=float)


def fmt_money(eur):
    if eur is None or not np.isfinite(eur):
        return "–"
    a = abs(eur)
    return f"{eur:.2f} €" if a >= 1 else f"{eur:.3f} €" if a >= 0.01 else f"{eur:.4f} €"


# ---------------------------------------------------------------- decision in force

def decision_at(plan: pd.DataFrame, now: pd.Timestamp, step_s: float, c_deg: float, tz: str):
    """The plan step that contains `now` (or the next one, if the plan starts later)."""
    if plan is None or plan.empty:
        return None
    step = pd.Timedelta(seconds=step_s)
    ts = pd.to_datetime(plan["_ts"], utc=True)
    current = plan[(ts <= now) & (ts + step > now)]
    upcoming = False
    if current.empty:
        later = plan[ts > now]
        if later.empty:
            return None
        current, upcoming = later.iloc[[0]], True
    row = current.iloc[-1]
    start = pd.Timestamp(row["_ts"])
    pos = plan.index.get_loc(current.index[-1])
    prev = plan.iloc[pos - 1] if isinstance(pos, int) and pos > 0 else None
    soc_start = float(prev["SoC_opt"]) if prev is not None and "SoC_opt" in prev and pd.Timestamp(prev["_ts"]) + step == start else None

    demand = float(row.get("Demand_power (W)", 0.0))
    grid = float(row.get("P_grid_opt (W)", 0.0))
    dis = float(row.get("P_bat_dis_opt (W)", 0.0))
    ren = max(float(row.get("Total_power (W)", 0.0)), 0.0)
    charge = float(row.get("P_bat_ch (W)", ren))
    curtailed = float(row.get("P_curtailed (W)", max(ren - charge, 0.0)))
    price_wh = float(row.get(PRICE_COL, np.nan))
    grid_rate = grid * price_wh  # W x EUR/Wh = EUR/h
    wear_rate = c_deg * (abs(charge) + abs(dis))
    plan_time = row.get("_plan")
    return {
        "upcoming": upcoming,
        "from": start.tz_convert(tz).strftime("%Y-%m-%d %H:%M"),
        "to": (start + step).tz_convert(tz).strftime("%Y-%m-%d %H:%M"),
        "plan_computed": pd.Timestamp(plan_time).tz_convert(tz).strftime("%Y-%m-%d %H:%M") if plan_time is not None and pd.notna(plan_time) else None,
        "demand_w": demand,
        "grid_w": grid,
        "battery_discharge_w": dis,
        "battery_charge_w": charge,
        "renewable_w": ren,
        "curtailed_w": curtailed,
        "grid_share": grid / demand if demand > 0 else None,
        "battery_share": dis / demand if demand > 0 else None,
        "price_eur_mwh": price_wh * 1e6 if np.isfinite(price_wh) else None,
        "cost_rate_eur_h": grid_rate + wear_rate if np.isfinite(price_wh) else None,
        "grid_cost_rate_eur_h": grid_rate if np.isfinite(price_wh) else None,
        "wear_cost_rate_eur_h": wear_rate,
        "soc": float(row["SoC_opt"]) if "SoC_opt" in row else None,
        "soc_start": soc_start,
        "slot_fraction": 0.0 if upcoming else float(min(max((now - start) / step, 0.0), 1.0)),
    }


def _soc_now(decision):
    """Planned SoC at this moment: linear between the slot's start and end SoC."""
    end, start = decision.get("soc"), decision.get("soc_start")
    if end is None or start is None:
        return end
    return start + (end - start) * decision.get("slot_fraction", 1.0)


def compare_now(decision: dict | None, latest: dict, settings: dict, c_deg: float):
    """Plan of the current slot next to the latest measurements.

    Only measurements younger than `stale_after_s` are used. A row is flagged when the
    difference exceeds max(deviation_abs_w, deviation_rel x |plan|) (SoC: deviation_soc).
    Battery power is compared as net power, + discharge and − charge, like the measurement.
    """
    if not decision or not latest or not latest.get("values"):
        return None
    fresh = {k: v["value"] for k, v in latest["values"].items() if v["age_s"] <= settings["stale_after_s"]}
    if not fresh:
        return None
    ren_parts = [fresh[k] for k in ("pv_w", "wind_w") if k in fresh]
    measured = {
        "load": fresh.get("load_w"),
        "grid": fresh.get("grid_w"),
        "battery": fresh.get("battery_w"),
        "renewables": sum(ren_parts) if ren_parts else None,
        "soc": fresh.get("soc"),
    }
    planned = {
        "load": decision["demand_w"],
        "grid": decision["grid_w"],
        "battery": decision["battery_discharge_w"] - decision["battery_charge_w"],
        "renewables": decision["renewable_w"],
        "soc": _soc_now(decision),
    }
    labels = {"load": "Load", "grid": "Grid", "battery": "Battery (+ out, − in)", "renewables": "Renewables", "soc": "SoC"}
    rows = []
    for key, label in labels.items():
        p, m = planned[key], measured[key]
        diff = m - p if p is not None and m is not None else None
        if diff is None:
            flag = False
        elif key == "soc":
            flag = abs(diff) > settings["deviation_soc"]
        else:
            flag = abs(diff) > max(settings["deviation_abs_w"], settings["deviation_rel"] * abs(p))
        rows.append({"key": key, "label": label, "unit": "%" if key == "soc" else "W",
                     "plan": p, "measured": m, "diff": diff, "flag": bool(flag)})

    from live_data import balance_residual
    balance = balance_residual(fresh)
    out = {"age_s": min(v["age_s"] for k, v in latest["values"].items() if k in fresh),
           "rows": rows, "deviation": any(r["flag"] for r in rows),
           "balance_w": balance,
           "balance_ok": None if balance is None else abs(balance) <= settings["deviation_abs_w"],
           "cost_rate_eur_h": None, "grid_cost_rate_eur_h": None, "wear_cost_rate_eur_h": None}
    price_mwh = decision.get("price_eur_mwh")
    if measured["grid"] is not None and price_mwh is not None:
        grid_rate = measured["grid"] * price_mwh / 1e6
        wear_rate = c_deg * abs(measured["battery"]) if measured["battery"] is not None else 0.0
        out.update(cost_rate_eur_h=grid_rate + wear_rate, grid_cost_rate_eur_h=grid_rate,
                   wear_cost_rate_eur_h=wear_rate if measured["battery"] is not None else None)
    return out


# ---------------------------------------------------------------- costs

def plan_costs(plan: pd.DataFrame, step_s: float, tz: str | None = None):
    """Cost of a whole plan, and of covering the same demand from the grid only.

    Also returns the grid energy (kWh), the average grid price paid and, with `tz`, the local
    time range the plan covers, so the dashboard can say exactly what the numbers belong to.
    """
    if plan is None or plan.empty or PRICE_COL not in plan:
        return None
    dt_h = step_s / 3600
    price = _col(plan, PRICE_COL)
    grid_w = _col(plan, "P_grid_opt (W)")
    grid_cost = float((grid_w * price).sum() * dt_h)
    total = float(_col(plan, "Grid and degradation cost").sum()) if "Grid and degradation cost" in plan else grid_cost
    baseline = float((_col(plan, "Demand_power (W)") * price).sum() * dt_h)
    savings = baseline - total
    grid_kwh = float(grid_w.clip(lower=0).sum() * dt_h / 1000)
    out = {
        "total_eur": total,
        "grid_eur": grid_cost,
        "wear_eur": total - grid_cost,
        "grid_only_eur": baseline,
        "savings_eur": savings,
        "savings_pct": 100 * savings / baseline if baseline > 0 else None,
        "grid_kwh": grid_kwh,
        "demand_kwh": float(_col(plan, "Demand_power (W)").clip(lower=0).sum() * dt_h / 1000),
        "avg_price_eur_kwh": grid_cost / grid_kwh if grid_kwh > 0 else None,
        "hours": len(plan) * dt_h,
    }
    if tz and "_ts" in plan:
        ts = pd.to_datetime(plan["_ts"], utc=True)
        out["from"] = ts.min().tz_convert(tz).strftime("%Y-%m-%d %H:%M")
        out["to"] = (ts.max() + pd.Timedelta(seconds=step_s)).tz_convert(tz).strftime("%Y-%m-%d %H:%M")
        if "_plan" in plan and pd.notna(plan["_plan"].iloc[-1]):
            out["computed"] = pd.Timestamp(plan["_plan"].iloc[-1]).tz_convert(tz).strftime("%Y-%m-%d %H:%M")
    return out


def _step_parts(executed: pd.DataFrame, start, end, step_s: float, c_deg: float):
    """Per plan step: overlap hours with [start, end) and planned cost rate (EUR/h)."""
    step = pd.Timedelta(seconds=step_s)
    ts = pd.to_datetime(executed["_ts"], utc=True)
    a = ts.clip(lower=start)
    b = (ts + step).clip(upper=end)
    hours = ((b - a).dt.total_seconds() / 3600).clip(lower=0).values
    price = _col(executed, PRICE_COL).values
    wear = c_deg * (_col(executed, "P_bat_ch (W)").abs() + _col(executed, "P_bat_dis_opt (W)").abs()).values
    rate = _col(executed, "P_grid_opt (W)").values * price + wear
    return ts, hours, price, rate


def costs_between(executed: pd.DataFrame, measured: pd.DataFrame | None, start, end, step_s: float, c_deg: float):
    """Planned and measured cost between start and end (UTC), step by step (partial steps pro rata).

    `plan_same_eur` is what the plan expected for exactly the hours that have measurements, so
    measured and planned can be compared like for like.
    """
    out = {"planned_eur": None, "measured_eur": None, "measured_hours": 0.0, "plan_same_eur": None}
    if executed is None or executed.empty or PRICE_COL not in executed:
        return out
    ts, hours, price, rate = _step_parts(executed, start, end, step_s, c_deg)
    out["planned_eur"] = float(np.nansum(rate * hours))

    ok = _measured_mask(measured, ts)
    if ok is not None and ok.any():
        m = measured.reindex(pd.DatetimeIndex(ts))
        wear_m = c_deg * np.abs(m["battery_w"].values) if "battery_w" in m else 0.0
        cost = (m["grid_w"].values * price + np.nan_to_num(wear_m)) * hours
        out["measured_eur"] = float(np.nansum(cost[ok]))
        out["measured_hours"] = float(hours[ok].sum())
        out["plan_same_eur"] = float(np.nansum((rate * hours)[ok]))
    return out


def _measured_mask(measured, ts):
    if measured is None or measured.empty or "grid_w" not in measured:
        return None
    return ~np.isnan(measured.reindex(pd.DatetimeIndex(ts))["grid_w"].values)


def day_costs(executed: pd.DataFrame, measured: pd.DataFrame | None, day_start, now, step_s: float, c_deg: float, tz: str):
    """Expected cost of one calendar day: measured where measurements exist, the plan elsewhere.

    day_start is local midnight in UTC. The result splits the day into the part already past
    (measured, or planned where nothing was measured) and the planned rest of the day, and says
    how many hours no plan covers (e.g. before the first MPC run).
    """
    day_end = (day_start.tz_convert(tz) + pd.Timedelta(days=1)).normalize().tz_convert("UTC")
    now = min(max(now, day_start), day_end)
    period_h = (day_end - day_start).total_seconds() / 3600
    out = {
        "from": day_start.tz_convert(tz).strftime("%Y-%m-%d %H:%M"),
        "to": day_end.tz_convert(tz).strftime("%Y-%m-%d %H:%M"),
        "now": now.tz_convert(tz).strftime("%H:%M"),
        "period_hours": period_h,
        "elapsed_hours": (now - day_start).total_seconds() / 3600,
        "expected_eur": None, "measured_eur": None, "measured_hours": 0.0, "plan_same_eur": None,
        "past_unmeasured_eur": 0.0, "past_unmeasured_hours": 0.0,
        "rest_planned_eur": None, "rest_hours": 0.0, "uncovered_hours": period_h,
    }
    if executed is None or executed.empty or PRICE_COL not in executed:
        return out
    ts, past_h, _, rate = _step_parts(executed, day_start, now, step_s, c_deg)
    _, rest_h, _, _ = _step_parts(executed, now, day_end, step_s, c_deg)
    past = costs_between(executed, measured, day_start, now, step_s, c_deg)
    ok = _measured_mask(measured, ts)
    unmeasured = ~ok if ok is not None else np.ones(len(ts), dtype=bool)

    out["measured_eur"] = past["measured_eur"]
    out["measured_hours"] = past["measured_hours"]
    out["plan_same_eur"] = past["plan_same_eur"]
    out["past_unmeasured_eur"] = float(np.nansum((rate * past_h)[unmeasured]))
    out["past_unmeasured_hours"] = float(past_h[unmeasured].sum())
    out["rest_planned_eur"] = float(np.nansum(rate * rest_h))
    out["rest_hours"] = float(rest_h.sum())
    covered = float(past_h.sum() + rest_h.sum())
    out["uncovered_hours"] = max(period_h - covered, 0.0)
    out["expected_eur"] = (out["measured_eur"] or 0.0) + out["past_unmeasured_eur"] + out["rest_planned_eur"]
    return out


# ---------------------------------------------------------------- summary

def _windows(mask, ts, step):
    """Contiguous runs of True in mask as (start, end) timestamps."""
    runs, start = [], None
    for i, on in enumerate(mask):
        if on and start is None:
            start = i
        if (not on or i == len(mask) - 1) and start is not None:
            end = i if on else i - 1
            runs.append((start, end))
            start = None
    return [(ts.iloc[s], ts.iloc[e] + step, s, e) for s, e in runs]


def summary_sentences(plan: pd.DataFrame, step_s: float, tz: str):
    if plan is None or plan.empty:
        return []
    step = pd.Timedelta(seconds=step_s)
    dt_h = step_s / 3600
    ts = pd.to_datetime(plan["_ts"], utc=True).dt.tz_convert(tz).reset_index(drop=True)
    p = plan.reset_index(drop=True)
    demand, grid, dis = _col(p, "Demand_power (W)"), _col(p, "P_grid_opt (W)"), _col(p, "P_bat_dis_opt (W)")
    ren = _col(p, "Total_power (W)").clip(lower=0)
    charge = _col(p, "P_bat_ch (W)") if "P_bat_ch (W)" in p else ren
    curt = _col(p, "P_curtailed (W)") if "P_curtailed (W)" in p else (ren - charge).clip(lower=0)
    price = _col(p, PRICE_COL, np.nan) * 1e6
    out = []

    start, end = ts.iloc[0], ts.iloc[-1] + step
    out.append(f"The plan covers {start:%a %d %b %H:%M} – {end:%a %H:%M} ({len(p) * dt_h:.0f} h).")

    e_dem, e_dis, e_grid = demand.sum() * dt_h, dis.sum() * dt_h, grid.sum() * dt_h
    if e_dem > 0.05:
        out.append(f"The load needs {e_dem:.0f} Wh: the battery supplies {100 * e_dis / e_dem:.0f} % and the grid {100 * e_grid / e_dem:.0f} %.")
    else:
        out.append("No production load is planned in this period.")

    wins = _windows((dis > 0.5).tolist(), ts, step)
    if wins:
        wins.sort(key=lambda w: dis.iloc[w[2]:w[3] + 1].sum(), reverse=True)
        main = sorted(wins[:2], key=lambda w: w[0])
        spans = " and ".join(f"{a:%H:%M}–{b:%H:%M}" for a, b, _, _ in main)
        used = dis > 0.5
        if price.notna().any() and used.any():
            p_used = float((price[used] * dis[used]).sum() / dis[used].sum())
            out.append(f"The battery is used mainly {spans}, when electricity costs {p_used:.0f} €/MWh on average "
                       f"(the average over the whole plan is {price.mean():.0f} €/MWh).")
        else:
            out.append(f"The battery is used mainly {spans}.")
    elif e_dem > 0.05:
        out.append("The battery is not discharged; the grid supplies the whole load.")

    e_ren, e_ch, e_curt = ren.sum() * dt_h, charge.sum() * dt_h, curt.sum() * dt_h
    if e_ren > 0.05:
        s = f"Renewables are expected to produce {e_ren:.0f} Wh, of which {e_ch:.0f} Wh is stored"
        s += f" and {e_curt:.0f} Wh is curtailed because the battery would otherwise exceed its limit." if e_curt > 0.5 else "."
        out.append(s)

    c = plan_costs(plan, step_s)
    if c and c["grid_only_eur"] > 0:
        diff = c["savings_eur"]
        word = "less" if diff >= 0 else "more"
        out.append(f"The plan costs {fmt_money(c['total_eur'])} (grid {fmt_money(c['grid_eur'])}, battery wear "
                   f"{fmt_money(c['wear_eur'])}), {fmt_money(abs(diff))} ({abs(c['savings_pct']):.0f} %) {word} than taking all energy from the grid.")

    if "SoC_opt" in p:
        soc = p["SoC_opt"].astype(float) * 100
        out.append(f"The battery goes from {soc.iloc[0]:.0f} % to {soc.iloc[-1]:.0f} % state of charge (lowest {soc.min():.0f} %).")
    return out


# ---------------------------------------------------------------- timeline

def measured_steps(hist: pd.DataFrame, step_s: float) -> pd.DataFrame:
    """Measurements averaged onto the MPC step grid (UTC step starts)."""
    if hist is None or hist.empty:
        return pd.DataFrame()
    m = hist.set_index("timestamp").sort_index()
    m = m.apply(pd.to_numeric, errors="coerce")
    res = m.resample(pd.Timedelta(seconds=step_s), origin="epoch", label="left", closed="left").mean()
    if {"pv_w", "wind_w"} & set(res.columns):
        res["renewable_w"] = res.reindex(columns=["pv_w", "wind_w"]).sum(axis=1, min_count=1)
    return res.dropna(how="all")


def _sigma(plan):
    """Forecast std the MPC used (calibrated); plans from older versions only have the GPR std."""
    raw = _col(plan, "Aggregated_deviation (W)", np.nan)
    return _col(plan, "Sigma_eff (W)", np.nan).fillna(raw) if "Sigma_eff (W)" in plan else raw


def timeline(executed: pd.DataFrame, measured: pd.DataFrame, now: pd.Timestamp, tz: str, step_s: float):
    if executed is None or executed.empty:
        return None
    e = executed.reset_index(drop=True)
    ts = pd.to_datetime(e["_ts"], utc=True)

    def arr(series):
        return [None if (v is None or not np.isfinite(v)) else round(float(v), 4) for v in series]

    def plan(name, scale=1.0):
        return arr(_col(e, name, np.nan) * scale) if name in e else None

    out = {
        "date": [t.tz_convert(tz).strftime("%Y-%m-%d %H:%M:%S") for t in ts],
        "now": now.tz_convert(tz).strftime("%Y-%m-%d %H:%M:%S"),
        "step_s": step_s,
        "plan": {
            "demand": plan("Demand_power (W)"),
            "grid": plan("P_grid_opt (W)"),
            "discharge": plan("P_bat_dis_opt (W)"),
            "charge": plan("P_bat_ch (W)") or plan("Total_power (W)"),
            "curtailed": plan("P_curtailed (W)"),
            "renewable": plan("Total_power (W)"),
            "sigma": arr(_sigma(e)) if "Aggregated_deviation (W)" in e else None,
            "soc": plan("SoC_opt", 100.0),
            "soc_min": plan("SoC_min_eff (%)"),
            "soc_max": plan("SoC_max_eff (%)"),
            "price": plan(PRICE_COL, 1e6),
        },
        "measured": None,
    }
    if measured is not None and not measured.empty:
        m = measured.reindex(pd.DatetimeIndex(ts))
        out["measured"] = {
            key: arr(m[col]) if col in m else None
            for key, col in [("load", "load_w"), ("grid", "grid_w"), ("battery", "battery_w"), ("renewable", "renewable_w"), ("soc", "soc")]
        }
        if out["measured"]["soc"] is not None:
            out["measured"]["soc"] = [None if v is None else round(v * 100, 2) for v in out["measured"]["soc"]]
    return out
