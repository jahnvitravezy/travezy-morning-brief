import datetime as dt

import pytest

from mb.budget import BudgetExceeded
from mb.context import build_full_context
from mb.contracts import OpeningPeriod, PlaceInfo
from mb.sources.itinerary import FileLoader
from mb.sources.openmeteo import Geo, OpenMeteo

DAILY = [OpeningPeriod(day=d, open=dt.time(8), close=dt.time(23)) for d in range(7)]
MONDAY_ONLY = [OpeningPeriod(day=1, open=dt.time(10), close=dt.time(19))]


def place(name, periods=DAILY):
    return PlaceInfo(place_id=f"id_{name}", name=name, lat=25.2, lon=55.3,
                     business_status="OPERATIONAL", periods=periods, source="live")


class StubMeteo(OpenMeteo):
    async def geocode(self, name):
        return Geo(name=name, lat=25.2, lon=55.3, timezone="Asia/Dubai")


class StubPlaces:
    def __init__(self, overrides=None):
        self.overrides = overrides or {}

    async def lookup(self, query, run_id):
        name = query.split(",")[0]
        return self.overrides[name] if name in self.overrides else place(name)


class StubRoutes:
    def __init__(self, minutes=20):
        self.minutes = minutes

    async def leg(self, a, b, run_id):
        return self.minutes, 5.0, "live"


@pytest.fixture(scope="module")
def trip():
    return FileLoader().load("dubai_up01")


def _id(ctx, name):
    return next(a.id for a in ctx.day.activities if a.name == name)


async def _ctx(trip, places=None, routes=None):
    return await build_full_context(trip, 1, StubMeteo(), "dubai_clear_warm",
                                    places=places or StubPlaces(), routes=routes or StubRoutes())


async def test_booking_beats_conflicting_live_hours(trip):
    ctx = await _ctx(trip, StubPlaces({"Museum of the Future": place("Museum", MONDAY_ONLY)}))
    flags = {(f.code, f.activity_id) for f in ctx.flags}
    museum = _id(ctx, "Museum of the Future")
    assert ("hours_conflict", museum) in flags and ("closed", museum) not in flags


async def test_unbooked_stop_outside_hours_is_flagged_closed(trip):
    ctx = await _ctx(trip, StubPlaces({"Gold and Spice Souks": place("Souks", MONDAY_ONLY)}))
    assert ("closed", _id(ctx, "Gold and Spice Souks")) in {(f.code, f.activity_id) for f in ctx.flags}


async def test_tight_connection_is_flagged(trip):
    ctx = await _ctx(trip, routes=StubRoutes(minutes=90))   # souks -> museum has a 60 min gap
    assert any(f.code == "tight_connection" for f in ctx.flags)


async def test_missing_place_is_flagged_and_context_still_builds(trip):
    ctx = await _ctx(trip, StubPlaces({"Al Ustad Special Kebab": None}))
    kebab = _id(ctx, "Al Ustad Special Kebab")
    assert ("place_not_found", kebab) in {(f.code, f.activity_id) for f in ctx.flags}
    assert kebab not in ctx.places and ctx.ctx_id.startswith("ctx_")


async def test_free_hour_keeps_its_length_and_notes_travel(trip):
    ctx = await _ctx(trip, routes=StubRoutes(minutes=20))
    w = next(w for w in ctx.free_windows if w.start == dt.time(14, 0))
    assert w.end == dt.time(15, 0) and w.travel_min == 20


async def test_abra_crossing_needs_no_routes_call(trip):
    ctx = await _ctx(trip)
    fahidi = _id(ctx, "Al Fahidi Historical District")
    assert not any(l.from_id == fahidi for l in ctx.legs)


async def test_budget_cap_is_never_swallowed(trip):
    class BrokePlaces:
        async def lookup(self, q, run_id):
            raise BudgetExceeded("cap")
    with pytest.raises(BudgetExceeded):
        await _ctx(trip, BrokePlaces())

async def test_closing_mid_visit_is_not_called_closed(trip):
    early = [OpeningPeriod(day=d, open=dt.time(8), close=dt.time(13)) for d in range(7)]
    ctx = await _ctx(trip, StubPlaces({"Gold and Spice Souks": place("Souks", early)}))
    souks = _id(ctx, "Gold and Spice Souks")      # visit 12:00-14:00, closes 13:00
    codes = {(f.code, f.activity_id) for f in ctx.flags}
    assert ("closes_during_visit", souks) in codes and ("closed", souks) not in codes