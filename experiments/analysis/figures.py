"""Every figure of the manuscript, one figure per file, white background.

    python figures_v4.py        # writes paper/figures/figNN_*.pdf and .png

Inputs: the data inventory (inv/), the industrial-load study (ind/results_{A,B,C}), the walk-forward winter test
(rw/results), the protection split and map table of make_numbers3.py (run it first), and the second load case
(results/paper of the code repository). Nothing is drawn by hand.
"""
import json
import os
import sys
import warnings

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

warnings.filterwarnings("ignore")
PKG = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
RES = os.environ.get("RESULTS", os.path.join(PKG, "results"))          # shipped results (input of the analysis)
K = os.environ.get("KAIROS_CODE", os.path.join(PKG, "code"))          # controller code
OUTD = os.environ.get("OUT", os.path.join(PKG, "out"))                 # generated tables and figures
H = INV = f"{RES}/analysis"
P2 = f"{RES}/production_schedule"                 # second load case
FIG = f"{OUTD}/figures"
os.makedirs(FIG, exist_ok=True)
sys.path.insert(0, f"{K}/src")
TZ = "Europe/Budapest"

WHITE, INK, INK2, GRID = "#ffffff", "#111111", "#4a4a4a", "#dddddd"
COL = {"det": "#7f7f7f", "det_bo": "#e0662b", "ua": "#1a9e6e", "grid_only": "#2a6fdb"}
MK = {"det": "s", "det_bo": "^", "ua": "o"}
LAB = {"det": "DET", "det_bo": "DET-BO", "ua": "UA-MPC", "grid_only": "Grid only"}
PV, WIND, LOAD = "#e0662b", "#2a6fdb", "#4a4a4a"
COLW, WIDE = 3.5, 7.2                               # column and page width (in)

plt.rcParams.update({
    "font.size": 8, "font.family": "DejaVu Sans", "axes.edgecolor": INK2, "axes.labelcolor": INK,
    "xtick.color": INK2, "ytick.color": INK2, "axes.spines.top": False, "axes.spines.right": False,
    "figure.facecolor": WHITE, "axes.facecolor": WHITE, "savefig.facecolor": WHITE,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6, "axes.axisbelow": True,
    "legend.frameon": False, "legend.fontsize": 7})


def save(fig, name):
    for ext in ("pdf", "png"):
        fig.savefig(f"{FIG}/{name}.{ext}", dpi=300, bbox_inches="tight", facecolor=WHITE)
    plt.close(fig)
    print("written", name)


split = pd.read_csv(f"{H}/protection_split.csv")
mp = pd.read_csv(f"{H}/map_all.csv")

# ------------------------------------------------------------------ 1. coverage of the data and periods
T = pd.Timestamp
fig, ax = plt.subplots(figsize=(WIDE, 2.4))
bars = [
    ("Plant main meter (1 h)", [(T("2025-10-01"), T("2026-09-30"), LOAD, "")]),
    ("Day-ahead price HU (15 min)", [(T("2025-10-01"), T("2026-10-22"), LOAD, "")]),
    ("Weather forecast and reanalysis (1 h)", [(T("2025-06-01"), T("2026-10-16"), LOAD, "")]),
    ("GP training, summer models", [(T("2025-06-01"), T("2026-06-16"), "#8a6bd1", "")]),
    ("Sub-meters (not used)", [(T("2025-02-01"), T("2025-03-01"), GRID, ""), (T("2025-08-01"), T("2025-09-01"), GRID, ""),
                               (T("2025-10-01"), T("2025-11-01"), GRID, "")]),
    ("Summer protocol", [(T("2026-06-16"), T("2026-07-07"), "#c3d5f4", "cal"), (T("2026-07-07"), T("2026-08-11"), "#93b4ec", "tune"),
                         (T("2026-08-11"), T("2026-09-02"), "#5f8fe0", "dev"), (T("2026-09-02"), T("2026-09-29"), "#2a6fdb", "test")]),
    ("Winter walk-forward test", [(T(f"{y}-{m:02d}-01"), T(f"{y2}-{m2:02d}-01"), "#1a9e6e" if k % 2 == 0 else "#0e6b49", "")
                                  for k, (y, m, y2, m2) in enumerate([(2025, 10, 2025, 11), (2025, 11, 2025, 12),
                                                                      (2025, 12, 2026, 1), (2026, 1, 2026, 2),
                                                                      (2026, 2, 2026, 3), (2026, 3, 2026, 4)])]),
]
for i, (lab, segs) in enumerate(bars):
    y = len(bars) - 1 - i
    for s0, s1, c, txt in segs:
        x0, x1 = mdates.date2num(s0), mdates.date2num(s1)
        ax.barh(y, x1 - x0, left=x0, height=0.6, color=c, edgecolor=WHITE, lw=1)
        if txt:
            ax.text((x0 + x1) / 2, y, txt, ha="center", va="center", fontsize=6,
                    color="white" if txt in ("dev", "test") else INK)
ax.set_yticks(range(len(bars)))
ax.set_yticklabels([b[0] for b in bars][::-1])
ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=[1, 4, 7, 10]))
ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
ax.set_xlim(mdates.date2num(T("2025-01-20")), mdates.date2num(T("2026-11-01")))
ax.grid(axis="y", visible=False)
ax.tick_params(axis="y", length=0)
save(fig, "fig01_coverage")

# ------------------------------------------------------------------ 2. plant load profile
h = pd.read_csv(f"{INV}/hourly_pu.csv", index_col=0, parse_dates=True)["pu"].tz_convert(TZ)
fig, ax = plt.subplots(figsize=(COLW, 2.4))
for mask, lab, c in [(h.index.dayofweek < 5, "Monday–Friday", "#e0662b"), (h.index.dayofweek >= 5, "Saturday–Sunday", "#2a6fdb")]:
    q = h[mask].groupby(h[mask].index.hour).quantile([0.1, 0.5, 0.9]).unstack()
    ax.fill_between(q.index, q[0.1], q[0.9], color=c, alpha=0.15, lw=0)
    ax.plot(q.index, q[0.5], color=c, lw=1.8, label=f"{lab}, median")
ax.set_xlim(0, 23)
ax.set_xticks([0, 6, 12, 18, 23])
ax.set_xlabel("Local hour")
ax.set_ylabel("Load / annual mean load")
ax.legend(loc="upper right")
save(fig, "fig02_load_profile")

# ------------------------------------------------------------------ 3. monthly energy of the cell
mon = pd.read_csv(f"{INV}/monthly_energy.csv", index_col=0)
mon.index = pd.to_datetime(mon.index, utc=True).tz_convert(TZ)
fig, ax = plt.subplots(figsize=(COLW, 2.4))
x = np.arange(len(mon))
w = 0.38
ax.bar(x - w / 2, mon["load"], w, color=LOAD, label="Load", edgecolor=WHITE, lw=0.5)
ax.bar(x + w / 2, mon["pv"], w, color=PV, label="PV", edgecolor=WHITE, lw=0.5)
ax.bar(x + w / 2, mon["wind"], w, bottom=mon["pv"], color=WIND, label="Wind", edgecolor=WHITE, lw=0.5)
ax.set_xticks(x)
ax.set_xticklabels([d.strftime("%b") for d in mon.index], fontsize=6.5)
ax.set_ylabel("Energy of the cell (kWh per month)")
ax.set_xlabel("October 2025 – September 2026")
ax.set_ylim(0, 12)
ax.legend(ncol=3, loc="upper center")
ax.grid(axis="x", visible=False)
save(fig, "fig03_monthly_energy")

# ------------------------------------------------------------------ 4. battery configurations (schematic)
fig, axs = plt.subplots(1, 3, figsize=(WIDE, 2.0))
nodes = {"ren": (0.18, 0.78, "PV + wind"), "load": (0.82, 0.78, "Load"), "bat": (0.18, 0.18, "Battery"),
         "grid": (0.82, 0.18, "Grid")}
flows = {"A": [("ren", "bat"), ("bat", "load"), ("grid", "load")],
         "B": [("ren", "load"), ("ren", "bat"), ("bat", "load"), ("grid", "load"), ("grid", "bat")],
         "C": [("ren", "load"), ("ren", "bat"), ("bat", "load"), ("grid", "load"), ("grid", "bat")]}
err = {"A": ("ren", "bat", "error to SoC"), "B": ("ren", "bat", "error (±) to SoC"), "C": ("ren", "grid", "error to grid")}
titles = {"A": "A: renewable buffer", "B": "B: self-consumption", "C": "C: set-point"}
for ax, X in zip(axs, "ABC"):
    ax.set_xlim(0, 1)
    ax.set_ylim(-0.02, 1)
    ax.axis("off")
    ax.set_title(titles[X], fontsize=8, color=INK, loc="center")
    for k, (xx, yy, t) in nodes.items():
        ax.add_patch(FancyBboxPatch((xx - 0.15, yy - 0.09), 0.3, 0.18, boxstyle="round,pad=0.01,rounding_size=0.03",
                                    fc=WHITE, ec=INK2, lw=0.9))
        ax.text(xx, yy, t, ha="center", va="center", fontsize=7, color=INK)

    def edge(a, b, off=0.0):
        (x1, y1, _), (x2, y2, _) = nodes[a], nodes[b]
        dx, dy = x2 - x1, y2 - y1
        n = np.hypot(dx, dy)
        sx, sy = (0.16 * dx / n if dx else 0), (0.1 * dy / n if dy else 0)
        return (x1 + sx + off, y1 + sy), (x2 - sx + off, y2 - sy)

    for a, b in flows[X]:
        p0, p1 = edge(a, b, 0.03 if (a, b) == ("ren", "bat") else 0.0)
        ax.add_patch(FancyArrowPatch(p0, p1, arrowstyle="-|>", mutation_scale=8, color=INK, lw=0.9))
    a, b, t = err[X]
    p0, p1 = edge(a, b, -0.04 if X != "C" else 0.0)
    if X == "C":
        p0, p1 = (p0[0] + 0.03, p0[1] - 0.02), (p1[0] - 0.03, p1[1] + 0.02)
    ax.add_patch(FancyArrowPatch(p0, p1, arrowstyle="-|>", mutation_scale=8, color="#b3261e", lw=1.1, ls="--"))
    mx, my = (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2
    ax.text(mx - 0.05 if X != "C" else mx + 0.08, my, t, fontsize=6.3, color="#b3261e",
            ha="right" if X != "C" else "left", va="center")
save(fig, "fig04_configurations")

# ------------------------------------------------------------------ 5./6. final test: cost and protection
CONFN = {"A": "A\nrenewable buffer", "B": "B\nself-consumption", "C": "C\nset-point"}
fin = {}
for X in "ABC":
    s = pd.read_csv(f"{RES}/summer/{X}/final_test/summary.csv")
    fin[X] = s[s.plant == "bms"].set_index("variant")


def grouped(ax, value):
    x0, ticks = 0, []
    for X in "ABC":
        ctrls = [c for c in ("det", "det_bo", "ua") if c in fin[X].index]
        for i, c in enumerate(ctrls):
            value(ax, X, c, x0 + i)
        ticks.append(x0 + (len(ctrls) - 1) / 2)
        x0 += len(ctrls) + 1
    ax.set_xticks(ticks)
    ax.set_xticklabels([CONFN[X] for X in "ABC"], fontsize=7)
    ax.tick_params(axis="x", length=0)
    ax.grid(axis="x", visible=False)


fig, ax = plt.subplots(figsize=(COLW, 2.5))
x0 = 0
for X in "ABC":
    n = sum(c in fin[X].index for c in ("det", "det_bo", "ua"))
    ax.hlines(fin[X].loc["grid_only", "net_cost_eur"], x0 - 0.45, x0 + n - 0.55, color=COL["grid_only"], lw=1.4,
              ls=(0, (3, 2)), label="Grid only" if X == "A" else None)
    x0 += n + 1
grouped(ax, lambda a, X, c, x: a.bar(x, fin[X].loc[c, "net_cost_eur"], 0.8, color=COL[c], edgecolor=WHITE,
                                     label=LAB[c] if X == "B" else None))
ax.set_ylim(0.6, 1.3)
ax.set_ylabel("Net cost (€, 27 days)")
ax.legend(ncol=4, loc="upper center", fontsize=6.5, columnspacing=1)
save(fig, "fig05_final_cost")

fig, ax = plt.subplots(figsize=(COLW, 2.5))


def prot_bar(a, X, c, x):
    p = split[(split.study == "final") & (split.config == X) & (split.variant == c)].iloc[0]
    a.bar(x, p.prot_lo_h, 0.8, color=COL[c], edgecolor=WHITE, label=LAB[c] if X == "B" else None)
    a.bar(x, p.prot_up_h, 0.8, bottom=p.prot_lo_h, color=WHITE, edgecolor=COL[c], hatch="//////", lw=0.8)


grouped(ax, prot_bar)
ax.bar([0], [0], color=WHITE, edgecolor=INK2, hatch="//////", label="upper edge (hatched)")
ax.set_ylabel("BMS protection (h, 27 days)")
ax.legend(ncol=2, loc="upper left", fontsize=6.5)
ax.set_ylim(0, 14)
save(fig, "fig06_final_protection")

# ------------------------------------------------------------------ 7.-9. decision maps, one per configuration
for n, X in zip((7, 8, 9), "ABC"):
    g = mp[mp.config == X].sort_values("r_E_gen")
    fig, ax = plt.subplots(figsize=(COLW, 2.5))
    xs = g.r_E_gen.to_numpy()
    if X == "C":
        xs = g.r_E.to_numpy()          # C: generalised ratio is 0; plotted against the configuration-blind ratio
    for xv, (_, r) in zip(xs, g.iterrows()):
        ax.plot([xv, xv], [r.bo_prot_h, r.ua_prot_h], color=GRID, lw=1.4, zorder=1)
    bo = "det" if X == "C" else "det_bo"
    ax.scatter(xs, g.bo_prot_h, s=28, marker=MK[bo], color=COL[bo], edgecolor=WHITE, lw=0.8, zorder=3,
               label=LAB[bo] if X == "C" else "DET-BO (constant back-off)")
    ax.scatter(xs, g.ua_prot_h, s=28, marker=MK["ua"], color=COL["ua"], edgecolor=WHITE, lw=0.8, zorder=3,
               label=LAB["ua"])
    if X == "B":
        ax.axvspan(0.0235, 0.0465, color="#eeeeee", lw=0, zorder=0)
        ax.text(0.035, 22, "tie | difference", ha="center", fontsize=6.5, color=INK2, rotation=90)
    ax.set_xlim(0, 0.1)
    ax.set_ylim(-1, 40)
    ax.set_ylabel("BMS protection (h, 22 days)")
    if X == "C":
        ax.set_xlabel(r"configuration-blind ratio $r_E^{\mathrm{blind}}$ (generalised $r_E$ = 0 in every cell)")
    else:
        ax.set_xlabel(r"generalised ratio $r_E$ (95 % deficit / usable capacity)")
    ax.legend(loc="upper left")
    save(fig, f"fig{n:02d}_map_{X}")

# ------------------------------------------------------------------ 10./11. winter months
wm = pd.read_csv(f"{H}/winter_monthly.csv")
en = pd.read_csv(f"{INV}/energy.csv")
share = en[en.period.str.startswith("winter")].ren_share_pct.to_numpy()
months = ["Oct", "Nov", "Dec", "Jan", "Feb", "Mar"]
x = np.arange(6)
fig, ax = plt.subplots(figsize=(COLW, 2.5))
for X, ls in (("B", "-"), ("C", (0, (3, 2)))):
    g0 = wm[(wm.config == X) & (wm.controller == "grid_only")].set_index("month").cost
    for c in ("det", "det_bo", "ua"):
        g = wm[(wm.config == X) & (wm.controller == c)].set_index("month")
        if g.empty:
            continue
        ax.plot(x, 100 * (1 - g.cost / g0).to_numpy(), color=COL[c], ls=ls, lw=1.5, marker=MK[c], ms=4,
                mec=WHITE, label=f"{X}, {LAB[c]}")
ax.set_xticks(x)
ax.set_xticklabels([f"{m}\n{v:.0f} %" for m, v in zip(months, share)], fontsize=7)
ax.set_xlabel("Month (renewable share of the load)", fontsize=7)
ax.set_ylabel("Saving against grid only (%)")
ax.set_ylim(0, 28)
ax.legend(ncol=2, loc="upper center", fontsize=6.3)
save(fig, "fig10_winter_saving")

fig, ax = plt.subplots(figsize=(COLW, 2.5))
for c, dx in (("det", -0.27), ("det_bo", 0.0), ("ua", 0.27)):
    g = wm[(wm.config == "B") & (wm.controller == c)].set_index("month")
    ax.bar(x + dx, g.prot_h.to_numpy(), 0.26, color=COL[c], edgecolor=WHITE, lw=0.5, label=LAB[c])
ax.set_xticks(x)
ax.set_xticklabels(months)
ax.set_ylabel("BMS protection, configuration B (h)")
ax.grid(axis="x", visible=False)
ax.legend(loc="upper left")
save(fig, "fig11_winter_protection")

# ------------------------------------------------------------------ 12.-14. second load case
reg2 = json.load(open(f"{P2}/final_test/preregistration.json"))["selected"]["primary_threshold_pp_h"]
m2 = pd.read_csv(f"{P2}/decision_map_recovery/map.csv")
bo_pts = json.load(open(f"{P2}/tuning/det_bo_pareto.json"))
b2 = next(p for p in bo_pts["points"] if p["trial"] == reg2["det_bo"]["trial"])["overrides"]["mpc"]["soc_backoff"]
fig, ax = plt.subplots(figsize=(COLW, 2.6))
for _, r in m2.iterrows():
    ax.plot([r.r_E, r.r_E], [max(r.ua_viol, 1e-3), max(r.bo_viol, 1e-3)], color=GRID, lw=1.4, zorder=1)
ax.scatter(m2.r_E, m2.bo_viol.clip(lower=1e-3), marker=MK["det_bo"], s=28, color=COL["det_bo"], edgecolor=WHITE,
           lw=0.8, label="DET-BO (constant back-off)", zorder=3)
ax.scatter(m2.r_E, m2.ua_viol.clip(lower=1e-3), marker=MK["ua"], s=28, color=COL["ua"], edgecolor=WHITE, lw=0.8,
           label="UA-MPC", zorder=3)
ax.axvline(b2, color=INK2, ls="--", lw=0.9)
ax.text(b2 + 0.002, 0.007, f"tuned back-off b = {b2:.3f}", fontsize=6.5, color=INK2)
ax.set_yscale("log")
ax.set_ylim(5e-3, 60)
ax.set_xlabel(r"$r_E$ (95 % deficit / usable capacity)")
ax.set_ylabel("SoC-band violation (pp·h, 22 days)")
ax.legend(loc="upper left")
save(fig, "fig12_case2_map")

va = pd.read_csv(f"{P2}/variability/variability.csv").sort_values("r_E")
sh = pd.read_csv(f"{P2}/variability/sigma_shapes.csv").set_index("shape")
fig, ax = plt.subplots(figsize=(WIDE * 0.7, 2.6))
lab = [f"{int(r.cap_wh)} Wh, s = {r.error_scale:g}\n{int(r.replan_steps) * 15 // 60}-h re-plan" for _, r in va.iterrows()]
series = [("bo_viol", "Constant back-off", COL["det_bo"]),
          ("constant_viol", f"UA-MPC, constant σ (κσ = {sh.loc['constant', 'kappa_sigma']:.2f})", "#a8dcc6"),
          ("ua_viol", f"UA-MPC, real σ (κσ = {sh.loc['real', 'kappa_sigma']:.2f})", COL["ua"]),
          ("amplified_viol", f"UA-MPC, doubled variation (κσ = {sh.loc['amplified', 'kappa_sigma']:.2f})", "#0b5e40")]
x = np.arange(len(va))
w = 0.2
for i, (c, name, colr) in enumerate(series):
    ax.bar(x + (i - 1.5) * w, va[c], w - 0.02, color=colr, label=name, edgecolor=WHITE, lw=0.5)
ax.set_yscale("log")
ax.set_ylim(0.01, 200)
ax.set_ylabel("SoC-band violation (pp·h, log)")
ax.set_xticks(x)
ax.set_xticklabels(lab, fontsize=6.5)
ax.grid(axis="x", visible=False)
ax.legend(loc="upper left", fontsize=6.3, ncol=2)
save(fig, "fig13_case2_variability")

ce = pd.read_csv(f"{P2}/capacity_economics/capacity_economics.csv")
c = ce[ce.error_scale == 1.0].sort_values("cap_wh")
fig, ax = plt.subplots(figsize=(COLW, 2.4))
ax.plot(c.cap_wh, c.ua_saving_eur, color=COL["ua"], lw=1.8, marker="o", ms=5, mec=WHITE)
for _, r in c.iterrows():
    ax.annotate(f"{100 * r.ua_share_of_largest_saving:.0f} % of the 144-Wh saving\n{r.ua_saving_per_wh_meur:.1f} m€/Wh",
                (r.cap_wh, r.ua_saving_eur), textcoords="offset points", xytext=(6, -22) if r.cap_wh < 144 else (-6, -26),
                ha="left" if r.cap_wh < 144 else "right", fontsize=6,
                color=INK2)
ax.set_xticks(c.cap_wh)
ax.set_xlim(15, 165)
ax.set_ylim(0.15, 0.42)
ax.set_xlabel("Battery capacity (Wh)")
ax.set_ylabel("UA-MPC saving vs grid only (€, 22 days)")
save(fig, "fig14_case2_capacity")
