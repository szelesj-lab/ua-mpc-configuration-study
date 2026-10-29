import os
import time

import joblib
import pandas as pd

from features import create_price_feature_vector as create_feature_vector  # shared with svr_train_newest.py
from settings import MODELS_DIR, RESULTS_DIR, ROOT_DIR, get_entsoe_api_key, load_config

# The ENTSO-E API key is read from the ENTSOE_API_KEY environment variable (or .env).
# Country, history length and cache lifetime come from the "entsoe" section of dataConfig.json,
# the site time zone from general.timezone.
_cfg = load_config()
_entsoe_cfg = _cfg.get('entsoe', {})
CONFIG = {
    'COUNTRY_CODE': _entsoe_cfg.get('country_code', 'HU'),
    'TIMEZONE': _cfg.get('general', {}).get('timezone', 'Europe/Budapest'),
    'HISTORY_DAYS': _entsoe_cfg.get('history_days', 10),
    'CACHE_MINUTES': _entsoe_cfg.get('cache_minutes', 60),
    'MODEL_PATH': MODELS_DIR / 'svr_model_15min.pkl',
    'HORIZON_HOURS': 24,
}
CACHE_DIR = ROOT_DIR / 'cache'


def _cached_query(name, query, country, start, end, cache_minutes):
    """Run an ENTSO-E query, reusing a result saved less than cache_minutes ago."""
    path = CACHE_DIR / f'entsoe_{country}_{name}_{start:%Y%m%d}_{end:%Y%m%d}.pkl'
    if cache_minutes > 0 and path.exists() and time.time() - path.stat().st_mtime < cache_minutes * 60:
        print(f'  {name}: from cache')
        return pd.read_pickle(path)
    data = query(country, start=start, end=end)
    if cache_minutes > 0:
        CACHE_DIR.mkdir(exist_ok=True)
        data.to_pickle(path)
    return data


def to_local_timestamps(timestamps, tz) -> pd.DatetimeIndex:
    """Horizon timestamps in the site time zone.

    Time-zone aware input is converted. Naive input is treated as UTC, because that is
    what numpy datetime64 values taken from a time-zone aware DataFrame column contain.
    """
    ts = pd.DatetimeIndex(pd.to_datetime(timestamps))
    if ts.tz is None:
        ts = ts.tz_localize('UTC')
    return ts.tz_convert(tz)


## Get the electricity prices for the next 24 hours, using the ENTSO-E API. If some prices are missing, use the SVR model to predict them. The function returns a DataFrame with the timestamps, prices in EUR/MWh and EUR/Wh, and a flag indicating whether the price is real or predicted.
def get_electricity_prices(horizon_timestamps=None, config=CONFIG) -> pd.DataFrame:
    from entsoe import EntsoePandasClient

    client = EntsoePandasClient(api_key=config.get('API_KEY') or get_entsoe_api_key())
    country = config['COUNTRY_CODE']
    tz = config.get('TIMEZONE', 'Europe/Budapest')

    if horizon_timestamps is not None:
        timestamps_series = to_local_timestamps(horizon_timestamps, tz)
    else:
        horizon_steps = config['HORIZON_HOURS'] * 4
        now = pd.Timestamp.now(tz=tz)
        start_time = now.floor('15min')
        timestamps_series = pd.date_range(start=start_time, periods=horizon_steps, freq='15min', tz=tz)

    start_time = timestamps_series[0]
    end_time_horizon = timestamps_series[-1]

    fetch_start = (start_time - pd.Timedelta(days=config.get('HISTORY_DAYS', 10))).floor('D')
    fetch_end = (end_time_horizon + pd.Timedelta(days=2)).floor('D')

    print(f'ENTSOE data fetching: {fetch_start} -> {fetch_end} ---')

    cache_minutes = config.get('CACHE_MINUTES', 60)
    try:
        prices_raw = _cached_query('prices', client.query_day_ahead_prices, country, fetch_start, fetch_end, cache_minutes)
        load_fc_raw = _cached_query('load', client.query_load_forecast, country, fetch_start, fetch_end, cache_minutes)
        # Day-ahead wind and solar forecast: the same source as the Solar / Wind Onshore columns the
        # SVR was trained on (query_generation_forecast only gives the total generation forecast)
        gen_fc_raw = _cached_query('wind_solar', client.query_wind_and_solar_forecast, country, fetch_start, fetch_end, cache_minutes)
    except Exception as e:
        raise RuntimeError(f'ENTSO-E request failed: {e}')


    df_forecasts = pd.DataFrame(index=load_fc_raw.index)
    df_forecasts['load_forecast'] = load_fc_raw

    if isinstance(gen_fc_raw, pd.Series):
        df_forecasts['solar_forecast'] = gen_fc_raw if 'solar' in str(gen_fc_raw.name).lower() else 0.0
        df_forecasts['wind_forecast'] = gen_fc_raw if 'wind' in str(gen_fc_raw.name).lower() else 0.0
    else:
        solar_cols = [c for c in gen_fc_raw.columns if 'solar' in str(c).lower()]
        wind_cols = [c for c in gen_fc_raw.columns if 'wind' in str(c).lower()]
        df_forecasts['solar_forecast'] = gen_fc_raw[solar_cols].sum(axis=1) if solar_cols else 0.0
        df_forecasts['wind_forecast'] = gen_fc_raw[wind_cols].sum(axis=1) if wind_cols else 0.0

    df_forecasts = df_forecasts.resample('15min').ffill()
    prices_15m = prices_raw.resample('15min').ffill()

    df_all = df_forecasts.join(prices_15m.rename('price'), how='left')

    # Model predicting missing prices
    if not os.path.exists(config['MODEL_PATH']):
        raise FileNotFoundError(
            f'The model file could not be found: {config["MODEL_PATH"]}. '
            'Train it first: python src/svr_train_newest.py'
        )

    saved_model = joblib.load(config['MODEL_PATH'])
    svr = saved_model['svr']
    scaler = saved_model['scaler']
    features = saved_model['features']

    start_idx = df_all.index.get_loc(start_time)
    history_prices = list(df_all['price'].iloc[start_idx - 672 : start_idx].values)

    result_rows = []
    real_count = 0
    pred_count = 0

    for ts in timestamps_series:
        has_real_price = (ts in df_all.index and not pd.isna(df_all.loc[ts, 'price']))

        if has_real_price:
            price_val = df_all.loc[ts, 'price']
            is_real_val = 1
            real_count += 1
            history_prices.append(price_val)
        else:
            load_fc = df_all.loc[ts, 'load_forecast'] if ts in df_all.index else 0.0
            solar_fc = df_all.loc[ts, 'solar_forecast'] if ts in df_all.index else 0.0
            wind_fc = df_all.loc[ts, 'wind_forecast'] if ts in df_all.index else 0.0

            X_single = create_feature_vector(ts, history_prices, load_fc, solar_fc, wind_fc, features)
            X_scaled = scaler.transform(X_single)

            price_val = svr.predict(X_scaled)[0]
            is_real_val = 0
            pred_count += 1

            history_prices.append(price_val)

        result_rows.append({
            'Timestamp': ts,
            'price_EUR_MWh': price_val,
            'price_EUR_Wh': price_val / 1e6,
            'isReal': is_real_val,
        })

    if pred_count == 0:
        print('All prices for the entire horizon are available!')
    else:
        print(f'{real_count} real prices are available. The missing {pred_count} time steps were predicted using the SVR model.')

    result_df = pd.DataFrame(result_rows)
    return result_df


if __name__ == '__main__':
  df_prices = get_electricity_prices()
  print('\n Dataframe:')
  print(df_prices)
  RESULTS_DIR.mkdir(exist_ok=True)
  df_prices.to_csv(RESULTS_DIR / "SVR_results.csv", index=False)
