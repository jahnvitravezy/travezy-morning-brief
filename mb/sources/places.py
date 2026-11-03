"""Google Places (New), backed by a places table: each place is found once and reused.

- A planned stop is found with an IDs-only Text Search (free), then its details (hours, rating,
  location) come from Place Details. Both are stored: the search in place_searches, the place
  in places. The next traveller with the same stop is served from the table at no cost.
- "Ideas near here" use one Text Search with details (5 places per call), and every place
  it returns goes into the table too.
- Refresh rule (config/app.yaml): opening hours are re-checked after places_hours_refresh_days
  (one Place Details call); a stop's search after places_search_refresh_days. If a refresh
  fails, the stored details are used.

The table is Postgres (places, place_searches) when DATABASE_URL is set, local files otherwise.
Every paid call is checked against and written to the budget ledger.
"""
from __future__ import annotations

import datetime as dt
import time

import httpx

from mb.limits import api_client
from decouple import config

from mb.budget import Budget
from mb.contracts import OpeningPeriod, PlaceInfo
from mb.sources.cache import JsonCache

TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
DETAILS_URL = "https://places.googleapis.com/v1/places/"
SKU = "google_places_text_search"            # Enterprise: used for "ideas near here"
SKU_IDS = "google_places_text_search_ids"     # Essentials (IDs only): free
SKU_DETAILS = "google_places_details"         # Enterprise: hours and rating for one place
# The field masks set the price tier: ask only for what the brief needs.
_F = ["id", "displayName", "formattedAddress", "location", "businessStatus", "regularOpeningHours", "rating", "types"]
FIELDS = ",".join("places." + f for f in _F)
DETAIL_FIELDS = ",".join(_F)
DAY_S = 86400


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


class FilePlaceTable:
    """The places table on local files (dev and tests)."""

    def __init__(self, cache: JsonCache):
        self.c = cache

    def get_search(self, key: str):
        hit = self.c.get({"search": key})
        return (hit["ids"], hit["at"]) if hit else None

    def put_search(self, key: str, query: dict, ids: list[str]) -> None:
        self.c.set({"search": key}, {"ids": ids, "at": time.time()})

    def get_place(self, pid: str):
        hit = self.c.get({"place": pid})
        return (hit["raw"], hit["at"]) if hit else None

    def put_place(self, raw: dict) -> None:
        self.c.set({"place": raw["id"]}, {"raw": raw, "at": time.time()})


class PgPlaceTable:
    """The places and place_searches tables (migrations/001_init.sql)."""

    def __init__(self):
        from mb import db
        self.conn = db.conn()

    def get_search(self, key: str):
        r = self.conn.execute("SELECT place_ids, searched_at FROM place_searches WHERE query_key=%s", (key,)).fetchone()
        return (list(r[0]), r[1].timestamp()) if r else None

    def put_search(self, key: str, query: dict, ids: list[str]) -> None:
        from psycopg.types.json import Jsonb
        self.conn.execute("""INSERT INTO place_searches (query_key, query, place_ids) VALUES (%s,%s,%s)
                             ON CONFLICT (query_key) DO UPDATE SET place_ids=EXCLUDED.place_ids, searched_at=now()""",
                          (key, Jsonb(query), ids))

    def get_place(self, pid: str):
        r = self.conn.execute("SELECT details, details_checked_at FROM places WHERE place_id=%s", (pid,)).fetchone()
        return (r[0], r[1].timestamp()) if r else None

    def put_place(self, raw: dict) -> None:
        from psycopg.types.json import Jsonb
        loc = raw.get("location") or {}
        self.conn.execute(
            """INSERT INTO places (place_id, name, lat, lng, address, types, rating, opening_hours, details)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (place_id) DO UPDATE SET name=EXCLUDED.name, lat=EXCLUDED.lat, lng=EXCLUDED.lng,
                 address=EXCLUDED.address, types=EXCLUDED.types, rating=EXCLUDED.rating,
                 opening_hours=EXCLUDED.opening_hours, details=EXCLUDED.details, details_checked_at=now()""",
            (raw["id"], (raw.get("displayName") or {}).get("text", ""), loc.get("latitude"), loc.get("longitude"),
             raw.get("formattedAddress"), raw.get("types", []), raw.get("rating"),
             Jsonb(raw.get("regularOpeningHours")), Jsonb(raw)))


class GooglePlaces:
    def __init__(self, budget: Budget, api_key: str | None = None,
                 client: httpx.AsyncClient | None = None, cache: JsonCache | None = None,
                 hours_refresh_days: int = 3, search_refresh_days: int = 30):
        from mb import db
        self.key = api_key or config("GOOGLE_MAPS_API_KEY")
        self.client = client or api_client("places")
        self.table = FilePlaceTable(cache) if cache is not None else (
            PgPlaceTable() if db.enabled() else FilePlaceTable(JsonCache("google_places_table")))
        self.budget = budget
        self.hours_s = hours_refresh_days * DAY_S
        self.search_s = search_refresh_days * DAY_S

    def _fresh(self, at: float, max_age_s: float) -> bool:
        return time.time() - at < max_age_s

    async def _paid_post(self, sku: str, run_id: str, body: dict, mask: str) -> list[dict]:
        self.budget.check(self.budget.prices.get(sku, 0.0))   # refuse before spending, not after
        r = await self.client.post(TEXT_SEARCH_URL, json=body,
                                   headers={"X-Goog-Api-Key": self.key, "X-Goog-FieldMask": mask})
        r.raise_for_status()
        if sku in self.budget.prices:
            self.budget.record_call(run_id=run_id, sku=sku)
        return r.json().get("places") or []

    async def _place(self, pid: str, run_id: str) -> PlaceInfo | None:
        """One place from the table; its details are fetched or refreshed only when due."""
        row = self.table.get_place(pid)
        if row and self._fresh(row[1], self.hours_s):
            return self._parse(row[0], "cached", pid)
        try:
            self.budget.check(self.budget.prices[SKU_DETAILS])
            r = await self.client.get(DETAILS_URL + pid,
                                      headers={"X-Goog-Api-Key": self.key, "X-Goog-FieldMask": DETAIL_FIELDS})
            r.raise_for_status()
            self.budget.record_call(run_id=run_id, sku=SKU_DETAILS)
        except httpx.HTTPError:
            if row:                                   # refresh failed: the stored details still serve
                return self._parse(row[0], "cached", pid)
            raise
        raw = r.json()
        self.table.put_place(raw)
        return self._parse(raw, "live", pid)

    async def lookup(self, query: str, run_id: str) -> PlaceInfo | None:
        """A planned stop: found once (IDs only, free), details from the table."""
        key = "text:" + query.lower().strip()
        s = self.table.get_search(key)
        if s is None or not self._fresh(s[1], self.search_s):
            found = await self._paid_post(SKU_IDS, run_id, {"textQuery": query, "pageSize": 1}, "places.id")
            ids = [p["id"] for p in found if p.get("id")]
            self.table.put_search(key, {"text": query}, ids)
        else:
            ids = s[0]
        return await self._place(ids[0], run_id) if ids else None

    async def search_near(self, query: str, lat: float, lon: float, run_id: str,
                          radius_m: int = 1500, n: int = 5) -> list[PlaceInfo]:
        """Up to n places matching the query near (lat, lon). One call for all n; all stored."""
        key = f"near:{query.lower().strip()}|{round(lat, 3)}|{round(lon, 3)}|{radius_m}|{n}"
        s = self.table.get_search(key)
        rows = [self.table.get_place(pid) for pid in s[0]] if s else []
        if s and self._fresh(s[1], self.hours_s) and all(rows):
            return [self._parse(r[0], "cached", query) for r in rows]
        found = await self._paid_post(SKU, run_id, {"textQuery": query, "pageSize": n, "locationBias": {"circle": {
            "center": {"latitude": lat, "longitude": lon}, "radius": float(radius_m)}}}, FIELDS)
        found = [p for p in found if p.get("id")]
        for p in found:
            self.table.put_place(p)
        self.table.put_search(key, {"near": query, "lat": lat, "lon": lon, "r": radius_m, "n": n},
                              [p["id"] for p in found])
        return [self._parse(p, "live", query) for p in found]

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