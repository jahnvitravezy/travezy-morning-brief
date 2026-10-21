"""Context layer: one package of facts per trip day, built once, read by every agent.

This builds the offline part (no API calls). The source layer adds places, weather,
travel times and the time zone, and updates the freshness flags.
"""
from __future__ import annotations

import hashlib
import json
from datetime import time

from mb.contracts import Context, Preferences, Trip, TripDay, Window

DAY_START = time(8, 0)
DAY_END = time(22, 0)
MIN_WINDOW_MIN = 45
SOURCES = ("places", "weather", "routes", "web_search")


def _m(t: time) -> int:
    return t.hour * 60 + t.minute


def _t(m: int) -> time:
    return time(m // 60, m % 60)


def free_windows(day: TripDay, prefs: Preferences, min_minutes: int = MIN_WINDOW_MIN) -> list[Window]:
    """Gaps between planned items, inside the traveller's day, long enough to use.

    Leading travel (arriving) and trailing travel (leaving) shrink the day: nothing can be
    added before the flight lands or after leaving for the airport. Travel time between
    stops is subtracted once Routes is connected.
    """
    lo = _m(prefs.pace.start_not_before or DAY_START)
    hi = _m(prefs.pace.back_by or DAY_END)
    acts = day.activities
    i = 0
    while i < len(acts) and acts[i].kind == "move":
        lo = max(lo, _m(acts[i].end or acts[i].start))
        i += 1
    j = len(acts) - 1
    while j >= i and acts[j].kind == "move":
        hi = min(hi, _m(acts[j].start))
        j -= 1
    windows, cursor = [], lo
    for a in acts:
        s, e = _m(a.start), _m(a.end or a.start)
        if e <= lo:
            continue
        if s >= hi:
            break
        if s > cursor and s - cursor >= min_minutes:
            windows.append(Window(start=_t(cursor), end=_t(s)))
        cursor = max(cursor, e)
    if hi - cursor >= min_minutes:
        windows.append(Window(start=_t(cursor), end=_t(hi)))
    return windows


def build_context(trip: Trip, index: int) -> Context:
    day = trip.days[index]
    other = sorted({a.name for d in trip.days if d.index != index
                    for a in d.activities if a.kind in ("do", "eat")})
    ctx = Context(ctx_id="pending", trip_id=trip.trip_id, date=day.date, day=day,
                  preferences=trip.preferences, free_windows=free_windows(day, trip.preferences),
                  other_days=other, freshness={s: "not_fetched" for s in SOURCES})
    # Content-addressed ID: the same inputs always give the same ctx_id, any change gives a new one.
    body = json.dumps(ctx.model_dump(mode="json", exclude={"ctx_id"}), sort_keys=True)
    return ctx.model_copy(update={"ctx_id": "ctx_" + hashlib.sha256(body.encode()).hexdigest()[:12]})