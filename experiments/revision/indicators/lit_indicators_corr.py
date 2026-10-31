"""The indicators of lit_indicators.py on the correlation cells of the pre-registered test (synthetic AR(1) errors, B)."""
import os as _os  # paths: see experiments/revision/README.md
UA_WORK = _os.environ.get("UA_WORK", "work/revision")      # revision working tree (layout in README)
UA_CODE = _os.environ.get("UA_CODE", "code")               # controller code (Kairos)
UA_KAIROS = _os.environ.get("UA_KAIROS", "work")           # work copies made by experiments/make_work.sh
import sys, json, os, pandas as pd
S = UA_WORK
sys.path.insert(0, f"{S}/rev3"); import common
from replay import build_data, named_periods
src = open(f"{S}/rev4/bench/lit_indicators.py").read()
exec(src.split("rows = []")[0].split("import numpy as np, pandas as pd")[1])   # defines inds()
cfg = common.base_cfg(); data = build_data(cfg); t0, t1 = named_periods(data, cfg)["development"]
st = f"{S}/rev3/runs_rE/steps"; rows = []
for f in sorted(os.listdir(st)):
    if f.startswith("corr_ua_") and f.endswith(".json"):
        tag = f[len("corr_ua_"):-5]; cap = int(tag.split("_")[0][3:]); rp = int(tag.split("_")[1][2:]); phi = float(tag.split("phi")[1])
        u = json.load(open(f"{st}/{f}"))["metrics"]["protection_h"]; b = json.load(open(f"{st}/corr_det_bo_{tag}.json"))["metrics"]["protection_h"]
        r = inds(common.synth(data, phi), cfg, t0, t1, cap, 1.0, rp)  # noqa: F821  (inds is defined by the exec above)
        rows.append(dict(cap=cap, rp=rp, phi=phi, bo=b, ua=u, label=int(b - u >= 4 and b >= 2 * u), **{k: v for k, v in r.items() if k != "arch"}))
pd.DataFrame(rows).to_csv(f"{S}/rev4/bench/ind_corr.csv", index=False); print(pd.DataFrame(rows).round(4).to_string())
