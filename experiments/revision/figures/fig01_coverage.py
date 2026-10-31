"""Fig. 1, simplified: data coverage and experiment periods (unused sub-meters dropped, larger labels)."""
import os as _os  # paths: see experiments/revision/README.md
UA_WORK = _os.environ.get("UA_WORK", "work/revision")      # revision working tree (layout in README)
UA_CODE = _os.environ.get("UA_CODE", "code")               # controller code (Kairos)
UA_KAIROS = _os.environ.get("UA_KAIROS", "work")           # work copies made by experiments/make_work.sh
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt, matplotlib.dates as mdates, pandas as pd
OUT = UA_WORK + "/rev2/work/figs/fig01_coverage"
INK, INK2, GRID, LOAD = "#111111", "#4a4a4a", "#dddddd", "#4a4a4a"
plt.rcParams.update({"font.size": 9, "font.family": "DejaVu Sans", "axes.edgecolor": INK2, "xtick.color": INK2, "ytick.color": INK,
                     "axes.spines.top": False, "axes.spines.right": False, "axes.spines.left": False})
T = pd.Timestamp
bars = [
    ("Plant load (1 h)", [(T("2025-10-01"), T("2026-09-30"), LOAD, "")]),
    ("Day-ahead price (15 min)", [(T("2025-10-01"), T("2026-10-22"), LOAD, "")]),
    ("Weather forecast and reanalysis (1 h)", [(T("2025-06-01"), T("2026-10-16"), LOAD, "")]),
    ("GP training, summer models", [(T("2025-06-01"), T("2026-06-16"), "#8a6bd1", "")]),
    ("Summer protocol", [(T("2026-06-16"), T("2026-07-07"), "#c3d5f4", "cal"), (T("2026-07-07"), T("2026-08-11"), "#93b4ec", "tune"),
                         (T("2026-08-11"), T("2026-09-02"), "#5f8fe0", "dev"), (T("2026-09-02"), T("2026-09-29"), "#2a6fdb", "test")]),
    ("Winter walk-forward test", [(T(f"{y}-{m:02d}-01"), T(f"{y2}-{m2:02d}-01"), "#1a9e6e", "")
                                  for k, (y, m, y2, m2) in enumerate([(2025, 10, 2025, 11), (2025, 11, 2025, 12), (2025, 12, 2026, 1),
                                                                      (2026, 1, 2026, 2), (2026, 2, 2026, 3), (2026, 3, 2026, 4)])]),
]
fig, ax = plt.subplots(figsize=(7.2, 2.2))
for i, (lab, segs) in enumerate(bars):
    y = len(bars) - 1 - i
    for s0, s1, c, txt in segs:
        x0, x1 = mdates.date2num(s0), mdates.date2num(s1)
        ax.barh(y, x1 - x0, left=x0, height=0.62, color=c, edgecolor="white", lw=1)
        if txt:
            ax.text((x0 + x1) / 2, y, txt, ha="center", va="center", fontsize=7.5, color="white" if txt in ("dev", "test") else INK)
ax.text(mdates.date2num(T("2026-01-01")), 0, "Oct 2025 – Mar 2026", ha="center", va="center", fontsize=7.5, color="white")
ax.set_yticks(range(len(bars))); ax.set_yticklabels([b[0] for b in bars][::-1])
ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=[1, 4, 7, 10])); ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
ax.set_xlim(mdates.date2num(T("2025-05-15")), mdates.date2num(T("2026-11-01")))
ax.tick_params(axis="y", length=0); ax.grid(axis="x", color=GRID, lw=0.6); ax.set_axisbelow(True)
fig.savefig(OUT + ".pdf", bbox_inches="tight"); fig.savefig(OUT + ".png", dpi=200, bbox_inches="tight"); print("ok")
