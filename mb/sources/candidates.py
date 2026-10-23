"""Candidate providers for Agent 2, the Surprise finder.

Each provider turns a free window into verifiable candidates (a place ID or a source URL).
Code filters them for feasibility, opening hours, weather, children and repeats, so the
model only ever chooses from things that could really happen. A new provider, such as
Viator, is one new class with a `candidates` method: the agent does not change.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
from typing import Protocol

import yaml
from pydantic import BaseModel, ConfigDict, Field

from mb.config import CONFIG_DIR
from mb.contracts import Activity, Candidate, Context, Window
from mb.sources.places import GooglePlaces, is_open
from mb.sources.routes import estimate, haversine_km
from mb.sources.tavily import Tavily

WALK_KMH = 4.5
WALK_MAX_KM = 1.2            # beyond this, assume a taxi
FOOD_WORDS = ("cafe", "restaurant", "food", "dining", "tea", "dessert", "bakery")


class CandidateRules(BaseModel):
    """config/candidates.yaml, validated on load."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    visit_min: int = Field(gt=0)
    latest_start: dt.time
    max_per_window: int = Field(gt=0)
    interest_queries: dict[str, str]
    allowed_types: set[str]
    allowed_type_suffixes: list[str]
    excluded_types: set[str]
    outdoor_types: set[str]
    adult_types: set[str]

    def visitable(self, types: list[str]) -> bool:
        t = set(types)
        if t & self.excluded_types:
            return False
        return bool(t & self.allowed_types) or any(x.endswith(s) for x in t for s in self.allowed_type_suffixes)


RULES = CandidateRules(**yaml.safe_load((CONFIG_DIR / "candidates.yaml").read_text(encoding="utf-8")))
VISIT_MIN = RULES.visit_min
LATEST_START = RULES.latest_start
MAX_PER_WINDOW = RULES.max_per_window


def _m(t: dt.time) -> int:
    return t.hour * 60 + t.minute


def _t(m: int) -> dt.time:
    m = max(0, min(m, 23 * 60 + 59))
    return dt.time(m // 60, m % 60)


def travel_minutes(a: tuple[float, float], b: tuple[float, float]) -> int:
    km = haversine_km(a, b)
    return round(km / WALK_KMH * 60) if km <= WALK_MAX_KM else estimate(a, b)[0]


def queries_for(ctx: Context) -> list[str]:
    """Two searches from the trip's interests, made vegetarian where the interest is food."""
    veg = "vegetarian" in ctx.preferences.diet.restrictions
    out = []
    for interest in (ctx.preferences.interests or ["things to do"])[:2]:
        phrase = RULES.interest_queries.get(interest.lower(), interest)
        food = any(w in phrase.lower() for w in FOOD_WORDS)
        out.append(f"{'vegetarian ' if veg and food else ''}{phrase}")
    return out


def _rainy(ctx: Context, start: dt.time, end: dt.time, threshold: int = 60) -> bool:
    if not ctx.weather or not ctx.weather.hours:
        return False
    return any(h.rain_prob >= threshold for h in ctx.weather.hours if start.hour <= h.hour.hour <= end.hour)


def anchors(ctx: Context, w: Window) -> tuple[Activity | None, Activity | None]:
    """The stop before the window (where the traveller is) and the stop after it."""
    acts = [a for a in ctx.day.activities if a.kind != "move"]
    before = next((a for a in reversed(acts) if a.end and a.end <= w.start), None)
    after = next((a for a in acts if a.start >= w.end), None)
    return before, after


def _coords(ctx: Context, a: Activity | None) -> tuple[float, float] | None:
    info = ctx.places.get(a.id) if a else None
    return (info.lat, info.lon) if info and info.lat is not None else None


class CandidateProvider(Protocol):
    name: str

    async def candidates(self, ctx: Context, w: Window, run_id: str) -> list[Candidate]: ...


class PlacesCandidates:
    name = "google_places"

    def __init__(self, places: GooglePlaces):
        self.places = places

    async def candidates(self, ctx: Context, w: Window, run_id: str) -> list[Candidate]:
        before, after = anchors(ctx, w)
        here, nxt = _coords(ctx, before), _coords(ctx, after)
        if not here:
            return []                                    # we don't know where they are: suggest nothing
        # Search near where they are; if the next stop is far, also near the next stop
        # (spend the free time close to where they need to be next).
        centers = [(before, here)]
        if after and nxt and haversine_km(here, nxt) > WALK_MAX_KM:
            centers.append((after, nxt))
        found = await asyncio.gather(*(
            self.places.search_near(f"{q} near {stop.name}, {ctx.region or ''}", pt[0], pt[1], run_id)
            for stop, pt in centers for q in queries_for(ctx)))
        planned = {a.name.lower() for a in ctx.day.activities} | {n.lower() for n in ctx.other_days}
        length, seen, out = _m(w.end) - _m(w.start), set(), []
        for info in (p for group in found for p in group):
            if info.place_id in seen or info.lat is None:
                continue
            seen.add(info.place_id)
            if info.name.lower() in planned or (info.business_status or "OPERATIONAL") != "OPERATIONAL":
                continue
            if not RULES.visitable(info.types):
                continue                                 # e.g. a photo studio or a hotel
            if ctx.preferences.party.children_ages and RULES.adult_types & set(info.types):
                continue
            t_in = travel_minutes(here, (info.lat, info.lon))
            t_out = travel_minutes((info.lat, info.lon), nxt) if nxt else 0
            if t_in + VISIT_MIN + t_out > length:
                continue                                 # doesn't fit the window, travel included
            start, end = _t(_m(w.start) + t_in), _t(_m(w.start) + t_in + VISIT_MIN)
            if is_open(info, ctx.date, start, end) is False:
                continue
            if RULES.outdoor_types & set(info.types) and _rainy(ctx, start, end):
                continue
            out.append(Candidate(id=f"places:{info.place_id}", provider=self.name, name=info.name, window=w,
                                 place_id=info.place_id,
                                 source_url=f"https://www.google.com/maps/place/?q=place_id:{info.place_id}",
                                 lat=info.lat, lon=info.lon, travel_min=t_in, rating=info.rating,
                                 types=info.types[:5]))
        return sorted(out, key=lambda c: -(c.rating or 0))[:MAX_PER_WINDOW]


class WebCandidates:
    """Web results have a source URL but no verified location, so travel time is unknown."""
    name = "tavily"

    def __init__(self, tavily: Tavily, max_results: int = 3):
        self.tavily = tavily
        self.max_results = max_results

    async def candidates(self, ctx: Context, w: Window, run_id: str) -> list[Candidate]:
        interest = (ctx.preferences.interests or ["things to do"])[0]
        results, _ = await self.tavily.search(f"{interest} in {ctx.region} {ctx.date:%B %Y}", run_id,
                                              max_results=self.max_results)
        return [Candidate(id="web:" + hashlib.sha256(r.url.encode()).hexdigest()[:10], provider=self.name, name=r.title, window=w,
                          source_url=r.url, snippet=r.snippet[:200]) for r in results]


async def gather_candidates(ctx: Context, providers: list[CandidateProvider], run_id: str) -> list[Candidate]:
    """Candidates for every usable free window. Web search runs once, on the longest window."""
    usable = [w for w in ctx.free_windows if w.start < LATEST_START and _m(w.end) - _m(w.start) >= VISIT_MIN]
    if not usable:
        return []
    longest = max(usable, key=lambda w: _m(w.end) - _m(w.start))
    jobs = [p.candidates(ctx, w, run_id) for w in usable for p in providers
            if p.name != "tavily" or w == longest]
    out, seen = [], set()
    for group in await asyncio.gather(*jobs):
        for c in group:
            if c.id not in seen:
                seen.add(c.id)
                out.append(c)
    return out
