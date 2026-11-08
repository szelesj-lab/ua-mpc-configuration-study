"""Figures of a run, drawn after every run in the style and layout of the article's figures (PNG and PDF).

Each figure is the article's figure of the same name, computed from this run instead of the stored results:

    fig02_load_profile        load profile: weekday and weekend median per local hour, 10-90 % band (article Fig. 2)
    fig03_monthly_energy      load, PV and wind energy per simulated month (Fig. 3)
    fig05_net_cost            net cost per configuration and controller, grid only dashed (Fig. 6)
    fig06_protection          BMS protection hours by SoC edge: lower edge filled, upper edge hatched (Fig. 7)
    fig10_monthly_saving      saving against grid only per month (Fig. 11; runs over several months)
    fig11_monthly_protection  BMS protection per month and controller (Fig. 12; runs over several months)
    fig15a_summary_cost       UA-MPC minus each reference: net cost (%), 95 % paired daily bootstrap interval (Fig. 13)
    fig15b_summary_protection UA-MPC minus each reference: protection (h), same interval (Fig. 14)
    fig16_gamma               DET-BO minus UA-MPC protection per month against Γ (Fig. 10)
    rE_gamma                  r_E and Γ per month and configuration (a table in the article; screening runs too)
    soc_<X>                   state of charge of every controller over the run (no article counterpart)
"""
from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("MPLBACKEND", "Agg")
import matplotlib

matplotlib.use("Agg")
import matplotlib.dates
import matplotlib.pyplot as plt
import matplotlib.ticker
import numpy as np
import pandas as pd

TZ = "Europe/Budapest"
WHITE, INK, INK2, GRID = "#ffffff", "#111111", "#4a4a4a", "#dddddd"
COL = {"det": "#7f7f7f", "det_bo": "#e0662b", "det_bo_sized": "#8a6bd1", "det_bo_scenario": "#c4a000", "det_bo_robust": "#8c564b",
       "ua": "#1a9e6e", "grid_only": "#2a6fdb"}
MK = {"det": "s", "det_bo": "^", "det_bo_sized": "v", "det_bo_scenario": "D", "det_bo_robust": "P", "ua": "o"}
LAB = {"det": "DET", "det_bo": "DET-BO", "det_bo_sized": "DET-BO re-sized", "det_bo_scenario": "DET-BO scenario",
       "det_bo_robust": "DET-BO robust", "ua": "UA-MPC", "grid_only": "Grid only"}
PV, WIND, LOAD = "#e0662b", "#2a6fdb", "#4a4a4a"
CONFN = {"A": "A\nrenewable buffer", "B": "B\nself-consumption", "C": "C\nset-point"}
COLW, WIDE = 3.5, 7.2
ORDER = ["det", "det_bo", "det_bo_sized", "det_bo_scenario", "det_bo_robust", "ua"]

# the article's figures: (file, article figure, title)
CAPTIONS = {
    "fig02_load_profile": ("Fig. 2", "Load profile"),
    "fig03_monthly_energy": ("Fig. 3", "Monthly energy"),
    "fig05_net_cost": ("Fig. 6", "Net cost"),
    "fig06_protection": ("Fig. 7", "Protection hours by SoC edge"),
    "fig10_monthly_saving": ("Fig. 11", "Monthly saving against grid only"),
    "fig11_monthly_protection": ("Fig. 12", "Monthly protection"),
    "fig15a_summary_cost": ("Fig. 13", "UA-MPC minus reference: net cost"),
    "fig15b_summary_protection": ("Fig. 14", "UA-MPC minus reference: protection"),
    "fig16_gamma": ("Fig. 10", "Protection difference against Γ"),
}

STYLE = {"font.size": 8, "font.family": "DejaVu Sans", "axes.edgecolor": INK2, "axes.labelcolor": INK,
         "xtick.color": INK2, "ytick.color": INK2, "axes.spines.top": False, "axes.spines.right": False,
         "figure.facecolor": WHITE, "axes.facecolor": WHITE, "savefig.facecolor": WHITE, "axes.grid": True,
         "grid.color": GRID, "grid.linewidth": 0.6, "axes.axisbelow": True, "legend.frameon": False, "legend.fontsize": 7,
         "text.hinting": "no_hinting"}


def _mlab(m):
    return "Sep 2026" if m == "final_test" else pd.Period(m, freq="M").strftime("%b %Y")


def _steps(d: Path) -> dict:
    """{(X, ctrl): DataFrame of every step over the run (cell units)}, months in order."""
    out = {}
    runs = d / "runs"
    if not runs.exists():
        return out
    for X in sorted(p.name for p in runs.iterdir() if p.is_dir()):
        for c in ["grid_only"] + ORDER:
            parts = sorted(p for p in (runs / X).glob(f"{c}_*.csv")
                           if not p.name.endswith("_mpc.csv") and p.stem[len(c) + 1:][:1] in "2f")
            if parts:
                frames = []
                for p in parts:
                    df = pd.read_csv(p)
                    df["month"] = p.stem[len(c) + 1:]
                    frames.append(df)
                df = pd.concat(frames, ignore_index=True)
                df["t"] = pd.to_datetime(df["t"], utc=True)
                out[(X, c)] = df
    return out


def _daily(df: pd.DataFrame, f: float) -> pd.DataFrame:
    """Per local day: net cost (site €) and protection hours."""
    day = df["t"].dt.tz_convert(TZ).dt.date
    prot = ((df["cut_discharge_w"] > 1e-3) | (df["cut_charge_w"] > 1e-3)) * 0.25
    return pd.DataFrame({"cost": (df["grid_eur"] + df["wear_eur"]) / f, "prot": prot}).groupby(day).sum()


def _boot(diff: np.ndarray, n=4000, seed=1) -> tuple[float, float]:
    """95 % interval of the total of paired daily differences (days resampled with replacement)."""
    if len(diff) < 2:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    tot = diff[rng.integers(0, len(diff), (n, len(diff)))].sum(axis=1)
    return float(np.percentile(tot, 2.5)), float(np.percentile(tot, 97.5))


def draw(d: Path, rep: dict, out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    for old in list(out.glob("*.png")) + list(out.glob("*.pdf")):
        old.unlink()
    plt.rcParams.update(STYLE)
    f = rep["cell"]["factor"]
    tot = pd.DataFrame(rep.get("totals") or [])
    mon = pd.DataFrame(rep.get("monthly") or [])
    gam = pd.DataFrame(rep.get("gamma") or [])
    months = [m["month"] for m in rep.get("months") or []]
    files: list[Path] = []


    def save(fig, name):
        for ext in ("png", "pdf"):
            fig.savefig(out / f"{name}.{ext}", dpi=200 if ext == "png" else 300, bbox_inches="tight", facecolor=WHITE)
        plt.close(fig)
        files.append(out / f"{name}.png")

    # Fig. 2: load profile (the uploaded load over the simulated period, or the whole upload)
    try:
        load = pd.read_pickle(d / "load.pkl").dropna()
        h = (load / load.mean()).tz_convert(TZ)
        fig, ax = plt.subplots(figsize=(COLW, 2.4))
        for mask, lab, c in [(h.index.dayofweek < 5, "Monday–Friday", "#e0662b"), (h.index.dayofweek >= 5, "Saturday–Sunday", "#2a6fdb")]:
            g = h[mask]
            if len(g):
                q = g.groupby(g.index.hour).quantile([0.1, 0.5, 0.9]).unstack()
                ax.fill_between(q.index, q[0.1], q[0.9], color=c, alpha=0.15, lw=0)
                ax.plot(q.index, q[0.5], color=c, lw=1.8, label=f"{lab}, median")
        ax.set_xlim(0, 23); ax.set_xticks([0, 6, 12, 18, 23])
        ax.set_xlabel("Local hour"); ax.set_ylabel("Load / mean load")
        ax.legend(ncol=2, loc="lower center", bbox_to_anchor=(0.5, 1.0), fontsize=6.5)
        save(fig, "fig02_load_profile")
    except Exception:  # noqa: BLE001  (a figure must not stop the export)
        plt.close("all")

    # Fig. 3: monthly energy of the site (load, PV, wind) over the simulated months
    rows = []
    for m in months:
        p = d / "data" / f"{m}.pkl"
        info = next((x for x in rep["months"] if x["month"] == m), None)
        if p.exists() and info:
            x = pd.read_pickle(p)
            x = x[(x.index >= pd.Timestamp(info["test_start"])) & (x.index < pd.Timestamp(info["test_end"]))]
            k = 0.25 / f / 1000   # cell W per 15 min -> site kWh
            rows.append({"month": m, "load": x.demand_w.sum() * k, "pv": x.solar_real.sum() * k, "wind": x.wind_real.sum() * k})
    if rows:
        e = pd.DataFrame(rows)
        fig, ax = plt.subplots(figsize=(max(COLW, 0.45 * len(e) + 1.5), 2.4))
        xx = np.arange(len(e)); w = 0.38
        ax.bar(xx - w / 2, e["load"], w, color=LOAD, label="Load", edgecolor=WHITE, lw=0.5)
        ax.bar(xx + w / 2, e["pv"], w, color=PV, label="PV", edgecolor=WHITE, lw=0.5)
        ax.bar(xx + w / 2, e["wind"], w, bottom=e["pv"], color=WIND, label="Wind", edgecolor=WHITE, lw=0.5)
        ax.set_xticks(xx); ax.set_xticklabels([_mlab(m) for m in e["month"]], fontsize=6.5)
        ax.set_ylabel("Energy (kWh per month)"); ax.legend(ncol=3, loc="upper center", bbox_to_anchor=(0.5, 1.15))
        ax.grid(axis="x", visible=False)
        save(fig, "fig03_monthly_energy")

    if len(tot):
        cfgs = list(dict.fromkeys(tot["config"]))

        def grouped(ax, value):
            x0, ticks = 0, []
            for X in cfgs:
                ctrls = [c for c in ORDER if len(tot[(tot.config == X) & (tot.controller == c)])]
                for i, c in enumerate(ctrls):
                    value(ax, X, c, x0 + i)
                ticks.append(x0 + (len(ctrls) - 1) / 2)
                x0 += len(ctrls) + 1
            ax.set_xticks(ticks); ax.set_xticklabels([CONFN[X] for X in cfgs], fontsize=7)
            ax.tick_params(axis="x", length=0); ax.grid(axis="x", visible=False)

        val = lambda X, c, k: float(tot[(tot.config == X) & (tot.controller == c)][k].iloc[0])
        seen = set()

        def lab(c):
            if c in seen:
                return None
            seen.add(c); return LAB[c]
        # Fig. 6: net cost
        fig, ax = plt.subplots(figsize=(max(COLW, 1.4 * len(cfgs) + 0.8), 2.5))
        x0 = 0
        for X in cfgs:
            n = sum(len(tot[(tot.config == X) & (tot.controller == c)]) > 0 for c in ORDER)
            if len(tot[(tot.config == X) & (tot.controller == "grid_only")]):
                ax.hlines(val(X, "grid_only", "net_cost_eur"), x0 - 0.45, x0 + n - 0.55, color=COL["grid_only"], lw=1.4,
                          ls=(0, (3, 2)), label=lab("grid_only"))
            x0 += n + 1
        grouped(ax, lambda a, X, c, x: a.bar(x, val(X, c, "net_cost_eur"), 0.8, color=COL[c], edgecolor=WHITE, label=lab(c)))
        vals = tot["net_cost_eur"].to_numpy(dtype=float)
        lo, hi = float(np.nanmin(vals)), float(np.nanmax(vals))
        ax.set_ylim(max(0.0, lo - 0.6 * (hi - lo) - 0.02 * hi), hi + 0.25 * (hi - lo) + 0.02 * hi)
        ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}" if hi >= 100 else f"{v:,.3g}"))
        ax.set_ylabel(f"Net cost (€, {sum(x['steps'] for x in rep['months']) / 96:.0f} days)" if rep.get("months") else "Net cost (€)")
        ax.legend(ncol=5, loc="upper center", fontsize=6.5, columnspacing=1, bbox_to_anchor=(0.5, 1.16))
        save(fig, "fig05_net_cost")

    stp = _steps(d)
    # Fig. 7: protection by SoC edge
    if stp:
        cfgs = list(dict.fromkeys(X for X, _ in stp))
        fig, ax = plt.subplots(figsize=(max(COLW, 1.4 * len(cfgs) + 0.8), 2.5))
        x0, ticks, seen = 0, [], set()
        for X in cfgs:
            ctrls = [c for c in ORDER if (X, c) in stp]
            for i, c in enumerate(ctrls):
                df = stp[(X, c)]
                lo_h = float((df["cut_discharge_w"] > 1e-3).sum() / 4)
                up_h = float(((df["cut_charge_w"] > 1e-3) & ~(df["cut_discharge_w"] > 1e-3)).sum() / 4)
                ax.bar(x0 + i, lo_h, 0.8, color=COL[c], edgecolor=WHITE, label=LAB[c] if c not in seen else None)
                ax.bar(x0 + i, up_h, 0.8, bottom=lo_h, color=WHITE, edgecolor=COL[c], hatch="//////", lw=0.8)
                seen.add(c)
            ticks.append(x0 + (len(ctrls) - 1) / 2); x0 += len(ctrls) + 1
        ax.set_xticks(ticks); ax.set_xticklabels([CONFN[X] for X in cfgs], fontsize=7)
        ax.tick_params(axis="x", length=0); ax.grid(axis="x", visible=False)
        ax.bar([0], [0], color=WHITE, edgecolor=INK2, hatch="//////", label="upper SoC edge (hatched)")
        ax.set_ylabel("BMS protection (h)")
        ax.legend(ncol=5, loc="lower center", bbox_to_anchor=(0.5, 1.0), fontsize=6.3, columnspacing=1)
        ax.set_ylim(0, None)
        save(fig, "fig06_protection")

    # Fig. 11 and 12: per month (only for runs over several months)
    if len(mon) and mon["month"].nunique() > 1:
        ms = [m for m in months if m in set(mon["month"])]
        xx = np.arange(len(ms))
        share = []
        for m in ms:
            r = next((x for x in rep["months"] if x["month"] == m), None)
            share.append(100 * r["renewable_kwh_cell"] / r["demand_kwh_cell"] if r and r.get("demand_kwh_cell") else np.nan)
        fig, ax = plt.subplots(figsize=(max(COLW, 0.55 * len(ms) + 1.2), 2.5))
        styles = ["-", (0, (3, 2)), (0, (1, 1.5))]
        for X, ls in zip(dict.fromkeys(mon["config"]), styles):
            g0 = mon[(mon.config == X) & (mon.controller == "grid_only")].set_index("month").reindex(ms).net_cost_eur
            for c in ORDER:
                g = mon[(mon.config == X) & (mon.controller == c)].set_index("month").reindex(ms)
                if g.net_cost_eur.notna().any():
                    ax.plot(xx, 100 * (1 - g.net_cost_eur / g0).to_numpy(), color=COL[c], ls=ls, lw=1.5, marker=MK[c], ms=4,
                            mec=WHITE, label=f"{X}, {LAB[c]}")
        ax.set_xticks(xx); ax.set_xticklabels([f"{_mlab(m)}\n{v:.0f} %" for m, v in zip(ms, share)], fontsize=6.5)
        ax.set_xlabel("Month (renewable share of the load)", fontsize=7); ax.set_ylabel("Saving against grid only (%)")
        ax.legend(ncol=2, loc="upper center", fontsize=6.3, bbox_to_anchor=(0.5, 1.22))
        save(fig, "fig10_monthly_saving")
        cfgs = [X for X in dict.fromkeys(mon["config"]) if X != "C"] or list(dict.fromkeys(mon["config"]))
        fig, axs = plt.subplots(1, len(cfgs), figsize=(max(COLW, 0.55 * len(ms) + 1.2) * len(cfgs), 2.5), squeeze=False)
        for ax, X in zip(axs[0], cfgs):
            ctrls = [c for c in ORDER if len(mon[(mon.config == X) & (mon.controller == c)])]
            w = 0.8 / max(len(ctrls), 1)
            for j, c in enumerate(ctrls):
                g = mon[(mon.config == X) & (mon.controller == c)].set_index("month").reindex(ms)
                ax.bar(xx - 0.4 + w * (j + 0.5), g.protection_h.to_numpy(), w * 0.95, color=COL[c], edgecolor=WHITE, lw=0.5, label=LAB[c])
            ax.set_xticks(xx); ax.set_xticklabels([_mlab(m) for m in ms], fontsize=6.5)
            ax.set_ylabel(f"BMS protection, configuration {X} (h)"); ax.grid(axis="x", visible=False); ax.legend(loc="upper left")
        save(fig, "fig11_monthly_protection")

    # Fig. 13 and 14: UA-MPC minus each reference, paired daily bootstrap
    rows = []
    for X in dict.fromkeys(X for X, _ in stp):
        if (X, "ua") not in stp:
            continue
        du = _daily(stp[(X, "ua")], f)
        for c in ("det", "det_bo", "det_bo_sized", "det_bo_scenario", "det_bo_robust"):
            if (X, c) not in stp:
                continue
            dr = _daily(stp[(X, c)], f)
            j = du.index.intersection(dr.index)
            dc = (du.loc[j, "cost"] - dr.loc[j, "cost"]).to_numpy(); dp = (du.loc[j, "prot"] - dr.loc[j, "prot"]).to_numpy()
            base = float(dr.loc[j, "cost"].sum())
            clo, chi = _boot(dc); plo, phi = _boot(dp)
            rows.append({"row": f"{X}: vs {LAB[c]}", "cost_pct": 100 * dc.sum() / base, "cost_lo": 100 * clo / base,
                         "cost_hi": 100 * chi / base, "prot_h": dp.sum(), "prot_lo": plo, "prot_hi": phi, "days": len(j)})
    if rows:
        s = pd.DataFrame(rows)
        s.to_csv(out / "summary_ua_minus_reference.csv", index=False)
        y = np.arange(len(s))[::-1]
        for col, xl, name in (("cost", "UA-MPC minus reference: net cost (%)", "fig15a_summary_cost"),
                              ("prot", "UA-MPC minus reference: BMS protection (h)", "fig15b_summary_protection")):
            m = s["cost_pct"] if col == "cost" else s["prot_h"]
            fig, ax = plt.subplots(figsize=(COLW, max(1.4, 0.32 * len(s) + 0.9)))
            for yi, a, lo_, hi_ in zip(y, m, s[f"{col}_lo"], s[f"{col}_hi"]):
                sig = lo_ > 0 or hi_ < 0
                ax.plot([lo_, hi_], [yi, yi], color=INK2, lw=1.2)
                ax.plot(a, yi, "o", ms=4.5, color=COL["ua"] if sig else WHITE, mec=COL["ua"], mew=1.2)
            ax.axvline(0, color=INK, lw=0.8); ax.set_xlabel(xl)
            ax.set_yticks(y); ax.set_yticklabels(s["row"]); ax.set_ylim(-0.7, len(s) - 0.3)
            save(fig, name)

    # Fig. 10: DET-BO minus UA-MPC protection per month against Γ (reference back-off)
    pts = []
    if len(mon) and len(gam):
        for _, g in gam.iterrows():
            if g.get("Gamma_ref_b") is None or not np.isfinite(g.get("Gamma_ref_b") or np.nan):
                continue
            a = mon[(mon.config == g.config) & (mon.month == g.month)].set_index("controller")
            if {"det_bo", "ua"} <= set(a.index):
                pts.append((g.config, g.month, float(g.Gamma_ref_b), float(a.loc["det_bo", "protection_h"] - a.loc["ua", "protection_h"])))
    if pts:
        fig, ax = plt.subplots(figsize=(COLW, 3.0))
        for X, mk, col in (("A", "s", "#7f7f7f"), ("B", "o", "#1a9e6e")):
            p = [q for q in pts if q[0] == X]
            if p:
                ax.scatter([q[2] for q in p], [q[3] for q in p], marker=mk, s=24, facecolor=col, edgecolor=WHITE, lw=0.8,
                           label=f"configuration {X}", zorder=3)
                for q in p:
                    ax.annotate(_mlab(q[1]), (q[2], q[3]), fontsize=5.5, color=INK2, xytext=(3, 2), textcoords="offset points")
        ax.axvline(1.0, color=INK, lw=0.8, ls="--"); ax.axhline(0, color=INK2, lw=0.6)
        ax.axvspan(0.75, 1.25, color="#eeeeee", lw=0, zorder=0)
        ax.margins(y=0.2); ax.set_xscale("log"); ax.set_xticks([0.25, 0.5, 1, 2, 4]); ax.set_xticklabels(["0.25", "0.5", "1", "2", "4"])
        ax.xaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
        gx = [q[2] for q in pts]
        ax.set_xlim(min(0.2, min(gx) / 1.3), max(5, max(gx) * 1.3))
        ax.set_xlabel(r"$\Gamma = r_\mathrm{E}\,(x_{\max}-x_{\min})/b$ (reference back-off)")
        ax.set_ylabel("DET-BO minus UA-MPC protection (h)")
        ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.0), ncol=2, fontsize=6.5)
        save(fig, "fig16_gamma")

    # r_E and Γ per month (screening runs too; the article gives them as a table)
    if len(gam):
        ms = [m for m in months if m in set(gam["month"])] or list(dict.fromkeys(gam["month"]))
        xx = np.arange(len(ms))
        fig, (a1, a2) = plt.subplots(1, 2, figsize=(WIDE, 2.4))
        for X, mk, col in (("A", "s", "#7f7f7f"), ("B", "o", "#1a9e6e"), ("C", "D", "#2a6fdb")):
            g = gam[gam.config == X].set_index("month").reindex(ms)
            if g["r_E"].notna().any():
                a1.plot(xx, g["r_E"].to_numpy(dtype=float), marker=mk, color=col, lw=1.3, ms=4, mec=WHITE, label=f"configuration {X}")
            gg = pd.to_numeric(g["Gamma_ref_b"], errors="coerce")
            if gg.notna().any():
                a2.plot(xx, gg.to_numpy(), marker=mk, color=col, lw=1.3, ms=4, mec=WHITE, label=f"configuration {X}")
        a2.axhspan(0.75, 1.25, color="#eeeeee", lw=0, zorder=0); a2.axhline(1.0, color=INK, lw=0.8, ls="--")
        for a, yl in ((a1, r"$r_\mathrm{E}$ (window error / usable capacity)"), (a2, r"$\Gamma$ with the reference back-off")):
            a.set_xticks(xx); a.set_xticklabels([_mlab(m) for m in ms], fontsize=6.5); a.set_ylabel(yl); a.set_ylim(0, None)
            if a.get_legend_handles_labels()[0]:
                a.legend(loc="best", fontsize=6.5)
        save(fig, "rE_gamma")

    # SoC over the run (no counterpart in the article)
    for X in dict.fromkeys(X for X, _ in stp):
        fig, ax = plt.subplots(figsize=(WIDE, 2.3)); n = 0
        for c in ORDER:
            if (X, c) in stp:
                df = stp[(X, c)]
                ax.plot(df["t"].dt.tz_convert(TZ), df["soc"], lw=0.6, color=COL[c], label=LAB[c], alpha=0.9); n += 1
        if n:
            ax.axhline(0.1, color=INK2, lw=0.7, ls="--"); ax.axhline(0.9, color=INK2, lw=0.7, ls="--")
            ax.set_ylim(0, 1); ax.set_ylabel(f"SoC, configuration {X}")
            loc = matplotlib.dates.AutoDateLocator(tz=TZ, maxticks=10)
            ax.xaxis.set_major_locator(loc); ax.xaxis.set_major_formatter(matplotlib.dates.ConciseDateFormatter(loc, tz=TZ))
            ax.legend(ncol=4, loc="lower left", bbox_to_anchor=(0, 1.0), fontsize=6.5)
            save(fig, f"soc_{X}")
        else:
            plt.close(fig)
    (out / "captions.json").write_text(json.dumps({k: list(v) for k, v in CAPTIONS.items()}, ensure_ascii=False))
    return files
