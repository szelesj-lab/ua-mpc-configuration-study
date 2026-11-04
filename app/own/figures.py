"""Figures of a run (PNG, 150 dpi), drawn after every run from its report and its step files.

    cost.png          net cost per controller and configuration, with the saving against grid only
    protection.png    hours of BMS protection per controller and configuration
    monthly_cost.png  net cost per month and controller (one panel per configuration)
    energy.png        grid import and curtailed renewable energy per controller and configuration
    re_gamma.png      r_E per month and configuration, and Γ with the reference back-off
    soc_<X>.png       SoC of every controller over the run (one figure per configuration)
"""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("MPLBACKEND", "Agg")
import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

ORDER = ["grid_only", "det", "det_bo", "det_bo_sized", "ua"]
LABEL = {"grid_only": "Grid only", "det": "DET", "det_bo": "DET-BO", "det_bo_sized": "DET-BO re-sized", "ua": "UA-MPC"}
COLOR = {"grid_only": "#8a8a8a", "det": "#2f6db5", "det_bo": "#d9822b", "det_bo_sized": "#a05a2c", "ua": "#2a9d6f"}
CFG = {"A": "A · renewable buffer", "B": "B · self-consumption", "C": "C · set-point"}
plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True,
                     "grid.alpha": 0.25, "axes.axisbelow": True, "legend.frameon": False, "figure.dpi": 150})


def _mlab(m):
    return "Sep 2026" if m == "final_test" else m


def _grouped(ax, tot: pd.DataFrame, col: str, fmt: str):
    cfgs = list(dict.fromkeys(tot["config"]))
    ctrls = [c for c in ORDER if c in set(tot["controller"])]
    w = 0.8 / max(len(ctrls), 1)
    for j, c in enumerate(ctrls):
        xs, ys = [], []
        for i, X in enumerate(cfgs):
            r = tot[(tot.config == X) & (tot.controller == c)]
            if len(r) and pd.notna(r[col].iloc[0]):
                xs.append(i - 0.4 + w * (j + 0.5)); ys.append(float(r[col].iloc[0]))
        bars = ax.bar(xs, ys, w * 0.92, color=COLOR[c], label=LABEL[c])
        for b, y in zip(bars, ys):
            ax.annotate(fmt.format(y), (b.get_x() + b.get_width() / 2, y), ha="center", va="bottom", fontsize=6.5,
                        xytext=(0, 1), textcoords="offset points")
    ax.set_xticks(range(len(cfgs)), [CFG.get(X, X) for X in cfgs])
    ax.legend(ncol=min(len(ctrls), 5), loc="upper center", bbox_to_anchor=(0.5, -0.12), fontsize=8)


def _fmt_eur(v):
    a = abs(v)
    return "{:,.0f}" if a >= 1000 else "{:,.2f}" if a >= 10 else "{:,.4f}"


def draw(d: Path, rep: dict, out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.png"):
        old.unlink()
    files = []
    tot = pd.DataFrame(rep.get("totals") or [])
    mon = pd.DataFrame(rep.get("monthly") or [])
    gam = pd.DataFrame(rep.get("gamma") or [])

    def save(fig, name):
        p = out / name
        fig.savefig(p, bbox_inches="tight", facecolor="white"); plt.close(fig); files.append(p)

    if len(tot):
        fmt = _fmt_eur(float(tot["net_cost_eur"].abs().max()))
        fig, ax = plt.subplots(figsize=(7, 3.6))
        _grouped(ax, tot, "net_cost_eur", fmt)
        ax.set_ylabel("Net cost (€)"); ax.set_title("Net cost over the run", loc="left", fontsize=10)
        save(fig, "cost.png")
        fig, ax = plt.subplots(figsize=(7, 3.6))
        _grouped(ax, tot[tot.controller != "grid_only"], "protection_h", "{:.1f}")
        ax.set_ylabel("BMS protection (h)"); ax.set_title("Hours in which the BMS cut charge or discharge", loc="left", fontsize=10)
        save(fig, "protection.png")
        fig, axs = plt.subplots(1, 2, figsize=(9, 3.6))
        fe = _fmt_eur(1)
        for ax, col, ttl in ((axs[0], "grid_kwh", "Grid import (kWh)"), (axs[1], "curtailed_kwh", "Curtailed renewable (kWh)")):
            _grouped(ax, tot, col, "{:,.0f}" if float(tot[col].abs().max()) >= 100 else fe)
            ax.set_title(ttl, loc="left", fontsize=10)
        save(fig, "energy.png")
    if len(mon) and mon["month"].nunique() > 1:
        cfgs = list(dict.fromkeys(mon["config"]))
        fig, axs = plt.subplots(len(cfgs), 1, figsize=(8, 2.6 * len(cfgs)), squeeze=False, sharex=True)
        months = sorted(mon["month"].unique())
        for ax, X in zip(axs[:, 0], cfgs):
            for c in [c for c in ORDER if c in set(mon["controller"])]:
                m = mon[(mon.config == X) & (mon.controller == c)].set_index("month").reindex(months)
                if m["net_cost_eur"].notna().any():
                    ax.plot(range(len(months)), m["net_cost_eur"], marker="o", ms=3, lw=1.4, color=COLOR[c], label=LABEL[c])
            ax.set_title(CFG.get(X, X), loc="left", fontsize=9); ax.set_ylabel("Net cost (€)")
        axs[-1, 0].set_xticks(range(len(months)), [_mlab(m) for m in months], rotation=45, ha="right")
        axs[0, 0].legend(ncol=5, loc="lower left", bbox_to_anchor=(0, 1.12), fontsize=8)
        save(fig, "monthly_cost.png")
    if len(gam):
        months = list(dict.fromkeys(gam["month"]))
        fig, axs = plt.subplots(1, 2, figsize=(9, 3.4))
        for X in dict.fromkeys(gam["config"]):
            g = gam[gam.config == X].set_index("month").reindex(months)
            axs[0].plot(range(len(months)), g["r_E"], marker="o", ms=3, label=CFG.get(X, X))
            if g["Gamma_ref_b"].notna().any():
                axs[1].plot(range(len(months)), g["Gamma_ref_b"], marker="o", ms=3, label=CFG.get(X, X))
        axs[0].set_title("r_E per month", loc="left", fontsize=10); axs[0].set_ylabel("r_E")
        axs[1].set_title("Γ with the reference back-off", loc="left", fontsize=10); axs[1].set_ylabel("Γ")
        axs[1].axhspan(0.75, 1.25, color="#d9822b", alpha=0.12, lw=0); axs[1].axhline(1, color="#555", lw=0.8, ls="--")
        for ax in axs:
            ax.set_xticks(range(len(months)), [_mlab(m) for m in months], rotation=45 if len(months) > 3 else 0,
                          ha="right" if len(months) > 3 else "center")
            if ax.get_legend_handles_labels()[0]:
                ax.legend(fontsize=7.5)
        save(fig, "re_gamma.png")
    # SoC traces from the step files
    runs = d / "runs"
    for X in sorted(p.name for p in runs.iterdir()) if runs.exists() else []:
        fig, ax = plt.subplots(figsize=(9, 3.2)); n = 0
        for c in [c for c in ORDER if c != "grid_only"]:
            parts = sorted(p for p in (runs / X).glob(f"{c}_*.csv") if not p.name.endswith("_mpc.csv")
                           and p.stem[len(c) + 1:][:1] in "2f")
            if not parts:
                continue
            s = pd.concat([pd.read_csv(p, usecols=["t", "soc"]) for p in parts])
            t = pd.to_datetime(s["t"], utc=True).dt.tz_convert("Europe/Budapest")
            ax.plot(t, s["soc"], lw=0.7, color=COLOR[c], label=LABEL[c], alpha=0.9); n += 1
        if n:
            ax.axhline(0.1, color="#555", lw=0.8, ls="--"); ax.axhline(0.9, color="#555", lw=0.8, ls="--")
            ax.set_ylim(0, 1); ax.set_ylabel("SoC"); ax.set_title(f"State of charge, {CFG.get(X, X)}", loc="left", fontsize=10)
            ax.legend(ncol=4, loc="lower left", bbox_to_anchor=(0, 1.08), fontsize=8)
            fig.autofmt_xdate()
            save(fig, f"soc_{X}.png")
        else:
            plt.close(fig)
    return files
