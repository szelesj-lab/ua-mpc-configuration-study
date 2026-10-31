"""Architecture of the simulation (closed-loop replay) and of the evaluation protocol."""
import os as _os  # paths: see experiments/extended/README.md
UA_WORK = _os.environ.get("UA_WORK", "work/extended")      # working tree (layout in README)
UA_CODE = _os.environ.get("UA_CODE", "code")               # controller code (Kairos)
UA_KAIROS = _os.environ.get("UA_KAIROS", "work")           # work copies made by experiments/make_work.sh
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

OUT = UA_WORK + "/rev2/work/figs/fig00_architecture"
INK, INK2, MUTED = "#0b0b0b", "#52514e", "#8a8984"
FILL = {"data": "#e8eef8", "model": "#e9f6f0", "ctrl": "#fdf1e3", "plant": "#eef0f2", "met": "#f3eefa"}
EDGE = {"data": "#2a78d6", "model": "#1baf7a", "ctrl": "#eb6834", "plant": "#52514e", "met": "#6250d6"}
plt.rcParams.update({"font.family": "DejaVu Sans", "mathtext.fontset": "dejavusans"})

fig = plt.figure(figsize=(7.2, 5.0))
ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, 144); ax.set_ylim(0, 100); ax.axis("off")

def box(x, y, w, h, title, lines=(), kind=None, fc="white", ec=INK2, tsize=7.2, lsize=6.3, lw=0.9, gap=3.6):
    if kind: fc, ec = FILL[kind], EDGE[kind]
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=1.2", fc=fc, ec=ec, lw=lw, zorder=2))
    ax.text(x + w / 2, y + h - 2.6, title, ha="center", va="center", fontsize=tsize, weight="bold", color=INK, zorder=3)
    for i, t in enumerate(lines):
        ax.text(x + w / 2, y + h - 6.2 - gap * i, t, ha="center", va="center", fontsize=lsize, color=INK2, zorder=3)

def arrow(pts, color=INK2, lw=0.9, ls="-", label=None, lpos=None, lsize=6.0, lcolor=None, ha="center"):
    """poly-line with an arrow head on the last segment"""
    for (x0, y0), (x1, y1) in zip(pts[:-2], pts[1:-1]):
        ax.plot([x0, x1], [y0, y1], color=color, lw=lw, ls=ls, zorder=4, solid_capstyle="butt")
    a = FancyArrowPatch(pts[-2], pts[-1], arrowstyle="-|>,head_length=3.0,head_width=1.7", color=color, lw=lw, linestyle=ls,
                        shrinkA=0, shrinkB=0, zorder=4)
    ax.add_patch(a)
    if label:
        ax.text(lpos[0], lpos[1], label, fontsize=lsize, ha=ha, va="center", color=lcolor or color, zorder=5,
                bbox=dict(fc="white", ec="none", pad=0.25))

def header(x, w, label, kind):
    ax.text(x + w / 2, 97.6, label, ha="center", va="center", fontsize=7.8, weight="bold", color=EDGE[kind])
    ax.plot([x + 1, x + w - 1], [95.6, 95.6], color=EDGE[kind], lw=1.3, solid_capstyle="round")

header(1, 27, "Data (recorded)", "data"); header(31, 31, "Forecast", "model"); header(65, 35, "Controller", "ctrl")
header(103, 23, "Plant", "plant"); header(128, 15, "Metrics", "met")

# data
box(1, 81, 27, 12, "Weather forecast", ("Open-Meteo, as issued",), "data")
box(1, 66, 27, 12, "Weather reanalysis", ("Open-Meteo",), "data")
box(1, 51, 27, 12, "Day-ahead price", ("ENTSO-E, 15 min",), "data")
box(1, 36, 27, 12, "Plant load", ("main meter, scaled",), "data")

# forecast
box(31, 77, 31, 16, "GP forecast, calibrated", ("residual GP per channel", r"$\mu_k$, $\sigma_k=\kappa\,\sigma^{\mathrm{GP}}_k$, AR(1)"), "model")
box(31, 60, 31, 13, "Physical PV and wind", (r"realised power $R_k$",), "model")

# controller
box(65, 36, 35, 57, "Receding-horizon MPC", ("24-h horizon, re-plan every hour", "configuration A / B / C"), "ctrl")
chips = [("DET", "nominal SoC band"), ("DET-BO", r"constant back-off $b$"), ("UA-MPC", r"margin grows with $\sigma_k$")]
for i, (n, t) in enumerate(chips):
    y = 67 - 9.5 * i
    ax.add_patch(FancyBboxPatch((67.3, y), 30.4, 7.4, boxstyle="round,pad=0,rounding_size=1.0", fc="white", ec=EDGE["ctrl"], lw=0.8, zorder=3))
    ax.text(68.8, y + 3.7, n, fontsize=6.8, weight="bold", va="center", ha="left", color=INK, zorder=4)
    ax.text(80.0, y + 3.7, t, fontsize=5.9, va="center", ha="left", color=INK2, zorder=4)

# plant
box(103, 36, 23, 57, "Cell model", ("configuration A / B / C", "72 Wh, band 10–90 %", "", "applies the", "first hour"), "plant")
ax.add_patch(FancyBboxPatch((104.5, 39.5), 20, 13, boxstyle="round,pad=0,rounding_size=1.0", fc="white", ec=EDGE["plant"], lw=0.8, zorder=3))
ax.text(114.5, 48.0, "BMS plant", fontsize=6.6, weight="bold", ha="center", va="center", zorder=4)
ax.text(114.5, 44.6, "(primary)", fontsize=5.8, ha="center", va="center", color=INK2, zorder=4)
ax.text(114.5, 41.4, "physical: secondary", fontsize=5.4, ha="center", va="center", color=INK2, zorder=4)

# metrics
box(128, 36, 15, 57, "Per replay", ("net cost", "", "protection", "hours", "", "extra grid", "cost", "", "curtailment"), "met", lsize=6.0, gap=3.6)

# arrows
arrow([(28, 87), (31, 87)], EDGE["data"])                                   # forecast weather -> GP
arrow([(28, 70), (29.0, 70), (29.0, 63.5), (31, 63.5)], EDGE["data"])                                 # reanalysis -> physical
arrow([(28, 84), (30.0, 84), (30.0, 69.5), (31, 69.5)], EDGE["data"])           # forecast weather -> physical (physical forecast)
arrow([(46.5, 73), (46.5, 77)], EDGE["model"])                              # physical -> GP
arrow([(62, 85), (65, 85)], EDGE["model"], label=r"$\mu_k,\sigma_k$", lpos=(63.5, 88.3))
arrow([(28, 57), (65, 57)], EDGE["data"], label="price", lpos=(46.5, 57))
arrow([(28, 42), (65, 42)], EDGE["data"], label="load (perfect forecast)", lpos=(46.5, 42))
arrow([(100, 80), (103, 80)], EDGE["ctrl"], label="plan", lpos=(101.5, 83.0))
arrow([(103, 62), (100, 62)], INK, lw=1.1, label=r"SoC $x_0$", lpos=(101.5, 65.0), lcolor=INK)
arrow([(46.5, 60), (46.5, 33), (114.5, 33), (114.5, 36)], EDGE["model"], label=r"realised $R_k$ and $D_k$", lpos=(82.5, 33))
ax.plot([14.5, 14.5, 46.5], [36, 33, 33], color=EDGE["data"], lw=0.9, zorder=4); ax.text(30.5, 33, r"$D_k$", fontsize=5.0, ha="center", va="center", color=EDGE["data"], zorder=5, bbox=dict(fc="white", ec="none", pad=0.25))
arrow([(126, 64), (128, 64)], EDGE["plant"])

# protocol band
ax.add_patch(FancyBboxPatch((1, 1.0), 142, 27.5, boxstyle="round,pad=0,rounding_size=1.6", fc="#f6f5f1", ec="none", zorder=0))
ax.text(3, 25.6, "Evaluation protocol, all with the same closed-loop replay", fontsize=7.4, weight="bold", ha="left", va="center", color=INK)
steps = [("Tuning", ["ParEGO,", "30 evaluations"]),
         ("Development", ["22 days,", "decision map"]),
         ("Selection rule", ["time-stamped", "before the test"]),
         ("Final test", ["27 days,", "run once"]),
         ("Winter test", ["181 days, GP", "re-trained monthly"])]
x = 3.0
for i, (t, ls) in enumerate(steps):
    w = 21.0
    box(x, 3.5, w, 18.5, t, ls, fc="white", ec=MUTED, tsize=7.0, lsize=6.2, lw=0.7, gap=3.8)
    if i < len(steps) - 1:
        arrow([(x + w, 12.7), (x + w + 2.0, 12.7)], MUTED)
    x += w + 2.0
box(119.5, 3.5, 22.5, 18.5, r"Pre-deployment $r_\mathrm{E}$", ("forecast archive", "+ configuration", r"$\Gamma=r_\mathrm{E}(x_{\max}-x_{\min})/b$"), ec=EDGE["ctrl"], tsize=7.0, lsize=6.0, lw=0.9, gap=3.8)
arrow([(130.75, 22), (130.75, 30.0), (92, 30.0), (92, 36)], EDGE["ctrl"], ls=(0, (2.4, 1.5)),
      label="needed? how much?", lpos=(111, 30.0))

fig.savefig(OUT + ".pdf"); fig.savefig(OUT + ".png", dpi=250)
print("saved")
