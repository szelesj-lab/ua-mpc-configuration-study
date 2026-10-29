from functools import lru_cache

import joblib
import numpy as np
import pandas as pd

from features import solar_features, wind_features
from settings import DATA_DIR, MODELS_DIR, load_config

config = load_config()


## Data upload --> the GPR models are loaded on first use, so this module can be
## imported (e.g. by the tests) before the models have been trained
@lru_cache(maxsize=None)
def _load_gpr_models():
    try:
        return {
            "solar_gpr": joblib.load(MODELS_DIR / "gpr_solar_model.joblib"),
            "scaler_X_solar": joblib.load(MODELS_DIR / "scaler_X_solar.joblib"),
            "scaler_y_solar": joblib.load(MODELS_DIR / "scaler_y_solar.joblib"),
            "wind_gpr": joblib.load(MODELS_DIR / "gpr_wind_model.joblib"),
            "scaler_X_wind": joblib.load(MODELS_DIR / "scaler_X_wind.joblib"),
            "scaler_y_wind": joblib.load(MODELS_DIR / "scaler_y_wind.joblib"),
        }
    except FileNotFoundError as e:
        raise FileNotFoundError(
            f"{e.filename} not found. Train the GPR models first: python src/scicomp_training.py"
        ) from e


## Energy demand model --> production.csv is needed
## In respect of production hours or planned product number, we get the needed energy as demand
## It can be changed later, adjusted to the existing system, only a placeholder
def demand_model(
    schedule_csv_path=DATA_DIR / "production.csv",
    start_time: str = None,
    horizon_steps: int = None,
    step_duration_s: float = None
) -> pd.DataFrame:

    if step_duration_s is None:
        step_duration_s = config.get("general", {}).get("deltaT", 900)  # 900s = 15 min

    if horizon_steps is None:
        horizon_steps = config.get("general", {}).get("horizon", 96)   # 96 steps = 24 hours

    # production.csv uses the site's local time (general.timezone)
    site_tz = config.get("general", {}).get("timezone", "Europe/Budapest")
    if start_time is not None:
        start_dt = pd.Timestamp(start_time)
        if start_dt.tzinfo is not None:
            start_dt = start_dt.tz_convert(site_tz)
        start_dt = start_dt.tz_localize(None)
    else:
        # Current site time rounded up to the nearest step_duration_s
        start_dt = pd.Timestamp.now(tz=site_tz).tz_localize(None).ceil(f"{int(step_duration_s)}s")

    df_schedule = pd.read_csv(schedule_csv_path)
    df_schedule["Timestamp"] = pd.to_datetime(df_schedule["Timestamp"]).dt.tz_localize(None)
    df_schedule = df_schedule.sort_values("Timestamp")

    future_timestamps = pd.date_range(
        start=start_dt,
        periods=horizon_steps,
        freq=f"{int(step_duration_s)}s",
    )
    df_horizon = pd.DataFrame({"Timestamp": future_timestamps})

    df_merged = pd.merge_asof(
        df_horizon,
        df_schedule,
        on="Timestamp",
        direction="backward"
    )

    df_merged["Mode"] = df_merged["Mode"].fillna("STOPPED")
    df_merged["Target_Output"] = df_merged["Target_Output"].fillna(0)

    p_demand_list = [mode_power(row["Mode"], row["Target_Output"], step_duration_s) for _, row in df_merged.iterrows()]

    return pd.DataFrame({
        "Timestamp": df_horizon["Timestamp"],
        "P_demand_W": np.round(p_demand_list, 2)
    })


def mode_power(mode, target, step_duration_s, cfg=None):
    """Average power (W) of one step in a production mode.

    STOPPED: 0; MAX_PRODUCTION: as many units as fit in the step; FIXED_COUNT: target units/h
    (at most the maximum). One unit takes production.cat_time_s at production.cat_power_w.
    """
    production_cfg = (cfg or config).get("production", {})
    unit_time_s = production_cfg.get("cat_time_s", production_cfg.get("unit_time_s", 30))
    unit_power_w = production_cfg.get("cat_power_w", production_cfg.get("unit_power_w", 24))
    max_units_per_step = int(step_duration_s // unit_time_s)
    steps_per_hour = 3600.0 / step_duration_s
    if mode == "MAX_PRODUCTION":
        units = max_units_per_step
    elif mode == "FIXED_COUNT":
        units = min(float(target) / steps_per_hour, max_units_per_step)
    else:
        units = 0
    return unit_power_w * (units * unit_time_s / step_duration_s)


## Degradation function --> it gives back the cost of degradation. C deg is given from dataConfig
def BatteryDegradationModel(p_charge: float | list | np.ndarray, p_discharge: float | list | np.ndarray, deltaT: float) -> pd.DataFrame:

    p_charge_arr = np.atleast_1d(p_charge).astype(float).flatten()
    p_discharge_arr = np.atleast_1d(p_discharge).astype(float).flatten()
    c_deg = config["degradation"]["c_deg"]

    deltaT_hours = deltaT / 3600.0


    j_deg = (np.abs(p_charge_arr) + np.abs(p_discharge_arr)) * deltaT_hours * c_deg

    # Not rounded: rounding would hide small changes from the optimizer
    return pd.DataFrame({
        "Degradation_Cost": j_deg
    })

## SoC function --> it gives back the new SoC value
def SoCModel(current_soc: float, p_charge: float | list | np.ndarray, p_discharge: float | list | np.ndarray, deltaT: float) -> pd.DataFrame:

    p_charge_arr = np.atleast_1d(p_charge).astype(float).flatten()
    p_discharge_arr = np.atleast_1d(p_discharge).astype(float).flatten()
    eta = config["SoC"]["eta_batt"]
    e_cap = config["SoC"]["E_capacity"]

    p_eff = (p_charge_arr * eta) - (p_discharge_arr / eta)
    deltaT_hours = deltaT / 3600.0

    soc_changes = np.cumsum(p_eff * deltaT_hours / e_cap)
    new_soc = current_soc + soc_changes

    new_soc_physical = np.clip(new_soc, 0.0, 1.0)

    return pd.DataFrame({
        "SoC":new_soc_physical,
        "SoC_%": new_soc_physical * 100
    })

# Physical model parameters --> from the "pv" and "wind" sections of dataConfig.json
# (the defaults are the values the models were originally developed with)
# These functions are also used by scicomp_training.py, so the GPR is always trained on the
# same base model it is applied to. After changing them, retrain the GPR models.
def _pv_params(cfg=None):
    pv = (cfg or config).get("pv", {})
    return pv.get("rated_power_w", 30.0), pv.get("efficiency", 0.92)


def _wind_params(cfg=None):
    w = (cfg or config).get("wind", {})
    return (
        w.get("air_density", 1.225),
        w.get("rotor_area_m2", 0.4 * 0.8),
        w.get("power_coefficient", 0.2),
        w.get("cut_in_speed_ms", 1.0),
        w.get("rated_speed_ms", 8.0),
        w.get("cut_out_speed_ms", 12.0),
    )


# Base solar calculation function --> it gives back the base solar power based on the forecasted GTI values
def get_solar_power(global_tilted_irradiance, cfg=None):
    system_w, efficiency = _pv_params(cfg)
    return (global_tilted_irradiance / 1000) * system_w * efficiency

# Base wind calculation function --> it gives back the base wind power based on the forecasted wind speed values
def get_wind_power_vectorized(wind_speeds, cfg=None):
    air_density, rotor_area, power_coefficient, cut_in, rated_speed, cut_out = _wind_params(cfg)
    v_eff = np.clip(wind_speeds, 0, rated_speed)
    power_output = 0.5 * air_density * rotor_area * power_coefficient * (v_eff ** 3)
    res = np.where((wind_speeds < cut_in) | (wind_speeds > cut_out), 0.0, power_output)
    if isinstance(wind_speeds, pd.Series):
        return pd.Series(res, index=wind_speeds.index)
    return res

## Wind power prediction function --> it gives back the wind power prediction based on the forecasted wind speed values
def predict_windPower(forecast_df):
    df_base_wind = get_wind_power_vectorized(forecast_df["wind_speed_10m"].to_numpy(dtype=float))
    X_wind_df = wind_features(forecast_df, forecast_df["date"], df_base_wind)

    models = _load_gpr_models()
    X_scaled = models["scaler_X_wind"].transform(X_wind_df)
    mean_power_scaled, std_power = models["wind_gpr"].predict(X_scaled, return_std=True)

    wind_corr = models["scaler_y_wind"].inverse_transform(mean_power_scaled.reshape(-1, 1)).flatten()
    final_wind_power = np.clip(df_base_wind + wind_corr, 0, None)

    # The GPR std is in the scaled target space --> convert it back to W
    std_power_w = std_power * models["scaler_y_wind"].scale_[0]

    return final_wind_power, std_power_w


## Solar power prediction
def predict_solarPower(forecast_df):
    gti = forecast_df["global_tilted_irradiance"].to_numpy(dtype=float)
    df_base_solar = get_solar_power(gti)
    X_solar_df = solar_features(gti, forecast_df["date"], df_base_solar)

    models = _load_gpr_models()
    X_scaled = models["scaler_X_solar"].transform(X_solar_df)
    mean_power_scaled, std_power = models["solar_gpr"].predict(X_scaled, return_std=True)

    solar_corr = models["scaler_y_solar"].inverse_transform(mean_power_scaled.reshape(-1, 1)).flatten()
    final_solar_power = np.clip(df_base_solar + solar_corr, 0, None)

    # The GPR std is in the scaled target space --> convert it back to W
    std_power_w = std_power * models["scaler_y_solar"].scale_[0]

    # The solar GPR was trained on daytime samples only (GTI > 0). At night there is
    # no PV production and no forecast uncertainty, so the model output is not used.
    night = gti <= 0
    final_solar_power = np.where(night, 0.0, final_solar_power)
    std_power_w = np.where(night, 0.0, std_power_w)

    return final_solar_power, std_power_w


## Aggragated deviation function --> it gives back the aggregated deviation of solar and wind power prediction based on the forecasted GTI and wind speed values
def aggregated_deviation(solar_dev: float | list | np.ndarray, wind_dev: float | list | np.ndarray) -> pd.DataFrame:
    solar_dev_arr = np.atleast_1d(solar_dev).astype(float).flatten()
    wind_dev_arr = np.atleast_1d(wind_dev).astype(float).flatten()

    aggregated_dev = np.sqrt(solar_dev_arr ** 2 + wind_dev_arr ** 2)

    return pd.DataFrame({
        "Solar_Deviation_W": solar_dev_arr,
        "Wind_Deviation_W": wind_dev_arr,
        "Aggregated_Deviation_W": np.round(aggregated_dev, 2)
    })
