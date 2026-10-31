"""Column-width version of the Gamma figure, from the data file written by figures_rev3.py."""
import os as _os  # paths: see experiments/revision/README.md
UA_WORK = _os.environ.get("UA_WORK", "work/revision")      # revision working tree (layout in README)
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt, matplotlib.ticker, numpy as np, pandas as pd
OUT = UA_WORK + "/rev2/work/figs"
WHITE, INK, INK2, GRID = "#ffffff", "#111111", "#4a4a4a", "#dddddd"
plt.rcParams.update({"font.size": 8, "font.family": "DejaVu Sans", "axes.edgecolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
    "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
    "axes.axisbelow": True, "legend.frameon": False, "legend.fontsize": 6.5})
d = pd.read_csv(UA_WORK + "/rev3/figs/fig16_data.csv")
fig, ax = plt.subplots(figsize=(3.5, 3.0))
sty = {"Decision map (development)": ("s", "#7f7f7f", "Decision map"), "New cells (pre-registered)": ("o", "#1a9e6e", "New cells"),
       "Error correlation (pre-registered)": ("D", "#2a6fdb", "Error correlation"), "Back-off sweep (pre-registered)": ("^", "#e0662b", "Back-off sweep")}
for k, (m, col, lab) in sty.items():
    g = d[d.set == k]
    ax.scatter(g.Gamma, g.diff_h, marker=m, s=22, facecolor=np.where(g.label == "difference", col, WHITE), edgecolor=col, lw=1.0, label=lab, zorder=3)
ax.axvline(1.0, color=INK, lw=0.8, ls="--"); ax.axhline(0, color=INK2, lw=0.6)
ax.set_xscale("log"); ax.set_xticks([0.25, 0.5, 1, 2, 4]); ax.set_xticklabels(["0.25", "0.5", "1", "2", "4"])
ax.xaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
ax.set_xlabel(r"$\Gamma = r_\mathrm{E}\,(x_{\max}-x_{\min})/b$"); ax.set_ylabel("DET-BO minus UA-MPC protection (h)")
ax.legend(loc="upper left")
for ext in ("pdf", "png"):
    fig.savefig(f"{OUT}/fig16_gamma_col.{ext}", dpi=300, bbox_inches="tight", facecolor=WHITE)
print("ok")
