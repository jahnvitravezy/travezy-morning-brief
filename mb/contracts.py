"""Contracts: the typed shape of every piece of data that crosses a boundary.

Inputs (itinerary, trip preferences), the context package, and the output contract the
app renders. Format limits (title length, max surprises) are NOT enforced here: a draft
that breaks them must still be representable, so the judge can send it back for a fix.
"""
from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------- Itinerary ----------
class Money(Contract):
    amount: float = Field(ge=0)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")  # None when free or included
    per: Literal["person", "total", "night", "day"] = "total"


class Activity(Contract):
    id: str
    kind: Literal["do", "eat", "move", "stay"]
    start: dt.time
    end: dt.time | None = None
    name: str
    address: str | None = None
    cost_text: str | None = None
    cost: Money | None = None
    booked: bool = False
    timed_at: dt.time | None = None    # timed entry or pickup time, when stated
    move_mode: str | None = None
    note: str = ""


class TripDay(Contract):
    trip_id: str
    index: int = Field(ge=0)
    date: dt.date
    title: str
    activities: list[Activity]
    is_arrival: bool
    is_departure: bool
    is_free: bool
    wake_location: str                 # where the traveller wakes up: the previous night's stay


# ---------- Preferences: answers to the trip's planning questions ----------
class Party(Contract):
    adults: int = Field(ge=1)
    children_ages: list[int] = []


class Diet(Contract):
    summary: str
    restrictions: list[str] = []
    applies_to: Literal["all", "some"] = "all"


class Pace(Contract):
    summary: str
    start_not_before: dt.time | None = None
    back_by: dt.time | None = None
    dinner_around: dt.time | None = None
    max_walk_km: float | None = None
    avoid_steep: bool = False
    rest_after_lunch: bool = False


class Preferences(Contract):
    id: str
    who: str
    party: Party
    diet: Diet
    pace: Pace
    budget: Literal["low", "mid", "mid_high", "high"]
    home_currency: str = Field(pattern=r"^[A-Z]{3}$")
    interests: list[str] = []

    @property
    def party_size(self) -> int:
        return self.party.adults + len(self.party.children_ages)


class Trip(Contract):
    trip_id: str
    itinerary_id: str
    place_identity: str | None = None
    origin: str | None = None
    destination: str | None = None
    region: str | None = None          # e.g. "Bali", "Dubai": what the traveller calls the place
    ignored_user_preference: list[str] = []
    preferences: Preferences
    days: list[TripDay]


# ---------- Context package ----------
Freshness = Literal["live", "cached", "fixture", "estimated", "unavailable", "not_fetched"]


class Window(Contract):
    start: dt.time
    end: dt.time
    travel_min: int | None = None      # travel already needed inside this window, to the next stop
    
class HourWeather(Contract):
    hour: dt.time
    temp_c: float
    rain_prob: int = Field(ge=0, le=100)
    rain_mm: float = Field(ge=0)


class WeatherDay(Contract):
    location: str
    source: Freshness
    scenario: str | None = None        # set when this is a stored test scenario, not a forecast
    hours: list[HourWeather] = []

class OpeningPeriod(Contract):
    day: int = Field(ge=0, le=6)       # Google's numbering: 0 = Sunday
    open: dt.time
    close: dt.time | None = None       # None = open 24 hours

class PlaceInfo(Contract):
    place_id: str
    name: str
    address: str | None = None
    lat: float | None = None
    lon: float | None = None
    business_status: str | None = None # OPERATIONAL, CLOSED_TEMPORARILY, CLOSED_PERMANENTLY
    periods: list[OpeningPeriod] = []
    hours_text: list[str] = []
    rating: float | None = None
    types: list[str] = []
    source: Freshness

class TravelLeg(Contract):
    from_id: str
    to_id: str
    minutes: int = Field(ge=0)
    km: float = Field(ge=0)
    source: Freshness     
    
class WebResult(Contract):
    title: str
    url: str
    snippet: str = ""
    score: float = 0.0 
    
class Flag(Contract):
    code: Literal["hours_conflict", "closed", "closes_during_visit", "hours_unknown", "place_not_found",
                  "travel_estimated", "tight_connection"]
    activity_id: str | None = None
    message: str                       # plain English: shown to the judge and, if relevant, the user            # live/cached from Routes, or "estimated" if Routes failed


class Candidate(Contract):
    """A verifiable thing to do in a free window. Agent 2 may only choose from these."""
    id: str                            # "places:<place_id>" or "web:<n>"
    provider: str                      # google_places, tavily (Viator later)
    name: str
    window: Window
    place_id: str | None = None
    source_url: str | None = None
    lat: float | None = None
    lon: float | None = None
    travel_min: int | None = None      # from the previous stop (estimated); None if location unknown
    open_from: dt.time | None = None   # first time in the window it is open (long free days)
    rating: float | None = None
    types: list[str] = []
    snippet: str = ""


class Context(Contract):
    ctx_id: str
    trip_id: str
    date: dt.date
    region: str | None = None
    day: TripDay
    preferences: Preferences
    free_windows: list[Window]
    other_days: list[str]              # items planned on other days, so nothing repeats
    timezone: str | None = None        # filled by the source layer
    send_at_local: dt.time | None = None
    weather: WeatherDay | None = None
    places: dict[str, PlaceInfo] = {}  # keyed by activity id
    legs: list[TravelLeg] = []
    flags: list[Flag] = []
    candidates: list[Candidate] = []   # for Agent 2, gathered once so every model sees the same set
    freshness: dict[str, Freshness]


# ---------- Output contract: what the app renders ----------
class Notification(Contract):
    title: str = ""
    body: str = ""


class TimelineItem(Contract):
    time: str
    name: str
    booked: bool = False
    travel_to_next_min: int | None = None


class Weather(Contract):
    summary: str = ""
    clash: str | None = None
    suggested_swap: str | None = None


class Spend(Contract):
    local: str = ""
    home: str = ""


class SurpriseItem(Contract):
    name: str
    why_you: str
    fits_slot: str
    distance_min: int | None = None
    source_url: str | None = None
    place_id: str | None = None


class Confidence(Contract):
    score: int | None = Field(default=None, ge=0, le=100)
    badge: Literal["green", "amber", "red"] | None = None
    flags: list[str] = []


class Meta(Contract):
    models: dict[str, str] = {}
    latency_ms: int = 0
    tokens: dict[str, int] = {}
    judge_verdict: Literal["pass", "fix", "fallback"] | None = None


class MorningBrief(Contract):
    """Defaults mean every field is always present in the JSON, even when empty."""
    brief_id: str
    trip_id: str
    date: dt.date
    send_at_local: str = ""
    timezone: str = ""
    notification: Notification = Notification()
    timeline: list[TimelineItem] = []
    weather: Weather = Weather()
    heads_ups: list[str] = []
    spend_estimate: Spend = Spend()
    surprises: list[SurpriseItem] = []
    local_tip: str = ""
    confidence: Confidence = Confidence()
    actions: list[Literal["add_to_day", "swap", "ask"]] = []
    meta: Meta = Meta()

# ---------- Agent outputs ----------

class MealCheck(Contract):
    item: str
    fits_diet: bool
    reason: str = ""


class SummaryText(Contract):
    """The parts of out.summary the model writes. Facts (timeline, spend) are computed in code."""
    meal_checks: list[MealCheck] = []  # one verdict per meal, filled before anything else
    conflicts: list[str] = []          # filled FIRST: each plan item that clashes with the travellers
    notification_title: str
    notification_body: str
    heads_ups: list[str] = []
    weather_summary: str = ""          # since summariser-v6 written in code; kept for older prompts
    weather_clash: str | None = None   # since v6 found and worded in code
    suggested_swap: str | None = None  # since v6 chosen in code (a free, dry slot) or none
    local_tip: str

class Summary(Contract):
    """out.summary"""
    agent_id: str
    text: SummaryText
    timeline: list[TimelineItem]
    spend: Spend

class SurprisePick(Contract):
    candidate_id: str
    why_you: str


class SurprisePicks(Contract):
    """What the Surprise finder model returns: choices from the candidate list only."""
    picks: list[SurprisePick] = []
    none_reason: str | None = None     # when the right answer is no surprise


class Surprises(Contract):
    """out.surprises"""
    agent_id: str
    items: list[SurpriseItem] = []
    none_reason: str | None = None
    dropped: list[str] = []            # picks rejected by code checks, with the reason


class ComposerText(Contract):
    """What the Composer model writes: the final words, after merging and any fix request."""
    notification_title: str
    notification_body: str
    heads_ups: list[str] = []
    local_tip: str = ""


# ---------- Agent 4, the judge ----------
class CriterionScore(Contract):
    id: str                            # a criterion id from the rubric
    score: int = Field(ge=0, le=100)
    reason: str                        # one sentence, pointing at the part of the brief it is about


class JudgeOutput(Contract):
    """What the judge model returns. Code does the weighting, badge and decision."""
    scores: list[CriterionScore]
    issues: list[str] = []             # concrete, fixable problems, most serious first; empty if none


class Verdict(Contract):
    """out.verdict"""
    agent_id: str
    rubric_id: str
    model: str | None = None           # None when only the rule checks ran (judge unavailable)
    rule_failures: list[str] = []
    scores: list[CriterionScore] = []
    score: int | None = Field(default=None, ge=0, le=100)   # weighted, 0-100
    badge: Literal["green", "amber", "red"] | None = None
    decision: Literal["pass", "fix", "fallback"]
    reasons: list[str] = []            # what to fix (rule failures first, then the judge's issues)
    error: str | None = None           # why the judge model couldn't score, if it couldn't
