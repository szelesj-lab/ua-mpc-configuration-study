"""AUC, leave-one-out and leave-one-family-out (B, C, second load case) of the indicators on the 30 labelled cells."""
import os as _os  # paths: see experiments/revision/README.md
UA_WORK = _os.environ.get("UA_WORK", "work/revision")      # revision working tree (layout in README)
UA_CODE = _os.environ.get("UA_CODE", "code")               # controller code (Kairos)
UA_KAIROS = _os.environ.get("UA_KAIROS", "work")           # work copies made by experiments/make_work.sh
import numpy as np, pandas as pd
S = UA_WORK + "/rev4/bench"
d = pd.concat([pd.read_csv(f"{S}/ind_industrial.csv"), pd.read_csv(f"{S}/ind_second.csv")], ignore_index=True); d["r_E"] = d.r_E_ref
def auc(s, y):
    s1, s0 = s[y == 1], s[y == 0]; return ((s1[:, None] > s0[None, :]).sum() + 0.5 * (s1[:, None] == s0[None, :]).sum()) / (len(s1) * len(s0))
def best_thr(tr, col):
    t0, t1 = tr[tr.label == 0][col].max(), tr[tr.label == 1][col].min()
    if t0 < t1: return (t0 + t1) / 2
    v = np.sort(tr[col].unique()); c = np.r_[v[0] - 1, (v[1:] + v[:-1]) / 2, v[-1] + 1]
    return c[int(np.argmax([((tr[col] > t) == tr.label.astype(bool)).mean() for t in c]))]
rows = []
for col in ["r_E", "r_E_day", "S24", "Rday", "sigma_mean"]:
    loo = sum(int((d.loc[i, col] > best_thr(d.drop(i), col)) == bool(d.loc[i, "label"])) for i in d.index)
    lofo = sum(int(((d[d.case == f][col] > best_thr(d[d.case != f], col)) == d[d.case == f].label.astype(bool)).sum()) for f in d.case.unique())
    e = d[d.case != "industrial C"]
    rows.append(dict(indicator=col, auc_30=auc(d[col].values, d.label.values), loo=loo, lofo=lofo, auc_B_second=auc(e[col].values, e.label.values)))
out = pd.DataFrame(rows); out.to_csv(f"{S}/indicator_benchmark.csv", index=False); print(out.round(3).to_string())
