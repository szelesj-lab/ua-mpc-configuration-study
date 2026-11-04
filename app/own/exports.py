"""Files of a finished run: tables as CSV, every simulation step and every MPC solve as CSV, figures, one zip.

Written into <jobdir>/export/ by app/own/report.write. Powers are in kW and money in € of the site (scaled back from
the cell with the run's factor); SoC as a fraction of the capacity.

    steps.csv        one row per 15-min step, configuration, controller (the closed-loop result)
    mpc_solves.csv   one row per MPC solve: what the controller saw (forecast, limits), what it planned for the first
                     step and the SoC it planned for, and the solver result; the objective and its terms are in the
                     solver's own units (cell)
    totals.csv, monthly.csv, gamma.csv   the tables of the report
    figures/         the figures in the style of the article's figures, PNG and PDF (see app/own/figures.py),
                     and summary_ua_minus_reference.csv (the numbers of the UA-MPC minus reference figures)
    results.zip      all of the above with report.md and report.json
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pandas as pd

STEP_POWER = ("demand_w", "grid_w", "discharge_w", "charge_w", "renewable_w", "curtailed_w", "cut_discharge_w",
              "cut_charge_w")
STEP_EUR = ("grid_eur", "wear_eur")
MPC_POWER = ("renewable_forecast_w", "forecast_sd_w", "demand_w", "plan_grid_w", "plan_discharge_w", "plan_charge_w",
             "plan_curtailed_w", "sigma_eff_w")
TZ = "Europe/Budapest"


def _parts(d: Path, suffix: str):
    for f in sorted((d / "runs").glob(f"*/*{suffix}")) if (d / "runs").exists() else []:
        name = f.name[: -len(suffix)]
        if suffix == ".csv" and name.endswith("_mpc"):
            continue
        X = f.parent.name
        ctrl, month = name.rsplit("_", 1) if not name.endswith("final_test") else (name[: -len("_final_test")], "final_test")
        yield X, ctrl, month, f


def steps(d: Path, f: float) -> pd.DataFrame:
    out = []
    for X, ctrl, month, p in _parts(d, ".csv"):
        df = pd.read_csv(p)
        t = pd.to_datetime(df["t"], utc=True)
        o = pd.DataFrame({"time_utc": t.dt.strftime("%Y-%m-%d %H:%M"),
                          "time_local": t.dt.tz_convert(TZ).dt.strftime("%Y-%m-%d %H:%M%z"),
                          "config": X, "controller": ctrl, "month": month, "soc": df["soc"].round(5)})
        for c in STEP_POWER:
            if c in df:
                o[c.replace("_w", "_kw")] = (df[c] / f / 1000).round(4)
        o["price_eur_mwh"] = (df["price_eur_wh"] * 1e6).round(3)
        for c in STEP_EUR:
            o[c] = (df[c] / f).round(6)
        out.append(o)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def solves(d: Path, f: float) -> pd.DataFrame:
    out = []
    for X, ctrl, month, p in _parts(d, "_mpc.csv"):
        df = pd.read_csv(p)
        t = pd.to_datetime(df.pop("t"), utc=True)
        o = pd.DataFrame({"time_utc": t.dt.strftime("%Y-%m-%d %H:%M"),
                          "time_local": t.dt.tz_convert(TZ).dt.strftime("%Y-%m-%d %H:%M%z"),
                          "config": X, "controller": ctrl, "month": month})
        for c in df.columns:
            o[c.replace("_w", "_kw") if c in MPC_POWER else c] = (df[c] / f / 1000).round(4) if c in MPC_POWER else df[c]
        out.append(o)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def write(d: Path, rep: dict) -> list[str]:
    e = d / "export"
    e.mkdir(exist_ok=True)
    f = rep["cell"]["factor"]
    written = []
    tables = {"totals.csv": pd.DataFrame(rep.get("totals") or []), "monthly.csv": pd.DataFrame(rep.get("monthly") or []),
              "gamma.csv": pd.DataFrame(rep.get("gamma") or [])}
    for name, df in tables.items():
        if len(df):
            df.to_csv(e / name, index=False); written.append(name)
    for name, fn in (("steps.csv", steps), ("mpc_solves.csv", solves)):
        df = fn(d, f)
        if len(df):
            df.to_csv(e / name, index=False); written.append(name)
    from app.own.figures import draw
    figs = draw(d, rep, e / "figures")
    with zipfile.ZipFile(e / "results.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for name in ("report.md", "report.json"):
            if (d / name).exists():
                z.write(d / name, name)
        for name in written:
            z.write(e / name, name)
        for p in sorted((e / "figures").iterdir()):
            z.write(p, f"figures/{p.name}")
    extra = ["figures/summary_ua_minus_reference.csv"] if (e / "figures" / "summary_ua_minus_reference.csv").exists() else []
    return written + [f"figures/{p.name}" for p in figs] + extra + ["results.zip"]
