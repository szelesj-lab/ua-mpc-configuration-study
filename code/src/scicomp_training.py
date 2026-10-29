import joblib
import numpy as np
import pandas as pd
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import ConstantKernel as C, Matern, WhiteKernel
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import r2_score, root_mean_squared_error, mean_absolute_error
from sklearn.model_selection import train_test_split

from features import solar_features, wind_features
from referencemodel_functions import get_solar_power, get_wind_power_vectorized
from settings import DATA_DIR, MODELS_DIR, load_config


def evaluate_model(name, y_true, y_base, y_pred):
    mae_b, mae_p = mean_absolute_error(y_true, y_base), mean_absolute_error(y_true, y_pred)
    rmse_b, rmse_p = root_mean_squared_error(y_true, y_base), root_mean_squared_error(y_true, y_pred)
    r2_b, r2_p = r2_score(y_true, y_base), r2_score(y_true, y_pred)

    mae_imp = ((mae_b - mae_p) / mae_b) * 100 if mae_b != 0 else 0
    rmse_imp = ((rmse_b - rmse_p) / rmse_b) * 100 if rmse_b != 0 else 0

    output = (
        f"\n--- {name.upper()} model evaluation ---\n"
        f"Base MAE:  {mae_b:.4f}  |  Model MAE:  {mae_p:.4f}  (Improvement: {mae_imp:.2f}%)\n"
        f"Base RMSE: {rmse_b:.4f}  |  Model RMSE: {rmse_p:.4f}  (Improvement: {rmse_imp:.2f}%)\n"
        f"Base R2:   {r2_b:.4f}  |  Model R2:   {r2_p:.4f}"
    )
    print(output, flush=True)


def subsample_training_set(X_train, y_train, max_samples, random_state):
    """Random subset of the training rows (the test set is not touched).

    GPR training time grows with the cube of the training set size, memory and model
    file size with its square. A few thousand samples usually give nearly the same accuracy.
    """
    if not max_samples or len(X_train) <= max_samples:
        return X_train, y_train
    rng = np.random.default_rng(random_state)
    keep = np.sort(rng.choice(len(X_train), size=int(max_samples), replace=False))
    return X_train.iloc[keep], y_train.iloc[keep]


def main():
    gpr_cfg = load_config().get("gpr", {})
    max_samples = gpr_cfg.get("max_train_samples")
    random_state = gpr_cfg.get("random_state", 42)

    print("Past values loading...")
    df_forecast = pd.read_csv(DATA_DIR / "Past_Forecast_Values.csv", parse_dates=['date'])
    df_real = pd.read_csv(DATA_DIR / "Past_Real_Values.csv", parse_dates=['date'])

    df_merged = pd.merge(
        df_forecast, 
        df_real, 
        on="date", 
        suffixes=("_forecast", "_real")
    )

    # Optional sliding window: train only on the most recent months (gpr.train_months, 0 = all)
    train_months = gpr_cfg.get("train_months", 0)
    if train_months:
        dates_utc = pd.to_datetime(df_merged["date"], utc=True)
        cutoff = dates_utc.max() - pd.DateOffset(months=int(train_months))
        df_merged = df_merged[dates_utc >= cutoff].reset_index(drop=True)
        print(f"Training window: last {train_months} months ({len(df_merged)} hourly rows)")

    # Base calculations
    df_real_solar = get_solar_power(df_merged['global_tilted_irradiance_real'])
    df_real_wind = get_wind_power_vectorized(df_merged['wind_speed_10m_real'])

    df_base_solar = get_solar_power(df_merged['global_tilted_irradiance_forecast'])
    df_base_wind = get_wind_power_vectorized(df_merged['wind_speed_10m_forecast'])

    # Features (shared with the live prediction, see features.py)
    weather_forecast = df_merged.rename(columns=lambda c: c[:-len("_forecast")] if c.endswith("_forecast") else c)
    X_wind_df = wind_features(weather_forecast, df_merged['date'], df_base_wind, index=df_merged.index)

    # Residual calculations
    solar_residual_train = df_real_solar - df_base_solar
    wind_residual_train = df_real_wind - df_base_wind

    X_solar_df = solar_features(df_merged['global_tilted_irradiance_forecast'], df_merged['date'], df_base_solar, index=df_merged.index)

    # Solar modell - only when GTI > 0, so its not at night
    train_onlyDay = X_solar_df['global_tilted_irradiance'] > 0

    X_solar_day = X_solar_df.loc[train_onlyDay]
    y_solar_day = solar_residual_train.loc[train_onlyDay]

##  With that, train test split will be done with shuffle=True, so it will randomly select samples for training and testing
#    X_solar_train, X_solar_test, y_solar_train, y_solar_test, idx_solar_train, idx_solar_test = train_test_split(
#        X_solar_day, y_solar_day, X_solar_day.index, test_size=0.2, random_state=42
#)

## With that, train test split will be done with shuffle=False, so it will select the first 80% of the data for training and the last 20% for testing
    X_solar_train, X_solar_test, y_solar_train, y_solar_test, idx_solar_train, idx_solar_test = train_test_split(
    X_solar_day, y_solar_day, X_solar_day.index, test_size=0.2, shuffle=False
)

    # Scaling
    scaler_X_solar = StandardScaler()
    scaler_y_solar = StandardScaler()

    n_total = len(X_solar_train)
    X_solar_train, y_solar_train = subsample_training_set(X_solar_train, y_solar_train, max_samples, random_state)
    print(f"Solar GPR: training on {len(X_solar_train)} of {n_total} samples", flush=True)

    X_solar_train_scaled = scaler_X_solar.fit_transform(X_solar_train)
    y_solar_train_scaled = scaler_y_solar.fit_transform(np.asarray(y_solar_train).reshape(-1, 1))
    X_solar_test_scaled = scaler_X_solar.transform(X_solar_test)

    # GPR solar training
    gpr_solar = GaussianProcessRegressor(
        kernel = C(1.0) * Matern(length_scale=1.0, nu=1.5) + WhiteKernel(noise_level=0.1),
        alpha=1e-3,
        n_restarts_optimizer=0,
        random_state=42
    )
    gpr_solar.fit(X_solar_train_scaled, y_solar_train_scaled.flatten())

    # Solar modell test - only for day values
    pred_solar_test_scaled = gpr_solar.predict(X_solar_test_scaled)
    solar_corr_test = scaler_y_solar.inverse_transform(pred_solar_test_scaled.reshape(-1, 1)).flatten()
    final_solar_pred_test = df_base_solar.loc[idx_solar_test] + solar_corr_test

    evaluate_model(
        "Solar", 
        y_true=df_real_solar.loc[idx_solar_test], 
        y_base=df_base_solar.loc[idx_solar_test], 
        y_pred=final_solar_pred_test
    )

##  Same for wind (just like solar)
#    X_wind_train, X_wind_test, y_wind_train, y_wind_test, idx_wind_train, idx_wind_test = train_test_split(
#        X_wind_df, wind_residual_train, X_wind_df.index, test_size=0.2, random_state=42
#    )

    X_wind_train, X_wind_test, y_wind_train, y_wind_test, idx_wind_train, idx_wind_test = train_test_split(
    X_wind_df, wind_residual_train, X_wind_df.index, test_size=0.2, shuffle=False
)

    # Scaling for wind
    scaler_X_wind = StandardScaler()
    scaler_y_wind = StandardScaler()

    n_total = len(X_wind_train)
    X_wind_train, y_wind_train = subsample_training_set(X_wind_train, y_wind_train, max_samples, random_state)
    print(f"Wind GPR: training on {len(X_wind_train)} of {n_total} samples", flush=True)

    X_wind_train_scaled = scaler_X_wind.fit_transform(X_wind_train)
    y_wind_train_scaled = scaler_y_wind.fit_transform(np.asarray(y_wind_train).reshape(-1, 1))

    X_wind_test_scaled = scaler_X_wind.transform(X_wind_test)

    # GPR wind training
    gpr_wind = GaussianProcessRegressor(
        kernel=C(1.0) * Matern(length_scale=1.0, nu=1.5) + WhiteKernel(noise_level=0.1),
        alpha=0.1,
        n_restarts_optimizer=0,
        random_state=42
    )
    gpr_wind.fit(X_wind_train_scaled, y_wind_train_scaled.flatten())

    # Wind testing
    pred_wind_test_scaled = gpr_wind.predict(X_wind_test_scaled)
    wind_corr_test = scaler_y_wind.inverse_transform(pred_wind_test_scaled.reshape(-1, 1)).flatten()
    final_wind_pred_test = np.clip(df_base_wind[idx_wind_test] + wind_corr_test, 0, None)

    evaluate_model(
        "Wind turbine", 
        y_true=df_real_wind.loc[idx_wind_test],
        y_base=df_base_wind.loc[idx_wind_test],
        y_pred=final_wind_pred_test
    )
    # Full testing
    common_test_idx = idx_solar_test.intersection(idx_wind_test)

    y_true_total_test = df_real_solar.loc[common_test_idx] + df_real_wind[common_test_idx]
    y_base_total_test = df_base_solar.loc[common_test_idx] + df_base_wind[common_test_idx]

    solar_pred_map = pd.Series(final_solar_pred_test, index=idx_solar_test)
    wind_pred_map = pd.Series(final_wind_pred_test, index=idx_wind_test)

    y_pred_total_test = solar_pred_map.loc[common_test_idx] + wind_pred_map.loc[common_test_idx]

    evaluate_model(
        "Total energy production", 
        y_true=y_true_total_test, 
        y_base=y_base_total_test, 
        y_pred=y_pred_total_test
    )

    print("\nModel and scaler objects saving...")
    MODELS_DIR.mkdir(exist_ok=True)
    joblib.dump(gpr_solar, MODELS_DIR / "gpr_solar_model.joblib")
    joblib.dump(scaler_X_solar, MODELS_DIR / "scaler_X_solar.joblib")
    joblib.dump(scaler_y_solar, MODELS_DIR / "scaler_y_solar.joblib")

    joblib.dump(gpr_wind, MODELS_DIR / "gpr_wind_model.joblib")
    joblib.dump(scaler_X_wind, MODELS_DIR / "scaler_X_wind.joblib")
    joblib.dump(scaler_y_wind, MODELS_DIR / "scaler_y_wind.joblib")
    print("Saved successfully!")


if __name__ == "__main__":
    main()
