"""Post-hoc solver sensitivity (outside the protocol): configuration B, UA-MPC at its selected point, final-test
period, BMS plant, stricter solver (maxiter 2000, ftol 1e-9) vs the published run.

    KAIROS_ROOT=work/B python experiments/analysis/solver_sensitivity.py
"""
import json, os, sys, time
import pandas as pd
PKG = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
RES = os.environ.get("RESULTS", os.path.join(PKG, "results"))
ROOT = os.environ.get("KAIROS_ROOT", os.path.join(PKG, "work", "B"))
OUT = os.path.join(os.environ.get("OUT", os.path.join(PKG, "out")), "extended"); os.makedirs(OUT, exist_ok=True)
sys.path.insert(0, os.path.join(ROOT, "src"))
from replay import build_data, metrics, named_periods, simulate, load_config, with_overrides  # noqa: E402
R = f"{RES}/summer/B"
SC = {"SoC": {"E_capacity": 72}, "degradation": {"c_deg": 2e-5}, "limits": {"p_charge_max_w": 24.0},
      "mpc": {"architecture": "self_consumption", "soc_recovery": True},
      "replay": {"plant": "bms", "demand_profile_csv": "industrial_load_profile.csv", "demand_mean_w": 10.788}}
trial = json.load(open(f"{R}/final_test/preregistration.json"))["selected"]["primary_threshold_pp_h"]["ua"]["trial"]
ov = next(p["overrides"] for p in json.load(open(f"{R}/tuning/ua_pareto.json"))["points"] if p["trial"] == trial)
cfg = with_overrides(with_overrides(load_config(), SC), ov)
data = build_data(cfg)
t0, t1 = named_periods(data, cfg)["final_test"]
strict = with_overrides(cfg, {"optimizer": {"maxiter": 2000, "ftol": 1e-9}, "replay": {"solver_ftol": 0}})
t = time.time()
df, st = simulate(data, strict, "ua", t0, t1)
m = metrics(df, st, strict)
df.to_csv(f"{OUT}/B_ua_strict.csv"); pd.DataFrame(st).to_csv(f"{OUT}/B_ua_strict_solves.csv", index=False)
ref = json.load(open(f"{R}/final_test/steps/bms_ua.json"))["metrics"]
out = {k: (ref.get(k), m.get(k)) for k in ("net_cost_eur", "protection_h", "solver_success_pct", "extra_grid_eur", "curtailed_pct")}
json.dump(out, open(f"{OUT}/solver_sensitivity.json", "w"), indent=1, default=float)
print(json.dumps(out, indent=1, default=float), f"{time.time() - t:.0f}s")
