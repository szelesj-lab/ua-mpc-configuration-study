import argparse

import joblib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVR

from features import create_price_feature_vector as create_feature_vector
from settings import DATA_DIR, MODELS_DIR, RESULTS_DIR

CSV_FILENAME = DATA_DIR / 'ENTSOE_HU_Dataset_2025.csv'


def main(show_plot=True):
  print(f'Loading data from {CSV_FILENAME} file')
  df = pd.read_csv(CSV_FILENAME)
  df['Timestamp'] = pd.to_datetime(df['Timestamp'])
  df = df.set_index('Timestamp').sort_index()

  ## Preprocessing
  df_15m = df.resample('15min').mean().interpolate(method='linear')

  solar_cols = [c for c in df_15m.columns if 'solar' in c.lower()]
  wind_cols = [c for c in df_15m.columns if 'wind' in c.lower()]

  df_15m['solar_forecast'] = (
      df_15m[solar_cols].sum(axis=1) if solar_cols else 0.0
  )
  df_15m['wind_forecast'] = df_15m[wind_cols].sum(axis=1) if wind_cols else 0.0

  df_feat = df_15m.copy()

  if 'load_forecast' in df_feat.columns:
    df_feat['net_load'] = df_feat['load_forecast'] - (
        df_feat['solar_forecast'] + df_feat['wind_forecast']
    )

  df_feat['lag_15min'] = df_feat['price'].shift(1)
  df_feat['lag_24h'] = df_feat['price'].shift(96)
  df_feat['lag_48h'] = df_feat['price'].shift(192)
  df_feat['lag_168h'] = df_feat['price'].shift(672)

  minute_of_day = df_feat.index.hour * 60 + df_feat.index.minute
  df_feat['sin_day'] = np.sin(2 * np.pi * minute_of_day / 1440.0)
  df_feat['cos_day'] = np.cos(2 * np.pi * minute_of_day / 1440.0)

  dayofweek = df_feat.index.dayofweek
  month = df_feat.index.month

  df_feat['is_weekend'] = dayofweek.isin([5, 6]).astype(int)
  df_feat['sin_dayofweek'] = np.sin(2 * np.pi * dayofweek / 7.0)
  df_feat['cos_dayofweek'] = np.cos(2 * np.pi * dayofweek / 7.0)
  df_feat['sin_month'] = np.sin(2 * np.pi * month / 12.0)
  df_feat['cos_month'] = np.cos(2 * np.pi * month / 12.0)

  cols_to_drop = solar_cols + wind_cols + ['Solar', 'Wind Onshore']
  df_feat = df_feat.drop(
      columns=[c for c in cols_to_drop if c in df_feat.columns]
  )
  df_feat = df_feat.dropna()

  ## Train-test split --> 2% test, because the model only needs to predict 24h
  split_idx = int(len(df_feat) * 0.98)

  train_df = df_feat.iloc[:split_idx]
  test_df = df_feat.iloc[split_idx:]

  X_train = train_df.drop(columns=['price'])
  y_train = train_df['price']

  features = list(X_train.columns)

  print(
      f'Data Split: Train number: {len(X_train)} (98%) | Test number:'
      f' {len(test_df)} (2%)'
  )

  ## Model fitting
  scaler = StandardScaler()
  X_train_scaled = scaler.fit_transform(X_train)

  conditions = [
      y_train < 50,  # for low prices
      y_train > 200,  # for high prices
  ]
  weights = [
      15.0,
      15.0,
  ]  # They got the same value
  sample_weights = np.select(conditions, weights, default=1.0)

  print(f"SVR Model training with {X_train.shape[1]} X values")
  svr = SVR(kernel="rbf", C=39.6977, epsilon=0.0493, gamma=0.0342)
  svr.fit(X_train_scaled, y_train, sample_weight=sample_weights)

  # Saving model
  MODELS_DIR.mkdir(exist_ok=True)
  joblib.dump(
      {"svr": svr, "scaler": scaler, "features": features}, MODELS_DIR / "svr_model_15min.pkl"
  )
  print(f"Model saved: {MODELS_DIR / 'svr_model_15min.pkl'}")

  ## Test prediction
  print('\nTest prediction on the 2%')


  window_steps = 96
  test_length = len(test_df)
  num_windows = test_length // window_steps

  actual_prices = []
  predicted_prices = []
  timestamps = []

  for w in range(num_windows):
    start_pos = split_idx + (w * window_steps)

    history_prices = list(
        df_feat['price'].iloc[start_pos - 672 : start_pos].values
    )

    for step in range(window_steps):
      idx = start_pos + step
      ts = df_feat.index[idx]

      load_fc = df_feat['load_forecast'].iloc[idx]
      solar_fc = df_feat['solar_forecast'].iloc[idx]
      wind_fc = df_feat['wind_forecast'].iloc[idx]

      X_single = create_feature_vector(
          ts, history_prices, load_fc, solar_fc, wind_fc, features
      )
      X_scaled = scaler.transform(X_single)

      pred_price = svr.predict(X_scaled)[0]

      timestamps.append(ts)
      actual_prices.append(df_feat['price'].iloc[idx])
      predicted_prices.append(pred_price)

      history_prices.append(pred_price)

  ## Evaulation
  y_true = np.array(actual_prices)
  y_pred = np.array(predicted_prices)

  mae = mean_absolute_error(y_true, y_pred)
  rmse = np.sqrt(mean_squared_error(y_true, y_pred))
  r2 = r2_score(y_true, y_pred)
  wape = (np.sum(np.abs(y_true - y_pred)) / np.sum(y_true)) * 100


  print('\n' + '=' * 50)
  print(' Test evaulation: ')
  print('=' * 50)
  print(f'MAE: {mae:.2f} EUR/MWh')
  print(f'RMSE: {rmse:.2f} EUR/MWh')
  print(f'WAPE: {wape:.2f} %')
  print(f'R²: {r2:.4f}')
  print('=' * 50 + '\n')

  display_samples = 14 * 96

  plt.figure(figsize=(14, 6))
  plt.plot(
      timestamps[-display_samples:],
      y_true[-display_samples:],
      label='Real price',
      color='black',
      alpha=0.8,
  )
  plt.plot(
      timestamps[-display_samples:],
      y_pred[-display_samples:],
      label='SVR prediction',
      color='red',
      linestyle='--',
  )
  plt.title(
      'Grid price prediction',
      fontsize=14,
      fontweight='bold',
  )
  plt.ylabel('EUR / MWh')
  plt.legend()
  plt.grid(True)

  RESULTS_DIR.mkdir(exist_ok=True)
  plt.savefig(RESULTS_DIR / 'svr_test_prediction.png', dpi=150, bbox_inches='tight')
  if show_plot:
    plt.show()


if __name__ == '__main__':
  parser = argparse.ArgumentParser(description='Train the SVR grid price model.')
  parser.add_argument('--no-plot', action='store_true', help='only save the test plot, do not open a window (e.g. in CI)')
  args = parser.parse_args()
  main(show_plot=not args.no_plot)
