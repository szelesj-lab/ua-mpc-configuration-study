import json
import time

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from referencemodel_functions import (
    _pv_params, _wind_params, aggregated_deviation, demand_model, predict_solarPower, predict_windPower,
)
import live_data
from features import DIFFICULTY_FEATURES, difficulty_features
from settings import RESULTS_DIR, load_config


class _StageTimer:
    """Prints how long each stage of a run takes."""

    def __init__(self):
        self.t0 = self.last = time.perf_counter()

    def __call__(self, label):
        now = time.perf_counter()
        print(f"[timing] {label}: {now - self.last:.1f} s", flush=True)
        self.last = now

    def total(self):
        print(f"[timing] total: {time.perf_counter() - self.t0:.1f} s", flush=True)


def build_horizon_dataframe(config, timer=None):
    # Imported here so the cost function itself can be used and tested without the API dependencies
    from openmeteo_calling import get_solar_and_wind_forecast_15min
    from svr_function_grid import get_electricity_prices

    timer = timer or (lambda label: None)
    deltaT = config.get("general", {}).get("deltaT", 900)
    horizon = config.get("general", {}).get("horizon", 96)

    forecast_df = get_solar_and_wind_forecast_15min(config)
    timer("Open-Meteo weather forecast")

    # We get 2 days of forecast, but we only need the first 24 hours for our MPC horizon. Because its not always the first 24 element, we have to check the actual time and get the first 24 hours from that
    now_tz = pd.Timestamp.now(tz=forecast_df['date'].dt.tz)
    # weather difficulty features on the whole forecast, so the 3-hour statistics have history
    difficulty = difficulty_features(forecast_df, forecast_df["date"])
    keep = (forecast_df['date'] >= now_tz).to_numpy()
    difficulty = difficulty[keep].head(horizon).reset_index(drop=True)
    forecast_df = forecast_df[forecast_df['date'] >= now_tz].head(horizon).reset_index(drop=True)

    ## Now, we do the gpr. We have to scale the data before prediction, and after prediction, we have to inverse scale the data.
    mean_wind_power, std_wind_power = predict_windPower(forecast_df)
    mean_solar_power, std_solar_power = predict_solarPower(forecast_df)
    timer("GPR renewable forecast (incl. model loading)")

    ## Now we have the aggregated deviation
    aggregated_deviation_df = aggregated_deviation(std_solar_power, std_wind_power)
    total_power = mean_wind_power + mean_solar_power

    ## Now we merge these: forecast wind, solar, total, and aggregated deviation into one dataframe. Only that!!
    horizon_dataframe = pd.DataFrame({
        "Date": forecast_df["date"],
        "Mean_wind_power (W)": mean_wind_power,
        "Mean_solar_power (W)": mean_solar_power,
        "Total_power (W)": total_power,
        "Aggregated_deviation (W)": aggregated_deviation_df["Aggregated_Deviation_W"]
    })
    # forecast-time weather difficulty, used when mpc.sigma_model = "weather"
    horizon_dataframe = pd.concat([horizon_dataframe, difficulty], axis=1)

    print(f"Forecast data with predicted power and aggregated deviation for the next {len(horizon_dataframe)} steps:\n", horizon_dataframe)

    ## Demand model from the production schedule (data/production.csv).
    ## It starts at the first forecast step, in the site's local time (the forecast uses the site
    ## time zone), so the demand is aligned with the forecast whatever time zone the computer uses.
    demand_df = demand_model(
        start_time=horizon_dataframe["Date"].iloc[0],
        step_duration_s=deltaT,
        horizon_steps=len(horizon_dataframe),
    )
    horizon_dataframe["Demand_power (W)"] = demand_df["P_demand_W"].values
    print(horizon_dataframe)

    ## Grid prices from ENTSO-E, completed with SVR forecasts if needed
    priced_df = get_electricity_prices(horizon_timestamps=pd.DatetimeIndex(horizon_dataframe["Date"]))
    horizon_dataframe["Grid Price (€/Wh)"] = priced_df["price_EUR_Wh"].values
    timer("ENTSO-E prices and SVR price forecast")

    ## The horizon_dataframe now has the following columns:
    ## - Date, Mean_wind_power (W), Mean_solar_power (W), Total_power (W),
    ## - Aggregated_deviation (W), Demand_power (W), Grid Price (€/Wh)
    return horizon_dataframe


## ---------------------------------------------------------------- MPC model
## Decision variables per time step: grid import u_grid, battery discharge u_dis and, if
## mpc.charge_is_decision is true, battery charge u_ch (0 <= u_ch <= renewable forecast).
## Functions taking u_flat use the interleaved layout [u_grid_0, u_dis_0, u_grid_1, u_dis_1, ...];
## the charge profile is passed separately (None = charge follows the forecast, capped at the limit).

def _mpc_params(config):
    soc = config["SoC"]
    w = config["weights"]
    lim = config.get("limits", {})
    mpc = config.get("mpc", {})
    return {
        "dt_h": config["general"]["deltaT"] / 3600.0,
        "eta": soc["eta_batt"],
        "e_cap": soc["E_capacity"],
        "x_ref": soc.get("x_ref", 0.5),
        "soc_min": soc.get("soc_min", 0.10),
        "soc_max": soc.get("soc_max", 0.90),
        "c_deg": config["degradation"]["c_deg"],
        "w_x": w["w_x"], "w_u": w["w_u"], "w_du": w["w_delta_u"],
        "w_T": w["w_T"], "w_ff": w["w_ff"], "lam": w["lambda"],
        # defaults = the limits used before they were moved to the config
        "p_ch_max": lim.get("p_charge_max_w", np.inf),
        "p_dis_max": lim.get("p_discharge_max_w", 5000.0),
        "p_grid_max": lim.get("p_grid_max_w", 10000.0),
        # model options (defaults = original formulation)
        # battery configuration (see ARCHITECTURES); every configuration but the renewable buffer charges
        # by decision, from the renewable surplus and from the grid
        "architecture": _architecture(mpc),
        "charge_is_decision": bool(mpc.get("charge_is_decision", False)) or _architecture(mpc) != "renewable_buffer",
        "smoothness": mpc.get("smoothness", "total"),
        "soc_chance_z": float(mpc.get("soc_chance_z", 0.0)),
        # constant tightening of the SoC limits (SoC fraction), independent of the forecast uncertainty;
        # the "constant back-off" baseline of the deterministic MPC
        "soc_backoff": float(mpc.get("soc_backoff", 0.0) or 0.0),
        # relax the SoC band where it cannot be reached from the current SoC (start outside the band)
        "soc_recovery": bool(mpc.get("soc_recovery", True)),
        # uncertainty handling (defaults = original formulation)
        "unc_scaling": mpc.get("uncertainty_scaling", "absolute"),
        "sigma_ref": sigma_reference_w(config),
        "sigma_scale": float(mpc.get("sigma_scale", 1.0)),
        "sigma_model": mpc.get("sigma_model", "constant"),
        # steps over which the forecast error builds up into the SoC margin (0 = the whole horizon)
        "margin_steps": margin_window_steps(config),
        "sigma_weather_coef": [float(c) for c in mpc.get("sigma_weather_coef", [0.0, 0.0, 0.0])],
        "error_autocorr": float(mpc.get("error_autocorr", 0.0)),
    }


ARCHITECTURES = ("renewable_buffer", "self_consumption", "setpoint")
# renewable_buffer: the renewable power only charges the battery (it never supplies the load directly); the
#                   battery is charged from nothing else; the grid and the battery supply the load (original model)
# self_consumption: the renewable power supplies the load first; the surplus and the grid can charge the battery;
#                   in operation the battery takes up the deviation of the renewable power from its forecast
# setpoint:         as self_consumption in the plan; in operation the battery follows its plan exactly and the grid
#                   (or curtailment) takes up the deviation, so the SoC carries no forecast risk


def _architecture(mpc):
    a = str(mpc.get("architecture", "renewable_buffer") or "renewable_buffer")
    if a not in ARCHITECTURES:
        raise ValueError(f"mpc.architecture must be one of {ARCHITECTURES}, not {a!r}")
    return a


def renewable_split(horizon_dataframe):
    """(surplus, load deficit) of the renewable forecast against the demand, W, both >= 0."""
    mu = np.clip(horizon_dataframe["Total_power (W)"].to_numpy(dtype=float), 0.0, None)
    demand = horizon_dataframe["Demand_power (W)"].to_numpy(dtype=float)
    return np.clip(mu - demand, 0.0, None), np.clip(demand - mu, 0.0, None)


def installed_renewable_w(config):
    """Rated PV power plus the wind turbine power at rated wind speed (W)."""
    pv_w, _ = _pv_params(config)
    rho_air, area, cp, _, v_rated, _ = _wind_params(config)
    return pv_w + 0.5 * rho_air * area * cp * v_rated ** 3


def sigma_reference_w(config):
    """Power that makes the forecast std dimensionless (mpc.sigma_ref_w, 0 = installed renewables)."""
    ref = float(config.get("mpc", {}).get("sigma_ref_w", 0.0) or 0.0)
    return ref if ref > 0 else max(installed_renewable_w(config), 1e-9)


def sigma_multiplier(horizon_dataframe, config):
    """Calibration factor of the GPR std per step.

    mpc.sigma_model = "constant": mpc.sigma_scale everywhere.
    mpc.sigma_model = "weather":  exp(c0 + c1 * gust excess + c2 * irradiance variability / 100), with
    [c0, c1, c2] = mpc.sigma_weather_coef fitted by calibrate_uncertainty.py; the std grows where the
    forecast itself shows gusty, convective or broken-cloud weather (and shrinks in calm weather).
    mpc.sigma_model = "weather_floor": the larger of the two, so the std only ever grows above the
    constant calibration. Without the feature columns the constant factor is used.
    """
    p = _mpc_params(config)
    n = len(horizon_dataframe)
    constant = np.full(n, p["sigma_scale"])
    if p["sigma_model"] not in ("weather", "weather_floor") or not set(DIFFICULTY_FEATURES) <= set(horizon_dataframe.columns):
        return constant
    c0, c1, c2 = p["sigma_weather_coef"]
    gust = np.nan_to_num(horizon_dataframe[DIFFICULTY_FEATURES[0]].to_numpy(dtype=float))
    gvar = np.nan_to_num(horizon_dataframe[DIFFICULTY_FEATURES[1]].to_numpy(dtype=float))
    weather = np.exp(np.clip(c0 + c1 * gust + c2 * gvar / 100.0, -3.0, 3.0))
    # "weather_floor": never below the constant factor, so calm-looking hours keep their reserve
    return np.maximum(constant, weather) if p["sigma_model"] == "weather_floor" else weather


def effective_sigma(horizon_dataframe, config):
    """Forecast std used by the MPC: the GPR std times the calibration factor (sigma_multiplier)."""
    sigma = np.clip(horizon_dataframe["Aggregated_deviation (W)"].to_numpy(dtype=float), 0.0, None)
    return sigma_multiplier(horizon_dataframe, config) * sigma


def forecast_confidence(sigma, p):
    """rho in (0, 1]: how much the charging should follow the forecast.

    "absolute": rho = 1 / (1 + lambda * sigma^2)            (lambda in 1/W^2, depends on system size)
    "relative": rho = 1 / (1 + lambda * (sigma / sigma_ref)^2)  (lambda dimensionless)
    """
    s = sigma / p["sigma_ref"] if p["unc_scaling"] == "relative" else sigma
    return 1.0 / (1.0 + p["lam"] * s ** 2)


def replan_interval_steps(config):
    """MPC steps between two re-plannings: mpc.replan_steps when a caller sets it (the replay does),
    otherwise the live MPC interval (live.mpc_interval_min) divided by the step length."""
    mpc = config.get("mpc", {})
    if mpc.get("replan_steps"):
        return max(1, int(mpc["replan_steps"]))
    step_min = float(config["general"]["deltaT"]) / 60.0
    return max(1, int(round(float(config.get("live", {}).get("mpc_interval_min", 15)) / step_min)))


def margin_window_steps(config):
    """Number of steps the SoC margin accumulates over (0 = the whole horizon).

    mpc.margin_window: "horizon" (original formulation), "replan" (until the next re-planning, which
    restarts from the measured SoC) or "steps" (mpc.margin_steps). Without the key, margin_steps > 0
    means "steps" and 0 means "horizon".
    """
    mpc = config.get("mpc", {})
    steps = int(mpc.get("margin_steps", 0) or 0)
    mode = mpc.get("margin_window") or ("steps" if steps > 0 else "horizon")
    if mode == "replan":
        return replan_interval_steps(config)
    if mode == "steps":
        return max(0, steps)
    return 0


def window_std(sigma, phi, window):
    """Std of the sum of the forecast errors over the `window` steps ending at each step.

    With re-planning every `window` steps, a SoC error can only build up from the last re-planning
    (where the measured SoC resets it) to the step itself; steps before that are corrected by the
    controller. For the first steps the window is shorter (it starts at the beginning of the plan).
    """
    sigma = np.asarray(sigma, dtype=float)
    n = len(sigma)
    if window <= 0 or window >= n:
        return cumulative_std(sigma, phi)
    idx = np.arange(n)
    cov = np.outer(sigma, sigma) * (phi ** np.abs(idx[:, None] - idx[None, :]) if phi > 0 else np.eye(n))
    # summed-area table: the sum of the covariance block [a, k] x [a, k] for each window
    sat = np.zeros((n + 1, n + 1))
    sat[1:, 1:] = cov.cumsum(0).cumsum(1)
    k = idx + 1
    a = np.maximum(k - window, 0)
    var = sat[k, k] - sat[a, k] - sat[k, a] + sat[a, a]
    return np.sqrt(np.clip(var, 0.0, None))


def cumulative_std(sigma, phi):
    """Std of the running sum of forecast errors with per-step std sigma and AR(1) correlation phi.

    phi = 0 gives sqrt(cumsum(sigma^2)) (independent errors). With phi > 0 the errors of
    neighbouring steps add up, so the uncertainty of the stored energy grows faster.
    """
    sigma = np.asarray(sigma, dtype=float)
    if phi <= 0:
        return np.sqrt(np.cumsum(sigma ** 2))
    var = np.empty_like(sigma)
    total, c = 0.0, 0.0  # c_k = sum_{j<k} sigma_j * phi^(k-j)
    for k, s in enumerate(sigma):
        total += s * s + 2.0 * s * c
        var[k] = total
        c = phi * (c + s)
    return np.sqrt(np.clip(var, 0.0, None))


def charge_profile(horizon_dataframe, config):
    """Charging power if charging simply follows the forecast: renewable forecast capped at the limit.
    In the self_consumption and setpoint configurations: the forecast renewable surplus over the demand."""
    p = _mpc_params(config)
    if p["architecture"] != "renewable_buffer":
        return np.minimum(renewable_split(horizon_dataframe)[0], p["p_ch_max"])
    mu_ren = np.clip(horizon_dataframe["Total_power (W)"].to_numpy(dtype=float), 0.0, None)
    return np.minimum(mu_ren, p["p_ch_max"])


def soc_margins(horizon_dataframe, config):
    """Tightening of the SoC limits for the forecast uncertainty (chance constraint).

    With independent renewable forecast errors sigma_j, the SoC error after k steps has the std
    k_ch * sqrt(sum_{j<=k} sigma_j^2); with mpc.error_autocorr > 0 the errors are AR(1) correlated
    in time (see cumulative_std). sigma is the calibrated std (mpc.sigma_scale x GPR std).
    With mpc.margin_steps > 0 the errors only add up over that many steps (the re-planning interval),
    because each re-planning starts again from the measured SoC (see window_std). The limits are tightened by z times this std, so the SoC
    stays inside them with roughly the one-sided normal probability of z (z = 1 -> ~84 %).
    The lower limit is always tightened (less renewable energy than forecast drains the battery).
    The upper limit only when charging follows the forecast; when charging is a decision,
    surplus renewable power is curtailed and cannot overfill the battery.
    mpc.soc_backoff adds a constant tightening (SoC fraction) on top, independent of the uncertainty:
    with soc_chance_z = 0 this is the deterministic MPC with a constant back-off.
    """
    p = _mpc_params(config)
    N = len(horizon_dataframe)
    if p["soc_chance_z"] <= 0 and p["soc_backoff"] <= 0:
        return np.zeros(N), np.zeros(N)
    if p["architecture"] == "setpoint":
        # the battery follows its plan; forecast errors go to the grid, not into the SoC
        return np.zeros(N), np.zeros(N)
    margin = np.full(N, max(p["soc_backoff"], 0.0))
    if p["soc_chance_z"] > 0:
        sigma = effective_sigma(horizon_dataframe, config)
        k_ch = p["eta"] * p["dt_h"] / p["e_cap"]
        sigma_soc = k_ch * window_std(sigma, p["error_autocorr"], p["margin_steps"])
        margin = margin + p["soc_chance_z"] * sigma_soc
    # never tighten so much that the band closes
    margin = np.minimum(margin, 0.5 * (p["soc_max"] - p["soc_min"]) - 1e-3)
    if p["architecture"] == "self_consumption":
        # the battery takes up the deviation in both directions: more renewable power than forecast fills it
        return margin, margin.copy()
    upper = np.zeros(N) if p["charge_is_decision"] else margin
    return margin, upper


def _smooth(x, w, prev=None):
    """w * sum of squared increments of x and its gradient. prev=None: the first step is free."""
    if prev is None:
        delta = np.diff(x, prepend=x[:1])
    else:
        delta = np.diff(x, prepend=prev)
    J = w * np.sum(delta ** 2)
    grad = 2.0 * w * (delta - np.append(delta[1:], 0.0))
    return J, grad


def _evaluate(u_flat, horizon_dataframe, x_0, config, prev_u=0.0, with_grad=False, charge=None, charge_ren=None):
    p = _mpc_params(config)
    N = len(horizon_dataframe)
    U = np.asarray(u_flat, dtype=float).reshape((N, 2))
    u_grid = U[:, 0]
    u_bat_dis = U[:, 1]

    grid_prices = horizon_dataframe["Grid Price (€/Wh)"].to_numpy(dtype=float)
    mu_ren = horizon_dataframe["Total_power (W)"].to_numpy(dtype=float)
    sigma = effective_sigma(horizon_dataframe, config)
    u_bat_ch = charge_profile(horizon_dataframe, config) if charge is None else np.asarray(charge, dtype=float)

    # SoC dynamics (same model as SoCModel): x_k = x_0 + sum_j (eta*P_ch - P_dis/eta) * dt / E
    k_ch = p["eta"] * p["dt_h"] / p["e_cap"]
    k_dis = p["dt_h"] / (p["eta"] * p["e_cap"])
    # The optimizer works with the unclipped SoC: the SoC constraints keep it inside
    # [soc_min, soc_max], and clipping would hide the way back from an infeasible start.
    # The reported SoC is clipped to the physical range [0, 1].
    soc_raw = x_0 + np.cumsum(k_ch * u_bat_ch - k_dis * u_bat_dis)
    soc = np.clip(soc_raw, 0.0, 1.0)
    e_x = soc_raw - p["x_ref"]

    # Cost parts
    J_x = p["w_x"] * np.sum(e_x ** 2)

    deg = (np.abs(u_bat_ch) + np.abs(u_bat_dis)) * p["dt_h"] * p["c_deg"]
    E_u = u_grid * grid_prices * p["dt_h"] + deg
    J_u = p["w_u"] * np.sum(E_u)

    # Control smoothness
    #   "total":  increments of u_grid + u_dis (original). With a fixed demand this sum is fixed
    #             by the power balance, so the term is constant and does not affect the decision.
    #   "inputs": increments of every input (grid, discharge and, if it is a decision, charge).
    if p["smoothness"] == "inputs":
        J_g, d_g = _smooth(u_grid, p["w_du"])
        J_d, d_d = _smooth(u_bat_dis, p["w_du"])
        J_c, d_c = _smooth(u_bat_ch, p["w_du"]) if p["charge_is_decision"] else (0.0, np.zeros(N))
        J_du = J_g + J_d + J_c
    else:
        J_du, d_tot = _smooth(u_grid + u_bat_dis, p["w_du"], prev=prev_u)
        d_g = d_d = d_tot
        d_c = np.zeros(N)

    # Uncertainty-weighted feed-forward: following the renewable forecast is rewarded more
    # where the forecast is confident (rho -> 1 for small sigma, -> 0 for large sigma)
    rho = forecast_confidence(sigma, p)
    if p["architecture"] == "renewable_buffer":
        J_ff = p["w_ff"] * np.sum(rho * (u_bat_ch - mu_ren) ** 2)
    else:
        # the renewable part of the charge follows the forecast surplus (charge_ren; the rest is from the grid)
        c_ren = u_bat_ch if charge_ren is None else np.asarray(charge_ren, dtype=float)
        ff_dev = c_ren - charge_profile(horizon_dataframe, config)
        J_ff = p["w_ff"] * np.sum(rho * ff_dev ** 2)

    J_T = p["w_T"] * e_x[-1] ** 2

    out = {
        "cost": J_x + J_u + J_du + J_ff + J_T,
        "terms": {"J_x": J_x, "J_u": J_u, "J_du": J_du, "J_ff": J_ff, "J_T": J_T},
        "soc": soc, "soc_raw": soc_raw, "deg": deg, "E_u": E_u, "u_bat_ch": u_bat_ch, "rho": rho,
        "k_ch": k_ch, "k_dis": k_dis,
    }
    if not with_grad:
        return out

    # Analytic gradient. dSoC_k/dP_dis_j = -k_dis and dSoC_k/dP_ch_j = +k_ch for j <= k
    a = 2.0 * p["w_x"] * e_x
    a[-1] += 2.0 * p["w_T"] * e_x[-1]
    rev = np.cumsum(a[::-1])[::-1]

    grad = np.empty(2 * N)
    grad[0::2] = p["w_u"] * grid_prices * p["dt_h"] + d_g
    grad[1::2] = -k_dis * rev + p["w_u"] * p["c_deg"] * p["dt_h"] * np.where(u_bat_dis >= 0, 1.0, -1.0) + d_d
    out["grad"] = grad
    if p["architecture"] == "renewable_buffer":
        out["grad_charge"] = (
            k_ch * rev
            + p["w_u"] * p["c_deg"] * p["dt_h"] * np.where(u_bat_ch >= 0, 1.0, -1.0)
            + 2.0 * p["w_ff"] * rho * (u_bat_ch - mu_ren)
            + d_c
        )
    else:
        # gradient w.r.t. the total charge, and the extra term of its renewable part
        out["grad_charge"] = k_ch * rev + p["w_u"] * p["c_deg"] * p["dt_h"] * np.where(u_bat_ch >= 0, 1.0, -1.0) + d_c
        out["grad_charge_ren"] = 2.0 * p["w_ff"] * rho * ff_dev
    return out


def cost_function(u_flat, horizon_dataframe, x_0, config, prev_u=0.0, charge=None):
    r = _evaluate(u_flat, horizon_dataframe, x_0, config, prev_u, charge=charge)
    soc_history = [x_0] + r["soc"].tolist()
    return r["cost"], soc_history, r["deg"].tolist(), r["E_u"].tolist()


def cost_gradient(u_flat, horizon_dataframe, x_0, config, prev_u=0.0, charge=None):
    return _evaluate(u_flat, horizon_dataframe, x_0, config, prev_u, with_grad=True, charge=charge)["grad"]


def get_only_cost(u_flat, horizon_df, current_soc, cfg, charge=None):
    return _evaluate(u_flat, horizon_df, current_soc, cfg, charge=charge)["cost"]


def power_balance_constraint(u_flat, horizon_df):
    N = len(horizon_df)
    U = np.asarray(u_flat, dtype=float).reshape((N, 2))
    p_demand = horizon_df["Demand_power (W)"].to_numpy(dtype=float)
    return (U[:, 0] + U[:, 1]) - p_demand


def power_balance_jacobian(N):
    J = np.zeros((N, 2 * N))
    idx = np.arange(N)
    J[idx, 2 * idx] = 1.0
    J[idx, 2 * idx + 1] = 1.0
    return J


def soc_bounds_constraint(u_flat, horizon_df, current_soc, cfg, charge=None):
    p = _mpc_params(cfg)
    soc_future = _evaluate(u_flat, horizon_df, current_soc, cfg, charge=charge)["soc_raw"]
    m_low, m_up = soc_margins(horizon_df, cfg)
    return np.concatenate([soc_future - (p["soc_min"] + m_low), (p["soc_max"] - m_up) - soc_future])


def soc_bounds_jacobian(u_flat, horizon_df, current_soc, cfg, charge=None, wrt_charge=False):
    """Jacobian of soc_bounds_constraint w.r.t. u_flat, or w.r.t. the charge profile."""
    N = len(horizon_df)
    r = _evaluate(u_flat, horizon_df, current_soc, cfg, charge=charge)
    tril = np.tril(np.ones((N, N)))
    if wrt_charge:
        J_soc = r["k_ch"] * tril
    else:
        J_soc = np.zeros((N, 2 * N))
        J_soc[:, 1::2] = -r["k_dis"] * tril
    return np.vstack([J_soc, -J_soc])


def recovery_relaxation(horizon_dataframe, config, x_0, dis_lower, dis_upper, charge_max):
    """How far the SoC band has to be relaxed at each step so that it can be reached from x_0.

    After a disturbance the measured SoC can start below the lower (or above the upper) effective limit.
    The band constraints are then infeasible for the first steps and the solver fails. Instead, the
    band at step i is lowered to the highest SoC reachable by step i (no discharge beyond what the
    grid limit forces, full charging), so the plan returns to the band as fast as the battery allows
    and keeps the band from then on. Zero wherever the band is reachable, i.e. in normal operation.
    Returns (relax_low, relax_up), both >= 0, added to the lower and upper constraint values.
    """
    p = _mpc_params(config)
    N = len(horizon_dataframe)
    if not p["soc_recovery"]:
        return np.zeros(N), np.zeros(N)
    k_ch = p["eta"] * p["dt_h"] / p["e_cap"]
    k_dis = p["dt_h"] / (p["eta"] * p["e_cap"])
    m_low, m_up = soc_margins(horizon_dataframe, config)
    ch_min = np.zeros(N) if p["charge_is_decision"] else np.asarray(charge_max, dtype=float)
    soc_highest = x_0 + np.cumsum(k_ch * np.asarray(charge_max, dtype=float) - k_dis * dis_lower)
    soc_lowest = x_0 + np.cumsum(k_ch * ch_min - k_dis * dis_upper)
    tol = 1e-6
    relax_low = np.clip((p["soc_min"] + m_low) - soc_highest + tol, 0.0, None)
    relax_up = np.clip(soc_lowest - (p["soc_max"] - m_up) + tol, 0.0, None)
    # once the band is reachable it stays enforced: the relaxation can only shrink along the horizon
    relax_low = np.minimum.accumulate(relax_low)
    relax_up = np.minimum.accumulate(relax_up)
    relax_low[relax_low <= tol] = 0.0
    relax_up[relax_up <= tol] = 0.0
    return relax_low, relax_up


def optimize(horizon_dataframe, config, x_0=None, init=None):
    """Solve the MPC problem. init: optional starting point {"dis": W array, "charge": W array}
    (e.g. the previous plan shifted by one step); by default the solver starts from no discharge
    and full charging."""
    p = _mpc_params(config)
    if p["architecture"] != "renewable_buffer":
        return _optimize_grid_charging(horizon_dataframe, config, x_0, init)
    N = len(horizon_dataframe)
    if x_0 is None:
        x_0 = config.get("SoC", {}).get("initial_soc", 0.5)
    opt_cfg = config.get("optimizer", {})
    demand = horizon_dataframe["Demand_power (W)"].to_numpy(dtype=float)
    charge_max = charge_profile(horizon_dataframe, config)

    # The power balance u_grid + u_dis = demand fixes the grid power once the discharge is chosen,
    # so the optimizer decides the discharge (and the charge, if it is a decision variable):
    #   u_grid = demand - u_dis,  0 <= u_dis <= p_dis_max,  0 <= u_grid <= p_grid_max
    lower = np.clip(demand - p["p_grid_max"], 0.0, None)
    upper = np.minimum(p["p_dis_max"], np.clip(demand, 0.0, None))
    if np.any(lower > upper + 1e-9):
        bad = int(np.argmax(lower > upper + 1e-9))
        raise ValueError(
            f"Demand at step {bad} ({demand[bad]:.1f} W) exceeds p_grid_max_w + p_discharge_max_w; "
            "increase the limits in dataConfig.json"
        )
    upper = np.maximum(upper, lower)

    # Normalised variables keep the problem well conditioned for any system size
    s_dis = max(float(np.max(upper)), 1e-9)
    s_ch = max(float(np.max(charge_max)), 1e-9)
    n_c = N if p["charge_is_decision"] else 0

    def split(z):
        d = z[:N] * s_dis
        c = z[N:] * s_ch if n_c else charge_max
        u = np.empty(2 * N)
        u[0::2] = demand - d
        u[1::2] = d
        return u, c

    def raw_fun(z):
        u, c = split(z)
        r = _evaluate(u, horizon_dataframe, x_0, config, with_grad=True, charge=c)
        g = r["grad"]
        grad_d = (g[1::2] - g[0::2]) * s_dis
        if n_c:
            return r["cost"], np.concatenate([grad_d, r["grad_charge"] * s_ch])
        return r["cost"], grad_d

    relax_low, relax_up = recovery_relaxation(horizon_dataframe, config, x_0, lower, upper, charge_max)
    relax = np.concatenate([relax_low, relax_up])

    def soc_con(z):
        u, c = split(z)
        return soc_bounds_constraint(u, horizon_dataframe, x_0, config, charge=c) + relax

    def soc_jac(z):
        u, c = split(z)
        J = soc_bounds_jacobian(u, horizon_dataframe, x_0, config, charge=c)
        J_d = (J[:, 1::2] - J[:, 0::2]) * s_dis
        if n_c:
            J_c = soc_bounds_jacobian(u, horizon_dataframe, x_0, config, charge=c, wrt_charge=True) * s_ch
            return np.hstack([J_d, J_c])
        return J_d

    bounds = list(zip(lower / s_dis, upper / s_dis))
    z_init = np.clip(np.zeros(N), lower / s_dis, upper / s_dis)
    if n_c:
        bounds += [(0.0, cm / s_ch) for cm in charge_max]
        z_init = np.concatenate([z_init, charge_max / s_ch])  # start from "charge everything"
    if init is not None:
        lo_b, hi_b = np.array([b_[0] for b_ in bounds]), np.array([b_[1] for b_ in bounds])
        z_w = z_init.copy()
        if init.get("dis") is not None:
            d0 = np.asarray(init["dis"], dtype=float)[:N]
            z_w[:len(d0)] = d0 / s_dis
        if n_c and init.get("charge") is not None:
            c0 = np.asarray(init["charge"], dtype=float)[:N]
            z_w[N:N + len(c0)] = c0 / s_ch
        z_init = np.clip(z_w, lo_b, hi_b)

    # The cost terms have different units (EUR, W^2, SoC^2), so the total can be anywhere from 1e-3
    # to 1e5 depending on the weights. The objective is normalised to ~1 at the starting point; if the
    # solver still stops on numerical precision, it restarts from where it stopped, rescaled there.
    def solve(z_start):
        f_scale = max(abs(raw_fun(z_start)[0]), 1e-9)

        def fun(z):
            f, g = raw_fun(z)
            return f / f_scale, g / f_scale

        return minimize(
            fun,
            z_start,
            jac=True,
            method="SLSQP",
            bounds=bounds,
            constraints=[{"type": "ineq", "fun": soc_con, "jac": soc_jac}],
            options={"ftol": opt_cfg.get("ftol", 1e-12), "maxiter": opt_cfg.get("maxiter", 500)},
        )

    # Optimization
    res = solve(z_init)
    total_iterations = int(res.get("nit", 0))
    for _ in range(2):
        if res.success or res.get("status") != 8:  # 8 = "Positive directional derivative for linesearch"
            break
        res = solve(np.clip(res.x, [b_[0] for b_ in bounds], [b_[1] for b_ in bounds]))
        total_iterations += int(res.get("nit", 0))
    res.nit = total_iterations

    # Result evaulation
    best_u, best_c = split(res.x)  # Thats the best
    r = _evaluate(best_u, horizon_dataframe, x_0, config, charge=best_c)
    best_cost = r["cost"]
    m_low, m_up = soc_margins(horizon_dataframe, config)

    U_best = best_u.reshape((N, 2))
    result_df = horizon_dataframe.copy()

    result_df["P_grid_opt (W)"] = np.round(U_best[:, 0], 2)
    result_df["P_bat_dis_opt (W)"] = np.round(U_best[:, 1], 2)
    result_df["P_bat_ch (W)"] = np.round(r["u_bat_ch"], 2)
    result_df["P_curtailed (W)"] = np.round(np.clip(result_df["Total_power (W)"].to_numpy(dtype=float), 0, None) - r["u_bat_ch"], 2)
    result_df["SoC_opt"] = np.round(r["soc"], 4)
    result_df["SoC_opt (%)"] = np.round(r["soc"] * 100, 2)
    result_df["SoC_min_eff (%)"] = np.round((p["soc_min"] + m_low) * 100, 2)
    result_df["SoC_max_eff (%)"] = np.round((p["soc_max"] - m_up) * 100, 2)
    result_df["Forecast_confidence"] = np.round(r["rho"], 4)
    result_df["Sigma_eff (W)"] = np.round(effective_sigma(horizon_dataframe, config), 4)
    result_df["Grid and degradation cost"] = np.round(r["E_u"], 6)

    res.cost_terms = {k: float(v) for k, v in r["terms"].items()}
    res.max_balance_error_w = float(np.max(np.abs(power_balance_constraint(best_u, horizon_dataframe))))
    res.min_soc_margin = float(np.min(soc_bounds_constraint(best_u, horizon_dataframe, x_0, config, charge=best_c)))
    res.initial_soc = float(x_0)
    res.recovery_steps = int(np.count_nonzero(relax_low) + np.count_nonzero(relax_up))
    return res, best_cost, result_df


PLAN_HISTORY_DAYS = 7


def save_plan_history(result_df, now=None):
    """Keep a copy of every plan (results/plans/plan_<UTC time>.csv) for the plan-vs-measured view.

    With the MPC re-running every interval, the dashboard shows for each past moment the plan that
    was in force then. Plans older than PLAN_HISTORY_DAYS are removed.
    """
    now = pd.Timestamp.now(tz="UTC") if now is None else now
    plans_dir = RESULTS_DIR / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    result_df.to_csv(plans_dir / f"plan_{now:%Y%m%dT%H%M%S}.csv", index=False)
    cutoff = now - pd.Timedelta(days=PLAN_HISTORY_DAYS)
    for p in plans_dir.glob("plan_*.csv"):
        try:
            if pd.Timestamp(p.stem[5:], tz="UTC") < cutoff:
                p.unlink()
        except ValueError:
            continue


def main():
    config = load_config()
    timer = _StageTimer()
    horizon_dataframe = build_horizon_dataframe(config, timer)

    # Receding horizon: start from the measured SoC when live measurements are fresh enough
    x_0, soc_source = config.get("SoC", {}).get("initial_soc", 0.5), "config (SoC.initial_soc)"
    measured = live_data.measured_soc(config)
    if measured is not None:
        x_0, soc_source = measured, "live measurement"
    print(f"Initial SoC: {x_0:.3f} from {soc_source}")

    t_solve = time.perf_counter()
    res, best_cost, result_df = optimize(horizon_dataframe, config, x_0=x_0)
    solve_s = time.perf_counter() - t_solve
    timer("MPC optimisation")

    RESULTS_DIR.mkdir(exist_ok=True)
    result_df.to_csv(RESULTS_DIR / "CostFunction_results.csv", index=False)
    save_plan_history(result_df)
    summary = {
        "success": bool(res.success),
        "message": str(res.message),
        "iterations": int(res.nit),
        "total_cost": float(best_cost),
        "cost_terms": res.cost_terms,
        "max_power_balance_error_w": res.max_balance_error_w,
        "min_soc_margin": res.min_soc_margin,
        "initial_soc": res.initial_soc,
        "initial_soc_source": soc_source,
        "model": {k: config.get("mpc", {}).get(k) for k in ("charge_is_decision", "smoothness", "soc_chance_z")},
    }
    with open(RESULTS_DIR / "CostFunction_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    # run log and decision log: the record of the live operation (results/runs/, see run_log.py)
    try:
        import run_log
        run_log.log_run(res, result_df, config, soc_source, solve_s, total_cost=best_cost)
    except Exception as e:  # the log must never stop the controller
        print(f"Run log not written: {e}")

    print(f"Optimizer: {'converged' if res.success else 'DID NOT CONVERGE'} ({res.message}, {res.nit} iterations)")
    print("Lowest cost:", best_cost)
    print("Cost terms:", res.cost_terms)
    print(result_df)
    timer.total()


if __name__ == "__main__":
    main()


def _optimize_grid_charging(horizon_dataframe, config, x_0=None, init=None):
    """optimize() for the self_consumption and setpoint configurations.

    The renewable forecast first supplies the demand. Per step the solver decides the discharge d (only into
    the remaining load deficit, the battery does not export), the charge from the forecast surplus c_ren and
    the charge from the grid c_grid:
        grid = deficit - d + c_grid,   curtailed = surplus - c_ren,   c_ren + c_grid <= p_charge_max.
    The plan is the same in both configurations; they differ in operation (replay.simulate) and in the SoC
    margins (soc_margins). init: {"dis", "charge", "charge_grid"} in W.
    """
    p = _mpc_params(config)
    N = len(horizon_dataframe)
    if x_0 is None:
        x_0 = config.get("SoC", {}).get("initial_soc", 0.5)
    if not np.isfinite(p["p_ch_max"]):
        raise ValueError("limits.p_charge_max_w must be finite when the battery can charge from the grid")
    opt_cfg = config.get("optimizer", {})
    surplus, deficit = renewable_split(horizon_dataframe)
    cr_max = np.minimum(surplus, p["p_ch_max"])
    cg_max = np.full(N, p["p_ch_max"])
    d_max = np.minimum(p["p_dis_max"], deficit)
    if np.any(deficit - d_max > p["p_grid_max"] + 1e-9):
        bad = int(np.argmax(deficit - d_max > p["p_grid_max"] + 1e-9))
        raise ValueError(f"Demand at step {bad} exceeds p_grid_max_w + p_discharge_max_w + renewables")

    s_dis = max(float(np.max(d_max)), 1e-9)
    s_ch = max(float(p["p_ch_max"]), 1e-9)

    def split(z):
        d, cr, cg = z[:N] * s_dis, z[N:2 * N] * s_ch, z[2 * N:] * s_ch
        u = np.empty(2 * N)
        u[0::2] = deficit - d + cg
        u[1::2] = d
        return u, cr, cg

    def raw_fun(z):
        u, cr, cg = split(z)
        r = _evaluate(u, horizon_dataframe, x_0, config, with_grad=True, charge=cr + cg, charge_ren=cr)
        g, gc = r["grad"], r["grad_charge"]
        return r["cost"], np.concatenate([(g[1::2] - g[0::2]) * s_dis,
                                          (gc + r["grad_charge_ren"]) * s_ch,
                                          (gc + g[0::2]) * s_ch])

    relax_low, relax_up = recovery_relaxation(horizon_dataframe, config, x_0, np.zeros(N), d_max, cg_max)
    relax = np.concatenate([relax_low, relax_up])

    def soc_con(z):
        u, cr, cg = split(z)
        return soc_bounds_constraint(u, horizon_dataframe, x_0, config, charge=cr + cg) + relax

    def soc_jac(z):
        u, cr, cg = split(z)
        J = soc_bounds_jacobian(u, horizon_dataframe, x_0, config, charge=cr + cg)
        J_c = soc_bounds_jacobian(u, horizon_dataframe, x_0, config, charge=cr + cg, wrt_charge=True) * s_ch
        return np.hstack([(J[:, 1::2] - J[:, 0::2]) * s_dis, J_c, J_c])

    # linear limits: total charge power and grid power
    eye = np.eye(N)
    A_lin = np.vstack([np.hstack([np.zeros((N, N)), -eye * s_ch, -eye * s_ch]),     # p_ch_max - cr - cg >= 0
                       np.hstack([eye * s_dis, np.zeros((N, N)), -eye * s_ch])])    # p_grid_max - grid >= 0
    b_lin = np.concatenate([np.full(N, p["p_ch_max"]), np.full(N, p["p_grid_max"]) - deficit])
    lin = {"type": "ineq", "fun": lambda z: A_lin @ z + b_lin, "jac": lambda z: A_lin}

    bounds = ([(0.0, v / s_dis) for v in d_max] + [(0.0, v / s_ch) for v in cr_max]
              + [(0.0, v / s_ch) for v in cg_max])
    lo_b, hi_b = np.array([b_[0] for b_ in bounds]), np.array([b_[1] for b_ in bounds])
    z_init = np.concatenate([np.zeros(N), cr_max / s_ch, np.zeros(N)])  # no discharge, charge the surplus
    if init is not None:
        if init.get("dis") is not None:
            d0 = np.asarray(init["dis"], dtype=float)[:N]
            z_init[:len(d0)] = d0 / s_dis
        if init.get("charge") is not None:
            c0 = np.asarray(init["charge"], dtype=float)[:N]
            g0 = np.zeros(len(c0)) if init.get("charge_grid") is None else np.asarray(init["charge_grid"], dtype=float)[:N]
            z_init[N:N + len(c0)] = np.minimum(c0 - g0, cr_max[:len(c0)]) / s_ch
            z_init[2 * N:2 * N + len(g0)] = g0 / s_ch
    z_init = np.clip(z_init, lo_b, hi_b)

    def solve(z_start):
        f_scale = max(abs(raw_fun(z_start)[0]), 1e-9)

        def fun(z):
            f, g = raw_fun(z)
            return f / f_scale, g / f_scale

        return minimize(fun, z_start, jac=True, method="SLSQP", bounds=bounds,
                        constraints=[{"type": "ineq", "fun": soc_con, "jac": soc_jac}, lin],
                        options={"ftol": opt_cfg.get("ftol", 1e-12), "maxiter": opt_cfg.get("maxiter", 500)})

    res = solve(z_init)
    total_iterations = int(res.get("nit", 0))
    for _ in range(2):
        if res.success or res.get("status") != 8:
            break
        res = solve(np.clip(res.x, lo_b, hi_b))
        total_iterations += int(res.get("nit", 0))
    res.nit = total_iterations

    best_u, cr, cg = split(np.clip(res.x, lo_b, hi_b))
    r = _evaluate(best_u, horizon_dataframe, x_0, config, charge=cr + cg, charge_ren=cr)
    m_low, m_up = soc_margins(horizon_dataframe, config)
    U_best = best_u.reshape((N, 2))
    result_df = horizon_dataframe.copy()
    result_df["P_grid_opt (W)"] = np.round(U_best[:, 0], 2)
    result_df["P_bat_dis_opt (W)"] = np.round(U_best[:, 1], 2)
    result_df["P_bat_ch (W)"] = np.round(cr + cg, 2)
    result_df["P_bat_ch_grid (W)"] = np.round(cg, 2)
    result_df["P_curtailed (W)"] = np.round(surplus - cr, 2)
    result_df["SoC_opt"] = np.round(r["soc"], 4)
    result_df["SoC_opt (%)"] = np.round(r["soc"] * 100, 2)
    result_df["SoC_min_eff (%)"] = np.round((p["soc_min"] + m_low) * 100, 2)
    result_df["SoC_max_eff (%)"] = np.round((p["soc_max"] - m_up) * 100, 2)
    result_df["Forecast_confidence"] = np.round(r["rho"], 4)
    result_df["Sigma_eff (W)"] = np.round(effective_sigma(horizon_dataframe, config), 4)
    result_df["Grid and degradation cost"] = np.round(r["E_u"], 6)

    mu = np.clip(horizon_dataframe["Total_power (W)"].to_numpy(dtype=float), 0.0, None)
    demand = horizon_dataframe["Demand_power (W)"].to_numpy(dtype=float)
    res.cost_terms = {k: float(v) for k, v in r["terms"].items()}
    res.max_balance_error_w = float(np.max(np.abs(U_best[:, 0] + U_best[:, 1] + mu - (surplus - cr) - (cr + cg) - demand)))
    res.min_soc_margin = float(np.min(soc_bounds_constraint(best_u, horizon_dataframe, x_0, config, charge=cr + cg)))
    res.initial_soc = float(x_0)
    res.recovery_steps = int(np.count_nonzero(relax_low) + np.count_nonzero(relax_up))
    return res, r["cost"], result_df
