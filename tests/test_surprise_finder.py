import datetime as dt

import pytest

from mb.agents.surprise_finder import MAX_SURPRISES, run_surprise_finder, validate
from mb.config import load_config
from mb.context import build_full_context
from mb.contracts import OpeningPeriod, PlaceInfo, SurprisePick, SurprisePicks
from mb.llm import LLMResult, load_prompt
from mb.sources.candidates import PlacesCandidates, gather_candidates, queries_for
from mb.sources.itinerary import FileLoader
from mb.sources.openmeteo import Geo, OpenMeteo

DAILY = [OpeningPeriod(day=d, open=dt.time(8), close=dt.time(23)) for d in range(7)]


def info(name, lat=25.20, lon=55.30, types=("cafe",), periods=DAILY, rating=4.5, status="OPERATIONAL"):
    return PlaceInfo(place_id=f"id_{name}", name=name, lat=lat, lon=lon, business_status=status,
                     periods=list(periods), rating=rating, types=list(types), source="live")


class StubMeteo(OpenMeteo):
    async def geocode(self, name):
        return Geo(name=name, lat=25.2, lon=55.3, timezone="Asia/Dubai")


class StubPlaces:
    """Planned stops all sit at the same point; nearby search returns a fixed list."""
    def __init__(self, nearby):
        self.nearby = nearby

    async def lookup(self, query, run_id):
        return info(query.split(",")[0])

    async def search_near(self, query, lat, lon, run_id, radius_m=1500, n=5):
        return self.nearby


class StubRoutes:
    async def leg(self, a, b, run_id):
        return 10, 2.0, "live"


async def _ctx(nearby, trip_id="dubai_up01", day=1):
    places = StubPlaces(nearby)
    return await build_full_context(FileLoader().load(trip_id), day, StubMeteo(), "dubai_clear_warm",
                                    places=places, routes=StubRoutes(), providers=[PlacesCandidates(places)])


async def test_filters_planned_far_closed_and_adult_places():
    nearby = [info("Arabian Tea House"), info("Museum of the Future"),          # planned today
              info("Far Cafe", lat=25.60), info("Shut Cafe", status="CLOSED_PERMANENTLY"),
              info("Night Bar", types=("bar",))]
    ctx = await _ctx(nearby)
    names = {c.name for c in ctx.candidates}
    assert "Arabian Tea House" in names
    assert not names & {"Museum of the Future", "Far Cafe", "Shut Cafe"}
    kids = await _ctx(nearby, trip_id="dubai_up02")
    assert "Night Bar" not in {c.name for c in kids.candidates}


async def test_candidates_are_in_the_context_hash():
    a = await _ctx([info("Arabian Tea House")])
    b = await _ctx([info("Arabian Tea House"), info("Second Cafe")])
    assert a.ctx_id != b.ctx_id


def test_vegetarian_food_queries():
    ctx_prefs = FileLoader().load("dubai_up01")
    from mb.context import build_context
    qs = queries_for(build_context(ctx_prefs, 1))
    assert qs == ["vegetarian cafe", "scenic photo spot viewpoint"]


async def test_validation_drops_invented_duplicate_slot_and_extra_picks():
    ctx = await _ctx([info("Arabian Tea House"), info("Second Cafe")])
    c = ctx.candidates
    picks = SurprisePicks(picks=[SurprisePick(candidate_id=c[0].id, why_you="Veg menu"),
                                 SurprisePick(candidate_id="places:invented", why_you="x"),
                                 SurprisePick(candidate_id=c[1].id, why_you="same slot")])
    out = validate(ctx, picks, "a2")
    same_slot = c[0].window == c[1].window
    assert out.items[0].name == c[0].name and out.items[0].place_id
    assert any("not in the candidate list" in d for d in out.dropped)
    assert len(out.items) == (1 if same_slot else 2) and len(out.items) <= MAX_SURPRISES


async def test_no_candidates_means_no_model_call():
    ctx = await _ctx([])
    spec = load_config().agents["mb.surprise_finder.v1"]

    class NoCallLLM:
        async def call(self, **kw):
            raise AssertionError("model should not be called")
    out, res = await run_surprise_finder(ctx, spec, NoCallLLM(), run_id="t")
    assert out.items == [] and out.none_reason and res is None


async def test_items_come_from_candidate_data_not_the_model():
    ctx = await _ctx([info("Arabian Tea House")])
    spec = load_config().agents["mb.surprise_finder.v1"]
    cid = ctx.candidates[0].id

    class FakeLLM:
        async def call(self, **kw):
            return LLMResult(SurprisePicks(picks=[SurprisePick(candidate_id=cid, why_you="Veg menu, close by")]),
                             "fake", 1, 1, 0.0, 1)
    out, _ = await run_surprise_finder(ctx, spec, FakeLLM(), run_id="t")
    item = out.items[0]
    assert item.name == "Arabian Tea House" and item.source_url.startswith("https://www.google.com/maps")


def test_surprise_prompt_exists():
    assert load_prompt(load_config().agents["mb.surprise_finder.v1"].prompt_version)


async def test_studios_and_hotels_are_not_surprises():
    nearby = [info("Future Photo Studio", types=("manufacturer", "store", "service")),
              info("Heritage Stay", types=("hotel", "lodging")),
              info("Photo View Point", types=("tourist_attraction",))]
    names = {c.name for c in (await _ctx(nearby)).candidates}
    assert names == {"Photo View Point"}
