"""Items 2-3: which indicator separates the decision-map cells where the controllers tied from those where they
differed (B and C of the industrial load, A of the second load case); leave-one-out and cell bootstrap of the
r_E split; day-block bootstrap of the per-cell difference (are the labels themselves robust?)."""
import os as _os  # paths: see experiments/extended/README.md
UA_WORK = _os.environ.get("UA_WORK", "work/extended")      # working tree (layout in README)
UA_CODE = _os.environ.get("UA_CODE", "code")               # controller code (Kairos)
UA_KAIROS = _os.environ.get("UA_KAIROS", "work")           # work copies made by experiments/make_work.sh
import sys, numpy as np, pandas as pd
sys.path.insert(0, UA_WORK + "/ind")
from engine import daily
S = UA_WORK; O = f"{S}/rev2/rE"
K2 = UA_CODE + "/results/paper/decision_map_recovery"
cells = []
for X in ("A", "B", "C"):
    d = pd.read_csv(f"{S}/ind/results_{X}/decision_map_recovery/map.csv")
    d["case"] = f"industrial {X}"; d["diff"] = d.bo_prot_h - d.ua_prot_h; d["metric"] = "protection h"
    d["dir"] = f"{S}/ind/results_{X}/decision_map_recovery/steps"; d["kpi"] = "prot"; cells.append(d)
d = pd.read_csv(f"{K2}/map.csv"); d["case"] = "second load A"; d["r_E_gen"] = d.r_E
d["diff"] = d.bo_viol - d.ua_viol; d["metric"] = "violation pp h"; d["dir"] = f"{K2}/steps"; d["kpi"] = "viol"; cells.append(d)
c = pd.concat(cells, ignore_index=True)
# label: difference = the back-off needed at least twice the protection (violation) of UA and the gap is at least
# 4 h (1 pp h); otherwise tie
c["label"] = ((c["diff"] >= np.where(c.kpi == "prot", 4.0, 1.0)) &
              (c.bo_prot_h.where(c.kpi == "prot", c.bo_viol) >= 2 * c.ua_prot_h.where(c.kpi == "prot", c.ua_viol))).astype(int)
# day-block bootstrap of each cell's difference (22 days, 5-day blocks)
rng = np.random.default_rng(42)
lo, hi = [], []
for r in c.itertuples():
    tag = f"cap{r.cap_wh}_err{r.error_scale}_rp{r.replan_steps}"
    try:
        a = daily(f"{r.dir}/det_bo_{tag}.csv"); b = daily(f"{r.dir}/ua_{tag}.csv")
    except FileNotFoundError:
        a = daily(f"{r.dir}/det_{tag}.csv"); b = daily(f"{r.dir}/ua_{tag}.csv")
    x = (a[r.kpi] - b[r.kpi]).values * (0.25 if r.kpi == "prot" else 1.0); n = len(x); k = int(np.ceil(n / 5))
    idx = ((rng.integers(0, n, (10000, k))[:, :, None] + np.arange(5)) % n).reshape(10000, -1)[:, :n]
    m = x[idx].sum(1) * (n / idx.shape[1]); lo.append(np.percentile(m, 2.5)); hi.append(np.percentile(m, 97.5))
c["diff_lo"], c["diff_hi"] = lo, hi
c.drop(columns=["dir"]).to_csv(f"{O}/cells.csv", index=False)

def auc(score, y):
    s1, s0 = score[y == 1], score[y == 0]
    return float(((s1[:, None] > s0[None, :]).sum() + 0.5 * (s1[:, None] == s0[None, :]).sum()) / (len(s1) * len(s0)))
main = c[c.case != "industrial A"]
y = main.label.values
res = {k: auc(main[k].values, y) for k in ("r_E_gen", "r_E", "r_P", "sigma_mean_w")}
res["r_E_gen (industrial A included)"] = auc(c.r_E_gen.values, c.label.values)
print("AUC", {k: round(v, 3) for k, v in res.items()})

def split(df):
    t, d = df[df.label == 0].r_E_gen, df[df.label == 1].r_E_gen
    return (t.max() if len(t) else -np.inf), (d.min() if len(d) else np.inf)
# leave-one-out with the midpoint of the gap
ok = 0
for i in main.index:
    tr = main.drop(i); a, b = split(tr); thr = (a + b) / 2
    ok += int((main.loc[i, "r_E_gen"] > thr) == bool(main.loc[i, "label"]))
print("LOO accuracy", ok, "of", len(main))
# cell bootstrap
B = 10000; sep = 0; mids = []
for _ in range(B):
    s = main.sample(len(main), replace=True, random_state=rng.integers(1e9))
    if s.label.nunique() < 2: continue
    a, b = split(s)
    if a < b: sep += 1; mids.append((a + b) / 2)
print("separable in", round(100 * sep / B, 1), "% of resamples; midpoint 2.5/50/97.5 %:", np.round(np.percentile(mids, [2.5, 50, 97.5]), 4))
print(c[["case", "cap_wh", "error_scale", "replan_steps", "r_E_gen", "r_E", "r_P", "diff", "diff_lo", "diff_hi", "label"]].round(3).to_string(index=False))
pd.Series({**res, "loo_ok": ok, "loo_n": len(main), "separable_pct": 100 * sep / B,
           "mid_p2_5": np.percentile(mids, 2.5), "mid_p50": np.percentile(mids, 50), "mid_p97_5": np.percentile(mids, 97.5)}).to_csv(f"{O}/discrim_summary.csv")
