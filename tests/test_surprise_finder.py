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


async def test_free_day_is_anchored_at_the_hotel():
    """Free day, no stops: candidates are searched around where they wake up."""
    seen = []

    class Spy(StubPlaces):
        async def search_near(self, query, lat, lon, run_id, radius_m=1500, n=5):
            seen.append(query)
            return self.nearby

    places = Spy([info("Warung Sopa", lat=-8.69, lon=115.17)])
    ctx = await build_full_context(FileLoader().load("bali_up01"), 2, StubMeteo(), "bali_clear",
                                   places=places, routes=StubRoutes(), providers=[PlacesCandidates(places)])
    assert "wake" in ctx.places
    assert seen and all("The Haven Bali Seminyak" in q for q in seen)


async def test_arrival_day_does_not_use_home_as_anchor():
    places = StubPlaces([])
    ctx = await build_full_context(FileLoader().load("dubai_up01"), 0, StubMeteo(), "dubai_clear_warm",
                                   places=places, routes=StubRoutes(), providers=[PlacesCandidates(places)])
    assert "wake" not in ctx.places          # they wake up in Mumbai


async def test_unknown_hours_shops_and_morning_night_markets_are_dropped():
    nearby = [info("Al Rigga The Night Market", types=("market",), periods=()),
              info("Spice Shop", types=("market",), periods=()),
              info("Shri Krishna Temple", types=("hindu_temple",), periods=()),
              info("OCEAN CAFE", types=("cafe",))]
    ctx = await _ctx(nearby, "dubai_up03", 2)                  # 21:00 window aside, morning window 07:00-10:00
    names = {c.name for c in ctx.candidates}
    assert "Spice Shop" not in names and "Al Rigga The Night Market" not in names
    assert "Shri Krishna Temple" in names and "Ocean Cafe" in names   # any-time place; caps tidied


def test_listicles_are_not_web_candidates():
    from mb.sources.candidates import _is_article
    assert _is_article("THE 10 BEST Water & Amusement Parks in Indonesia ...")
    assert _is_article("Bali in December 2026 – Weather, Festivals, Things to Do & Tips!")
    assert not _is_article("Ubud Art Market")


def test_candidate_names_are_cleaned():
    from mb.sources.candidates import clean_name
    assert clean_name("Playa Pacha | Pool and Private Beach Club| FIVE LUXE JBR") == "Playa Pacha"
    assert clean_name("AquaFun Dubai Water Park - Official") == "AquaFun Dubai Water Park"
    assert clean_name("OCEAN GRAND BEACH BALI") == "Ocean Grand Beach Bali"


async def test_long_free_window_finds_the_first_open_time():
    """Free day 07:00-23:30: a bar opening at 17:00 is still a candidate."""
    evening = [OpeningPeriod(day=d, open=dt.time(17), close=dt.time(23)) for d in range(7)]
    ctx = await _ctx([info("Night Owl Bar", types=("bar",), periods=evening)], "bali_up03", 2)
    assert "Night Owl Bar" in {c.name for c in ctx.candidates}


async def test_surprise_slot_starts_when_the_place_opens():
    evening = [OpeningPeriod(day=d, open=dt.time(17), close=dt.time(23)) for d in range(7)]
    ctx = await _ctx([info("Night Owl Bar", types=("bar",), periods=evening)], "bali_up03", 2)
    c = next(c for c in ctx.candidates if c.name == "Night Owl Bar")
    s = validate(ctx, SurprisePicks(picks=[SurprisePick(candidate_id=c.id, why_you="x")]), "a2")
    assert s.items[0].fits_slot.startswith("17:")
