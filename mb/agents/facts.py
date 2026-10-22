"""Facts for the brief, computed in code rather than by a model: timeline, travel, spend."""
from __future__ import annotations

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
