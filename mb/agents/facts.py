"""Facts for the brief, computed in code rather than by a model: timeline, travel, spend."""
from __future__ import annotations

import re

import datetime as dt

import yaml

from mb.config import CONFIG_DIR
from mb.contracts import Context, Spend, TimelineItem
from mb.sources.places import hours_on

SYMBOLS = {"INR": "₹", "USD": "$", "SGD": "S$"}


def load_fx() -> dict[str, float]:
    """Today's rates (mb/sources/fx.py): live, cached up to 2 days, or unverified."""
    from mb.sources.fx import load_rates
    return load_rates()


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
    trusted = getattr(fx, "trusted", True)          # a plain dict is a rate table we chose (tests)
    home_text = (f"about {SYMBOLS.get(home, home + ' ')}{usd * fx[home]:,.0f} per person"
                 if home in fx and home != local and trusted else "")   # never a figure from a rate we aren't sure of
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
            usual = f", later than your usual {limit:%H:%M} return" if pace.back_by else ""
            out.append(f"Long day: you finish around {last.end:%H:%M} at {last.name}{usual}")
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
            # No dry, open slot today: offer a checked indoor idea instead (blind review, 6 Nov:
            # no rain or heat clash in the pack got a swap).
            indoor = next((c for c in ctx.candidates
                           if any(w in f"{c.name} {' '.join(c.types)}".lower().replace("_", " ") for w in INDOOR_WORDS)), None)
            if indoor:
                return (f"If the weather turns, swap {a.name} for {indoor.name} "
                        f"(indoor, {indoor.window.start:%H:%M}-{indoor.window.end:%H:%M})")
            return None
    return None


# ---------- Checks that must reach the traveller ----------
def _diet_alternative(ctx: Context, words: tuple[str, ...]) -> str:
    """A checked place that suits the diet, from today's candidates (blind review, 3 Nov:
    "ask the restaurant" alone isn't enough). Empty when no candidate fits."""
    for c in ctx.candidates:
        text = f" {c.name} {' '.join(c.types)} {c.snippet} ".lower().replace("_", " ")
        if any(w in text for w in words):
            near = f", {c.travel_min} min away" if c.travel_min else ""
            return f", or try {c.name} ({c.window.start:%H:%M}-{c.window.end:%H:%M}{near})"
    return ""


def diet_checks(ctx: Context) -> list[str]:
    """Jain food rules are stricter than "vegetarian" or "plant-based": every meal needs asking."""
    r = set(ctx.preferences.diet.restrictions)
    if r & {"jain", "no_onion", "no_garlic", "no_root_vegetables"}:
        meals = [a.name for a in ctx.day.activities if a.kind == "eat" and "jain" not in a.note.lower()]
        if not meals:
            return []
        alt = _diet_alternative(ctx, ("jain",)) or _diet_alternative(ctx, ("vegetarian", "vegan", "pure veg"))
        if alt and "jain" not in alt.lower():
            alt += ", a vegetarian place: ask for Jain preparation"
        return [f"Jain food: ask {' and '.join(meals)} for dishes without onion, garlic or root vegetables" + alt]
    if r & {"vegetarian", "vegan"}:
        # Every planned meal not already marked vegetarian gets a check (stand-in review: B04, B06, B11)
        word = "vegan" if "vegan" in r else "vegetarian"
        meals = [a.name for a in ctx.day.activities if a.kind == "eat"
                 and not any(w in f"{a.name} {a.note}".lower() for w in ("veg", "plant"))]
        if not meals:
            return []
        who = "for the vegetarians in your group" if ctx.preferences.diet.applies_to == "some" else "for you"
        return [f"{word.capitalize()} food: check {' and '.join(meals)} has {word} dishes {who}"
                + _diet_alternative(ctx, ("vegan",) if word == "vegan" else ("vegetarian", "vegan", "veg ", "jain"))]
    return []


def access_checks(ctx: Context) -> list[str]:
    """Steep paths or stairs, for children or travellers who avoid them."""
    p, out = ctx.preferences, []
    for a in ctx.day.activities:
        text = f"{a.name} {a.note}".lower()
        steep = any(w in text for w in ("steep", "stairs", "steps"))
        uneven = "uneven" in text            # Tegallalang: "Outdoor, uneven paths" was missed (B11, B14, v8)
        if not steep and not uneven:
            continue
        if p.party.children_ages:
            ages = " and ".join(map(str, p.party.children_ages))
            out.append(f"{a.name}: steep path, keep the children ({ages}) close or skip the climb down" if steep
                       else f"{a.name}: uneven paths, keep the children ({ages}) close and wear good shoes")
        elif p.pace.avoid_steep:
            out.append(f"{a.name}: steep path, enjoy it from the top instead of climbing down" if steep
                       else f"{a.name}: uneven paths, take it slowly and stay near the viewpoint")
    return out


OLDER = re.compile(r"\b(6\d|7\d|8\d)s?\b|senior|elderly|retired", re.I)
WALK_WORDS = ("walk", "walking", "stroll", "hike", "trek", "on foot", "souk", "old town", "heritage", "mall")


def _older(ctx: Context) -> bool:
    return bool(OLDER.search(ctx.preferences.who or ""))


def _safety_rules() -> list[dict]:
    import yaml
    from mb.config import CONFIG_DIR
    return yaml.safe_load((CONFIG_DIR / "safety.yaml").read_text(encoding="utf-8")) or []


def walk_checks(ctx: Context) -> list[str]:
    """The trip's walking limit (pace.max_walk_km) against stops that mean a lot of walking.
    Blind review (6 Nov): a couple in their 60s with a 2 km limit got long walks with no caution."""
    km = ctx.preferences.pace.max_walk_km
    if not km:
        return []
    out = []
    for a in ctx.day.activities:
        if a.kind in ("do", "eat") and any(w in f"{a.name} {a.note}".lower() for w in WALK_WORDS):
            out.append(f"{a.name}: a lot of walking for your {km:g} km limit; take breaks, "
                       f"or a taxi between parts of it")
    return out


def safety_items(ctx: Context) -> list[tuple[str, str, list[str]]]:
    """(stop, caution, topic words) from config/safety.yaml, only for the travellers each applies to.
    A rule with "all" applies to everyone; otherwise it needs children or an older traveller."""
    kids = ctx.preferences.party.children_ages
    who = {"all"} | ({"children"} if kids else set()) | ({"older"} if _older(ctx) else set())
    children = f"the children ({' and '.join(map(str, kids))})" if kids else "everyone"
    out = []
    for a in ctx.day.activities:
        text = f"{a.name} {a.note}".lower()
        r = next((r for r in _safety_rules() if any(m in text for m in r["match"]) and who & set(r["who"])), None)
        if r:
            out.append((a.name, r["line"].format(place=a.name, children=children), r["topic"]))
    return out


def safety_checks(ctx: Context) -> list[str]:
    return [line for _, line, _ in safety_items(ctx)]


def airport_checks(ctx: Context) -> list[str]:
    """Departure day: when to leave for the airport, or when to be ready for the planned transfer.
    A planned taxi used to mean no line at all, so the brief never mentioned the flight (B18)."""
    acts = ctx.day.activities
    flights = [a for a in acts if a.move_mode == "flight"]
    if not ctx.day.is_departure or not flights:
        return []
    f = flights[-1]
    before = [a for a in acts if a.start < f.start]
    if before and before[-1].kind == "move":          # a taxi or driver to the airport is planned
        t = before[-1]
        ready = _mins(t.start) - 30
        return [f"Airport transfer ({t.name}) at {t.start:%H:%M} for your {f.start:%H:%M} flight: "
                f"be packed and ready by {ready // 60:02d}:{ready % 60:02d}"]
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
            out.append((name_words(f.message.split(":")[0]), ["leave", "head out", "be out by", "finish by", "exit by"],
                        leave_by_line(ctx, f)))
        elif f.code == "closed":
            out.append((name_words(f.message.split(":")[0]), ["closed", "close"], f.message))
    acts = [a.name for a in ctx.day.activities]
    for line in pace_checks(ctx):
        name = next((n for n in acts if line.startswith(n)), None)
        if line.startswith("Long day"):
            # not "late": it matches "later than your usual dinner" and hid a missing long-day line (B11)
            out.append(([], ["long day", "ends", "ending", "finish", "back at", "back by", "late return"], line))
        elif name and "dinner" in line:
            # covered by any dinner line that mentions the planned time ("Book dinner for 19:00 instead of
            # 19:30"); the restaurant's name used to be required, which duplicated the line (B11)
            start = next(a.start for a in ctx.day.activities if a.name == name)
            out.append((["dinner"], [f"{start:%H:%M}"], line))
        elif name:
            out.append((name_words(name), ["earl", "start"], line))
    for line in access_checks(ctx):
        name = next(n for n in acts if line.startswith(n))
        out.append((name_words(name), ["steep", "stairs", "steps", "uneven"], line))
    out += [(["jain"] if line.startswith("Jain") else ["veg"], [], line) for line in diet_checks(ctx)]  # any Jain / veg line covers it
    for line in walk_checks(ctx):
        name = next(n for n in acts if line.startswith(n))
        out.append((name_words(name), ["walk", "taxi", "break"], line))
    out += [(name_words(name), topic, line) for name, line, topic in safety_items(ctx)]
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
            if line.startswith("Leave "):             # a closing time: replace "closes at 17:00, adjust" lines
                out = [h for h in out if not (_covers(h, name, []) and "clos" in h.lower())]
            out.append(line if line.endswith(".") else line + ".")
    return out


def leave_by_line(ctx: Context, flag) -> str:
    """A closing time as an action: "Leave X by 17:00: it closes before your planned 17:30 finish"."""
    import re
    a = next((x for x in ctx.day.activities if x.id == flag.activity_id), None)
    m = re.search(r"closes at (\d{2}:\d{2})", flag.message)
    if not a or not m:
        return flag.message
    return f"Leave {a.name} by {m.group(1)}: it closes before your planned {(a.end or a.start):%H:%M} finish"


# ---------- The local tip: only places we know about, no risky advice ----------
# Claims about a place that no source we use can confirm (stand-in review, v6-v7: "rooftop dining",
# "beach club" for a restaurant, "best views from the north side at sunset", "close to hotel").
CLAIM_WORDS = ("rooftop", "beach club", "sunset", "best ", "secluded", "hidden", "thrilling", "stunning",
               "must-see", "famous", "north side", "close to hotel", "close to your", "near your hotel")


def unverified_claims(text: str, support: str = "") -> list[str]:
    """Claim words in a line, unless the place's own name or type supports them ("X Rooftop Bar")."""
    low, sup = f" {text.lower()} ", support.lower()
    return [c for c in CLAIM_WORDS if c in low and c.strip() not in sup]


TIP_RISK_WORDS = ("steep", "even if it rains", "even if it's raining", "in the rain", "despite the rain", "midday sun")
# Safety promises we can't verify from any source: a tip may not say a place is safe for anyone.
SAFETY_CLAIMS = (r"\bsafe(ly)?\b", r"\bperfect for (kids|children)\b", r"\b(kid|child)[- ]friendly\b",
                 r"\bcalm waters?\b", r"\bno risk\b", r"\bguaranteed\b")


# ---------- Times: a model may only repeat a time code or the plan already has ----------
_TIME = __import__("re").compile(r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\b")


def _hhmm(text: str) -> set[str]:
    return {f"{int(h):02d}:{m}" for h, m in _TIME.findall(text)}


def known_times(ctx: Context) -> set[str]:
    """Every time the brief may state: the plan, the free windows, the send time, and every line code wrote."""
    out = set()
    for a in ctx.day.activities:
        out |= {f"{t:%H:%M}" for t in (a.start, a.end, a.timed_at) if t}
    for w in ctx.free_windows:
        out |= {f"{w.start:%H:%M}", f"{w.end:%H:%M}"}
    if ctx.send_at_local:
        out.add(f"{ctx.send_at_local:%H:%M}")
    p = ctx.preferences.pace
    out |= {f"{t:%H:%M}" for t in (p.dinner_around, p.start_not_before, p.back_by) if t}
    code_text = [weather_summary(ctx), *weather_clashes(ctx), *(line for _, _, line in must_cover(ctx))]
    code_text += [suggested_swap(ctx) or ""] + [f.message for f in ctx.flags]
    for c in ctx.candidates:
        code_text.append(f"{(c.open_from or c.window.start):%H:%M}-{c.window.end:%H:%M}")
    for line in code_text:
        out |= _hhmm(line)
    return out


def unknown_times(text: str, ctx: Context) -> list[str]:
    """Times in a model's line that nothing gave it. "Leave Kelingking by 11:16" (B01, v7) was the model's
    own arithmetic, and wrong; the plan said 12:30 and code had the travel time."""
    known = known_times(ctx)
    return sorted(t for t in _hhmm(text) if t not in known)


def known_names(ctx: Context) -> set[str]:
    """Every word of every place the brief may name: today's plan, the hotel, the ideas, the region."""
    names = [a.name for a in ctx.day.activities] + [c.name for c in ctx.candidates]
    names += [ctx.region or "", ctx.day.wake_location or "", ctx.day.title or ""]
    return {w.strip(".,:;!?'()").lower() for n in names for w in n.split() if w}


def tip_problems(tip: str, ctx: Context) -> list[str]:
    """Why a tip can't be sent: it names a place we don't know, or gives risky advice."""
    import re
    out = []
    known = known_names(ctx)
    words = re.findall(r"[A-Za-z][\w'&-]*", tip)
    for i, w in enumerate(words):
        first = i == 0 or re.search(r"[.!?]\s*$", tip[:tip.find(w)])
        if w[0].isupper() and not first and w.lower() not in known and w.lower() not in COMMON_CAPS:
            out.append(f"tip names '{w}', which isn't in today's plan or ideas")
    low = tip.lower()
    out += [f"tip advises '{r}'" for r in TIP_RISK_WORDS if r in low]
    out += [f"tip makes a safety claim we can't verify ('{m.group(0)}')" for p in SAFETY_CLAIMS
            if (m := re.search(p, low))]
    out += [f"tip uses a time nothing gave it ({t})" for t in unknown_times(tip, ctx)]
    out += [f"tip makes a claim we can't verify ('{c.strip()}')" for c in unverified_claims(tip)]
    clash = {c.split(" ")[0].lower() for c in weather_clashes(ctx)}
    for a in ctx.day.activities:
        if any(c.startswith(f"{a.name} ") for c in weather_clashes(ctx)) and a.name.lower() in low:
            out.append(f"tip is about {a.name}, which has a weather clash today")
    return out


COMMON_CAPS = {"i", "you", "your", "am", "pm", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
               "sunday", "january", "february", "march", "april", "may", "june", "july", "august", "september",
               "october", "november", "december", "aed", "idr", "inr", "sgd", "usd"}


def safe_tip(ctx: Context) -> str:
    """When the model's tip can't be sent: a practical one built from today's facts."""
    if ctx.weather and ctx.weather.hours:
        rain = _spans([h.hour.hour for h in ctx.weather.hours if 7 <= h.hour.hour <= 21 and h.rain_prob >= RAIN_PROB])
        hottest = max(h.temp_c for h in ctx.weather.hours)
        if rain:
            return f"Pack a light rain jacket: rain is likely from {rain[0][0]:02d}:00 to {rain[0][1]:02d}:00."
        if hottest >= 30:
            return f"Carry water and sun cover: it reaches {hottest:.0f}°C today."
    return "Keep a copy of today's bookings on your phone in case you lose signal."


def fix_idea_count(body: str, n: int) -> str:
    """The notification counts the ideas the brief really has. "plus 0 ideas for you" (B01, v7) was a
    model filling a template; the count is a fact, so code sets it."""
    import re
    pat = re.compile(r"[,;]?\s*(?:and|plus|\+)\s+(?:\d+|one|two|three|no)\s+ideas?(?:\s+for\s+you)?", re.I)
    if not pat.search(body):
        return body
    repl = "" if n == 0 else f", plus {n} idea{'s' if n > 1 else ''} for you"
    out = pat.sub(repl, body, count=1)
    return re.sub(r"\s+([.,!])", r"\1", out).strip()


def _short(name: str) -> str:
    return re.split(r"\s[-–:(]\s?|,", name)[0].strip()


def key_title(ctx: Context, limit: int = 40) -> str:
    """The notification title names the day's key moments, worked out in code.

    Blind review (3 Nov): "Day 2 in Dubai" said nothing. Now: "Burj Khalifa 16:30, then Dhow cruise",
    the booked or timed stops first, then the first planned visit. A free day says so."""
    acts = [a for a in ctx.day.activities if a.kind in ("do", "eat")]
    key = sorted([a for a in acts if a.booked or a.timed_at], key=lambda a: a.timed_at or a.start) \
        or [a for a in acts if a.kind == "do"][:1]
    if not key:
        return (f"Free day in {ctx.region}" if ctx.region else "Free day")[:limit]
    first = f"{_short(key[0].name)} {(key[0].timed_at or key[0].start):%H:%M}"
    flight = next((a for a in ctx.day.activities if a.kind == "move" and "flight" in f"{a.name} {a.note}".lower()), None)
    if flight and len(both := f"{first}, flight {(flight.timed_at or flight.start):%H:%M}") <= limit:
        return both                             # a departure day leads with what must not be missed
    if len(key) > 1 and len(both := f"{first}, then {_short(key[1].name)}") <= limit:
        return both
    return first if len(first) <= limit else first[:limit - 1].rstrip() + "…"
