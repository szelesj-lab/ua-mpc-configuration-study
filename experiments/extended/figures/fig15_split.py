"""Split the summary figure into two column-width figures: net cost and BMS protection of UA-MPC minus each reference."""
import os as _os  # paths: see experiments/extended/README.md
UA_WORK = _os.environ.get("UA_WORK", "work/extended")      # working tree (layout in README)
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt, numpy as np, pandas as pd
OUT = UA_WORK + "/rev2/work/figs"
WHITE, INK, INK2, GRID, ACC = "#ffffff", "#111111", "#4a4a4a", "#dddddd", "#1a9e6e"
plt.rcParams.update({"font.size": 8, "font.family": "DejaVu Sans", "axes.edgecolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
    "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6, "axes.axisbelow": True})
d = pd.read_csv(UA_WORK + "/rev3/figs/fig15_data.csv")
y = np.arange(len(d))[::-1]
for col, xl, name in (("cost", "UA-MPC minus reference: net cost (%)", "fig15a_summary_cost"),
                      ("prot", "UA-MPC minus reference: BMS protection (h)", "fig15b_summary_protection")):
    m, lo, hi = (d[f"{col}_pct"] if col == "cost" else d[f"{col}_h"]), d[f"{col}_lo"], d[f"{col}_hi"]
    fig, ax = plt.subplots(figsize=(3.5, 2.6))
    for yi, a, l, h in zip(y, m, lo, hi):
        sig = l > 0 or h < 0
        ax.plot([l, h], [yi, yi], color=INK2, lw=1.2)
        ax.plot(a, yi, "o", ms=4.5, color=ACC if sig else WHITE, mec=ACC, mew=1.2)
    ax.axvline(0, color=INK, lw=0.8); ax.set_xlabel(xl)
    ax.set_yticks(y); ax.set_yticklabels(d["row"])
    for ext in ("pdf", "png"):
        fig.savefig(f"{OUT}/{name}.{ext}", dpi=300, bbox_inches="tight", facecolor=WHITE)
    plt.close(fig); print("written", name)
