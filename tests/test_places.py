import datetime as dt
import re

import httpx
import pytest
import respx

from mb.budget import Budget, BudgetExceeded
from mb.sources.cache import JsonCache
from mb.sources.places import DETAILS_URL, SKU, SKU_DETAILS, SKU_IDS, TEXT_SEARCH_URL, GooglePlaces, is_open

MUSEUM = {"places": [{
    "id": "ChIJ_museum", "displayName": {"text": "Museum of the Future"},
    "formattedAddress": "Sheikh Zayed Rd, Dubai", "location": {"latitude": 25.219, "longitude": 55.282},
    "businessStatus": "OPERATIONAL",
    "regularOpeningHours": {
        "periods": [{"open": {"day": d, "hour": 10, "minute": 0}, "close": {"day": d, "hour": 21, "minute": 30}}
                    for d in range(7)],
        "weekdayDescriptions": ["Monday: 10:00 AM - 9:30 PM"]}}]}


RAW = MUSEUM["places"][0]
PRICES = {SKU: 0.0105, SKU_IDS: 0.0, SKU_DETAILS: 0.006}
DETAILS = re.compile(re.escape(DETAILS_URL) + ".*")


def make(tmp_path, limit=1.0, **kw):
    budget = Budget(limit_usd=limit, prices=PRICES, ledger=tmp_path / "ledger.jsonl")
    return GooglePlaces(budget, api_key="test", client=httpx.AsyncClient(),
                        cache=JsonCache("p", root=tmp_path), **kw)


@pytest.fixture
def places(tmp_path):
    return make(tmp_path)


def mock_google():
    search = respx.post(TEXT_SEARCH_URL).mock(return_value=httpx.Response(200, json={"places": [{"id": RAW["id"]}]}))
    details = respx.get(url__regex=DETAILS).mock(return_value=httpx.Response(200, json=RAW))
    return search, details


@respx.mock
async def test_lookup_finds_the_id_free_then_reads_details(places):
    search, details = mock_google()
    info = await places.lookup("Museum of the Future, Dubai", run_id="t")
    assert info.place_id == "ChIJ_museum" and info.source == "live"
    assert info.periods[0].open == dt.time(10, 0)
    assert search.calls[0].request.headers["X-Goog-FieldMask"] == "places.id"     # IDs only: the free tier
    assert places.budget.spent_usd() == pytest.approx(0.006)                       # one Place Details call


@respx.mock
async def test_same_place_for_the_next_traveller_comes_from_the_table(places):
    search, details = mock_google()
    await places.lookup("Museum of the Future, Dubai", run_id="t")
    again = await places.lookup("museum of the future, dubai ", run_id="t2")
    assert again.source == "cached" and search.call_count == 1 and details.call_count == 1
    assert places.budget.spent_usd() == pytest.approx(0.006)


@respx.mock
async def test_opening_hours_are_refreshed_after_the_set_days(tmp_path, monkeypatch):
    p = make(tmp_path, hours_refresh_days=3)
    search, details = mock_google()
    await p.lookup("Museum of the Future", run_id="t")
    import mb.sources.places as mod
    later = mod.time.time() + 4 * 86400
    monkeypatch.setattr(mod.time, "time", lambda: later)
    info = await p.lookup("Museum of the Future", run_id="t")
    assert info.source == "live" and details.call_count == 2 and search.call_count == 1   # only the details again


@respx.mock
async def test_failed_refresh_uses_the_stored_details(tmp_path, monkeypatch):
    p = make(tmp_path, hours_refresh_days=3)
    mock_google()
    await p.lookup("Museum of the Future", run_id="t")
    import mb.sources.places as mod
    later = mod.time.time() + 4 * 86400
    monkeypatch.setattr(mod.time, "time", lambda: later)
    respx.get(url__regex=DETAILS).mock(return_value=httpx.Response(503))
    assert (await p.lookup("Museum of the Future", run_id="t")).source == "cached"


@respx.mock
async def test_ideas_search_stores_every_place_it_returns(places):
    near = respx.post(TEXT_SEARCH_URL).mock(return_value=httpx.Response(200, json=MUSEUM))
    found = await places.search_near("museum", 25.2, 55.28, run_id="t")
    assert [f.place_id for f in found] == ["ChIJ_museum"]
    again = await places.search_near("museum", 25.2, 55.28, run_id="t")
    assert near.call_count == 1 and again[0].source == "cached"
    assert places.table.get_place("ChIJ_museum") is not None                     # reusable by any traveller


@respx.mock
async def test_no_match_returns_none_and_is_remembered(places):
    route = respx.post(TEXT_SEARCH_URL).mock(return_value=httpx.Response(200, json={}))
    assert await places.lookup("Nowhere Cafe", run_id="t") is None
    assert await places.lookup("Nowhere Cafe", run_id="t") is None
    assert route.call_count == 1


async def test_budget_exhausted_blocks_the_paid_call(tmp_path):
    p = make(tmp_path, limit=0.001)
    with respx.mock(assert_all_called=False) as mock:
        mock.post(TEXT_SEARCH_URL).mock(return_value=httpx.Response(200, json={"places": [{"id": RAW["id"]}]}))
        details = mock.get(url__regex=DETAILS)
        with pytest.raises(BudgetExceeded):
            await p.lookup("Museum of the Future", run_id="t")
        assert details.call_count == 0


@respx.mock
async def test_opening_hours_check(places):
    mock_google()
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