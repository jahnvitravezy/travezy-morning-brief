"""Loads Travezy's production itinerary format into typed trip days.

Parts of the format are free text: booked status, timed entries, costs and durations
live inside strings. This module is the only place that knows the format, so moving to
the live itinerary API later means adding one loader and changing one config line.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Protocol

import yaml

from mb.config import Config
from mb.contracts import Activity, Money, Preferences, Trip, TripDay

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
MEAL_MIN = 75          # eat items have no end time in the source
STAY_MIN = 30          # hotel check-in
DEFAULT_DO_MIN = 60    # a "do" item without hours
LATEST = time(23, 59)  # items running past midnight are capped at the end of the day
SYMBOLS = {"$": "USD", "₹": "INR"}

_COST = re.compile(r"^~?\s*(?P<cur>\$|₹|[A-Z]{3})\s?(?P<amt>[\d,]+(?:\.\d+)?)(?P<rest>.*)$")
_TIMED = re.compile(r"(?:timed entry|pickup)\s+at\s+(\d{1,2}:\d{2}\s*[AP]M)", re.I)
_BOOKED = re.compile(r"\bbooked\b", re.I)
_DURATION = re.compile(r"(\d+)\s*(hour|hr|minute|min)", re.I)
_FLIGHT = re.compile(r"Flight (?P<frm>.+?) to (?P<to>.+?)(?: via .+?)?\.", re.I)


def parse_clock(s: str) -> time:
    return datetime.strptime(s.strip().upper(), "%I:%M %p").time()


def parse_duration(s: str | None) -> int | None:
    parts = _DURATION.findall(s or "")
    if not parts:
        return None
    return sum(int(n) * (60 if unit.lower().startswith("h") else 1) for n, unit in parts)

def _region(addresses: list[str]) -> str | None:
    """The word shared by most address endings: 'Bali', 'Dubai'."""
    from collections import Counter
    words = Counter(w for a in addresses for w in set(a.split(",")[-1].split()))
    if not words:
        return None
    word, n = words.most_common(1)[0]
    return word if n >= 0.6 * len(addresses) else None

def parse_cost(text: str | None) -> Money | None:
    if not text:
        return None
    t = text.strip()
    if t.lower() in {"free", "included"}:
        return Money(amount=0)
    m = _COST.match(t)
    if not m:
        return None  # cost_text is kept; the spend estimate flags what it couldn't read
    rest = m["rest"].lower()
    per = ("person" if "per person" in rest else "night" if "per night" in rest
           else "day" if "for the day" in rest else "total")
    return Money(amount=float(m["amt"].replace(",", "")),
                 currency=SYMBOLS.get(m["cur"], m["cur"]), per=per)


def _add(t: time, minutes: int) -> time:
    moved = datetime(2000, 1, 1, t.hour, t.minute) + timedelta(minutes=minutes)
    return moved.time() if moved.day == 1 else LATEST


def _activity(trip_id: str, d: int, i: int, raw: dict) -> Activity:
    kind = raw.get("type")
    start = parse_clock(raw["tm"])
    desc = raw.get("desc") or ""
    timed = _TIMED.search(desc)
    common = dict(id=f"{trip_id}.d{d}.a{i}", start=start, note=desc,
                  booked=bool(_BOOKED.search(desc)),
                  timed_at=parse_clock(timed.group(1)) if timed else None,
                  cost_text=raw.get("cost"), cost=parse_cost(raw.get("cost")))
    if kind == "do":
        hours = raw.get("hours") or ""
        end = parse_clock(hours.split("-")[1]) if "-" in hours else _add(start, DEFAULT_DO_MIN)
        return Activity(kind="do", end=end, name=raw["place"], address=raw.get("addr"), **common)
    if kind == "eat":
        return Activity(kind="eat", end=_add(start, MEAL_MIN), name=raw["eat_name"],
                        address=raw.get("eat_addr"), **common)
    if kind == "move":
        mins = parse_duration(raw.get("duration")) or 30
        mode, ref = raw.get("move_type", "move"), (raw.get("move_no") or "").strip()
        if mode == "car" or mode.replace("_", " ").lower() in ref.lower():
            name = ref or mode                       # "Private driver", "Dubai Creek abra"
        else:
            name = f"{mode.replace('_', ' ').title()} {ref}".strip()   # "Flight EK501"
        return Activity(kind="move", end=_add(start, mins), name=name,
                        move_mode=raw.get("move_type"), **common)
    if kind == "relax_at_hotel_or_explore":
        return Activity(kind="stay", end=_add(start, STAY_MIN), name=raw["name"],
                        address=raw.get("addr"), **common)
    raise ValueError(f"{trip_id} day {d}: unknown activity type '{kind}'")


def parse_trip(trip_id: str, itinerary_id: str, raw: dict, prefs: Preferences) -> Trip:
    plan = raw["plan"]
    origin = destination = None
    for a in (plan[0]["acts"] if plan else []):
        m = _FLIGHT.search(a.get("desc", "")) if a.get("type") == "move" else None
        if m:
            origin, destination = m["frm"].strip(), m["to"].strip()
            break
    days, sleeps_at = [], origin or "unknown"
    for d, day in enumerate(plan):
        acts = sorted((_activity(trip_id, d, i, a) for i, a in enumerate(day.get("acts", []))),
                      key=lambda a: a.start)
        days.append(TripDay(trip_id=trip_id, index=d, date=day["dt"], title=day.get("act_ttl", ""),
                            activities=acts, is_arrival=d == 0, is_departure=d == len(plan) - 1,
                            is_free=not acts, wake_location=sleeps_at))
        for a in acts:
            if a.kind == "stay":
                sleeps_at = a.name  # carried forward: the hotel only appears on check-in day
    region = _region([a.address for d in days for a in d.activities if a.address])
    return Trip(trip_id=trip_id, itinerary_id=itinerary_id, place_identity=raw.get("place_identity"),
                origin=origin, destination=destination, region=region,
                ignored_user_preference=raw.get("ignored_user_preference", []), preferences=prefs, days=days)


class ItineraryLoader(Protocol):
    def trip_ids(self) -> list[str]: ...
    def trips_with_day_between(self, first: date, last: date) -> list[str]: ...  # API: GET /trips?day_from=&day_to=
    def load(self, trip_id: str) -> Trip: ...


class FileLoader:
    """Reads sample files in the production format. Swapped for an API loader later."""

    def __init__(self, root: Path = DATA):
        self.trips = {t["trip_id"]: t for t in yaml.safe_load((root / "trips.yaml").read_text(encoding="utf-8"))}
        self.prefs = yaml.safe_load((root / "preferences.yaml").read_text(encoding="utf-8"))
        self.itinerary_dir = root / "itineraries"

    def trip_ids(self) -> list[str]:
        return sorted(self.trips)

    def trips_with_day_between(self, first: date, last: date) -> list[str]:
        """Only trips with a day in [first, last]: the batch never loads every trip."""
        return [t for t in self.trip_ids() if any(first <= d.date <= last for d in self.load(t).days)]

    def load(self, trip_id: str) -> Trip:
        if trip_id not in self.trips:
            raise KeyError(f"Unknown trip '{trip_id}'. Known: {self.trip_ids()}")
        rec = self.trips[trip_id]
        raw = json.loads((self.itinerary_dir / rec["itinerary"]).read_text(encoding="utf-8"))
        prefs = Preferences(id=rec["preferences"], **self.prefs[rec["preferences"]])
        return parse_trip(trip_id, Path(rec["itinerary"]).stem, raw, prefs)


def get_loader(cfg: Config) -> ItineraryLoader:
    if cfg.app.itinerary_loader == "file":
        return FileLoader(ROOT / Path(cfg.app.itinerary_dir).parent)
    raise NotImplementedError("API loader: the internal itinerary API is not available yet")