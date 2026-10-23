"""Facts for the brief, computed in code rather than by a model: timeline, travel, spend."""
from __future__ import annotations

import datetime as dt

import yaml

from mb.config import CONFIG_DIR
from mb.contracts import Context, Spend, TimelineItem

SYMBOLS = {"INR": "₹", "USD": "$", "SGD": "S$"}


def load_fx() -> dict[str, float]:
    return yaml.safe_load((CONFIG_DIR / "fx.yaml").read_text(encoding="utf-8"))


def timeline(ctx: Context) -> list[TimelineItem]:
    travel = {l.from_id: l.minutes for l in ctx.legs}
    return [TimelineItem(time=f"{a.start:%H:%M}", name=a.name, booked=a.booked,
                         travel_to_next_min=travel.get(a.id)) for a in ctx.day.activities]


def spend(ctx: Context, fx: dict[str, float]) -> Spend:
    """Entry fees, meals and local transport for the day, per person. Flights and hotels excluded."""
    party = ctx.preferences.party_size
    totals: dict[str, float] = {}
    for a in ctx.day.activities:
        if a.kind == "stay" or a.move_mode == "flight" or not a.cost:
            continue
        c = a.cost
        if c.amount == 0 or not c.currency or c.currency not in fx:
            continue
        per_person = c.amount if c.per == "person" else c.amount / party
        totals[c.currency] = totals.get(c.currency, 0) + per_person
    if not totals:
        return Spend()
    local = max(totals, key=totals.get)
    usd = sum(v / fx[k] for k, v in totals.items())
    home = ctx.preferences.home_currency
    home_text = f"about {SYMBOLS.get(home, home + ' ')}{usd * fx[home]:,.0f} per person" if home in fx else ""
    return Spend(local=f"about {local} {usd * fx[local]:,.0f} per person", home=home_text)

# ---------- Weather clashes, computed in code ----------
# A "do" item is outdoor unless its name or note says otherwise. Meals and transport are not checked.
INDOOR_WORDS = ("museum", "mall", "aquarium", "gallery", "cinema", "spa", "cafe", "restaurant",
                "indoor", "theatre", "theater", "observation deck", "at the top")
RAIN_PROB = 60
HOT_C = 35


def is_outdoor(a) -> bool:
    if a.kind != "do":
        return False
    text = f"{a.name} {a.note}".lower()
    if "outdoor" in text:
        return True
    return not any(w in text for w in INDOOR_WORDS)


def weather_clashes(ctx: Context) -> list[str]:
    """Outdoor items with heavy rain risk or heat during their own hours."""
    if not ctx.weather or not ctx.weather.hours:
        return []
    out = []
    for a in ctx.day.activities:
        if not is_outdoor(a):
            continue
        end = (a.end or a.start).hour
        hrs = [h for h in ctx.weather.hours if a.start.hour <= h.hour.hour <= end]
        if not hrs:
            continue
        rain, heat = max(h.rain_prob for h in hrs), max(h.temp_c for h in hrs)
        reasons = ([f"rain up to {rain}%"] if rain >= RAIN_PROB else []) + \
                  ([f"up to {heat:.0f}C"] if heat >= HOT_C else [])
        if reasons:
            out.append(f"{a.name} {a.start:%H:%M}-{(a.end or a.start):%H:%M}: {', '.join(reasons)}")
    return out



# ---------- Dry windows and pace checks, computed in code ----------
def dry_windows(ctx: Context, max_rain: int = 30) -> list[str]:
    """Hours from 08:00 to 21:00 with low rain risk, merged: e.g. ["08:00-13:00", "17:00-21:00"]."""
    if not ctx.weather or not ctx.weather.hours:
        return []
    dry = sorted(h.hour.hour for h in ctx.weather.hours if 8 <= h.hour.hour <= 20 and h.rain_prob < max_rain)
    spans, start, prev = [], None, None
    for h in dry:
        if start is None:
            start = prev = h
        elif h == prev + 1:
            prev = h
        else:
            spans.append((start, prev))
            start = prev = h
    if start is not None:
        spans.append((start, prev))
    return [f"{a:02d}:00-{b + 1:02d}:00" for a, b in spans]


def _mins(t: dt.time) -> int:
    return t.hour * 60 + t.minute


def pace_checks(ctx: Context) -> list[str]:
    """Plan items that clash with the trip's stated pace: late dinners, early starts, late finishes."""
    pace, acts, out = ctx.preferences.pace, ctx.day.activities, []
    if pace.dinner_around:
        for a in acts:
            if a.kind == "eat" and a.start.hour >= 17 and _mins(a.start) - _mins(pace.dinner_around) >= 30:
                out.append(f"{a.name} at {a.start:%H:%M}: later than your usual dinner around "
                           f"{pace.dinner_around:%H:%M}")
    if pace.start_not_before:
        early = next((a for a in acts if a.kind != "move" and a.start < pace.start_not_before), None)
        if early:
            out.append(f"{early.name} at {early.start:%H:%M}: earlier than you like to start "
                       f"({pace.start_not_before:%H:%M})")
    limit = pace.back_by or dt.time(22, 0)
    ground = [a for a in acts if a.end and a.move_mode != "flight"]
    if ground:
        last = max(ground, key=lambda a: a.end)
        if last.end > limit:
            out.append(f"The day ends around {last.end:%H:%M} ({last.name}), after {limit:%H:%M}")
    return out
