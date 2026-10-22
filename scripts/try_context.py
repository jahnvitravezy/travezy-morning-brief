"""Smoke test: full live contexts for one Dubai and one Bali day."""
import asyncio

from mb.budget import Budget
from mb.context import build_full_context
from mb.sources.itinerary import FileLoader
from mb.sources.openmeteo import OpenMeteo
from mb.sources.places import GooglePlaces
from mb.sources.routes import GoogleRoutes


async def main():
    budget, loader = Budget.from_config(), FileLoader()
    meteo, places, routes = OpenMeteo(), GooglePlaces(budget), GoogleRoutes(budget)
    for trip_id, day, scenario in [("dubai_up01", 1, "dubai_clear_warm"), ("bali_up04", 1, "bali_rain_afternoon")]:
        ctx = await build_full_context(loader.load(trip_id), day, meteo, scenario,
                                       places=places, routes=routes, run_id="smoke-context")
        print(f"\n== {trip_id} day {day}  {ctx.ctx_id}  tz={ctx.timezone}  send={ctx.send_at_local}")
        print("freshness:", ctx.freshness)
        stops = sum(a.kind != "move" for a in ctx.day.activities)
        print(f"places verified: {len(ctx.places)}/{stops}")
        for l in ctx.legs:
            print(f"  leg {l.from_id} -> {l.to_id}: {l.minutes} min, {l.km} km ({l.source})")
        for w in ctx.free_windows:
            print(f"  free {w.start:%H:%M}-{w.end:%H:%M}  travel_in_window={w.travel_min}")
        for f in ctx.flags:
            print(f"  FLAG {f.code}: {f.message}")
    print("\n", budget.status())

asyncio.run(main())