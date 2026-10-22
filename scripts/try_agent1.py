"""Smoke test: Agent 1 on one Dubai and one Bali day."""
import asyncio

from mb.agents.summariser import run_summariser
from mb.budget import Budget
from mb.config import load_config
from mb.context import build_full_context
from mb.llm import LLM
from mb.sources.itinerary import FileLoader
from mb.sources.openmeteo import OpenMeteo
from mb.sources.places import GooglePlaces
from mb.sources.routes import GoogleRoutes


async def main():
    cfg, budget, loader = load_config(), Budget.from_config(), FileLoader()
    meteo, places, routes, llm = OpenMeteo(), GooglePlaces(budget), GoogleRoutes(budget), LLM(budget)
    spec = cfg.agents["mb.summariser.v1"]
    for trip_id, day, scenario in [("dubai_up01", 1, "dubai_clear_warm"), ("bali_up04", 1, "bali_rain_afternoon")]:
        ctx = await build_full_context(loader.load(trip_id), day, meteo, scenario,
                                       places=places, routes=routes, run_id="smoke-agent1")
        summary, res = await run_summariser(ctx, spec, llm, run_id="smoke-agent1")
        print(f"\n== {trip_id} day {day}  ({res.model}, {res.tokens_in}+{res.tokens_out} tokens, "
              f"${res.cost_usd:.4f}, {res.latency_ms} ms)")
        print(summary.model_dump_json(indent=2))
    print("\n", budget.status())

asyncio.run(main())