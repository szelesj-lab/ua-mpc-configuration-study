"""Download new training data and append it to the CSV files in data/.

    python src/update_data.py                         # append everything new since the last stored row
    python src/update_data.py --only prices           # only the ENTSO-E price dataset (SVR)
    python src/update_data.py --only weather          # only the Open-Meteo weather datasets (GPR)
    python src/update_data.py --rebuild-from 2025-06-01   # re-download the weather history from that date

Sources
- ENTSO-E Transparency Platform (needs ENTSOE_API_KEY): day-ahead prices, load forecast and
  day-ahead wind and solar generation forecast -> data/ENTSOE_HU_Dataset_2025.csv (15 min, UTC)
- Open-Meteo Historical Forecast API: the weather forecasts that were issued for past days
  -> data/Past_Forecast_Values.csv (hourly)
- Open-Meteo Historical Weather API (reanalysis): what the weather actually was
  -> data/Past_Real_Values.csv (hourly)

The irradiance value (instantaneous or preceding-hour mean) follows openmeteo.gti_variable,
the same setting the live forecast uses. The weather files should be built with one setting
throughout; use --rebuild-from after changing it (or the panel tilt/azimuth).
After an update, retrain the models (dashboard Models tab, or the training scripts).
"""

import argparse
import shutil
from datetime import date, timedelta

import pandas as pd

from settings import DATA_DIR, get_entsoe_api_key, load_config

ENTSOE_CSV = DATA_DIR / "ENTSOE_HU_Dataset_2025.csv"
FORECAST_CSV = DATA_DIR / "Past_Forecast_Values.csv"
REAL_CSV = DATA_DIR / "Past_Real_Values.csv"

WEATHER_COLUMNS = [
    "wind_speed_10m", "global_tilted_irradiance", "wind_speed_100m",
    "wind_gusts_10m", "wind_direction_10m", "temperature_2m",
]
HISTORICAL_FORECAST_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
REANALYSIS_DELAY_DAYS = 6  # the reanalysis lags a few days behind real time
ENTSOE_CHUNK_DAYS = 30


# ---------------------------------------------------------------- helpers

def last_timestamp(path, column):
    if not path.exists():
        return None
    df = pd.read_csv(path, usecols=[column])
    if df.empty:
        return None
    return pd.to_datetime(df[column], utc=True).max()


def append_rows(path, new_rows: pd.DataFrame, column: str) -> int:
    """Append rows, replacing existing rows with the same timestamp. Returns the number of new timestamps."""
    if new_rows.empty:
        return 0
    if path.exists():
        old = pd.read_csv(path)
        key_old = pd.to_datetime(old[column], utc=True)
        key_new = pd.to_datetime(new_rows[column], utc=True)
        added = int((~key_new.isin(key_old)).sum())
        combined = pd.concat([old[~key_old.isin(key_new)], new_rows], ignore_index=True)
    else:
        added = len(new_rows)
        combined = new_rows
    combined = combined.assign(_k=pd.to_datetime(combined[column], utc=True)).sort_values("_k").drop(columns="_k")
    combined.to_csv(path, index=False)
    return added


# ---------------------------------------------------------------- ENTSO-E (SVR training data)

def _first_series(obj) -> pd.Series:
    return obj.iloc[:, 0] if isinstance(obj, pd.DataFrame) else obj


def combine_entsoe(prices, load_forecast, wind_solar) -> pd.DataFrame:
    """Build rows in the format of ENTSOE_HU_Dataset_2025.csv (15-minute UTC timestamps)."""
    price = _first_series(prices).resample("15min").ffill()
    load = _first_series(load_forecast).resample("15min").ffill()
    ws = wind_solar if isinstance(wind_solar, pd.DataFrame) else wind_solar.to_frame()
    ws = ws.resample("15min").ffill()

    df = pd.DataFrame({"price": price}).join(load.rename("load_forecast"), how="left")
    solar_cols = [c for c in ws.columns if "solar" in str(c).lower()]
    wind_cols = [c for c in ws.columns if "wind" in str(c).lower() and "offshore" not in str(c).lower()]
    df["Solar"] = ws[solar_cols].sum(axis=1).reindex(df.index) if solar_cols else 0.0
    df["Wind Onshore"] = ws[wind_cols].sum(axis=1).reindex(df.index) if wind_cols else 0.0
    df = df.dropna(subset=["price"])

    idx = df.index.tz_convert("UTC").tz_localize(None) if df.index.tz is not None else df.index
    df.index = idx
    df.index.name = "Timestamp"
    return df.reset_index()


def update_entsoe(config, client=None) -> int:
    country = config.get("entsoe", {}).get("country_code", "HU")
    tz = config.get("general", {}).get("timezone", "Europe/Budapest")
    if client is None:
        from entsoe import EntsoePandasClient
        client = EntsoePandasClient(api_key=get_entsoe_api_key())

    last = last_timestamp(ENTSOE_CSV, "Timestamp")
    start = (last.tz_convert(tz) + pd.Timedelta(minutes=15)) if last is not None else pd.Timestamp("2025-01-01", tz=tz)
    end = (pd.Timestamp.now(tz=tz) + pd.Timedelta(days=2)).floor("D")  # includes published day-ahead prices
    if start >= end:
        print("ENTSO-E: already up to date")
        return 0

    added = 0
    chunk_start = start.floor("D")
    while chunk_start < end:
        chunk_end = min(chunk_start + pd.Timedelta(days=ENTSOE_CHUNK_DAYS), end)
        print(f"ENTSO-E: {chunk_start:%Y-%m-%d} -> {chunk_end:%Y-%m-%d}", flush=True)
        prices = client.query_day_ahead_prices(country, start=chunk_start, end=chunk_end)
        load = client.query_load_forecast(country, start=chunk_start, end=chunk_end)
        wind_solar = client.query_wind_and_solar_forecast(country, start=chunk_start, end=chunk_end)
        rows = combine_entsoe(prices, load, wind_solar)
        if last is not None:
            rows = rows[pd.to_datetime(rows["Timestamp"]) > last.tz_localize(None)]
        rows = rows.assign(Timestamp=pd.to_datetime(rows["Timestamp"]).dt.strftime("%Y-%m-%d %H:%M:%S"))
        added += append_rows(ENTSOE_CSV, rows, "Timestamp")
        chunk_start = chunk_end
    print(f"ENTSO-E: {added} new rows")
    return added


# ---------------------------------------------------------------- Open-Meteo (GPR training data)

def _gti_variable(config):
    return "global_tilted_irradiance_instant" if config.get("openmeteo", {}).get("gti_variable", "instant") == "instant" else "global_tilted_irradiance"


def hourly_frame(response, variables) -> pd.DataFrame:
    """Turn an Open-Meteo hourly response into a DataFrame with local-time 'date' column."""
    hourly = response.Hourly()
    dates = pd.date_range(
        start=pd.to_datetime(hourly.Time(), unit="s", utc=True),
        end=pd.to_datetime(hourly.TimeEnd(), unit="s", utc=True),
        freq=pd.Timedelta(seconds=hourly.Interval()),
        inclusive="left",
    ).tz_convert(response.Timezone().decode())
    data = {"date": dates}
    for i, name in enumerate(variables):
        data[name] = hourly.Variables(i).ValuesAsNumpy()
    return pd.DataFrame(data)


def fetch_weather(url, config, start: date, end: date) -> pd.DataFrame:
    from openmeteo_calling import _get_client

    om = config["openmeteo"]
    gti_var = _gti_variable(config)
    variables = ["wind_speed_10m", gti_var, "wind_speed_100m", "wind_gusts_10m", "wind_direction_10m", "temperature_2m"]
    params = {
        "latitude": om["latitude"],
        "longitude": om["longitude"],
        "hourly": variables,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "timezone": "auto",
        "tilt": om.get("tilt", 35),
        "azimuth": om.get("azimuth", 0),
        "wind_speed_unit": "ms",
    }
    response = _get_client().weather_api(url, params=params)[0]
    df = hourly_frame(response, variables).rename(columns={gti_var: "global_tilted_irradiance"})
    return df.dropna(subset=WEATHER_COLUMNS, how="all")


def _format_dates(df):
    return df.assign(date=df["date"].astype(str))


def update_weather(config, rebuild_from=None) -> dict:
    today = date.today()
    targets = {
        "forecast": (FORECAST_CSV, HISTORICAL_FORECAST_URL, today - timedelta(days=1)),
        "real": (REAL_CSV, ARCHIVE_URL, today - timedelta(days=REANALYSIS_DELAY_DAYS)),
    }
    added = {}
    for name, (path, url, end) in targets.items():
        if rebuild_from is not None:
            start = rebuild_from
            if path.exists():
                shutil.copy(path, path.with_suffix(".csv.bak"))
                print(f"{path.name}: previous file kept as {path.with_suffix('.csv.bak').name}")
        else:
            last = last_timestamp(path, "date")
            start = (last + pd.Timedelta(hours=1)).date() if last is not None else date(2025, 1, 1)
        if start > end:
            print(f"{path.name}: already up to date")
            added[name] = 0
            continue
        print(f"{path.name}: {start} -> {end}", flush=True)
        df = fetch_weather(url, config, start, end)
        if rebuild_from is not None:
            _format_dates(df)[["date", *WEATHER_COLUMNS]].to_csv(path, index=False)
            added[name] = len(df)
        else:
            last = last_timestamp(path, "date")
            if last is not None:
                df = df[df["date"] > last]
            added[name] = append_rows(path, _format_dates(df)[["date", *WEATHER_COLUMNS]], "date")
        print(f"{path.name}: {added[name]} new rows")
    return added


def data_status(now=None) -> dict:
    """Last timestamp of every training dataset (for the dashboard)."""
    now = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="UTC")
    out = {}
    for key, path, col in [
        ("prices", ENTSOE_CSV, "Timestamp"),
        ("weather_forecast", FORECAST_CSV, "date"),
        ("weather_real", REAL_CSV, "date"),
    ]:
        last = last_timestamp(path, col)
        out[key] = {
            "file": path.name,
            "last": last.isoformat() if last is not None else None,
            "age_days": round((now - last).total_seconds() / 86400, 1) if last is not None else None,
        }
    return out


def main():
    parser = argparse.ArgumentParser(description="Append new training data from ENTSO-E and Open-Meteo.")
    parser.add_argument("--only", choices=["prices", "weather"], help="update only one source")
    parser.add_argument("--rebuild-from", type=date.fromisoformat, metavar="YYYY-MM-DD",
                        help="re-download the weather history from this date (the old files are kept as .bak)")
    args = parser.parse_args()

    config = load_config()
    if args.only in (None, "prices"):
        update_entsoe(config)
    if args.only in (None, "weather"):
        update_weather(config, rebuild_from=args.rebuild_from)
    print("Done. Retrain the models to use the new data.")


if __name__ == "__main__":
    main()
