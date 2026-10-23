"""Smoke test: the whole pipeline (context, Agents 1-3) on one Dubai and one Bali day."""
import asyncio

from mb.budget import Budget
from mb.config import load_config
from mb.context import build_full_context
from mb.llm import LLM
from mb.pipeline import run_brief
from mb.sources.candidates import PlacesCandidates, WebCandidates
from mb.sources.itinerary import FileLoader
from mb.sources.openmeteo import OpenMeteo
from mb.sources.places import GooglePlaces
from mb.sources.routes import GoogleRoutes
from mb.sources.tavily import Tavily


async def main():
    cfg, budget, loader = load_config(), Budget.from_config(), FileLoader()
    places = GooglePlaces(budget)
    providers = [PlacesCandidates(places), WebCandidates(Tavily(budget))]
    meteo, routes, llm = OpenMeteo(), GoogleRoutes(budget), LLM(budget)
    for trip_id, day, scenario in [("dubai_up01", 1, "dubai_clear_warm"), ("bali_up04", 1, "bali_rain_afternoon")]:
        ctx = await build_full_context(loader.load(trip_id), day, meteo, scenario, places=places,
                                       routes=routes, run_id="smoke-brief", providers=providers)
        run = await run_brief(ctx, cfg, llm, run_id="smoke-brief")
        print(f"\n== {trip_id} day {day}   ${run.cost_usd:.4f}   {run.brief.meta.latency_ms} ms")
        for e in run.events:
            print("  ", e)
        print(run.brief.model_dump_json(indent=2, exclude={"timeline"}))
    print("\n", budget.status())

asyncio.run(main())
