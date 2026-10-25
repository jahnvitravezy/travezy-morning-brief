import datetime as dt

import httpx
import pytest
import respx

from mb.context import build_full_context, offset_minutes, send_time
from mb.sources.cache import JsonCache
from mb.sources.itinerary import FileLoader
from mb.sources.openmeteo import GEOCODE_URL, OpenMeteo

GEO = {"Dubai": (25.07, 55.17, "Asia/Dubai"), "Mumbai": (19.07, 72.88, "Asia/Kolkata"),
       "Denpasar": (-8.65, 115.22, "Asia/Makassar"), "Chennai": (13.08, 80.27, "Asia/Kolkata")}


def _geo(request):
    name = request.url.params["name"]
    lat, lon, tz = GEO[name]
    return httpx.Response(200, json={"results": [{"name": name, "latitude": lat, "longitude": lon, "timezone": tz}]})


@pytest.fixture
def meteo(tmp_path):
    return OpenMeteo(client=httpx.AsyncClient(), cache=JsonCache("t", root=tmp_path))


@pytest.fixture(scope="module")
def loader():
    return FileLoader()


def test_offset_india_to_dubai_is_minus_90():
    assert offset_minutes("Asia/Kolkata", "Asia/Dubai", dt.date(2026, 11, 20)) == -90


def test_send_time_moves_earlier_for_an_early_flight(loader):
    bali = loader.load("bali_up04")
    assert send_time(bali.days[0]) == dt.time(5, 30)   # 06:30 flight
    assert send_time(bali.days[2]) == dt.time(7, 30)   # free day


def test_trip_knows_origin_and_destination(loader):
    t = loader.load("dubai_up01")
    assert (t.origin, t.destination) == ("Mumbai", "Dubai")


@respx.mock
async def test_arrival_day_uses_origin_timezone_and_local_landing(loader, meteo):
    respx.get(GEOCODE_URL).mock(side_effect=_geo)
    ctx = await build_full_context(loader.load("dubai_up01"), 0, meteo, weather_scenario="dubai_clear_warm")
    assert ctx.timezone == "Asia/Kolkata"                 # they wake up in Mumbai
    # Lands 10:30 Dubai time (09:00 IST + 3h), but nothing is suggested before the 13:00-13:30 check-in.
    assert ctx.free_windows[0].start == dt.time(13, 30)
    assert ctx.freshness["weather"] == "fixture"


@respx.mock
async def test_normal_day_uses_destination_timezone(loader, meteo):
    respx.get(GEOCODE_URL).mock(side_effect=_geo)
    ctx = await build_full_context(loader.load("bali_up04"), 1, meteo, weather_scenario="bali_rain_afternoon")
    assert ctx.timezone == "Asia/Makassar"
    assert next(h for h in ctx.weather.hours if h.hour == dt.time(14)).rain_prob >= 80


@respx.mock
async def test_far_future_without_scenario_is_flagged_not_invented(loader, meteo):
    respx.get(GEOCODE_URL).mock(side_effect=_geo)
    ctx = await build_full_context(loader.load("dubai_up01"), 1, meteo, today=dt.date(2026, 10, 21))
    assert ctx.freshness["weather"] == "unavailable" and ctx.weather.hours == []


@respx.mock
async def test_geocoder_down_still_gives_a_context(loader, meteo):
    respx.get(GEOCODE_URL).mock(return_value=httpx.Response(503))
    ctx = await build_full_context(loader.load("dubai_up01"), 1, meteo)
    assert ctx.timezone is None and ctx.freshness["geocode"] == "unavailable"
    assert ctx.ctx_id.startswith("ctx_")