"""Calibrate the renewable forecast uncertainty that the MPC uses.

The GPR std is compared with the real forecast errors on the calibration days of the held-out
period (see replay.named_periods). Out come:

* mpc.sigma_scale         one factor that makes the std match the observed error size
                          (RMS of the errors / RMS of the predicted std) - the "constant" model
* mpc.sigma_weather_coef  [c0, c1, c2] of the "weather" model: the factor is
                          exp(c0 + c1 * gust excess + c2 * irradiance variability / 100), fitted by
                          maximum likelihood. Gusty, convective or broken-cloud weather in the forecast
                          itself then widens the std where the errors really are larger.
* mpc.error_autocorr      AR(1) correlation of the errors between two MPC steps, from the hourly
                          lag-1 autocorrelation of the standardised errors; it widens the SoC margin
                          because errors in neighbouring steps add up

The development period is only reported, never fitted (the final test period is not touched): coverage overall and on the quarter of days with the
largest forecast errors, for both models.

    python src/calibrate_uncertainty.py                     # report only
    python src/calibrate_uncertainty.py --write             # store the values, keep the selected model
    python src/calibrate_uncertainty.py --write --model weather   # and switch the MPC to the weather model
"""

import argparse
import json

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from features import DIFFICULTY_FEATURES
from replay import build_data, named_periods
from settings import CONFIG_PATH, load_config


def coverage(err, sd, k):
    ok = sd > 0
    return 100 * float(np.mean(np.abs(err[ok]) <= k * sd[ok]))


def _hourly(data, start, end):
    d = data[(data.index >= start) & (data.index < end)]
    d = d[d.index.minute == 0]  # hourly: the native resolution of the weather data
    err = ((d["solar_real"] + d["wind_real"]) - (d["solar_mu"] + d["wind_mu"])).to_numpy()
    sd = np.sqrt(d["solar_sd"] ** 2 + d["wind_sd"] ** 2).to_numpy()
    feats = d.reindex(columns=DIFFICULTY_FEATURES).fillna(0.0).to_numpy(dtype=float)
    return d, err, sd, feats


def weather_factor(coef, feats):
    c0, c1, c2 = coef
    return np.exp(np.clip(c0 + c1 * feats[:, 0] + c2 * feats[:, 1] / 100.0, -3.0, 3.0))


def fit_weather(err, sd, feats, start_scale=1.0):
    """Maximum-likelihood fit of the weather model on hours with a positive GPR std."""
    ok = sd > 0
    e, s, x = err[ok], sd[ok], feats[ok]

    def nll(c):
        sig = s * weather_factor(c, x)
        return float(np.sum(np.log(sig) + 0.5 * (e / sig) ** 2))

    res = minimize(nll, x0=[np.log(start_scale), 0.0, 0.0], method="Nelder-Mead",
                   options={"xatol": 1e-6, "fatol": 1e-8, "maxiter": 4000})
    return [float(c) for c in res.x]


def calibrate(data, cfg, start, end):
    d, err, sd, feats = _hourly(data, start, end)
    ok = sd > 0
    scale = float(np.sqrt(np.mean(err[ok] ** 2)) / np.sqrt(np.mean(sd[ok] ** 2)))
    z = pd.Series(np.where(ok, err / np.where(ok, sd, 1), 0.0), index=d.index)
    r_hour = float(z.autocorr(1))
    step_h = float(cfg["general"]["deltaT"]) / 3600
    phi = float(max(r_hour, 0.0) ** step_h)
    day = (d["solar_mu"] > 0).to_numpy()
    e_s = (d["solar_real"] - d["solar_mu"]).to_numpy()[day]
    e_w = (d["wind_real"] - d["wind_mu"]).to_numpy()[day]
    return {
        "hours": int(ok.sum()),
        "rmse_w": float(np.sqrt(np.mean(err ** 2))),
        "rms_sigma_w": float(np.sqrt(np.mean(sd[ok] ** 2))),
        "coverage_1sigma_pct": coverage(err, sd, 1), "coverage_2sigma_pct": coverage(err, sd, 2),
        "coverage_1sigma_calibrated_pct": coverage(err, scale * sd, 1),
        "coverage_2sigma_calibrated_pct": coverage(err, scale * sd, 2),
        "sigma_scale": scale,
        "sigma_weather_coef": fit_weather(err, sd, feats, scale),
        "error_autocorr_1h": r_hour,
        "error_autocorr_step": phi,
        "solar_wind_error_corr_daytime": float(np.corrcoef(e_s, e_w)[0, 1]) if day.sum() > 2 else None,
    }


def evaluate(data, cfg, start, end, scale, coef):
    """Coverage of both models on a period, overall and on its quarter of hardest days."""
    tz = cfg["general"].get("timezone", "Europe/Budapest")
    d, err, sd, feats = _hourly(data, start, end)
    days = pd.Series(d.index.tz_convert(tz).date, index=d.index)
    day_err = pd.Series(np.abs(err), index=d.index).groupby(days.values).sum()
    hard_days = set(day_err[day_err > day_err.quantile(0.75)].index)
    hard = days.isin(hard_days).to_numpy()
    out = {}
    for name, mult in (("constant", np.full(len(sd), scale)), ("weather", weather_factor(coef, feats))):
        s = mult * sd
        for part, m in (("all", np.ones(len(sd), dtype=bool)), ("hard_days", hard), ("other_days", ~hard)):
            ok = m & (sd > 0)
            out[f"{name}_{part}_cov1"] = coverage(err[ok], s[ok], 1)
            out[f"{name}_{part}_cov2"] = coverage(err[ok], s[ok], 2)
            out[f"{name}_{part}_rmse_to_sigma"] = float(np.sqrt(np.mean(err[ok] ** 2)) / np.sqrt(np.mean(s[ok] ** 2)))
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="store the calibration in the config")
    parser.add_argument("--model", choices=["constant", "weather", "weather_floor"], help="with --write: the sigma model the MPC uses")
    args = parser.parse_args()

    cfg = load_config()
    data = build_data(cfg)
    per = named_periods(data, cfg)
    v0, v1 = per["calibration"]
    t0, t1 = per["development"]   # the report never touches the final test period
    tz = cfg["general"].get("timezone", "Europe/Budapest")
    res = calibrate(data, cfg, v0, v1)
    c0, c1, c2 = res["sigma_weather_coef"]
    print(f"Calibration days {v0.tz_convert(tz):%Y-%m-%d} – {v1.tz_convert(tz):%Y-%m-%d} ({res['hours']} hours)")
    print(f"  forecast error RMSE {res['rmse_w']:.2f} W, predicted std (RMS) {res['rms_sigma_w']:.2f} W")
    print(f"  coverage ±1σ {res['coverage_1sigma_pct']:.1f} % (ideal 68.3), ±2σ {res['coverage_2sigma_pct']:.1f} % (ideal 95.4)")
    print(f"  constant model: sigma_scale {res['sigma_scale']:.3f}")
    print(f"  weather model:  factor = exp({c0:.3f} + {c1:.3f} x gust excess [m/s] + {c2:.3f} x irradiance variability [W/m2] / 100)")
    print(f"  error autocorrelation: 1 h {res['error_autocorr_1h']:.2f} -> per MPC step {res['error_autocorr_step']:.3f}")
    print(f"  solar/wind error correlation (daytime): {res['solar_wind_error_corr_daytime']:.3f}")

    print(f"Development period {t0.tz_convert(tz):%Y-%m-%d} – {t1.tz_convert(tz):%Y-%m-%d} (not used for fitting): "
          "coverage ±1σ / ±2σ (ideal 68.3 / 95.4 %), RMSE/σ (ideal 1)")
    ev = evaluate(data, cfg, t0, t1, res["sigma_scale"], res["sigma_weather_coef"])
    for part, label in (("all", "all days"), ("hard_days", "hardest 25 % of days"), ("other_days", "other days")):
        for model in ("constant", "weather"):
            print(f"  {label:<21} {model:<8} {ev[f'{model}_{part}_cov1']:5.1f} / {ev[f'{model}_{part}_cov2']:5.1f} %"
                  f"   RMSE/σ {ev[f'{model}_{part}_rmse_to_sigma']:.2f}")

    if args.write:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        raw.setdefault("mpc", {})
        raw["mpc"]["sigma_scale"] = round(res["sigma_scale"], 3)
        raw["mpc"]["sigma_weather_coef"] = [round(c, 4) for c in res["sigma_weather_coef"]]
        raw["mpc"]["error_autocorr"] = round(res["error_autocorr_step"], 3)
        if args.model:
            raw["mpc"]["sigma_model"] = args.model
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(raw, f, indent=4, ensure_ascii=False)
            f.write("\n")
        print(f"Stored in {CONFIG_PATH.name}: sigma_scale {raw['mpc']['sigma_scale']}, sigma_weather_coef "
              f"{raw['mpc']['sigma_weather_coef']}, error_autocorr {raw['mpc']['error_autocorr']}, "
              f"sigma_model {raw['mpc'].get('sigma_model', 'constant')}")


if __name__ == "__main__":
    main()
