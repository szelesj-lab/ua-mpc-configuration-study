"""Benchmark of r_E against storage-sizing indicators from the literature, on the decision-map cells.
Indicators (all divided by the usable capacity E (x_max - x_min)):
  r_E          : ours - Q95 of the error routed to the battery, accumulated over the re-planning window (config-aware)
  r_E_day      : ours with the window extended to 24 h (config-aware, sizing-literature window)
  S24          : Q95 of |error accumulated over 24 h| (config-blind; day-ahead sizing, cf. Yu et al. 2019, Haessig et al. 2015)
  Rday         : Q95 over days of the range (max - min) of the within-day accumulated error (config-blind; Yu et al. 2019 Eq. 4)
  sigma_mean   : mean forecast standard deviation (W), spread-only indicator
mode: industrial | second"""
import os as _os  # paths: see experiments/revision/README.md
UA_WORK = _os.environ.get("UA_WORK", "work/revision")      # revision working tree (layout in README)
UA_CODE = _os.environ.get("UA_CODE", "code")               # controller code (Kairos)
UA_KAIROS = _os.environ.get("UA_KAIROS", "work")           # work copies made by experiments/make_work.sh
import sys, json, os
S = UA_WORK
mode = sys.argv[1]
if mode == "industrial":
    sys.path.insert(0, f"{S}/rev3")
    import common  # sets KAIROS env for the industrial cell
    from replay import build_data, named_periods, with_overrides, load_config
    from engine import SCEN
else:
    sys.path.insert(0, UA_CODE + "/results/paper/scripts")
    from engine import SCEN
    from replay import build_data, named_periods, with_overrides, load_config
import numpy as np, pandas as pd
from MPC_costfunction import _mpc_params

def inds(data, cfg, t0, t1, cap, err, rp):
    c = with_overrides(cfg, {"SoC": {"E_capacity": cap}, "replay": {"replan_steps": rp}})
    p = _mpc_params(c); d = data.loc[t0:t1]
    real = d.solar_real + d.wind_real
    mu = real + err * ((d.solar_mu + d.wind_mu) - real); e = mu - real
    usable = cap * (p["soc_max"] - p["soc_min"])
    arch = p["architecture"]
    if arch == "renewable_buffer":
        x = np.minimum(e.clip(lower=0), np.minimum(mu.clip(lower=0), p["p_ch_max"]))
    elif arch == "self_consumption":
        lim = p["p_ch_max"] + p["p_dis_max"]; x = e.clip(-lim, lim)
    else:
        x = e * 0.0
    q = lambda s, w: float((s.rolling(w).sum() * 0.25).clip(lower=0).quantile(0.95))
    r_E = q(x, rp) / usable; r_E_day = q(x, 96) / usable
    S24 = float((e.rolling(96).sum() * 0.25).abs().quantile(0.95)) / usable
    cum = (e * 0.25).groupby(e.index.date).cumsum(); g = cum.groupby(cum.index.date)
    Rday = float((g.max() - g.min()).quantile(0.95)) / usable
    sig = float(np.sqrt(d.solar_sd ** 2 + d.wind_sd ** 2).mean() * err)
    return dict(r_E=r_E, r_E_day=r_E_day, S24=S24, Rday=Rday, sigma_mean=sig, arch=arch)

rows = []
if mode == "industrial":
    cells = pd.read_csv(f"{S}/rev2/rE/cells.csv")
    for X, arch in (("B", "self_consumption"), ("C", "setpoint")):
        cfg = with_overrides(with_overrides(load_config(), SCEN), {"mpc": {"architecture": arch}})
        data = build_data(cfg); t0, t1 = named_periods(data, cfg)["development"]
        for r in cells[cells.case == f"industrial {X}"].itertuples():
            rows.append(dict(case=r.case, cap=r.cap_wh, err=r.error_scale, rp=r.replan_steps, label=r.label,
                             r_E_ref=r.r_E_gen, **inds(data, cfg, t0, t1, r.cap_wh, r.error_scale, r.replan_steps)))
    # correlation cells of the pre-registered test (synthetic AR(1) errors, B)
    cfg = with_overrides(with_overrides(load_config(), SCEN), {"mpc": {"architecture": "self_consumption"}})
    data = build_data(cfg); t0, t1 = named_periods(data, cfg)["development"]
    for f in sorted(os.listdir(f"{S}/rev3/runs_rE/steps")) if os.path.isdir(f"{S}/rev3/runs_rE/steps") else []:
        pass
    out = f"{S}/rev4/bench/ind_industrial.csv"
else:
    cells = pd.read_csv(f"{S}/rev2/rE/cells.csv")
    cfg = with_overrides(load_config(), SCEN); data = build_data(cfg); t0, t1 = named_periods(data, cfg)["development"]
    for r in cells[cells.case == "second load A"].itertuples():
        rows.append(dict(case=r.case, cap=r.cap_wh, err=r.error_scale, rp=r.replan_steps, label=r.label,
                         r_E_ref=r.r_E_gen, **inds(data, cfg, t0, t1, r.cap_wh, r.error_scale, r.replan_steps)))
    out = f"{S}/rev4/bench/ind_second.csv"
pd.DataFrame(rows).to_csv(out, index=False); print(pd.DataFrame(rows).round(4).to_string())
