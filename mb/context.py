"""Context layer: one package of facts per trip day, built once, read by every agent.

build_context is the offline part. build_full_context adds the time zone, the local send
time and the weather, and records the freshness of every source. If a source is down, the
context is still built, with that source marked unavailable.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import httpx

from mb.contracts import Context, Preferences, Trip, TripDay, Window
from mb.sources.openmeteo import OpenMeteo

DAY_START = time(8, 0)
DAY_END = time(22, 0)
MIN_WINDOW_MIN = 45
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
    j = len(acts) - 1
    while j >= i and acts[j].kind == "move":
        hi = min(hi, _m(acts[j].start))
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
    ctx = Context(ctx_id="pending", trip_id=trip.trip_id, date=day.date, day=day,
                  preferences=trip.preferences,
                  free_windows=free_windows(day, trip.preferences, arrival_offset_min=arrival_offset_min),
                  other_days=other, freshness={s: "not_fetched" for s in SOURCES})
    return _finalize(ctx)


async def build_full_context(trip: Trip, index: int, meteo: OpenMeteo,
                             weather_scenario: str | None = None, today: date | None = None) -> Context:
    day = trip.days[index]
    fresh: dict[str, str] = {}
    tz, dest, offset = None, None, 0
    try:
        if trip.destination:
            dest = await meteo.geocode(trip.destination)
            tz = dest.timezone
            if day.is_arrival and trip.origin:
                origin = await meteo.geocode(trip.origin)
                offset = offset_minutes(origin.timezone, dest.timezone, day.date)
                if day.activities and day.activities[0].kind == "move":
                    tz = origin.timezone          # they wake up at home on arrival morning
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

    base = build_context(trip, index, arrival_offset_min=offset)
    ctx = base.model_copy(update={"timezone": tz, "send_at_local": send_time(day), "weather": weather,
                                  "freshness": {**base.freshness, **fresh}})
    return _finalize(ctx)