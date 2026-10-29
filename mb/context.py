"""Context layer: one package of facts per trip day, built once, read by every agent.

build_context is the offline part. build_full_context adds the time zone, the local send
time and the weather, and records the freshness of every source. If a source is down, the
context is still built, with that source marked unavailable.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import httpx

from mb.budget import BudgetExceeded
from mb.contracts import Context, Flag, Preferences, TravelLeg, Trip, TripDay, Window
from mb.sources.openmeteo import OpenMeteo
from mb.sources.places import GooglePlaces, hours_on, is_open
from mb.sources.routes import GoogleRoutes

DAY_START = time(8, 0)
DAY_END = time(22, 0)
MIN_WINDOW_MIN = 45
AIRPORT_BEFORE_MIN = 180              # international flight: check-in, bags, the drive
READY_BEFORE_MIN = 30                 # back and ready before a pickup or transfer at the end of the day
ANY_TIME_TYPES = {"beach", "park", "natural_feature", "hiking_area", "marina", "plaza"}
ANY_TIME_WORDS = ("beach", " bay", "viewpoint")


def _any_time(info, name: str) -> bool:
    """A beach or a park has no opening hours; "check its hours" would be noise."""
    return bool(ANY_TIME_TYPES & set(info.types)) or any(w in f" {name.lower()}" for w in ANY_TIME_WORDS)
SOURCES = ("geocode", "places", "weather", "routes", "web_search")


def _m(t: time) -> int:
    return t.hour * 60 + t.minute


def _t(m: int) -> time:
    m = max(0, min(m, 23 * 60 + 59))
    return time(m // 60, m % 60)


def offset_minutes(tz_from: str, tz_to: str, on: date) -> int:
    noon = datetime.combine(on, time(12))
    a = noon.replace(tzinfo=ZoneInfo(tz_from)).utcoffset()
    b = noon.replace(tzinfo=ZoneInfo(tz_to)).utcoffset()
    return int((b - a).total_seconds() // 60)


def send_time(day: TripDay, default: time = time(7, 30), lead_min: int = 60) -> time:
    """07:30 local, or lead_min before the first activity if that is earlier."""
    if not day.activities:
        return default
    return _t(min(_m(default), _m(day.activities[0].start) - lead_min))


def free_windows(day: TripDay, prefs: Preferences, min_minutes: int = MIN_WINDOW_MIN,
                 arrival_offset_min: int = 0) -> list[Window]:
    """Gaps between planned items, inside the traveller's day, long enough to use.

    Leading travel (arriving) and trailing travel (leaving) shrink the day. On arrival day,
    the flight's end is converted from origin time to destination time.
    """
    lo = _m(prefs.pace.start_not_before or DAY_START)
    hi = _m(prefs.pace.back_by or DAY_END)
    acts = day.activities
    shift = arrival_offset_min if day.is_arrival else 0
    i = 0
    while i < len(acts) and acts[i].kind == "move":
        lo = max(lo, _m(acts[i].end or acts[i].start) + shift)
        i += 1
    if day.is_arrival:                     # nothing before check-in: they have luggage
        stay = next((a for a in acts[i:] if a.kind == "stay"), None)
        if stay:
            lo = max(lo, _m(stay.end or stay.start))
    j = len(acts) - 1
    while j >= i and acts[j].kind == "move":
        hi = min(hi, _m(acts[j].start) - READY_BEFORE_MIN)   # an idea must end before the pickup, not at it
        if acts[j].move_mode == "flight":  # be at the airport in time
            hi = min(hi, _m(acts[j].start) - AIRPORT_BEFORE_MIN)
        j -= 1
    windows, cursor = [], lo
    for a in acts[i:j + 1]:
        s, e = _m(a.start), _m(a.end or a.start)
        if e <= lo:
            continue
        if s >= hi:
            break
        if s - cursor >= min_minutes:
            windows.append(Window(start=_t(cursor), end=_t(s)))
        cursor = max(cursor, e)
    if hi - cursor >= min_minutes:
        windows.append(Window(start=_t(cursor), end=_t(hi)))
    return windows


def _finalize(ctx: Context) -> Context:
    """Content-addressed ID: the same inputs always give the same ctx_id."""
    body = json.dumps(ctx.model_dump(mode="json", exclude={"ctx_id"}), sort_keys=True)
    return ctx.model_copy(update={"ctx_id": "ctx_" + hashlib.sha256(body.encode()).hexdigest()[:12]})


def build_context(trip: Trip, index: int, arrival_offset_min: int = 0) -> Context:
    day = trip.days[index]
    other = sorted({a.name for d in trip.days if d.index != index
                    for a in d.activities if a.kind in ("do", "eat")})
    ctx = Context(ctx_id="pending", trip_id=trip.trip_id, date=day.date, region=trip.region, day=day,
                  preferences=trip.preferences,
                  free_windows=free_windows(day, trip.preferences, arrival_offset_min=arrival_offset_min),
                  other_days=other, freshness={s: "not_fetched" for s in SOURCES})
    return _finalize(ctx)

STOP_KINDS = ("do", "eat", "stay")


async def _lookup_places(day: TripDay, near: str, places: GooglePlaces, run_id: str):
    stops = [a for a in day.activities if a.kind in STOP_KINDS]
    results = await asyncio.gather(
        *(places.lookup(f"{a.name}, {a.address or near}", run_id) for a in stops), return_exceptions=True)
    found, flags, statuses = {}, [], set()
    for a, r in zip(stops, results):
        if isinstance(r, BudgetExceeded):
            raise r                                   # never swallow the budget cap
        if isinstance(r, Exception) or r is None:
            flags.append(Flag(code="place_not_found", activity_id=a.id,
                              message=f"{a.name}: could not be verified on Google Places"))
            continue
        found[a.id] = r
        statuses.add(r.source)
        if a.kind == "stay":
            continue
        if r.business_status == "CLOSED_PERMANENTLY":
            flags.append(Flag(code="closed", activity_id=a.id, message=f"{a.name}: listed as permanently closed"))
            continue
        ok = is_open(r, day.date, a.start, a.end or a.start)
        if ok is False and a.booked:   # live data never overrides a booking
            flags.append(Flag(code="hours_conflict", activity_id=a.id,
                              message=f"{a.name}: Google lists different hours. The booking stands; "
                                      f"check with the venue if unsure"))
        elif ok is False:
            open_at_start = [(o, c) for o, c in hours_on(r, day.date) if o <= a.start < c]
            if open_at_start:
                close = open_at_start[0][1]
                flags.append(Flag(code="closes_during_visit", activity_id=a.id,
                                  message=f"{a.name}: closes at {close:%H:%M}, before the planned end "
                                          f"at {(a.end or a.start):%H:%M}"))
            else:
                flags.append(Flag(code="closed", activity_id=a.id,
                                  message=f"{a.name}: Google lists it as closed at {a.start:%H:%M}"))
        elif ok is None and not _any_time(r, a.name):
            flags.append(Flag(code="hours_unknown", activity_id=a.id,
                              message=f"{a.name}: opening hours are not published"))
    status = "live" if "live" in statuses else "cached" if "cached" in statuses else "unavailable"
    return found, flags, status


async def _travel(day: TripDay, found: dict, routes: GoogleRoutes, run_id: str):
    pairs, prev = [], None
    for a in day.activities:
        if a.kind == "move":
            prev = None                               # the move itself is the travel
            continue
        if a.id not in found or found[a.id].lat is None:
            prev = None
            continue
        if prev:
            pairs.append((prev, a))
        prev = a
    results = await asyncio.gather(*(
        routes.leg((found[p.id].lat, found[p.id].lon), (found[n.id].lat, found[n.id].lon), run_id)
        for p, n in pairs))
    legs, flags, statuses = [], [], set()
    for (p, n), (minutes, km, src) in zip(pairs, results):
        legs.append(TravelLeg(from_id=p.id, to_id=n.id, minutes=minutes, km=km, source=src))
        statuses.add(src)
        if src == "estimated":
            flags.append(Flag(code="travel_estimated", activity_id=n.id,
                              message=f"{p.name} to {n.name}: travel time is an estimate"))
        gap = _m(n.start) - _m(p.end or p.start)
        if minutes > gap:
            flags.append(Flag(code="tight_connection", activity_id=n.id,
                              message=f"{p.name} to {n.name}: about {minutes} min of travel but only {gap} min "
                                      f"between them, so you'd reach {n.name} about {minutes - gap} min late"))
    status = ("live" if "live" in statuses else "cached" if "cached" in statuses
              else "estimated" if statuses else "not_fetched")
    return legs, flags, status


def _with_travel(windows: list[Window], day: TripDay, legs: list[TravelLeg]) -> list[Window]:
    ends = {a.id: a.end for a in day.activities}
    out = []
    for w in windows:
        leg = next((l for l in legs if ends.get(l.from_id) == w.start), None)
        out.append(w.model_copy(update={"travel_min": leg.minutes if leg else None}))
    return out


async def wake_timezone(trip: Trip, day: TripDay, meteo: OpenMeteo):
    """The time zone where the traveller wakes up that day: home on an arrival morning that starts
    with the flight, the destination otherwise. Returns (timezone, destination geo, arrival offset)."""
    if not trip.destination:
        return None, None, 0
    dest = await meteo.geocode(trip.destination)
    tz, offset = dest.timezone, 0
    if day.is_arrival and trip.origin:
        origin = await meteo.geocode(trip.origin)
        offset = offset_minutes(origin.timezone, dest.timezone, day.date)
        if day.activities and day.activities[0].kind == "move":
            tz = origin.timezone          # they wake up at home on arrival morning
    return tz, dest, offset


async def build_full_context(trip: Trip, index: int, meteo: OpenMeteo,
                             weather_scenario: str | None = None, today: date | None = None,
                             places: GooglePlaces | None = None, routes: GoogleRoutes | None = None,
                             run_id: str = "adhoc", providers: list | None = None) -> Context:
    day = trip.days[index]
    fresh: dict[str, str] = {}
    tz, dest, offset = None, None, 0
    try:
        tz, dest, offset = await wake_timezone(trip, day, meteo)
        fresh["geocode"] = "live"
    except (httpx.HTTPError, LookupError):
        fresh["geocode"] = "unavailable"

    weather = None
    if dest:
        try:
            weather = (meteo.scenario(weather_scenario, dest.name) if weather_scenario
                       else await meteo.forecast(dest, day.date, today))
            fresh["weather"] = weather.source
        except (httpx.HTTPError, KeyError):
            fresh["weather"] = "unavailable"

    found, legs, flags = {}, [], []
    if places:
        found, flags, fresh["places"] = await _lookup_places(day, trip.destination or "", places, run_id)
        if day.wake_location not in ("unknown", trip.origin):   # their hotel: anchor for early windows
            try:
                wake = await places.lookup(f"{day.wake_location}, {trip.region or trip.destination or ''}", run_id)
            except httpx.HTTPError:
                wake = None
            if wake:
                found["wake"] = wake
    if routes and found:
        legs, more, fresh["routes"] = await _travel(day, found, routes, run_id)
        flags += more

    base = build_context(trip, index, arrival_offset_min=offset)
    ctx = base.model_copy(update={
        "timezone": tz, "send_at_local": send_time(day), "weather": weather,
        "places": found, "legs": legs, "flags": flags,
        "free_windows": _with_travel(base.free_windows, day, legs),
        "freshness": {**base.freshness, **fresh}})
    if providers:  # Agent 2's candidates belong to the context, so every model sees the same set
        from mb.sources.candidates import gather_candidates
        cands = await gather_candidates(ctx, providers, run_id)
        web = [c for c in cands if c.provider == "tavily"]
        ctx = ctx.model_copy(update={"candidates": cands, "freshness": {
            **ctx.freshness, "web_search": "live" if web else ctx.freshness.get("web_search", "not_fetched")}})
    return _finalize(ctx)