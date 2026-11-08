"""Decision guide: from the forecast archive to the choice of uncertainty handling, with the evidence of the study.

The numbers in the boxes are read from the result files (results/winter, results/extended), as in the text."""
import os as _os  # paths: see experiments/extended/README.md
UA_WORK = _os.environ.get("UA_WORK", "work/extended")      # working tree (layout in README)
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
RES = Path(_os.environ.get("RESULTS", ROOT / "results")); EXT = RES / "extended"
OUT = UA_WORK + "/rev2/work/figs/fig17_decision_guide"
INK, INK2, MUTED = "#0b0b0b", "#52514e", "#8a8984"
FILL = {"in": "#e8eef8", "q": "#f4f4f2", "ok": "#e9f6f0", "mid": "#fbf3dc", "bad": "#fdece3", "opt": "#ffffff"}
EDGE = {"in": "#2a78d6", "q": "#52514e", "ok": "#1baf7a", "mid": "#c4a000", "bad": "#eb6834", "opt": "#eb6834"}
plt.rcParams.update({"font.family": "DejaVu Sans", "mathtext.fontset": "dejavusans", "text.hinting": "no_hinting"})


def jl(p):
    return json.loads(Path(p).read_text())


def tot(files, key):
    return sum(jl(f)["metrics"][key] for f in files)


ua = [RES / f"winter/B/ua_m{k}.json" for k in range(2, 7)]
t1 = [EXT / f"preregistered/t1/det_bo_sized_m{k}.json" for k in range(2, 7)]
sc = [EXT / f"preregistered/t3/det_bo_scenario_m{k}.json" for k in range(2, 7)]
rb = [EXT / f"preregistered/t3/det_bo_robust_m{k}.json" for k in range(2, 7)]
c_ua, p_ua = tot(ua, "net_cost_eur"), tot(ua, "protection_h")
x = lambda f: 100 * (tot(f, "net_cost_eur") / c_ua - 1)  # noqa: E731
g = jl(EXT / "ratio/gamma_check.json")

fig = plt.figure(figsize=(7.2, 3.55))
ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, 144); ax.set_ylim(0, 71); ax.axis("off")


def box(x0, y0, w, h, title, lines=(), kind="q", tsize=7.0, lsize=6.0, gap=3.3, bold=True):
    ax.add_patch(FancyBboxPatch((x0, y0), w, h, boxstyle="round,pad=0,rounding_size=1.2", fc=FILL[kind], ec=EDGE[kind], lw=0.9, zorder=2))
    ax.text(x0 + w / 2, y0 + h - 2.7, title, ha="center", va="center", fontsize=tsize, weight="bold" if bold else "normal", color=INK, zorder=3)
    for i, t in enumerate(lines):
        ax.text(x0 + w / 2, y0 + h - 6.4 - gap * i, t, ha="center", va="center", fontsize=lsize, color=INK2, zorder=3)


def arrow(pts, color=INK2, label=None, lpos=None, lsize=6.0):
    for (a0, b0), (a1, b1) in zip(pts[:-2], pts[1:-1]):
        ax.plot([a0, a1], [b0, b1], color=color, lw=0.9, zorder=1, solid_capstyle="butt")
    ax.add_patch(FancyArrowPatch(pts[-2], pts[-1], arrowstyle="-|>,head_length=3.0,head_width=1.7", color=color, lw=0.9,
                                 shrinkA=0, shrinkB=0, zorder=1))
    if label:
        ax.text(*lpos, label, fontsize=lsize, ha="center", va="center", color=color, zorder=4, bbox=dict(fc="white", ec="none", pad=0.3))


# 1 input and the two questions
box(1, 44, 26, 21, "Forecast archive", ("issued forecasts and", "realised power, past", "month; battery", "configuration"), "in")
box(32, 44, 28, 21, r"Compute $r_\mathrm{E}$", ("95 % quantile of the 1-h", "forecast deficit routed", "to the battery, over", "the usable band"), "q")
arrow([(27, 54.5), (32, 54.5)], EDGE["in"])
box(32, 11, 28, 21, r"$r_\mathrm{E}=0$", ("the error goes to the grid", "(set-point operation):", "deterministic MPC;", "no margin can pay off"), "ok")
arrow([(46, 44), (46, 32)], INK2, label="zero", lpos=(46, 38))
box(65, 44, 30, 21, r"$\Gamma=r_\mathrm{E}(x_{\max}-x_{\min})/b$", ("does the constant", "back-off $b$ hold the", "1-h deficit?"), "q")
arrow([(60, 54.5), (65, 54.5)], INK2, label="> 0", lpos=(62.5, 57.5))

# 2 the three readings
box(100, 52, 43, 15, r"$\Gamma<0.75$: $b$ is enough", ("keep the constant back-off; an", "uncertainty-scaled margin adds little"), "ok", gap=3.1)
box(100, 38, 43, 11, r"$0.75\leq\Gamma\leq1.25$: switch region", (r"no firm prediction; re-size $b$ for $\Gamma=0.75$",), "mid", gap=3.1)
box(100, 1, 43, 32, r"$\Gamma>1.25$: $b$ too small", (), "bad")
arrow([(95, 58), (97.5, 58), (97.5, 59.5), (100, 59.5)], INK2)
arrow([(95, 54.5), (97.5, 54.5), (97.5, 43.5), (100, 43.5)], INK2)
arrow([(95, 51), (96.5, 51), (96.5, 17), (100, 17)], INK2)
opts = [(f"re-size monthly, $\\Gamma=0.75$", f"{tot(t1, 'protection_h'):.1f} h, +{x(t1):.2f} %"),
        ("UA-MPC (no sizing step)", f"{p_ua:.1f} h, cheapest"),
        ("scenario / robust back-off", f"{tot(sc, 'protection_h'):.2f} / {tot(rb, 'protection_h'):.2f} h, +{x(sc):.1f} / +{x(rb):.1f} %")]
for i, (a, b) in enumerate(opts):
    y = 18.6 - 7.6 * i
    ax.add_patch(FancyBboxPatch((102, y), 39, 6.8, boxstyle="round,pad=0,rounding_size=0.9", fc="white", ec=EDGE["opt"], lw=0.7, zorder=3))
    ax.text(103.3, y + 4.8, a, fontsize=5.9, ha="left", va="center", color=INK, weight="bold", zorder=4)
    ax.text(103.3, y + 1.9, b, fontsize=5.6, ha="left", va="center", color=INK2, zorder=4)
ax.text(121.5, 27.6, "winter test of B, Nov–Mar: protection, cost vs UA-MPC", fontsize=5.4, ha="center", va="center", color=MUTED, zorder=4)

# 3 re-check
box(65, 11, 30, 21, "Re-check every month", (r"$r_\mathrm{E}$ changed by a factor", f"{g['rE_ratio_min']:.2f}–{g['rE_ratio_max']:.2f} between winter", "months; Γ = 0.75 leaves", "room for that"), "in")
arrow([(80, 44), (80, 32)], EDGE["in"], label="monthly", lpos=(80, 38))

Path(OUT).parent.mkdir(parents=True, exist_ok=True)
fig.savefig(OUT + ".pdf"); fig.savefig(OUT + ".png", dpi=250)
print("saved")
