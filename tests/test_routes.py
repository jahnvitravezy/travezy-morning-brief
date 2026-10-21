import httpx
import pytest
import respx

from mb.budget import Budget
from mb.sources.cache import JsonCache
from mb.sources.routes import ROUTE_MATRIX_URL, SKU, GoogleRoutes, haversine_km

FAHIDI = (25.2637, 55.2972)
GOLD_SOUK = (25.2697, 55.2962)
OK = [{"originIndex": 0, "destinationIndex": 0, "duration": "900s",
       "distanceMeters": 5200, "condition": "ROUTE_EXISTS"}]


@pytest.fixture
def routes(tmp_path):
    budget = Budget(limit_usd=1.0, prices={SKU: 0.005}, ledger=tmp_path / "ledger.jsonl")
    return GoogleRoutes(budget, api_key="test", client=httpx.AsyncClient(),
                        cache=JsonCache("r", root=tmp_path))


@respx.mock
async def test_live_leg_then_cached(routes):
    route = respx.post(ROUTE_MATRIX_URL).mock(return_value=httpx.Response(200, json=OK))
    assert await routes.leg(FAHIDI, GOLD_SOUK, run_id="t") == (15, 5.2, "live")
    assert await routes.leg(FAHIDI, GOLD_SOUK, run_id="t") == (15, 5.2, "cached")
    assert route.call_count == 1


@respx.mock
async def test_api_down_falls_back_to_estimate(routes):
    respx.post(ROUTE_MATRIX_URL).mock(return_value=httpx.Response(503))
    minutes, km, source = await routes.leg(FAHIDI, GOLD_SOUK, run_id="t")
    assert source == "estimated" and minutes > 0


@respx.mock
async def test_no_route_falls_back_to_estimate(routes):
    bad = [{"originIndex": 0, "destinationIndex": 0, "condition": "ROUTE_NOT_FOUND"}]
    respx.post(ROUTE_MATRIX_URL).mock(return_value=httpx.Response(200, json=bad))
    assert (await routes.leg(FAHIDI, GOLD_SOUK, run_id="t"))[2] == "estimated"


def test_haversine_is_sane():
    assert 0.5 < haversine_km(FAHIDI, GOLD_SOUK) < 1.0   # across the creek