"""Figures added in the revision of 30 Oct 2026: fig15 summary of UA-MPC vs references with 95 % intervals,
fig16 the Gamma test (protection difference DET-BO minus UA-MPC against Gamma, configuration B, development period)."""
import os as _os  # paths: see experiments/revision/README.md
UA_WORK = _os.environ.get("UA_WORK", "work/revision")      # revision working tree (layout in README)
UA_CODE = _os.environ.get("UA_CODE", "code")               # controller code (Kairos)
UA_KAIROS = _os.environ.get("UA_KAIROS", "work")           # work copies made by experiments/make_work.sh
import json, os
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker
import numpy as np, pandas as pd
S = UA_WORK
FIG = f"{S}/rev3/figs"; os.makedirs(FIG, exist_ok=True)
WHITE, INK, INK2, GRID = "#ffffff", "#111111", "#4a4a4a", "#dddddd"
plt.rcParams.update({"font.size": 8, "font.family": "DejaVu Sans", "axes.edgecolor": INK2, "axes.labelcolor": INK,
    "xtick.color": INK2, "ytick.color": INK2, "axes.spines.top": False, "axes.spines.right": False,
    "figure.facecolor": WHITE, "axes.facecolor": WHITE, "savefig.facecolor": WHITE, "axes.grid": True,
    "grid.color": GRID, "grid.linewidth": 0.6, "axes.axisbelow": True, "legend.frameon": False, "legend.fontsize": 7})
def save(fig, name):
    for ext in ("pdf", "png"):
        fig.savefig(f"{FIG}/{name}.{ext}", dpi=300, bbox_inches="tight", facecolor=WHITE)
    plt.close(fig); print("written", name)

# ---- fig15 summary
rows = []
for X in "ABC":
    b = pd.read_csv(f"{S}/ind/results_{X}/final_test/paired_bootstrap.csv"); b = b[(b.plant == "bms") & (b.a == "ua")]
    for ref in ("det", "det_bo"):
        g = b[b.b == ref]
        if g.empty: continue
        rows.append((f"Summer, {X}, vs {'DET' if ref=='det' else 'DET-BO'}", g, 0.25))
w = pd.read_csv(f"{S}/rw/results/paired_bootstrap.csv"); w = w[w.a == "ua"]
for X in "BC":
    for ref in ("det", "det_bo"):
        g = w[(w.config == X) & (w.b == ref)]
        if g.empty: continue
        rows.append((f"Winter, {X}, vs {'DET' if ref=='det' else 'DET-BO'}", g, 0.25))
lab, cost, prot = [], [], []
for name, g, h in rows:
    c = g[g.kpi == "cost"].iloc[0]; p = g[g.kpi == "prot"].iloc[0]
    lab.append(name)
    cost.append((100 * c.diff_total / c.b_total, 100 * c.lo_total / c.b_total, 100 * c.hi_total / c.b_total))
    prot.append((h * p.diff_total, h * p.lo_total, h * p.hi_total))
pd.DataFrame({"row": lab, "cost_pct": [x[0] for x in cost], "cost_lo": [x[1] for x in cost], "cost_hi": [x[2] for x in cost],
              "prot_h": [x[0] for x in prot], "prot_lo": [x[1] for x in prot], "prot_hi": [x[2] for x in prot]}).to_csv(f"{FIG}/fig15_data.csv", index=False)
y = np.arange(len(lab))[::-1]
fig, axs = plt.subplots(1, 2, figsize=(7.2, 2.9), sharey=True)
for ax, data, xl in ((axs[0], cost, "UA-MPC minus reference: net cost (%)"), (axs[1], prot, "UA-MPC minus reference: BMS protection (h)")):
    for yi, (m, lo, hi) in zip(y, data):
        sig = lo > 0 or hi < 0
        ax.plot([lo, hi], [yi, yi], color=INK2, lw=1.2)
        ax.plot(m, yi, "o", ms=4.5, color="#1a9e6e" if sig else WHITE, mec="#1a9e6e", mew=1.2)
    ax.axvline(0, color=INK, lw=0.8); ax.set_xlabel(xl)
axs[0].set_yticks(y); axs[0].set_yticklabels(lab)
axs[1].text(0.99, 0.02, "filled: 95 % interval excludes zero\nnegative = UA-MPC better", transform=axs[1].transAxes,
            ha="right", va="bottom", fontsize=6.5, color=INK2)
save(fig, "fig15_summary")

# ---- fig16 Gamma test
B = 0.030298875705271892; X = 0.8
pts = []
mp = pd.read_csv(f"{S}/ind/results_B/decision_map_recovery/map.csv")
for r in mp.itertuples():
    pts.append(("Decision map (development)", r.r_E_gen * X / B, r.bo_prot_h - r.ua_prot_h, r.bo_prot_h, r.ua_prot_h))
reg = json.load(open(f"{S}/rev3/preregistration_rE.json")); st = f"{S}/rev3/runs_rE/steps"
for c in reg["cells"]:
    if c["experiment"] == "grid":
        t = f"cap{c['cap']}_err{c['err']}"; a = json.load(open(f"{st}/grid_det_bo_{t}.json")); u = json.load(open(f"{st}/grid_ua_{t}.json"))
        name = "New cells (pre-registered)"
    else:
        t = f"cap{c['cap']}_rp{c['rp']}_phi{c['phi']}"; a = json.load(open(f"{st}/corr_det_bo_{t}.json")); u = json.load(open(f"{st}/corr_ua_{t}.json"))
        name = "Error correlation (pre-registered)"
    pa, pu = a["metrics"]["protection_h"], u["metrics"]["protection_h"]
    pts.append((name, c["Gamma"], pa - pu, pa, pu))
mps = f"{S}/ind/results_B/decision_map_recovery/steps"
for c in reg["b_sweep"]:
    a = json.load(open(f"{st}/bsw_det_bo_cap{c['cap']}_err{c['err']}_rp{c['rp']}_G{c['Gamma']}.json"))
    u = json.load(open(f"{mps}/ua_cap{c['cap']}_err{c['err']}_rp{c['rp']}.json"))
    pa, pu = a["metrics"]["protection_h"], u["metrics"]["protection_h"]
    pts.append(("Back-off sweep (pre-registered)", c["Gamma"], pa - pu, pa, pu))
d = pd.DataFrame(pts, columns=["set", "Gamma", "diff_h", "bo_h", "ua_h"])
d["label"] = np.where((d.bo_h >= d.ua_h + 4) & (d.bo_h >= 2 * d.ua_h), "difference", "tie")
d.to_csv(f"{FIG}/fig16_data.csv", index=False)
fig, ax = plt.subplots(figsize=(7.2, 3.0))
sty = {"Decision map (development)": ("s", "#7f7f7f"), "New cells (pre-registered)": ("o", "#1a9e6e"),
       "Error correlation (pre-registered)": ("D", "#2a6fdb"), "Back-off sweep (pre-registered)": ("^", "#e0662b")}
for k, (m, col) in sty.items():
    g = d[d.set == k]
    ax.scatter(g.Gamma, g.diff_h, marker=m, s=26, facecolor=np.where(g.label == "difference", col, WHITE), edgecolor=col, lw=1.1, label=k, zorder=3)
ax.axvline(1.0, color=INK, lw=0.8, ls="--"); ax.axhline(0, color=INK2, lw=0.6)
ax.set_xscale("log"); ax.set_xticks([0.25, 0.5, 1, 2, 4]); ax.set_xticklabels(["0.25", "0.5", "1", "2", "4"]); ax.xaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
ax.set_xlabel(r"$\Gamma = r_\mathrm{E}\,(x_{\max}-x_{\min})/b$"); ax.set_ylabel("Protection, DET-BO minus UA-MPC (h, 22 days)")
ax.legend(loc="upper left"); ax.text(0.99, 0.02, "filled: difference; open: tie (label rule)", transform=ax.transAxes, ha="right", va="bottom", fontsize=6.5, color=INK2)
save(fig, "fig16_gamma")
print(d.groupby(["set", "label"]).size())
