"""WeatherAPI.com: geocoding with time zone, and hourly forecasts (Travezy's weather provider).

Same interface as the Open-Meteo adapter (geocode, forecast, scenario), so the switch is the
`weather_provider` setting in config/app.yaml. The key is WEATHERAPI_KEY. The forecast reaches
as far ahead as the plan allows (FORECAST_HORIZON_DAYS); further out, weather is "unavailable"
and test days use stored scenarios, as before.
"""
from __future__ import annotations

import datetime as dt

from decouple import config

from mb.contracts import HourWeather, WeatherDay
from mb.limits import api_client
from mb.sources.cache import get_cache
from mb.sources.openmeteo import FIXTURES, Geo, OpenMeteo

BASE = "https://api.weatherapi.com/v1"
FORECAST_HORIZON_DAYS = 14


class WeatherAPI(OpenMeteo):
    def __init__(self, api_key: str | None = None, client=None, cache=None, fixtures=FIXTURES):
        super().__init__(client=client or api_client("weather"), cache=cache or get_cache("weatherapi"),
                         fixtures=fixtures)
        self.key = api_key or config("WEATHERAPI_KEY", default="")

    async def geocode(self, name: str) -> Geo:
        key = {"geocode": name}
        hit = self.cache.get(key)
        if hit is None:
            r = await self.client.get(f"{BASE}/timezone.json", params={"key": self.key, "q": name})
            if r.status_code == 400:
                raise LookupError(f"WeatherAPI found no place called '{name}'")
            r.raise_for_status()
            loc = r.json()["location"]
            hit = {"name": loc["name"], "lat": loc["lat"], "lon": loc["lon"], "timezone": loc["tz_id"]}
            self.cache.set(key, hit)
        return Geo(**hit)

    async def forecast(self, geo: Geo, day: dt.date, today: dt.date | None = None) -> WeatherDay:
        today = today or dt.date.today()
        ahead = (day - today).days
        if not 0 <= ahead <= FORECAST_HORIZON_DAYS:
            return WeatherDay(location=geo.name, source="unavailable")
        key = {"forecast": geo.name, "date": day.isoformat(), "fetched": today.isoformat()}
        hit = self.cache.get(key)
        source = "cached" if hit else "live"
        if hit is None:
            r = await self.client.get(f"{BASE}/forecast.json", params={
                "key": self.key, "q": f"{geo.lat},{geo.lon}", "days": ahead + 1, "dt": day.isoformat(),
                "aqi": "no", "alerts": "no"})
            r.raise_for_status()
            days = r.json()["forecast"]["forecastday"]
            hit = next((d["hour"] for d in days if d["date"] == day.isoformat()), [])
            self.cache.set(key, hit)
        hours = [HourWeather(hour=dt.datetime.strptime(h["time"], "%Y-%m-%d %H:%M").time(), temp_c=h["temp_c"],
                             rain_prob=int(h.get("chance_of_rain") or 0), rain_mm=float(h.get("precip_mm") or 0))
                 for h in hit]
        return WeatherDay(location=geo.name, source=source, hours=[h for h in hours if 6 <= h.hour.hour <= 22])


def weather_source(provider: str):
    return WeatherAPI() if provider == "weatherapi" else OpenMeteo()
