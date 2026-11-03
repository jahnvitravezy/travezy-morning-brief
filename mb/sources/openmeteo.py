"""Open-Meteo: free, keyless geocoding (with time zone) and hourly forecasts.

Forecasts reach about 16 days ahead. Test-set days are further out, so they use stored
weather scenarios marked source="fixture"; with no scenario, weather is "unavailable".
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import httpx

from mb.limits import api_client
import yaml
from pydantic import BaseModel

from mb.contracts import HourWeather, WeatherDay
from mb.sources.cache import ROOT, JsonCache, get_cache

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
FORECAST_HORIZON_DAYS = 16
FIXTURES = ROOT / "data" / "fixtures" / "weather.yaml"


class Geo(BaseModel):
    name: str
    lat: float
    lon: float
    timezone: str


class OpenMeteo:
    def __init__(self, client: httpx.AsyncClient | None = None, cache: JsonCache | None = None,
                 fixtures: Path = FIXTURES):
        self.client = client or api_client("weather")
        self.cache = cache or get_cache("open_meteo")
        self.fixtures = yaml.safe_load(fixtures.read_text(encoding="utf-8")) if fixtures.exists() else {}

    async def geocode(self, name: str) -> Geo:
        key = {"geocode": name}
        hit = self.cache.get(key)
        if hit is None:
            r = await self.client.get(GEOCODE_URL, params={"name": name, "count": 1})
            r.raise_for_status()
            results = r.json().get("results") or []
            if not results:
                raise LookupError(f"Open-Meteo found no place called '{name}'")
            g = results[0]
            hit = {"name": g["name"], "lat": g["latitude"], "lon": g["longitude"], "timezone": g["timezone"]}
            self.cache.set(key, hit)
        return Geo(**hit)

    async def forecast(self, geo: Geo, day: dt.date, today: dt.date | None = None) -> WeatherDay:
        today = today or dt.date.today()
        if not 0 <= (day - today).days <= FORECAST_HORIZON_DAYS:
            return WeatherDay(location=geo.name, source="unavailable")
        key = {"forecast": geo.name, "date": day.isoformat(), "fetched": today.isoformat()}
        hit = self.cache.get(key)
        source = "cached" if hit else "live"
        if hit is None:
            r = await self.client.get(FORECAST_URL, params={
                "latitude": geo.lat, "longitude": geo.lon, "timezone": geo.timezone,
                "hourly": "temperature_2m,precipitation_probability,precipitation",
                "start_date": day.isoformat(), "end_date": day.isoformat()})
            r.raise_for_status()
            hit = r.json()["hourly"]
            self.cache.set(key, hit)
        hours = [HourWeather(hour=dt.datetime.fromisoformat(t).time(), temp_c=temp,
                             rain_prob=int(p or 0), rain_mm=float(mm or 0))
                 for t, temp, p, mm in zip(hit["time"], hit["temperature_2m"],
                                           hit["precipitation_probability"], hit["precipitation"])]
        return WeatherDay(location=geo.name, source=source, hours=[h for h in hours if 6 <= h.hour.hour <= 22])

    def scenario(self, name: str, location: str) -> WeatherDay:
        """Expands a stored scenario into hourly weather, 06:00 to 22:00."""
        s = self.fixtures[name]
        rain = [(dt.time.fromisoformat(w["from"]), dt.time.fromisoformat(w["to"]), w["prob"], w["mm"])
                for w in s.get("rain", [])]
        hours = []
        for h in range(6, 23):
            t = dt.time(h)
            temp = s["temp_c"]["morning"] if h < 11 else s["temp_c"]["midday"] if h < 16 else s["temp_c"]["evening"]
            prob, mm = next(((p, mm) for a, b, p, mm in rain if a <= t < b), (5, 0.0))
            hours.append(HourWeather(hour=t, temp_c=temp, rain_prob=prob, rain_mm=mm))
        return WeatherDay(location=location, source="fixture", scenario=name, hours=hours)