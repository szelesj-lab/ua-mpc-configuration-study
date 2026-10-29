"""python stages.py <solver|prereg|final|map|report>   (each stage is resumable)"""
import json, os, sys
from math import erf, sqrt
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np, pandas as pd
from engine import PAPER, ROOT, SCEN, point, run, daily, paired

FINAL_LOG = f"{ROOT}/results/replay/final_test_runs.log"

# ---------- 1. solver failures vs violations (development, physical) ----------
def solver():
    out = f"{PAPER}/solver_check"
    specs = [dict(tag="det_t05", controller="det", overrides=point("det", 5)),
             dict(tag="det_bo_t13", controller="det_bo", overrides=point("det_bo", 13)),
             dict(tag="ua_t17", controller="ua", overrides=point("ua", 17)),
             dict(tag="ua_t01", controller="ua", overrides=point("ua", 1))]
    run(out, specs)
    rows = []
    for s in specs:
        st = pd.read_csv(f"{out}/steps/{s['tag']}.csv"); st.index = pd.to_datetime(st.t, utc=True)
        sv = pd.read_csv(f"{out}/steps/{s['tag']}_solves.csv"); sv.index = pd.to_datetime(sv.t, utc=True)
        ok = sv["success"].reindex(st.index, method="ffill").fillna(True).astype(bool)
        exc = 100 * ((0.1 - st.soc).clip(lower=0) + (st.soc - 0.9).clip(lower=0)) * 0.25
        viol_steps = exc > 0
        # a violation "starts" where the previous step was inside the band
        onset = viol_steps & ~viol_steps.shift(1, fill_value=False)
        rows.append(dict(run=s["tag"], solves=len(sv), failed_solves_pct=100 * (~sv.success).mean(),
                         steps_under_failed_plan_pct=100 * (~ok).mean(),
                         viol_pp_h=exc.sum(), viol_under_failed_pct=100 * exc[~ok].sum() / exc.sum() if exc.sum() else 0,
                         onsets=int(onset.sum()), onsets_under_failed=int((onset & ~ok).sum()),
                         median_iter_failed=float(sv.iterations[~sv.success].median()) if (~sv.success).any() else np.nan,
                         median_iter_ok=float(sv.iterations[sv.success].median())))
    r = pd.DataFrame(rows); r.to_csv(f"{out}/solver_vs_violation.csv", index=False); print(r.round(3).to_string(index=False))

# ---------- 2. pre-registered selection rule (development fronts only) ----------
RULE = {"primary_threshold_h": 5.0, "secondary_threshold_h": 10.0,
        "rule": "industrial-load study, plant 'bms' (the battery management system keeps the SoC band; surplus is "
                "curtailed, shortfall comes from the grid): per controller, among its tuning-period Pareto points "
                "replayed on the development period, the cheapest development net cost with battery protection "
                "<= threshold hours; if none, the point with the fewest protection hours"}
def prereg():
    out = f"{PAPER}/final_test"; os.makedirs(out, exist_ok=True)
    path = f"{out}/preregistration.json"
    if os.path.exists(path):
        print("pre-registration exists, not changed:", path); return
    f = pd.read_csv(f"{PAPER}/dev_fronts/fronts_dev.csv")
    pick = {}
    for thr_name in ("primary_threshold_h", "secondary_threshold_h"):
        thr = RULE[thr_name]; sel = {}
        for c, g in f.groupby("ctrl"):
            ok = g[g.dev_prot <= thr]
            r = ok.sort_values("dev_cost").iloc[0] if len(ok) else g.sort_values("dev_prot").iloc[0]
            sel[c] = {"trial": int(r.trial), "dev_cost": float(r.dev_cost), "dev_prot_h": float(r.dev_prot),
                      "dev_viol": float(r.dev_viol), "met_threshold": bool(len(ok))}
        # the names used by final() and map_()
        pick[thr_name.replace("_h", "_pp_h")] = sel
    reg = {"written_utc": pd.Timestamp.now(tz="UTC").isoformat(), **RULE, "selected": pick,
           "final_test_set": "grid_only, rule, det, det_bo, ua, ua_rho_only, ua_margin_only, oracle (det weights); "
                             "physical and bms plant; primary selection; ua also with the secondary selection if it differs",
           "primary_endpoints": "net cost (EUR), battery protection (h) and its extra grid cost on the bms plant; "
                                "band violation (pp*h) on the physical plant as the unmanaged reference; paired "
                                "circular block bootstrap, 5-day blocks"}
    json.dump(reg, open(path, "w"), indent=2); print(json.dumps(reg, indent=2))

# ---------- 3. final test (run once) ----------
def final():
    out = f"{PAPER}/final_test"
    reg = json.load(open(f"{out}/preregistration.json"))
    P, Q = reg["selected"]["primary_threshold_pp_h"], reg["selected"]["secondary_threshold_pp_h"]
    ua = point("ua", P["ua"]["trial"])
    v = {"grid_only": ("grid_only", {}), "rule": ("rule", {}),
         "det": ("det", point("det", P["det"]["trial"])),
         "ua": ("ua", ua),
         "ua_rho_only": ("ua", {**ua, "mpc": {**ua.get("mpc", {}), "soc_chance_z": 0.0}}),
         "ua_margin_only": ("ua", {**ua, "weights": {**ua["weights"], "lambda": 0.0}}),
         "oracle": ("oracle", point("det", P["det"]["trial"]))}
    if "det_bo" in P:
        v["det_bo"] = ("det_bo", point("det_bo", P["det_bo"]["trial"]))
    if Q["ua"]["trial"] != P["ua"]["trial"]:
        v["ua_secondary"] = ("ua", point("ua", Q["ua"]["trial"]))
    specs = [dict(tag=f"{plant}_{n}", controller=c, overrides=o, plant=plant)
             for plant in ("physical", "bms") for n, (c, o) in v.items()]
    run(out, specs, period="final_test", log=FINAL_LOG)
    stats(out, list(v))

def stats(out, names):
    rows, summ = [], []
    for plant in ("physical", "bms"):
        D = {n: daily(f"{out}/steps/{plant}_{n}.csv") for n in names}
        for n in names:
            summ.append({"plant": plant, "variant": n, **json.load(open(f"{out}/steps/{plant}_{n}.json"))["metrics"]})
        kp = ("cost", "viol") if plant == "physical" else ("cost", "viol", "prot", "extra")
        for a in [x for x in ("ua", "ua_secondary") if x in names]:
            for b in [x for x in names if x not in (a, "ua_secondary")]:
                for r in paired(D[a], D[b], kp):
                    rows.append({"plant": plant, "a": a, "b": b, **r})
    pd.DataFrame(summ).to_csv(f"{out}/summary.csv", index=False)
    pd.DataFrame(rows).to_csv(f"{out}/paired_bootstrap.csv", index=False)
    print(pd.DataFrame(summ)[["plant", "variant", "net_cost_eur", "violation_pp_h", "max_violation_pp",
                              "protection_steps", "extra_grid_eur", "solver_success_pct"]].round(4).to_string(index=False))

# ---------- 4. decision-rule map (development period) ----------
CAPS, ERRS, REPLANS = (36, 72, 144), (0.5, 1.0, 2.0), (4, 16)
# time budget: 4 h re-planning only for the base cell
CELLS = [(c, e, 4) for c in CAPS for e in ERRS] + [(72, 1.0, 16)]
ZS = (0.5, 1.0, 1.645, 2.326)
def indicators(data, cfg, t0, t1, cap, err, replan):
    d = data.loc[t0:t1]
    e = err * ((d.solar_mu + d.wind_mu) - (d.solar_real + d.wind_real))   # W, > 0: over-forecast = deficit
    sd = err * np.sqrt(d.solar_sd ** 2 + d.wind_sd ** 2)
    from MPC_costfunction import _mpc_params
    p = _mpc_params(cfg)
    usable = cap * (p["soc_max"] - p["soc_min"])
    deficit = e.rolling(replan).sum().clip(lower=0) * 0.25                 # Wh until the next re-plan
    p_dis = min(p["p_dis_max"], float(d.demand_w.max()))                   # what the battery can cover
    return dict(r_E=float(deficit.quantile(0.95) / usable), r_P=float(e.clip(lower=0).quantile(0.95) / p_dis),
                kappa_sigma=float(sd.std() / sd.mean()) if sd.mean() > 0 else 0.0, sigma_mean_w=float(sd.mean()))

def indicators_gen(data, cfg, t0, t1, cap, err, replan):
    """Generalised r_E and tau_E: only the part of the forecast error that moves the SoC (see the plan doc).
    renewable_buffer: positive errors (less renewable than forecast) up to the planned charge, no cancellation;
    self_consumption: the signed error, both directions, clipped by the battery power; setpoint: zero."""
    from MPC_costfunction import _mpc_params
    p = _mpc_params(cfg)
    d = data.loc[t0:t1]
    real = d.solar_real + d.wind_real
    mu = real + err * ((d.solar_mu + d.wind_mu) - real)  # same error scaling as engine.scale_errors
    e = mu - real                                       # W, > 0: less renewable than forecast
    usable = cap * (p["soc_max"] - p["soc_min"])
    if p["architecture"] == "renewable_buffer":
        x = np.minimum(e.clip(lower=0), np.minimum(mu.clip(lower=0), p["p_ch_max"]))
        p_rec = float(mu[mu > 0].median()) if (mu > 0).any() else 0.0
    elif p["architecture"] == "self_consumption":
        lim = p["p_ch_max"] + p["p_dis_max"]
        x = e.clip(-lim, lim)
        p_rec = p["p_ch_max"]
    else:
        x = e * 0.0
        p_rec = p["p_ch_max"]
    deficit = (x.rolling(replan).sum() * 0.25).clip(lower=0)
    surplus = ((-x).rolling(replan).sum() * 0.25).clip(lower=0) if p["architecture"] == "self_consumption" else deficit * 0
    q = float(deficit.quantile(0.95))
    t_rep = replan * 0.25
    return dict(r_E_gen=q / usable, r_E_gen_up=float(surplus.quantile(0.95)) / usable,
                tau_E=q / (p["eta"] * p_rec * t_rep) if p_rec > 0 else float("inf"), architecture=p["architecture"])


def map_(out=None):
    from replay import build_data, load_config, named_periods, with_overrides
    out = out or f"{PAPER}/decision_map"
    reg = json.load(open(f"{PAPER}/final_test/preregistration.json"))["selected"]["primary_threshold_pp_h"]
    bo_name = "det_bo" if "det_bo" in reg else "det"
    bo, ua = point(bo_name, reg[bo_name]["trial"]), point("ua", reg["ua"]["trial"])
    specs = []
    for cap, err, rp in CELLS:
                for n, c, o in (("det_bo", bo_name, bo), ("ua", "ua", ua)):
                    specs.append(dict(tag=f"{n}_cap{cap}_err{err}_rp{rp}", controller=c, overrides=o, error_scale=err,
                                      extra={"SoC": {"E_capacity": cap}, "replay": {"replan_steps": rp}},
                                      cap=cap, err=err, replan=rp))
    for z in ZS:   # nominal vs empirical chance level (72 Wh, real errors, 1 h re-planning)
        specs.append(dict(tag=f"ua_z{z}", controller="ua", overrides={**ua, "mpc": {**ua.get("mpc", {}), "soc_chance_z": z}}, z=z))
    run(out, specs)
    base = with_overrides(load_config(), SCEN); data = build_data(base); t0, t1 = named_periods(data, base)["development"]
    rows = []
    for cap, err, rp in CELLS:
                m = {n: json.load(open(f"{out}/steps/{n}_cap{cap}_err{err}_rp{rp}.json"))["metrics"] for n in ("det_bo", "ua")}
                cfg = with_overrides(base, {"SoC": {"E_capacity": cap}})
                rows.append(dict(cap_wh=cap, error_scale=err, replan_steps=rp, **indicators(data, cfg, t0, t1, cap, err, rp),
                                 **indicators_gen(data, with_overrides(cfg, {"replay": {"replan_steps": rp}}), t0, t1, cap, err, rp),
                                 bo_cost=m["det_bo"]["net_cost_eur"], ua_cost=m["ua"]["net_cost_eur"],
                                 bo_viol=m["det_bo"]["violation_pp_h"], ua_viol=m["ua"]["violation_pp_h"],
                                 bo_prot_h=m["det_bo"].get("protection_h"), ua_prot_h=m["ua"].get("protection_h"),
                                 bo_extra_eur=m["det_bo"].get("extra_grid_eur"), ua_extra_eur=m["ua"].get("extra_grid_eur"),
                                 bo_curt_pct=m["det_bo"].get("curtailed_pct"), ua_curt_pct=m["ua"].get("curtailed_pct"),
                                 cost_diff_pct=100 * (m["ua"]["net_cost_eur"] / m["det_bo"]["net_cost_eur"] - 1),
                                 viol_diff=m["ua"]["violation_pp_h"] - m["det_bo"]["violation_pp_h"]))
    pd.DataFrame(rows).to_csv(f"{out}/map.csv", index=False)
    zr = []
    for z in ZS:
        st = pd.read_csv(f"{out}/steps/ua_z{z}.csv"); m = json.load(open(f"{out}/steps/ua_z{z}.json"))["metrics"]
        zr.append(dict(z=z, nominal_alpha_pct=100 * 0.5 * (1 - erf(z / sqrt(2))),
                       empirical_below_pct=100 * float((st.soc < 0.1 - 1e-4).mean()),
                       viol_pp_h=m["violation_pp_h"], net_cost_eur=m["net_cost_eur"]))
    pd.DataFrame(zr).to_csv(f"{out}/chance_calibration.csv", index=False)
    print(pd.DataFrame(rows).round(4).to_string(index=False)); print(pd.DataFrame(zr).round(4).to_string(index=False))

def dev_recovery():
    """The pre-registered development points again, with mpc.soc_recovery (code after the final test)."""
    out = f"{PAPER}/recovery_check"
    reg = json.load(open(f"{PAPER}/final_test/preregistration.json"))["selected"]["primary_threshold_pp_h"]
    specs = [dict(tag=f"{c}_t{reg[c]['trial']:02d}", controller=c, overrides=point(c, reg[c]["trial"]))
             for c in ("det", "det_bo", "ua")]
    run(out, specs)
    rows = []
    for s in specs:
        new = json.load(open(f"{out}/steps/{s['tag']}.json"))["metrics"]
        old = json.load(open(f"{PAPER}/solver_check/steps/{s['tag']}.json"))["metrics"]
        rows.append(dict(run=s["tag"], cost_before=old["net_cost_eur"], cost_after=new["net_cost_eur"],
                         viol_before=old["violation_pp_h"], viol_after=new["violation_pp_h"],
                         solver_before=old["solver_success_pct"], solver_after=new["solver_success_pct"]))
    r = pd.DataFrame(rows); r.to_csv(f"{out}/before_after.csv", index=False); print(r.round(4).to_string(index=False))

def variability():
    """UA with the same mean sigma but no (constant) or doubled time variability, map cells with recovery."""
    out = f"{PAPER}/variability"
    reg = json.load(open(f"{PAPER}/final_test/preregistration.json"))["selected"]["primary_threshold_pp_h"]
    ua = point("ua", reg["ua"]["trial"])
    cells = [(72, 1.0, 4), (36, 1.0, 4), (72, 2.0, 4), (72, 1.0, 16)]
    specs = [dict(tag=f"ua_{shape}_cap{cap}_err{err}_rp{rp}", controller="ua", overrides=ua, error_scale=err,
                  sigma_shape=shape, extra={"SoC": {"E_capacity": cap}, "replay": {"replan_steps": rp}},
                  cap=cap, err=err, replan=rp)
             for cap, err, rp in cells for shape in ("constant", "amplified")]
    run(out, specs)
    rows = []
    mp = pd.read_csv(f"{PAPER}/decision_map_recovery/map.csv")
    for cap, err, rp in cells:
        r = mp[(mp.cap_wh == cap) & (mp.error_scale == err) & (mp.replan_steps == rp)].iloc[0]
        row = dict(cap_wh=cap, error_scale=err, replan_steps=rp, r_E=r.r_E, bo_viol=r.bo_viol, bo_cost=r.bo_cost,
                   ua_viol=r.ua_viol, ua_cost=r.ua_cost)
        for shape in ("constant", "amplified"):
            m = json.load(open(f"{out}/steps/ua_{shape}_cap{cap}_err{err}_rp{rp}.json"))["metrics"]
            row[f"{shape}_viol"] = m["violation_pp_h"]
            row[f"{shape}_cost"] = m["net_cost_eur"]
        rows.append(row)
    r = pd.DataFrame(rows); r.to_csv(f"{out}/variability.csv", index=False); print(r.round(4).to_string(index=False))

if __name__ == "__main__":
    {"solver": solver, "prereg": prereg, "final": final, "map": map_, "dev_recovery": dev_recovery,
     "map_recovery": lambda: map_(f"{PAPER}/decision_map_recovery"), "variability": variability}[sys.argv[1]]()
