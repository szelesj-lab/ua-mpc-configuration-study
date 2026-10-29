import pandas as pd

from settings import ROOT_DIR, load_config

_openmeteo_client = None


def _get_client():
    # The client is created on first use, so importing this module does not
    # require network access or create the cache file
    global _openmeteo_client
    if _openmeteo_client is None:
        import openmeteo_requests
        import requests_cache
        from retry_requests import retry

        cache_session = requests_cache.CachedSession(str(ROOT_DIR / ".cache"), expire_after=3600)
        retry_session = retry(cache_session, retries=5, backoff_factor=0.2)
        _openmeteo_client = openmeteo_requests.Client(session=retry_session)
    return _openmeteo_client


def get_solar_and_wind_forecast_15min(config: dict):
    url = "https://api.open-meteo.com/v1/forecast"
    # openmeteo.gti_variable: "instant" = irradiance at the time step, "mean" = mean over the preceding
    # 15 minutes. update_data.py downloads the training history with the same setting.
    gti_variable = (
        "global_tilted_irradiance_instant"
        if config["openmeteo"].get("gti_variable", "instant") == "instant"
        else "global_tilted_irradiance"
    )

    params = {
        "latitude": config["openmeteo"]["latitude"],
        "longitude": config["openmeteo"]["longitude"],
        "minutely_15": ["wind_speed_10m", gti_variable, "wind_speed_100m", "wind_gusts_10m", "temperature_2m", "wind_direction_10m"],
        "timezone": "auto",
        "forecast_days": config["openmeteo"].get("forecast_days", 2),
        "tilt": config["openmeteo"].get("tilt", 35),
        "azimuth": config["openmeteo"].get("azimuth", 180),
        "wind_speed_unit": "ms",
    }

    responses = _get_client().weather_api(url, params=params)
    response = responses[0]

    minutely_15 = response.Minutely15()
    wind_speed = minutely_15.Variables(0).ValuesAsNumpy()
    gti = minutely_15.Variables(1).ValuesAsNumpy()
    wind_speed_100m = minutely_15.Variables(2).ValuesAsNumpy()
    wind_gusts_10m = minutely_15.Variables(3).ValuesAsNumpy()
    temperature_2m = minutely_15.Variables(4).ValuesAsNumpy()
    wind_direction_10m = minutely_15.Variables(5).ValuesAsNumpy()

    minutely_15_data = {
        "date": pd.date_range(
            start=pd.to_datetime(minutely_15.Time(), unit="s", utc=True),
            end=pd.to_datetime(minutely_15.TimeEnd(), unit="s", utc=True),
            freq=pd.Timedelta(seconds=minutely_15.Interval()),
            inclusive="left"
        ).tz_convert(response.Timezone().decode())
    }

    minutely_15_data["wind_speed_10m"] = wind_speed
    minutely_15_data["global_tilted_irradiance"] = gti
    minutely_15_data["wind_speed_100m"] = wind_speed_100m
    minutely_15_data["wind_gusts_10m"] = wind_gusts_10m
    minutely_15_data["temperature_2m"] = temperature_2m
    minutely_15_data["wind_direction_10m"] = wind_direction_10m

    df = pd.DataFrame(data=minutely_15_data)
    return df


if __name__ == "__main__":
    df = get_solar_and_wind_forecast_15min(load_config())
    print(df)
