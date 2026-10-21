import datetime as dt

import httpx
import pytest
import respx

from mb.budget import Budget, BudgetExceeded
from mb.sources.cache import JsonCache
from mb.sources.places import SKU, TEXT_SEARCH_URL, GooglePlaces, is_open

MUSEUM = {"places": [{
    "id": "ChIJ_museum", "displayName": {"text": "Museum of the Future"},
    "formattedAddress": "Sheikh Zayed Rd, Dubai", "location": {"latitude": 25.219, "longitude": 55.282},
    "businessStatus": "OPERATIONAL",
    "regularOpeningHours": {
        "periods": [{"open": {"day": d, "hour": 10, "minute": 0}, "close": {"day": d, "hour": 21, "minute": 30}}
                    for d in range(7)],
        "weekdayDescriptions": ["Monday: 10:00 AM - 9:30 PM"]}}]}


@pytest.fixture
def places(tmp_path):
    budget = Budget(limit_usd=1.0, prices={SKU: 0.035}, ledger=tmp_path / "ledger.jsonl")
    return GooglePlaces(budget, api_key="test", client=httpx.AsyncClient(),
                        cache=JsonCache("p", root=tmp_path))


@respx.mock
async def test_lookup_parses_place(places):
    respx.post(TEXT_SEARCH_URL).mock(return_value=httpx.Response(200, json=MUSEUM))
    info = await places.lookup("Museum of the Future, Dubai", run_id="t")
    assert info.place_id == "ChIJ_museum" and info.source == "live"
    assert info.periods[0].open == dt.time(10, 0)


@respx.mock
async def test_second_lookup_is_cached_and_paid_once(places):
    route = respx.post(TEXT_SEARCH_URL).mock(return_value=httpx.Response(200, json=MUSEUM))
    await places.lookup("Museum of the Future, Dubai", run_id="t")
    again = await places.lookup("museum of the future, dubai ", run_id="t")
    assert route.call_count == 1 and again.source == "cached"
    assert places.budget.spent_usd() == pytest.approx(0.035)


@respx.mock
async def test_no_match_returns_none_and_is_cached(places):
    route = respx.post(TEXT_SEARCH_URL).mock(return_value=httpx.Response(200, json={}))
    assert await places.lookup("Nowhere Cafe", run_id="t") is None
    assert await places.lookup("Nowhere Cafe", run_id="t") is None
    assert route.call_count == 1


async def test_budget_exhausted_blocks_the_call(tmp_path):
    budget = Budget(limit_usd=0.01, prices={SKU: 0.035}, ledger=tmp_path / "l.jsonl")
    p = GooglePlaces(budget, api_key="test", client=httpx.AsyncClient(), cache=JsonCache("p", root=tmp_path))
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(TEXT_SEARCH_URL)
        with pytest.raises(BudgetExceeded):
            await p.lookup("Museum of the Future", run_id="t")
        assert route.call_count == 0


@respx.mock
async def test_opening_hours_check(places):
    respx.post(TEXT_SEARCH_URL).mock(return_value=httpx.Response(200, json=MUSEUM))
    info = await places.lookup("Museum of the Future", run_id="t")
    sat = dt.date(2026, 11, 21)
    assert is_open(info, sat, dt.time(15, 0), dt.time(17, 0)) is True
    assert is_open(info, sat, dt.time(8, 0), dt.time(9, 0)) is False


def test_unknown_hours_and_closed_status():
    from mb.contracts import PlaceInfo
    unknown = PlaceInfo(place_id="x", name="x", source="live")
    closed = PlaceInfo(place_id="y", name="y", business_status="CLOSED_TEMPORARILY", source="live")
    day = dt.date(2026, 11, 21)
    assert is_open(unknown, day, dt.time(10), dt.time(11)) is None
    assert is_open(closed, day, dt.time(10), dt.time(11)) is False