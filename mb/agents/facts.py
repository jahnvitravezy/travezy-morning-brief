"""Facts for the brief, computed in code rather than by a model: timeline, travel, spend."""
from __future__ import annotations

import datetime as dt

import yaml

from mb.config import CONFIG_DIR
from mb.contracts import Context, Spend, TimelineItem
from mb.sources.places import hours_on

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
                  ([f"up to {heat:.0f}°C"] if heat >= HOT_C else [])
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


# ---------- Weather line and swap times, computed in code ----------
def _spans(hours: list[int]) -> list[tuple[int, int]]:
    """[13, 14, 15, 18] -> [(13, 16), (18, 19)]: merged runs of whole hours, end exclusive."""
    out: list[tuple[int, int]] = []
    for h in sorted(hours):
        if out and out[-1][1] == h:
            out[-1] = (out[-1][0], h + 1)
        else:
            out.append((h, h + 1))
    return out


def weather_summary(ctx: Context) -> str:
    """One line, always the same format: "26-31°C, rain likely 14:00-17:00"."""
    if not ctx.weather or not ctx.weather.hours:
        return "Forecast not available yet"
    day = [h for h in ctx.weather.hours if 7 <= h.hour.hour <= 21] or ctx.weather.hours
    temps = f"{min(h.temp_c for h in day):.0f}-{max(h.temp_c for h in day):.0f}°C"
    rain = _spans([h.hour.hour for h in day if h.rain_prob >= RAIN_PROB])
    hot = _spans([h.hour.hour for h in day if h.temp_c >= HOT_C])
    parts = [temps]
    parts += [f"rain likely {a:02d}:00-{b:02d}:00" for a, b in rain] or ["no rain expected"]
    parts += [f"hot {a:02d}:00-{b:02d}:00" for a, b in hot]
    return ", ".join(parts)


def swap_slots(ctx: Context, a) -> list[str]:
    """Free, dry, not-too-hot times long enough for the item, while the venue is open.
    Code decides; if the list is empty, there is no good time to move it to today."""
    if not ctx.weather or not ctx.weather.hours:
        return []
    bad = {h.hour.hour for h in ctx.weather.hours if h.rain_prob >= RAIN_PROB or h.temp_c >= HOT_C}
    need = max(_mins(a.end or a.start) - _mins(a.start), 30)
    info = ctx.places.get(a.id)
    open_spans = [(_mins(o), _mins(c)) for o, c in hours_on(info, ctx.date)] if info and info.periods else None
    out = []
    for w in ctx.free_windows:
        start, end = _mins(w.start), _mins(w.end)
        t = start
        while t + need <= end:
            hours = range(t // 60, (t + need - 1) // 60 + 1)
            fits_open = open_spans is None or any(o <= t and t + need <= c for o, c in open_spans)
            if not bad.intersection(hours) and fits_open:
                out.append(f"{t // 60:02d}:{t % 60:02d}-{(t + need) // 60:02d}:{(t + need) % 60:02d}")
                break
            t += 15
    return out


def suggested_swap(ctx: Context) -> str | None:
    """Move the first clashing outdoor item to the first valid slot, or None. Never onto another plan."""
    for a in ctx.day.activities:
        if is_outdoor(a) and any(c.startswith(f"{a.name} ") for c in weather_clashes(ctx)) and not a.booked:
            slots = swap_slots(ctx, a)
            if slots:
                return f"Move {a.name} to {slots[0]}"
            return None
    return None


# ---------- Checks that must reach the traveller ----------
def diet_checks(ctx: Context) -> list[str]:
    """Jain food rules are stricter than "vegetarian" or "plant-based": every meal needs asking."""
    r = set(ctx.preferences.diet.restrictions)
    if not r & {"jain", "no_onion", "no_garlic", "no_root_vegetables"}:
        return []
    meals = [a.name for a in ctx.day.activities if a.kind == "eat" and "jain" not in a.note.lower()]
    if not meals:
        return []
    return [f"Jain food: ask {' and '.join(meals)} for dishes without onion, garlic or root vegetables"]


def access_checks(ctx: Context) -> list[str]:
    """Steep paths or stairs, for children or travellers who avoid them."""
    p, out = ctx.preferences, []
    for a in ctx.day.activities:
        text = f"{a.name} {a.note}".lower()
        if not any(w in text for w in ("steep", "stairs", "steps")):
            continue
        if p.party.children_ages:
            ages = " and ".join(map(str, p.party.children_ages))
            out.append(f"{a.name}: steep path, keep the children ({ages}) close or skip the climb down")
        elif p.pace.avoid_steep:
            out.append(f"{a.name}: steep path, enjoy it from the top instead of climbing down")
    return out


def airport_checks(ctx: Context) -> list[str]:
    """Departure flight with no transfer planned: say when to leave."""
    acts = ctx.day.activities
    flights = [a for a in acts if a.move_mode == "flight"]
    if not ctx.day.is_departure or not flights:
        return []
    f = flights[-1]
    before = [a for a in acts if a.start < f.start]
    if before and before[-1].kind == "move":          # a taxi or driver to the airport is planned
        return []
    leave = _mins(f.start) - 180
    return [f"Leave for the airport by {leave // 60:02d}:{leave % 60:02d}: your flight is at {f.start:%H:%M}"]


def free_day_weather(ctx: Context) -> list[str]:
    """A free day has no outdoor item to clash with, but rain still changes the day."""
    if not ctx.day.is_free or not ctx.weather or not ctx.weather.hours:
        return []
    rain = _spans([h.hour.hour for h in ctx.weather.hours if 7 <= h.hour.hour <= 21 and h.rain_prob >= RAIN_PROB])
    if not rain:
        return []
    a, b = rain[0]
    return [f"Rain likely {a:02d}:00-{b:02d}:00: keep that time for something indoors"]


def must_cover(ctx: Context) -> list[tuple[list[str], list[str], str]]:
    """What the brief must say: (name words, topic words, code's own wording).
    A heads-up covers an item when it names it AND is about the same topic (a line about the drive
    to Kelingking doesn't cover its steep path). If the model drops one, code adds its own line."""
    region = (ctx.region or "").lower()
    def name_words(name: str) -> list[str]:
        ws = [w for w in name.lower().replace("'", " ").split() if len(w) >= 5 and w not in region]
        return ws or [name.lower()]

    out: list[tuple[list[str], list[str], str]] = []
    for f in ctx.flags:
        if f.code == "tight_connection":
            out.append((name_words(f.message.split(":")[0].split(" to ")[-1]), ["late", "delay"], f.message))
        elif f.code == "closes_during_visit":
            out.append((name_words(f.message.split(":")[0]), ["close", "earlier"], f.message))
        elif f.code == "closed":
            out.append((name_words(f.message.split(":")[0]), ["closed", "close"], f.message))
    acts = [a.name for a in ctx.day.activities]
    for line in pace_checks(ctx):
        name = next((n for n in acts if line.startswith(n)), None)
        if line.startswith("The day ends"):
            out.append(([], ["ends", "late", "back at", "back by", "return", "long day"], line))
        elif name and "dinner" in line:
            out.append((name_words(name), ["dinner", "19:", "18:", "earlier"], line))
        elif name:
            out.append((name_words(name), ["earl", "start"], line))
    for line in access_checks(ctx):
        name = next(n for n in acts if line.startswith(n))
        out.append((name_words(name), ["steep", "stairs", "steps"], line))
    out += [(["jain"], [], line) for line in diet_checks(ctx)]          # any Jain line covers it
    out += [(["airport"], ["leave", "by"], line) for line in airport_checks(ctx)]
    out += [(["rain"], ["indoor", "inside", "spa", "shopping", "umbrella"], line) for line in free_day_weather(ctx)]
    for a in ctx.day.activities:
        if "cash" in a.note.lower():
            short = [w for w in a.name.lower().split() if len(w) >= 4 and w not in region] or name_words(a.name)
            out.append((short, ["cash"], f"{a.name}: cash only" + (f" ({a.cost_text})" if a.cost_text else "")))
        if a.timed_at:
            out.append((name_words(a.name), [f"{a.timed_at:%H:%M}", f"{a.timed_at.hour}:"],
                        f"{a.name}: {'pickup' if 'pickup' in a.note.lower() else 'timed entry'} at {a.timed_at:%H:%M}"))
    return out


def _covers(line: str, name: list[str], topic: list[str]) -> bool:
    l = line.lower()
    need = max(1, (len(name) + 1) // 2)                 # "Monkey Forest" names "Sacred Monkey Forest Sanctuary"
    named = not name or sum(w in l for w in name) >= need
    return named and (not topic or any(w in l for w in topic))


def ensure_covered(heads_ups: list[str], ctx: Context) -> list[str]:
    out = list(heads_ups)
    for name, topic, line in must_cover(ctx):
        if not any(_covers(h, name, topic) for h in out):
            out.append(line if line.endswith(".") else line + ".")
    return out
