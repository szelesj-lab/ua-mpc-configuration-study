"""Graphical abstract: error routing in three battery configurations -> r_E -> Gamma gauge -> key results."""
import os as _os  # paths: see experiments/revision/README.md
UA_WORK = _os.environ.get("UA_WORK", "work/revision")      # revision working tree (layout in README)
UA_CODE = _os.environ.get("UA_CODE", "code")               # controller code (Kairos)
UA_KAIROS = _os.environ.get("UA_KAIROS", "work")           # work copies made by experiments/make_work.sh
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, Wedge, Circle, Rectangle, Polygon

OUT = UA_WORK + "/rev2/work/figs/graphical_abstract"
INK, INK2, MUTED = "#0b0b0b", "#52514e", "#8a8984"
BAT, ERR, OK, WARN, SUN, GRID = "#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#eda100", "#3d3c39"
CARD = "#f3f2ee"
plt.rcParams.update({"font.family": "DejaVu Sans"})

fig = plt.figure(figsize=(13.3, 5.4), dpi=300)
ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, 133); ax.set_ylim(0, 54); ax.axis("off")
fig.patch.set_facecolor("white")

def arrow(p, q, color, dashed=False, lw=2.2, rad=0.0, head=11):
    a = FancyArrowPatch(p, q, arrowstyle=f"-|>,head_length={head/20},head_width={head/30}", mutation_scale=12,
                        color=color, lw=lw, linestyle=(0, (3.2, 2.2)) if dashed else "-",
                        connectionstyle=f"arc3,rad={rad}", shrinkA=0, shrinkB=0, zorder=4)
    ax.add_patch(a)

def sun(x, y, r=2.0):
    ax.add_patch(Circle((x, y), r, color=SUN, zorder=5))
    for t in np.linspace(0, 2 * np.pi, 9)[:-1]:
        ax.plot([x + 1.35 * r * np.cos(t), x + 1.8 * r * np.cos(t)], [y + 1.35 * r * np.sin(t), y + 1.8 * r * np.sin(t)],
                color=SUN, lw=1.6, solid_capstyle="round", zorder=5)

def turbine(x, y, h=5.0):
    ax.plot([x, x], [y - h, y], color=INK2, lw=1.8, zorder=5)
    for t in (90, 210, 330):
        tt = np.radians(t)
        ax.plot([x, x + 2.3 * np.cos(tt)], [y, y + 2.3 * np.sin(tt)], color=INK2, lw=2.2, solid_capstyle="round", zorder=5)
    ax.add_patch(Circle((x, y), 0.45, color=INK2, zorder=6))

def battery(x, y, soc, band=(0.1, 0.9), w=6.0, h=10.0, glow=False):
    if glow:
        ax.add_patch(FancyBboxPatch((x - w / 2 - 1.0, y - h / 2 - 1.0), w + 2.0, h + 2.0, boxstyle="round,pad=0,rounding_size=1.6",
                                    fc=ERR, ec="none", alpha=0.16, zorder=3))
    ax.add_patch(FancyBboxPatch((x - w / 2, y - h / 2), w, h, boxstyle="round,pad=0,rounding_size=0.9", fc="white", ec=INK, lw=1.8, zorder=5))
    ax.add_patch(Rectangle((x - 1.2, y + h / 2), 2.4, 0.9, fc=INK, ec="none", zorder=5))
    ax.add_patch(Rectangle((x - w / 2 + 0.6, y - h / 2 + 0.6), w - 1.2, (h - 1.2) * soc, fc=BAT, ec="none", zorder=6))
    for b in band:
        yy = y - h / 2 + 0.6 + (h - 1.2) * b
        ax.plot([x - w / 2 - 0.6, x + w / 2 + 0.6], [yy, yy], color=INK, lw=1.0, ls=(0, (1.5, 1.2)), zorder=7)

def pylon(x, y, s=1.0):
    pts = [(x - 2.2 * s, y - 4.5 * s), (x, y + 4.5 * s), (x + 2.2 * s, y - 4.5 * s)]
    ax.plot([p[0] for p in pts], [p[1] for p in pts], color=GRID, lw=1.8, zorder=5)
    for yy, ww in ((2.2, 2.6), (0.0, 1.9)):
        ax.plot([x - ww * s, x + ww * s], [y + yy * s, y + yy * s], color=GRID, lw=1.6, zorder=5)
    ax.plot([x - 1.0 * s, x + 1.5 * s], [y - 4.5 * s, y + 0.0], color=GRID, lw=1.0, zorder=5)
    ax.plot([x + 1.0 * s, x - 1.5 * s], [y - 4.5 * s, y + 0.0], color=GRID, lw=1.0, zorder=5)

def factory(x, y):
    ax.add_patch(Polygon([(x - 3.2, y - 3), (x - 3.2, y + 1), (x - 1.6, y + 0), (x - 1.6, y + 1), (x, y + 0), (x, y + 1), (x + 1.6, y + 0),
                          (x + 1.6, y + 3.4), (x + 2.6, y + 3.4), (x + 2.6, y - 3)], closed=True, fc=MUTED, ec="none", zorder=5))
    for dx in (-2.2, -0.6, 1.0):
        ax.add_patch(Rectangle((x + dx, y - 1.6), 0.9, 1.0, fc="white", ec="none", zorder=6))

# ---------------- title band ----------------
ax.text(2.5, 50.6, "Where does the forecast error go?", fontsize=19, weight="bold", color=INK, va="center")
ax.text(2.5, 46.9, "Behind-the-meter battery in food processing: the configuration decides whether uncertainty-aware MPC can matter",
        fontsize=10.5, color=INK2, va="center")

# ---------------- three configuration cards ----------------
cards = [("A", "Renewable buffer", True, "error → battery", r"$r_\mathrm{E}>0$"),
         ("B", "Self-consumption", True, "error → battery", r"$r_\mathrm{E}>0$"),
         ("C", "Set-point following", False, "error → grid", r"$r_\mathrm{E}=0$")]
x0, cw, ch, gap, y0 = 2.5, 22.5, 36.5, 2.0, 5.0
for i, (k, name, to_bat, route, rlabel) in enumerate(cards):
    cx = x0 + i * (cw + gap)
    ax.add_patch(FancyBboxPatch((cx, y0), cw, ch, boxstyle="round,pad=0,rounding_size=1.8", fc=CARD, ec="none", zorder=1))
    ax.text(cx + 1.6, y0 + ch - 2.6, k, fontsize=17, weight="bold", color=INK, va="center")
    ax.text(cx + 1.7, y0 + ch - 6.4, name, fontsize=10.5, color=INK, va="center")
    # nodes
    sx, sy = cx + 4.3, y0 + 22.2
    sun(sx, sy + 2.4); turbine(sx + 5.2, sy + 3.8, h=5.5)
    bx, by = cx + 16.5, y0 + 19.6
    battery(bx, by, soc=0.82 if to_bat else 0.5, glow=to_bat)
    gx, gy = cx + 4.6, y0 + 8.4
    pylon(gx, gy, 0.95)
    fx, fy = cx + 16.5, y0 + 7.6
    factory(fx, fy)
    # planned flows (solid grey)
    arrow((sx + 3.4, sy - 1.2), (bx - 3.6, by + 2.0), INK2, lw=1.6)
    arrow((bx, by - 5.6), (fx, fy + 3.9), INK2, lw=1.6)
    arrow((gx + 2.6, gy - 0.6), (fx - 3.6, fy - 0.4), INK2, lw=1.6)
    if k == "B":
        arrow((gx + 2.0, gy + 3.0), (bx - 3.6, by - 2.2), INK2, lw=1.6)
    # forecast error (dashed orange)
    if to_bat:
        arrow((sx + 1.5, sy - 3.0), (bx - 3.4, by - 0.4), ERR, dashed=True, lw=2.6, rad=0.25)
    else:
        arrow((sx - 0.5, sy - 3.0), (gx - 0.2, gy + 5.0), ERR, dashed=True, lw=2.6, rad=0.0)
    # route label + r_E pill
    ax.text(cx + cw / 2, y0 + 1.9, route, fontsize=9.5, color=ERR, ha="center", va="center", weight="bold")
    pill_fc = OK if not to_bat else "white"
    ax.add_patch(FancyBboxPatch((cx + cw - 9.0, y0 + ch - 4.2), 7.6, 3.2, boxstyle="round,pad=0,rounding_size=1.5",
                                fc=pill_fc, ec=OK if not to_bat else ERR, lw=1.4, zorder=6))
    ax.text(cx + cw - 5.2, y0 + ch - 2.6, rlabel, fontsize=10.5, ha="center", va="center", zorder=7,
            color="white" if not to_bat else ERR)

# legend line under cards
ax.plot([3.0, 6.0], [2.2, 2.2], color=INK2, lw=1.6); ax.text(6.6, 2.2, "planned flow", fontsize=8.5, color=INK2, va="center")
ax.plot([17.0, 20.0], [2.2, 2.2], color=ERR, lw=2.4, ls=(0, (3.2, 2.2))); ax.text(20.6, 2.2, "forecast error", fontsize=8.5, color=INK2, va="center")
ax.plot([31.5, 34.5], [2.2, 2.2], color=INK, lw=1.0, ls=(0, (1.5, 1.2))); ax.text(35.1, 2.2, "SoC limits", fontsize=8.5, color=INK2, va="center")

# ---------------- funnel to gauge ----------------
arrow((76.0, 23.0), (82.5, 23.0), INK, lw=2.6, head=14)

# ---------------- Gamma gauge ----------------
gx0, gy0, R = 98.0, 22.5, 13.5
zones = [(0.0, 0.75, OK, "back-off\nholds"), (0.75, 1.25, WARN, "switch"), (1.25, 2.0, ERR, "uncertainty\nhandling\nneeded")]
ang = lambda g: 180 - 180 * g / 2.0
for a, b, c, lab in zones:
    ax.add_patch(Wedge((gx0, gy0), R, ang(b), ang(a), width=3.6, fc=c, ec="white", lw=2.0, zorder=3))
    mid = np.radians(ang((a + b) / 2)); rr = R + 3.4
    ax.text(gx0 + rr * np.cos(mid), gy0 + rr * np.sin(mid), lab, fontsize=8.5, ha="center", va="center", color=INK2, linespacing=1.05)
for g in (0, 2):
    t = np.radians(ang(g))
    ax.text(gx0 + (R - 5.6) * np.cos(t), gy0 + (R - 5.6) * np.sin(t), f"{g}", fontsize=9, ha="center", va="center", color=INK2)
t = np.radians(ang(1.0))
ax.plot([gx0, gx0 + (R - 2.4) * np.cos(t)], [gy0, gy0 + (R - 2.4) * np.sin(t)], color=INK, lw=3.0, solid_capstyle="round", zorder=6)
ax.add_patch(Circle((gx0, gy0), 1.1, color=INK, zorder=7))
ax.text(gx0 + 1.6, gy0 + R - 4.6, "1", fontsize=9, ha="left", va="center", color=INK2)
ax.text(gx0, gy0 - 3.4, r"$\Gamma=\dfrac{r_\mathrm{E}\,(x_{\max}-x_{\min})}{b}$", fontsize=12.5, ha="center", va="center", color=INK)
ax.text(gx0, gy0 - 8.6, r"$r_\mathrm{E}$: forecast error that reaches the battery" + "\n" + r"$b$: constant SoC back-off",
        fontsize=8.8, ha="center", va="center", color=INK2, linespacing=1.3)
ax.text(gx0, 43.6, "A yardstick for uncertainty handling", fontsize=12, weight="bold", ha="center", va="center", color=INK)

# ---------------- key numbers ----------------
kx = 116.3
stats = [("46/53", "pre-registered predictions\nheld, two load cases"),
         ("0", r"difference at $r_\mathrm{E}=0$," + "\nsummer and winter"),
         ("+0.7%", "cost of a back-off re-sized\nmonthly from past $r_\\mathrm{E}$\nvs. uncertainty-aware MPC")]
for j, (big, small) in enumerate(stats):
    yy = 37.0 - j * 12.6
    ax.add_patch(FancyBboxPatch((kx, yy - 5.2), 15.6, 10.4, boxstyle="round,pad=0,rounding_size=1.4", fc=CARD, ec="none", zorder=1))
    ax.text(kx + 7.8, yy + 2.3, big, fontsize=17, weight="bold", ha="center", va="center", color=BAT)
    ax.text(kx + 7.8, yy - 2.4, small, fontsize=7.6, ha="center", va="center", color=INK2, linespacing=1.15)

fig.savefig(OUT + ".pdf"); fig.savefig(OUT + ".png", dpi=200)
print("saved")
