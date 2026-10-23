"""Smoke test: candidates and Agent 2 on one Dubai and one Bali day."""
import asyncio

from mb.agents.surprise_finder import run_surprise_finder
from mb.budget import Budget
from mb.config import load_config
from mb.context import build_full_context
from mb.llm import LLM
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
    spec = cfg.agents["mb.surprise_finder.v1"]
    for trip_id, day, scenario in [("dubai_up01", 1, "dubai_clear_warm"), ("bali_up04", 1, "bali_rain_afternoon")]:
        ctx = await build_full_context(loader.load(trip_id), day, meteo, scenario, places=places,
                                       routes=routes, run_id="smoke-agent2", providers=providers)
        print(f"\n== {trip_id} day {day}: {len(ctx.candidates)} candidates")
        for c in ctx.candidates:
            print(f"  {c.window.start:%H:%M}-{c.window.end:%H:%M}  {c.provider:13} {c.name[:45]:45} "
                  f"travel={c.travel_min} rating={c.rating}")
        out, res = await run_surprise_finder(ctx, spec, llm, run_id="smoke-agent2")
        cost = f"${res.cost_usd:.4f}" if res else "no model call"
        print(f"  -> picks ({cost}):")
        for s in out.items:
            print(f"     {s.fits_slot}  {s.name}: {s.why_you}  [{s.distance_min} min]")
        if out.none_reason:
            print(f"     none: {out.none_reason}")
        for d in out.dropped:
            print(f"     dropped: {d}")
    print("\n", budget.status())

asyncio.run(main())
