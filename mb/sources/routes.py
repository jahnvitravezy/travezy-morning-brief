"""Google Routes: travel time between consecutive stops.

If Routes fails or finds no route, the leg falls back to a straight-line estimate marked
"estimated", so the brief still checks feasibility and says the time is approximate.
"""
from __future__ import annotations

import math

import httpx

from mb.limits import api_client
from decouple import config

from mb.budget import Budget
from mb.sources.cache import JsonCache, get_cache

ROUTE_MATRIX_URL = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
SKU = "google_routes_matrix_element"
FIELDS = "originIndex,destinationIndex,duration,distanceMeters,condition"
ROAD_FACTOR = 1.3      # roads are longer than straight lines
CITY_KMH = 25          # conservative city driving speed
BUFFER_MIN = 10        # finding a taxi, parking, walking in


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def estimate(a: tuple[float, float], b: tuple[float, float]) -> tuple[int, float]:
    km = haversine_km(a, b) * ROAD_FACTOR
    return round(km / CITY_KMH * 60) + BUFFER_MIN, round(km, 1)


class GoogleRoutes:
    def __init__(self, budget: Budget, api_key: str | None = None,
                 client: httpx.AsyncClient | None = None, cache: JsonCache | None = None):
        self.key = api_key or config("GOOGLE_MAPS_API_KEY")
        self.client = client or api_client("routes")
        self.cache = cache or get_cache("google_routes")
        self.budget = budget

    async def leg(self, a: tuple[float, float], b: tuple[float, float], run_id: str,
                  mode: str = "DRIVE") -> tuple[int, float, str]:
        """Returns (minutes, km, source)."""
        key = {"a": [round(x, 4) for x in a], "b": [round(x, 4) for x in b], "mode": mode}
        hit = self.cache.get(key)
        if hit is not None:
            return hit["minutes"], hit["km"], "cached"
        try:
            self.budget.check(self.budget.prices[SKU])
            point = lambda p: {"waypoint": {"location": {"latLng": {"latitude": p[0], "longitude": p[1]}}}}
            r = await self.client.post(
                ROUTE_MATRIX_URL,
                json={"origins": [point(a)], "destinations": [point(b)],
                      "travelMode": mode, "routingPreference": "TRAFFIC_UNAWARE"},
                headers={"X-Goog-Api-Key": self.key, "X-Goog-FieldMask": FIELDS})
            r.raise_for_status()
            self.budget.record_call(run_id=run_id, sku=SKU)
            el = (r.json() or [{}])[0]
            if el.get("condition") != "ROUTE_EXISTS":
                raise LookupError("no route")
            minutes = round(int(float(str(el["duration"]).rstrip("s"))) / 60)
            km = round(el.get("distanceMeters", 0) / 1000, 1)
            self.cache.set(key, {"minutes": minutes, "km": km})
            return minutes, km, "live"
        except (httpx.HTTPError, LookupError, KeyError, ValueError):
            minutes, km = estimate(a, b)
            return minutes, km, "estimated"