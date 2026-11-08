"""Report of an own-data simulation: totals per configuration and controller in the site's units, the months,
r_E and Gamma with their reading, and the files to download (report.md, report.json, monthly.csv)."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd

LABEL = {"grid_only": "Grid only", "det": "DET", "det_bo": "DET-BO (reference back-off)",
         "det_bo_sized": "DET-BO (back-off re-sized monthly from r_E)", "ua": "UA-MPC"}
CONFIG = {"A": "A – renewable buffer (renewables only charge the battery)",
          "B": "B – self-consumption (the battery takes the forecast deviation)",
          "C": "C – set-point (the grid takes the forecast deviation)"}
EUR = ("net_cost_eur", "total_eur", "grid_eur", "wear_eur", "extra_grid_eur", "stored_value_eur")
KWH = ("grid_kwh", "demand_kwh", "renewable_kwh", "curtailed_kwh", "throughput_kwh",
       "undelivered_discharge_kwh", "undelivered_charge_kwh")
ADD = EUR[:-1] + KWH + ("protection_h", "violation_pp_h", "h_below_soc_min", "solves")
BAND = 0.8
GAMMA_LO, GAMMA_HI = 0.75, 1.25     # the switch lay between these in the pre-registered reference cells


def _site(m: dict, f: float) -> dict:
    o = dict(m)
    for k in EUR:
        if k in o and o[k] is not None:
            o[k] = o[k] / f
    for k in KWH:
        if k in o and o[k] is not None:
            o[k] = o[k] / f
    return o


def tuned_b(X: str):
    from app.own.job import has_det_bo, point
    return float(point(X, "det_bo")["mpc"]["soc_backoff"]) if has_det_bo(X) else None


def collect(d: Path):
    p = json.loads((d / "params.json").read_text()); up = json.loads((d / "upload.json").read_text())
    from app.own.profile import sizes_to_cell
    cell = sizes_to_cell(p["sizes"], up.get("scale_mean_kw", up["mean_kw"])); f = cell["factor"]
    months = json.loads((d / "months.json").read_text()) if (d / "months.json").exists() else []
    rows = []
    for j in sorted((d / "runs").glob("*/*.json")) if (d / "runs").exists() else []:
        r = json.loads(j.read_text())
        rows.append({"config": r["config"], "controller": r["controller"], "month": r["month"], "b": r.get("b"),
                     "soc_end": r["soc_end"], **_site(r["metrics"], f)})
    return p, up, cell, months, pd.DataFrame(rows)


def gamma_rows(p, months):
    out = []
    for X in p["configs"]:
        b = tuned_b(X)
        for r in months:
            rE = r["rE"][X]
            out.append({"config": X, "month": r["month"], "r_E": rE, "r_E_past": r["rE_past"][X],
                        "b_ref": b, "Gamma_ref_b": (rE * BAND / b) if b else None,
                        "b_sized": r["b_sized"][X] if X != "C" else None,
                        "Gamma_sized": (rE * BAND / r["b_sized"][X]) if X != "C" and r["b_sized"][X] > 0 else None,
                        "b_recommended": rE * BAND / GAMMA_LO if X != "C" else None})
    return out


ARCH = {"A": "renewable_buffer", "B": "self_consumption", "C": "set_point"}


def gamma_windows(d: Path, cell: dict) -> tuple[list[dict], pd.DataFrame]:
    """Window-by-window check of the Γ bound for the constant back-offs (app/own/gammacheck.py): every re-planning
    hour of DET-BO, classified by where it started and how large the routed deficit was."""
    from app.own.gammacheck import routed, summary, windows
    rows, allw = [], []
    for X in ("A", "B"):
        for c in ("det_bo", "det_bo_sized"):
            parts = sorted((d / "runs" / X).glob(f"{c}_*.json")) if (d / "runs" / X).exists() else []
            ws = []
            for j in parts:
                r = json.loads(j.read_text()); f = j.with_suffix(".csv"); dp = d / "data" / f"{r['month']}.pkl"
                if not f.exists() or not dp.exists() or not r.get("b"):
                    continue
                x = pd.read_pickle(dp)
                err = routed(x.solar_mu + x.wind_mu, x.solar_real + x.wind_real, ARCH[X], cell["p_max_w"], cell["p_max_w"])
                w = windows(pd.read_csv(f), err, float(r["b"]), cell["E_capacity"], r.get("soc0"))
                w.insert(0, "month", r["month"]); w.insert(0, "controller", c); w.insert(0, "config", X); w.insert(3, "b", r["b"])
                ws.append(w)
            if ws:
                w = pd.concat(ws, ignore_index=True); allw.append(w)
                rows.append({"config": X, "controller": c, **summary(w)})
    return rows, (pd.concat(allw, ignore_index=True) if allw else pd.DataFrame())


def _rng(lo, hi, n):
    return f"{lo:.{n}f}" if f"{lo:.{n}f}" == f"{hi:.{n}f}" else f"{lo:.{n}f}–{hi:.{n}f}"


def verdict(X: str, g: list[dict]) -> str:
    if X == "C":
        return ("r_E = 0: in set-point operation the forecast error goes to the grid, not into the battery. "
                "Uncertainty handling cannot pay off here; in the reference runs DET and UA-MPC were indistinguishable.")
    gs = [x["Gamma_ref_b"] for x in g if x["config"] == X and x["Gamma_ref_b"] is not None]
    rs = [x["r_E"] for x in g if x["config"] == X]
    last = [x for x in g if x["config"] == X][-1]
    rec = f" A constant back-off sized to Γ = 0.75 from the latest month would be b = {last['b_recommended']:.3f}."
    if not gs:
        return f"r_E {_rng(min(rs), max(rs), 3)}." + rec
    lo, hi = min(gs), max(gs)
    if hi < GAMMA_LO:
        s = (f"Γ with the reference back-off is {_rng(lo, hi, 2)} (< 0.75): the constant back-off already covers 95 % "
             "of the windows; the Γ reading predicts a tie between DET-BO and UA-MPC in protection.")
    elif lo > GAMMA_HI:
        s = (f"Γ with the reference back-off is {_rng(lo, hi, 2)} (> 1.25): the forecast error reaching the battery is "
             "larger than that back-off covers, so it should be re-sized (below). Whether UA-MPC protects better depends "
             "on whether its own margin covers the error: in the reference summer decision map it did, in the reference winter (heavier "
             "error tails) it did not. Compare the protection hours of the runs below.")
    else:
        s = (f"Γ with the reference back-off is {_rng(lo, hi, 2)}, at or across the switch (0.75–1.25 in the reference tests): "
             "no firm prediction; compare the protection hours of the runs below.")
    return f"r_E {_rng(min(rs), max(rs), 3)}. " + s + rec


def write(d: Path) -> dict:
    p, up, cell, months, df = collect(d)
    g = gamma_rows(p, months)
    totals = []
    if len(df):
        for (X, c), x in df.groupby(["config", "controller"], sort=False):
            done = len(x) == len(months)
            t = {"config": X, "controller": c, "months_done": int(len(x)), "complete": done}
            for k in ADD:
                if k in x:
                    t[k] = float(x[k].sum())
            t["curtailed_pct"] = 100 * t["curtailed_kwh"] / t["renewable_kwh"] if t.get("renewable_kwh") else 0.0
            totals.append(t)
        tot = pd.DataFrame(totals)
        for X in tot.config.unique():
            go = tot[(tot.config == X) & (tot.controller == "grid_only")]
            if len(go):
                base = float(go.net_cost_eur.iloc[0])
                sel = tot.config == X
                tot.loc[sel, "saving_eur"] = base - tot.loc[sel, "net_cost_eur"]
                tot.loc[sel, "saving_pct"] = 100 * tot.loc[sel, "saving_eur"] / base if base else None
            det = tot[(tot.config == X) & (tot.controller == "det")]
            if len(det):
                sel = tot.config == X
                tot.loc[sel, "vs_det_pct"] = 100 * (tot.loc[sel, "net_cost_eur"] / float(det.net_cost_eur.iloc[0]) - 1)
        order = {c: i for i, c in enumerate(["grid_only", "det", "det_bo", "det_bo_sized", "ua"])}
        tot = tot.sort_values(["config", "controller"], key=lambda c: c.map(order) if c.name == "controller" else c)
        totals = tot.to_dict("records")
        df.to_csv(d / "monthly.csv", index=False)
    rep = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "params": p, "upload": {k: v for k, v in up.items() if k != "gap_list"},
           "cell": cell, "months": months, "gamma": g, "totals": totals,
           "monthly": df.to_dict("records") if len(df) else [],
           "verdict": {X: verdict(X, g) for X in p["configs"]}}
    try:
        rep["gamma_windows"], gw = gamma_windows(d, cell)
        if len(gw):
            gw.to_csv(d / "gamma_windows.csv", index=False)
    except Exception as e:  # noqa: BLE001  (a diagnostic must not stop the report)
        rep["gamma_windows"], rep["gamma_windows_error"] = [], f"{type(e).__name__}: {e}"
    from app.own.reference import check
    rep["reference"] = check(d)
    rep = _clean(json.loads(json.dumps(rep, default=float)))
    (d / "report.md").write_text(markdown(rep))
    (d / "report.json").write_text(json.dumps(rep, indent=1, allow_nan=False))
    try:   # tables, every step and every MPC solve as CSV, figures, one zip (app/own/exports.py)
        from app.own.exports import write as export
        rep["files"] = export(d, rep)
    except Exception as e:  # the report stays usable
        rep["files"] = []; rep["export_error"] = f"{type(e).__name__}: {e}"
    (d / "report.json").write_text(json.dumps(rep, indent=1, allow_nan=False))
    return rep


def _clean(o):
    """NaN and infinities -> None, so that the JSON is valid for browsers."""
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, float) and (o != o or o in (float("inf"), float("-inf"))):
        return None
    return o


def _eur(v):
    if v is None or (isinstance(v, float) and v != v):
        return "–"
    a = abs(v)
    return _f(v, 0 if a >= 1000 else 2 if a >= 10 else 4)


def _mlab(m):
    return "Sep 2026 test period" if m == "final_test" else m


def _f(v, n=2):
    return "–" if v is None or (isinstance(v, float) and v != v) else f"{v:,.{n}f}"


def markdown(r: dict) -> str:
    p, up, s = r["params"], r["upload"], r["params"]["sizes"]
    per = "the September 2026 test period" if p["period"] == "final_test" else f"{p['period'][0]} – {p['period'][-1]}"
    L = [f"# Own-data simulation ({r['time']})", "",
         f"Mode: **{'screening (r_E, Γ only)' if p['mode'] == 'screen' else 'quick'}** · period: {per} · "
         f"days without load data: {'left out' if p.get('fill') == 'data' else 'filled from the profile'} · "
         f"forecast: {'summer GP models' if p['period'] == 'final_test' else 'GP retrained before every month (walk-forward)'}", "",
         "## Site", "",
         f"Load: {up.get('rows')} rows, {up.get('start')} – {up.get('end')} ({up.get('days')} days), mean {_f(up.get('mean_kw'))} kW, "
         f"peak {_f(up.get('max_kw'))} kW, {up.get('gaps')} gaps ({_f(up.get('gap_hours'), 1)} h).",
         f"Sizes: battery {_f(s['battery_kwh'], 1)} kWh ({_f(s['battery_kwh'] / up['mean_kw'], 2)} h of mean load), power limit "
         f"{_f(s['power_kw'], 1)} kW, PV {_f(s['pv_kwp'], 1)} kWp, wind {_f(s['wind_kw'], 1)} kW (rated).",
         f"Replayed on the reference cell with factor {r['cell']['factor']:.4g} (cell battery {_f(r['cell']['E_capacity'], 1)} Wh); "
         "costs and energies below are scaled back to the site. Prices: Hungarian day-ahead (energy only, no network "
         "charges or taxes).", ""]
    if r["months"]:
        L += ["## Months", "", "| Month | Steps | σ scale | load from data (%) | mean price (€/MWh) |", "|---|---|---|---|---|"]
        for m in r["months"]:
            L.append(f"| {_mlab(m['month'])} | {m['steps']} | {_f(m['sigma_scale'], 3)} | {_f(m['load_direct_pct'], 0)} | {_f(m['price_mean_eur_mwh'], 1)} |")
        L.append("")
    L += ["## r_E and Γ", "", "| Config | Month | r_E | r_E past | Γ (reference b) | b re-sized | b for Γ=0.75 |", "|---|---|---|---|---|---|---|"]
    for x in r["gamma"]:
        L.append(f"| {x['config']} | {_mlab(x['month'])} | {_f(x['r_E'], 3)} | {_f(x['r_E_past'], 3)} | {_f(x['Gamma_ref_b'])} | "
                 f"{_f(x['b_sized'], 3)} | {_f(x['b_recommended'], 3)} |")
    L.append("")
    for X in p["configs"]:
        L += [f"### {CONFIG[X]}", "", r["verdict"][X], ""]
        rows = [t for t in r["totals"] if t["config"] == X]
        if rows:
            L += ["| Controller | Net cost (€) | Saving vs grid (€) | Saving (%) | vs DET (%) | Protection (h) | Extra grid (€) | Curtailed (%) | Band violation (pp·h) |",
                  "|---|---|---|---|---|---|---|---|---|"]
            for t in rows:
                L.append(f"| {LABEL.get(t['controller'], t['controller'])}{'' if t['complete'] else ' (incomplete)'} | {_eur(t['net_cost_eur'])} | "
                         f"{_eur(t.get('saving_eur'))} | {_f(t.get('saving_pct'), 1)} | {_f(t.get('vs_det_pct'), 1)} | {_f(t.get('protection_h'))} | "
                         f"{_f(t.get('extra_grid_eur'), 3)} | {_f(t.get('curtailed_pct'), 1)} | {_f(t.get('violation_pp_h'))} |")
            L.append("")
    gw = r.get("gamma_windows") or []
    if gw:
        L += ["## Γ check, hour by hour (DET-BO)", "",
              "A back-off b holds b·E above the lower SoC limit. An hour that starts inside the tightened band can only end "
              "at the lower limit if the forecast deficit reaching the battery within that hour is larger than this reserve. "
              "Every re-planning hour of the DET-BO runs is classified accordingly; the last three columns count the hours in "
              "which the BMS stopped the discharge.", "",
              "| Config | Controller | Hours | Lower-edge hours | Started inside, deficit within reserve | Started inside, deficit above reserve | "
              "Started with the reserve used |", "|---|---|---|---|---|---|---|"]
        for x in gw:
            L.append(f"| {x['config']} | {LABEL.get(x['controller'], x['controller'])} | {x['hours']} | {x['lower_edge_hours']} | "
                     f"{x['inside_within_lower_edge']} of {x['inside_within']} | {x['inside_above_lower_edge']} of {x['inside_above']} | "
                     f"{x['reserve_used_lower_edge']} of {x['reserve_used']} |")
        L += ["", "Reading: interventions in the first class would contradict the bound (in the reference winter test there were "
              "none). Interventions in the last class mean the reserve was used up over several hours, which a one-hour Γ "
              "does not see; a larger b or a higher Γ target margin helps there.", ""]
    ref = r.get("reference")
    if ref is not None:
        L += ["## Reference check", ""]
        if not ref["applies"]:
            L += [f"The input is the reference load profile, but the run is not the reference set-up ({ref['reason']}), "
                  "so it is not compared with the stored results.", ""]
        elif not ref["rows"]:
            L += [ref["note"], ""]
        else:
            n_ok = sum(x["ok"] for x in ref["rows"])
            L += [f"Reference load, sizes and settings: the run is compared with the stored results of the same set-up. "
                  f"**{n_ok} of {len(ref['rows'])} runs match** within the tolerances (net cost 0.1 %, protection ±0.5 h, "
                  "band violation ±0.05 pp·h, extra grid cost ±0.0005 €, curtailment ±0.2 %; cell units).", "",
                  "| Config | Month | Controller | Net cost stored / now (€, cell) | Protection stored / now (h) | Match |",
                  "|---|---|---|---|---|---|"]
            for x in ref["rows"]:
                k = {c["kpi"]: c for c in x["kpis"]}
                nc, pr = k.get("net cost (€)", {}), k.get("protection (h)", {})
                L.append(f"| {x['config']} | {_mlab(x['month'])} | {LABEL.get(x['controller'], x['controller'])} | "
                         f"{nc.get('stored', float('nan')):.6f} / {nc.get('rerun', float('nan')):.6f} | "
                         f"{pr.get('stored', float('nan')):.2f} / {pr.get('rerun', float('nan')):.2f} | {'✓' if x['ok'] else '✗'} |")
            L.append("")
    L += ["## Limits", "",
          "- Quick mode uses the reference controller weights, tuned for the reference cell; they were not re-tuned for this "
          "site, so the costs are a conservative estimate for each controller and the ranking can change after tuning.",
          "- Weather, prices and forecasts are those of the reference site (Hungary, 2025–2026); only the load and the sizes are yours.",
          ("- Days the load data do not cover were left out." if p.get("fill") == "data" else
           "- Where the upload does not cover a simulated day, the load comes from the same weekday and quarter-hour of the "
           "nearest day of the year in the upload; public holidays are not treated separately."),
          "- The battery is the reference cell model (efficiency 0.95, SoC band 0.1–0.9, BMS protection), scaled to the size given.",
          ""]
    return "\n".join(L)
