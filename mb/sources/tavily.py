"""Tavily web search: nearby things to do, every result with a source URL.

Search results only ever become surprise candidates; a surprise without a URL or a
place ID is rejected by the judge's rule checks.
"""
from __future__ import annotations

import httpx
from decouple import config

from mb.budget import Budget
from mb.contracts import WebResult
from mb.sources.cache import JsonCache

SEARCH_URL = "https://api.tavily.com/search"
SKU = "tavily_search"


class Tavily:
    def __init__(self, budget: Budget, api_key: str | None = None,
                 client: httpx.AsyncClient | None = None, cache: JsonCache | None = None):
        self.key = api_key or config("TAVILY_API_KEY")
        self.client = client or httpx.AsyncClient(timeout=15)
        self.cache = cache or JsonCache("tavily")
        self.budget = budget

    async def search(self, query: str, run_id: str, max_results: int = 5) -> tuple[list[WebResult], str]:
        """Returns (results, source). On failure: ([], "unavailable"), never an exception."""
        key = {"q": query.lower().strip(), "n": max_results}
        hit = self.cache.get(key)
        if hit is not None:
            return [WebResult(**r) for r in hit], "cached"
        try:
            self.budget.check(self.budget.prices[SKU])
            r = await self.client.post(
                SEARCH_URL, headers={"Authorization": f"Bearer {self.key}"},
                json={"query": query, "max_results": max_results, "search_depth": "basic"})
            r.raise_for_status()
            self.budget.record_call(run_id=run_id, sku=SKU)
            results = [WebResult(title=x.get("title", ""), url=x["url"], snippet=x.get("content", "")[:500],
                                 score=float(x.get("score", 0)))
                       for x in r.json().get("results", []) if x.get("url")]
            self.cache.set(key, [x.model_dump() for x in results])
            return results, "live"
        except (httpx.HTTPError, KeyError, ValueError):
            return [], "unavailable"