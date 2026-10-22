import httpx
import pytest
import respx

from mb.budget import Budget
from mb.sources.cache import JsonCache
from mb.sources.tavily import SEARCH_URL, SKU, Tavily

RESP = {"results": [
    {"title": "Arabian Tea House", "url": "https://example.com/tea", "content": "Vegetarian menu", "score": 0.9},
    {"title": "No link", "content": "dropped"}]}


@pytest.fixture
def tavily(tmp_path):
    budget = Budget(limit_usd=1.0, prices={SKU: 0.008}, ledger=tmp_path / "ledger.jsonl")
    return Tavily(budget, api_key="test", client=httpx.AsyncClient(), cache=JsonCache("t", root=tmp_path))


@respx.mock
async def test_results_need_a_url_and_are_cached(tavily):
    route = respx.post(SEARCH_URL).mock(return_value=httpx.Response(200, json=RESP))
    results, source = await tavily.search("vegetarian cafe near Al Fahidi Dubai", run_id="t")
    assert source == "live" and [r.title for r in results] == ["Arabian Tea House"]
    _, again = await tavily.search("Vegetarian cafe near Al Fahidi Dubai ", run_id="t")
    assert again == "cached" and route.call_count == 1


@respx.mock
async def test_search_down_returns_unavailable_not_an_error(tavily):
    respx.post(SEARCH_URL).mock(return_value=httpx.Response(500))
    assert await tavily.search("anything", run_id="t") == ([], "unavailable")