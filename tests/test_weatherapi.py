import datetime as dt

import httpx
import respx

from mb.sources.cache import JsonCache
from mb.sources.weatherapi import BASE, WeatherAPI

HOURS = [{"time": f"2026-11-21 {h:02d}:00", "temp_c": 25 + h % 5, "chance_of_rain": 80 if 13 <= h < 17 else 5,
          "precip_mm": 1.2 if 13 <= h < 17 else 0} for h in range(24)]


@respx.mock
async def test_geocode_and_hourly_forecast(tmp_path):
    respx.get(f"{BASE}/timezone.json").mock(return_value=httpx.Response(200, json={
        "location": {"name": "Ubud", "lat": -8.5, "lon": 115.26, "tz_id": "Asia/Makassar"}}))
    fc = respx.get(f"{BASE}/forecast.json").mock(return_value=httpx.Response(200, json={
        "forecast": {"forecastday": [{"date": "2026-11-21", "hour": HOURS}]}}))
    w = WeatherAPI(api_key="k", client=httpx.AsyncClient(), cache=JsonCache("w", root=tmp_path))
    geo = await w.geocode("Ubud")
    assert geo.timezone == "Asia/Makassar"
    day = await w.forecast(geo, dt.date(2026, 11, 21), today=dt.date(2026, 11, 20))
    assert day.source == "live" and day.hours[0].hour == dt.time(6) and day.hours[-1].hour == dt.time(22)
    assert next(h for h in day.hours if h.hour == dt.time(14)).rain_prob == 80
    again = await w.forecast(geo, dt.date(2026, 11, 21), today=dt.date(2026, 11, 20))
    assert again.source == "cached" and fc.call_count == 1


async def test_beyond_the_forecast_range_is_unavailable(tmp_path):
    from mb.sources.openmeteo import Geo
    w = WeatherAPI(api_key="k", client=httpx.AsyncClient(), cache=JsonCache("w", root=tmp_path))
    geo = Geo(name="Ubud", lat=0, lon=0, timezone="Asia/Makassar")
    assert (await w.forecast(geo, dt.date(2026, 12, 30), today=dt.date(2026, 11, 3))).source == "unavailable"


def test_weather_provider_is_a_config_setting():
    from mb.config import load_config
    assert load_config().app.weather_provider == "weatherapi"
