"""Energy balance of the scaled cell per period (energy.csv) and per month (monthly_energy.csv).

    KAIROS_ROOT=work/B python experiments/analysis/energy.py

The summer periods use the replay data of the summer work root (needs the summer GP models, see README);
the winter months use the data.pkl files written by experiments/winter/prep_month.py; the monthly energies
need only the weather files and the physical PV and wind models.
"""
import json
import os
import sys
import warnings

import pandas as pd

warnings.filterwarnings("ignore")
PKG = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
RES = os.environ.get("RESULTS", os.path.join(PKG, "results"))
ROOT = os.environ.get("KAIROS_ROOT", os.path.join(PKG, "work", "B"))
WORK = os.environ.get("WORK", os.path.join(PKG, "work"))
sys.path.insert(0, os.path.join(ROOT, "src"))
from referencemodel_functions import get_solar_power, get_wind_power_vectorized  # noqa: E402
from replay import _to_steps, _weather, build_data, load_config, named_periods, with_overrides  # noqa: E402

TZ = "Europe/Budapest"
MEAN_W = 10.788
SCEN = {"SoC": {"E_capacity": 72}, "degradation": {"c_deg": 2e-5}, "limits": {"p_charge_max_w": 24.0},
        "replay": {"plant": "bms", "demand_profile_csv": "industrial_load_profile.csv", "demand_mean_w": MEAN_W},
        "mpc": {"architecture": "self_consumption"}}
OUT = os.path.join(RES, "analysis")
os.makedirs(OUT, exist_ok=True)
rows = []


def bal(name, x):
    r = x.solar_real + x.wind_real
    d = x.demand_w
    dt = 0.25
    rows.append(dict(period=name, start=x.index[0], end=x.index[-1], days=round(len(x) / 96, 1), load_wh=d.sum() * dt,
                     pv_wh=x.solar_real.sum() * dt, wind_wh=x.wind_real.sum() * dt, ren_share_pct=100 * r.sum() / d.sum(),
                     surplus_wh=(r - d).clip(lower=0).sum() * dt, surplus_hours_pct=100 * (r > d).mean(),
                     price_mean=x.price_eur_mwh.mean(), price_std=x.price_eur_mwh.std(),
                     neg_price_pct=100 * (x.price_eur_mwh < 0).mean()))


cfg = with_overrides(load_config(), SCEN)
data = build_data(cfg)
for k, (a, b) in named_periods(data, cfg).items():
    bal(k, data[(data.index >= a) & (data.index < b)])
for i, m in enumerate(["2025-10", "2025-11", "2025-12", "2026-01", "2026-02", "2026-03"], 1):
    cal = json.load(open(f"{WORK}/W/m{i}/results/rewind/calibration.json"))
    x = pd.read_pickle(f"{WORK}/W/m{i}/results/rewind/data.pkl")
    t0, t1 = pd.Timestamp(cal["test_start"]), pd.Timestamp(cal["test_end"])
    bal("winter " + m, x[(x.index >= t0) & (x.index < t1)])
pd.DataFrame(rows).to_csv(f"{OUT}/energy.csv", index=False)

# monthly energy of the cell over the plant year (weather reanalysis + physical models, plant load profile)
w = _weather()
cols = ["wind_speed_10m", "global_tilted_irradiance", "wind_speed_100m", "wind_gusts_10m", "wind_direction_10m", "temperature_2m"]
rl = _to_steps(w.rename(columns=lambda c: c[:-5] if c.endswith("_real") else c), cols, 900)
pv = pd.Series(get_solar_power(rl["global_tilted_irradiance"].to_numpy(), cfg), rl.index)
wd = pd.Series(get_wind_power_vectorized(rl["wind_speed_10m"].to_numpy(), cfg), rl.index)
lp = pd.read_csv(os.path.join(ROOT, "data", "industrial_load_profile.csv"), index_col=0, parse_dates=True)["load_pu"] * MEAN_W
m = pd.DataFrame({"load": lp, "pv": pv, "wind": wd}).dropna()
mon = m.tz_convert(TZ).resample("MS").sum() * 0.25 / 1000
mon = mon[(mon.index >= "2025-10-01") & (mon.index < "2026-10-01")]
mon.to_csv(f"{OUT}/monthly_energy.csv")
print(pd.DataFrame(rows).round(2).to_string(index=False))
print(mon.round(2))
