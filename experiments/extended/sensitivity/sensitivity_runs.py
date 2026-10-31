"""Review-2 sensitivity runs (post hoc, outside the recorded protocol). Final-test period, BMS plant, selected points.
usage: KAIROS_ROOT=... STUDY_RESULTS=... KAIROS_SCEN=... python3 sensitivity_runs.py <A|B|C> <outdir>
  solver : stricter solver (maxiter 2000, ftol 1e-9, replay.solver_ftol off) for every MPC controller
  load10 : the MPC sees a load forecast with a multiplicative AR(1) error (sd 10 %, phi 0.9 per 15 min, seed 1);
           the plant uses the measured load
  renm10 / renp10 : realised renewable power 10 % below / above the physical model (forecast unchanged)
  chan   : UA-MPC with separate sigma scales for solar and wind (fitted on the calibration period)
"""
import os as _os  # paths: see experiments/extended/README.md
UA_WORK = _os.environ.get("UA_WORK", "work/extended")      # working tree (layout in README)
UA_CODE = _os.environ.get("UA_CODE", "code")               # controller code (Kairos)
UA_KAIROS = _os.environ.get("UA_KAIROS", "work")           # work copies made by experiments/make_work.sh
import json, os, sys, time
sys.path.insert(0, UA_WORK + "/ind")
import numpy as np, pandas as pd
import engine
from engine import SCEN, point, _job
import replay
from replay import build_data, named_periods, with_overrides, load_config
from concurrent.futures import ProcessPoolExecutor, as_completed

X, OUT = sys.argv[1], sys.argv[2]
os.makedirs(f"{OUT}/steps", exist_ok=True)
S = UA_WORK
reg = json.load(open(f"{S}/ind/results_{X}/final_test/preregistration.json"))["selected"]["primary_threshold_pp_h"]
ctrls = [c for c in ("det", "det_bo", "ua") if c in reg]
P = {c: point(c, reg[c]["trial"]) for c in ctrls}

base = with_overrides(load_config(), SCEN)
base = with_overrides(base, {"replay": {"plant": "bms"}})
data = build_data(base)
t0, t1 = named_periods(data, base)["final_test"]
kappa = float(base["mpc"]["sigma_scale"])

# --- load forecast seen by the MPC
_orig_hf = replay.horizon_frame
def hf(d, i, n, forecast):
    fr = _orig_hf(d, i, n, forecast)
    if "demand_fc_w" in d:
        fr["Demand_power (W)"] = d["demand_fc_w"].iloc[i:i + n].to_numpy(dtype=float)
    return fr
replay.horizon_frame = hf

def load_err(d, sd=0.10, phi=0.9, seed=1):
    rng = np.random.default_rng(seed); n = len(d)
    e = np.zeros(n); w = rng.normal(0, sd * np.sqrt(1 - phi ** 2), n); e[0] = rng.normal(0, sd)
    for k in range(1, n):
        e[k] = phi * e[k - 1] + w[k]
    d = d.copy(); d["demand_fc_w"] = np.clip(d.demand_w.to_numpy() * (1 + e), 0, None); return d

def ren_bias(d, b):
    d = d.copy(); d["solar_real"] *= (1 + b); d["wind_real"] *= (1 + b); return d

def chan_scales():
    c0, c1 = named_periods(data, base)["calibration"]
    d = data[(data.index >= c0) & (data.index < c1)]; d = d[d.index.minute == 0]
    out = {}
    for ch in ("solar", "wind"):
        ok = d[f"{ch}_sd"] > 0
        e = (d[f"{ch}_real"] - d[f"{ch}_mu"])[ok]; s = d[f"{ch}_sd"][ok]
        out[ch] = float(np.sqrt((e ** 2).mean()) / np.sqrt((s ** 2).mean()))
    return out

def chan(d, ks):
    d = d.copy(); d["solar_sd"] *= ks["solar"] / kappa; d["wind_sd"] *= ks["wind"] / kappa; return d

ks = chan_scales()
json.dump({"kappa_total": kappa, "kappa_channel": ks, "load_err": {"sd": 0.10, "phi": 0.9, "seed": 1},
           "period": [t0.isoformat(), t1.isoformat()]}, open(f"{OUT}/setup.json", "w"), indent=1)
print(X, "channel scales", ks, flush=True)

STRICT = {"optimizer": {"maxiter": 2000, "ftol": 1e-9}, "replay": {"solver_ftol": 0}}
D = {"solver": data, "load10": load_err(data), "renm10": ren_bias(data, -0.10), "renp10": ren_bias(data, 0.10),
     "chan": chan(data, ks)}
order = sys.argv[3].split(",") if len(sys.argv) > 3 else ["solver", "chan", "load10", "renm10", "renp10"]
jobs = []
for sc in order:
    for c in (["ua"] if sc == "chan" else ctrls):
        if sc == "chan" and X == "C":
            continue  # C: the margin is zero, sigma does not enter the plan
        if sc == "solver" and X == "B" and c == "ua":
            continue  # done before (ms3/rev/solver_sensitivity.json)
        tag = f"{sc}_{c}"
        if os.path.exists(f"{OUT}/steps/{tag}.json"):
            continue
        cfg = with_overrides(base, P[c])
        if sc == "solver":
            cfg = with_overrides(cfg, STRICT)
        jobs.append((OUT, tag, c, D[sc], cfg, t0, t1, {"tag": tag, "config": X, "scenario": sc, "controller": c}))
print(f"[{time.strftime('%H:%M')}] {X}: {len(jobs)} runs", flush=True)
with ProcessPoolExecutor(2) as ex:
    for f in as_completed([ex.submit(_job, j) for j in jobs]):
        tag, m, s = f.result()
        print(f"[{time.strftime('%H:%M')}] {X} done {tag:<16} net {m['net_cost_eur']:.4f} prot_h {m.get('protection_h', 0):.2f} "
              f"solver {m.get('solver_success_pct', 100):.1f}% {s:.0f}s", flush=True)
