"""Google Places (New): place ID, location and opening hours for each planned stop.

Every lookup is cached (misses too), so each place is paid for once, and every paid call
is checked against and written to the budget ledger. The place ID is what makes a stop,
or a surprise, verifiable.
"""
from __future__ import annotations

import datetime as dt

import httpx
from decouple import config

from mb.budget import Budget
from mb.contracts import OpeningPeriod, PlaceInfo
from mb.sources.cache import JsonCache

TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
SKU = "google_places_text_search"
# The field mask controls cost: ask only for what the brief needs.
FIELDS = ",".join(["places.id", "places.displayName", "places.formattedAddress", "places.location",
                   "places.businessStatus", "places.regularOpeningHours", "places.rating", "places.types"])


def _period(p: dict) -> OpeningPeriod:
    o, c = p["open"], p.get("close")
    close = None
    if c:  # an overnight close (next day) is capped at the end of this day
        same_day = c.get("day") == o.get("day")
        close = dt.time(c.get("hour", 0), c.get("minute", 0)) if same_day else dt.time(23, 59)
    return OpeningPeriod(day=o.get("day", 0), open=dt.time(o.get("hour", 0), o.get("minute", 0)), close=close)

def hours_on(info: PlaceInfo, on: dt.date) -> list[tuple[dt.time, dt.time]]:
    """Opening periods for that date, as (open, close)."""
    google_day = (on.weekday() + 1) % 7
    return [(p.open, p.close or dt.time(23, 59)) for p in info.periods if p.day == google_day]

def is_open(info: PlaceInfo, on: dt.date, start: dt.time, end: dt.time) -> bool | None:
    """True if open for the whole visit, False if closed, None if hours aren't published."""
    if info.business_status and info.business_status != "OPERATIONAL":
        return False
    if not info.periods:
        return None
    if len(info.periods) == 1 and info.periods[0].close is None:
        return True  # open 24 hours
    google_day = (on.weekday() + 1) % 7  # Python: Monday = 0; Google: Sunday = 0
    for p in info.periods:
        if p.day == google_day and p.open <= start and end <= (p.close or dt.time(23, 59)):
            return True
    return False


class GooglePlaces:
    def __init__(self, budget: Budget, api_key: str | None = None,
                 client: httpx.AsyncClient | None = None, cache: JsonCache | None = None):
        self.key = api_key or config("GOOGLE_MAPS_API_KEY")
        self.client = client or httpx.AsyncClient(timeout=10)
        self.cache = cache or JsonCache("google_places")
        self.budget = budget

    async def lookup(self, query: str, run_id: str) -> PlaceInfo | None:
        key = {"text": query.lower().strip()}
        hit, source = self.cache.get(key), "cached"
        if hit is None:
            self.budget.check(self.budget.prices[SKU])  # refuse before spending, not after
            r = await self.client.post(
                TEXT_SEARCH_URL, json={"textQuery": query, "pageSize": 1},
                headers={"X-Goog-Api-Key": self.key, "X-Goog-FieldMask": FIELDS})
            r.raise_for_status()
            self.budget.record_call(run_id=run_id, sku=SKU)
            found = r.json().get("places") or []
            hit, source = (found[0] if found else {}), "live"
            self.cache.set(key, hit)
        return self._parse(hit, source, query) if hit else None

    async def search_near(self, query: str, lat: float, lon: float, run_id: str,
                          radius_m: int = 1500, n: int = 5) -> list[PlaceInfo]:
        """Up to n places matching the query, biased to a circle around (lat, lon). Cached."""
        key = {"near": query.lower().strip(), "lat": round(lat, 3), "lon": round(lon, 3), "r": radius_m, "n": n}
        hit, source = self.cache.get(key), "cached"
        if hit is None:
            self.budget.check(self.budget.prices[SKU])
            r = await self.client.post(
                TEXT_SEARCH_URL,
                json={"textQuery": query, "pageSize": n, "locationBias": {"circle": {
                    "center": {"latitude": lat, "longitude": lon}, "radius": float(radius_m)}}},
                headers={"X-Goog-Api-Key": self.key, "X-Goog-FieldMask": FIELDS})
            r.raise_for_status()
            self.budget.record_call(run_id=run_id, sku=SKU)
            hit, source = r.json().get("places") or [], "live"
            self.cache.set(key, hit)
        return [self._parse(p, source, query) for p in hit if p.get("id")]

    @staticmethod
    def _parse(hit: dict, source: str, fallback_name: str) -> PlaceInfo:
        hours = hit.get("regularOpeningHours") or {}
        loc = hit.get("location") or {}
        return PlaceInfo(
            place_id=hit["id"], name=(hit.get("displayName") or {}).get("text", fallback_name),
            address=hit.get("formattedAddress"), lat=loc.get("latitude"), lon=loc.get("longitude"),
            business_status=hit.get("businessStatus"),
            periods=[_period(p) for p in hours.get("periods", [])],
            hours_text=hours.get("weekdayDescriptions", []), rating=hit.get("rating"),
            types=hit.get("types", []), source=source)