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
import re
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
BUFFER_MIN = 15              # an idea ends 15 min before they must leave for the next stop (B04, v7)
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
    outdoor_name_words: list[str]
    hours_optional_types: set[str]
    adult_types: set[str]

    def outdoor(self, name: str, types: list[str]) -> bool:
        return bool(self.outdoor_types & set(types)) or any(w in name.lower() for w in self.outdoor_name_words)

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


def next_for(ctx: Context, after: Activity | None) -> tuple[float, float] | None:
    """Where the traveller must be at the end of a window: the next stop; for a pickup or transfer with
    no place of its own (the airport taxi), their hotel. The idea must leave time to get back (B18, v7)."""
    pt = _coords(ctx, after)
    if pt is None and after is not None and after.kind == "move":
        wake = ctx.places.get("wake")
        if wake and wake.lat is not None:
            return wake.lat, wake.lon
    return pt


def here_for(ctx: Context, before: Activity | None, after: Activity | None):
    """Where the traveller is at the start of a window: the stop before it; else their hotel
    (not on arrival day, when they wake up at home); else the next stop. Returns (name, coords)."""
    if before and _coords(ctx, before):
        return before.name, _coords(ctx, before)
    wake = ctx.places.get("wake")
    if wake and wake.lat is not None and not ctx.day.is_arrival:
        return ctx.day.wake_location, (wake.lat, wake.lon)
    if after and _coords(ctx, after):
        return after.name, _coords(ctx, after)
    return None, None


class CandidateProvider(Protocol):
    name: str

    async def candidates(self, ctx: Context, w: Window, run_id: str) -> list[Candidate]: ...


class PlacesCandidates:
    name = "google_places"

    def __init__(self, places: GooglePlaces):
        self.places = places

    async def candidates(self, ctx: Context, w: Window, run_id: str) -> list[Candidate]:
        before, after = anchors(ctx, w)
        here_name, here = here_for(ctx, before, after)
        nxt = next_for(ctx, after)
        if not here:
            return []                                    # we don't know where they are: suggest nothing
        # Search near where they are; if the next stop is far, also near the next stop
        # (spend the free time close to where they need to be next).
        centers = [(here_name, here)]
        if after and nxt and haversine_km(here, nxt) > WALK_MAX_KM:
            centers.append((after.name, nxt))
        found = await asyncio.gather(*(
            self.places.search_near(f"{q} near {name}, {ctx.region or ''}", pt[0], pt[1], run_id)
            for name, pt in centers for q in queries_for(ctx)))
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
            if t_in + VISIT_MIN + t_out + BUFFER_MIN > length:
                continue                                 # doesn't fit the window, travel and buffer included
            leave_by = _m(w.end) - t_out - BUFFER_MIN    # the idea ends in time to reach the next stop
            start, end, open_ = _first_open(info, ctx, _m(w.start) + t_in, leave_by)
            if open_ is False:
                continue                                 # closed for the whole window
            if open_ is None and not RULES.hours_optional_types & set(info.types):
                continue                                 # hours unknown and not an any-time place
            if "night" in info.name.lower() and start < dt.time(17, 0):
                continue
            window = Window(start=w.start, end=_t(leave_by))
            if RULES.outdoor(info.name, info.types):
                good = _good_weather_slot(info, ctx, _m(w.start) + t_in, leave_by)
                if good is None:
                    continue                             # no dry, not-too-hot time while it's open
                start, until = good
                window = Window(start=w.start, end=until)   # the idea is shown only for the good hours
            name = clean_name(info.name)
            if ctx.region and name.lower() == ctx.region.lower():
                continue                                 # "Ubud" is a town, not a place to visit
            out.append(Candidate(id=f"places:{info.place_id}", provider=self.name, name=name, window=window,
                                 place_id=info.place_id,
                                 source_url=f"https://www.google.com/maps/place/?q=place_id:{info.place_id}",
                                 lat=info.lat, lon=info.lon, travel_min=t_in, rating=info.rating,
                                 open_from=start if _m(start) > _m(w.start) + t_in else None,
                                 types=info.types[:5]))
        return sorted(out, key=lambda c: -(c.rating or 0))[:MAX_PER_WINDOW]


def _bad_hours(ctx: Context) -> set[int]:
    """Hours an outdoor idea must avoid: rain likely, or heat (the same thresholds as the weather checks)."""
    from mb.agents.facts import HOT_C, RAIN_PROB
    if not ctx.weather or not ctx.weather.hours:
        return set()
    return {h.hour.hour for h in ctx.weather.hours if h.rain_prob >= RAIN_PROB or h.temp_c >= HOT_C}


def _good_weather_slot(info, ctx: Context, earliest: int, latest_end: int) -> tuple[dt.time, dt.time] | None:
    """An outdoor idea's slot: the first time it is open with no rain or heat for the whole visit,
    extended for as long as the weather stays good. A beach on a free day with rain 13:00-17:00 is
    shown for 10:00-13:00, not 10:00-23:30 (stand-in review, B03/B18, 31 Oct)."""
    bad = _bad_hours(ctx)
    t = -(-earliest // 15) * 15
    while t + VISIT_MIN <= latest_end:
        hours = set(range(t // 60, (t + VISIT_MIN - 1) // 60 + 1))
        if not bad & hours and is_open(info, ctx.date, _t(t), _t(t + VISIT_MIN)) is not False:
            end = t + VISIT_MIN
            while end < latest_end and (end // 60) not in bad:
                nxt = min(latest_end, (end // 60 + 1) * 60)
                if is_open(info, ctx.date, _t(t), _t(nxt)) is False:
                    break                                # closes before the weather turns
                end = nxt
            return _t(t), _t(end)
        t += 30
    return None


def _first_open(info, ctx: Context, earliest: int, latest_end: int):
    """The first visit time in the window when the place is open (every 30 min). A long free day
    used to be checked only at its first minute, 07:00, when almost everything is shut."""
    t, result = -(-earliest // 15) * 15, None          # start on a quarter hour: 10:02 -> 10:15
    while t + VISIT_MIN <= latest_end:
        start, end = _t(t), _t(t + VISIT_MIN)
        result = is_open(info, ctx.date, start, end)
        if result is not False:
            return start, end, result
        t += 30
    return _t(earliest), _t(earliest + VISIT_MIN), False


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
                          source_url=r.url, snippet=r.snippet[:200]) for r in results if not _is_article(r.title)]


ARTICLE = re.compile(r"\b(best|top \d+|things to do|guide|must-see|tips|20\d\d)\b|\bpov\b|\|", re.I)


def clean_name(name: str) -> str:
    """'Playa Pacha | Pool and Private Beach Club| FIVE LUXE JBR' -> 'Playa Pacha'; tidy ALL CAPS."""
    name = re.split(r"\s*\|\s*", name)[0]
    name = re.sub(r"\s+-\s+Official$", "", name, flags=re.I).strip()
    return name.title() if name.isupper() else name


def _is_article(title: str) -> bool:
    """'THE 10 BEST Water Parks in Indonesia' is a listicle, not a place someone can go to."""
    return bool(ARTICLE.search(title))


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
    out = meal_and_rest_filter(out, ctx.day.activities, ctx.preferences.pace.rest_after_lunch)
    return walking_filter(out, ctx.preferences.pace.max_walk_km)


SEATED_IDEA = ("museum", "gallery", "aquarium", "cinema", "theater", "theatre", "spa", "cafe", "coffee",
               "restaurant", "bakery", "tea", "dessert", "show", "observation", "viewpoint")


def walking_filter(cands: list[Candidate], max_walk_km: float | None) -> list[Candidate]:
    """With a short walking limit (3 km or less), only ideas you can mostly do sitting or standing in
    one place: museums, cafes, shows, viewpoints. Blind review (6 Nov): a 2 km couple got Al Fahidi,
    a walking district, whose name has no "walk" in it, so a blocklist wasn't enough."""
    if not max_walk_km or max_walk_km > 3:
        return cands
    return [c for c in cands
            if any(w in f"{c.name} {' '.join(c.types)}".lower().replace("_", " ") for w in SEATED_IDEA)]


REST_MIN = 90            # a family's rest break after lunch
AFTER_MEAL_MIN = 120     # no food idea this soon after a planned meal ("a second lunch")


def _is_food(c: Candidate) -> bool:
    text = f"{c.name} {' '.join(c.types)}".lower()
    return any(w in text for w in FOOD_WORDS)


def meal_and_rest_filter(cands: list[Candidate], activities: list[Activity], rest_after_lunch: bool) -> list[Candidate]:
    """Ideas never sit in a family's rest break, and food ideas never come straight after a meal.

    Blind review (3 Nov): ideas clashed with a lunch or rest break, including a second lunch
    straight after lunch. The rest break is the 90 minutes after a planned lunch (or 13:30-15:00
    when no lunch is planned). An idea whose slot overlaps it keeps only the part outside it,
    if that part still fits a visit."""
    meals = [a for a in activities if a.kind == "eat"]
    blocks = []
    if rest_after_lunch:
        lunch = next((a for a in meals if dt.time(11) <= a.start <= dt.time(15)), None)
        start = _m(lunch.end or _t(_m(lunch.start) + 60)) if lunch else _m(dt.time(13, 30))
        blocks.append((start, start + REST_MIN))
    out = []
    for c in cands:
        a, b = _m(c.open_from or c.window.start), _m(c.window.end)
        if _is_food(c) and any(_m(m.start) - 30 <= a < _m(m.end or _t(_m(m.start) + 60)) + AFTER_MEAL_MIN for m in meals):
            continue
        for x, y in blocks:
            if a < y and x < b:                          # overlaps the rest break: keep the longer side
                a, b = max([(a, min(b, x)), (max(a, y), b)], key=lambda s: s[1] - s[0])
        if b - a < VISIT_MIN:
            continue
        if (a, b) != (_m(c.open_from or c.window.start), _m(c.window.end)):
            c = c.model_copy(update={"open_from": _t(a), "window": c.window.model_copy(update={"end": _t(b)})})
        out.append(c)
    return out
