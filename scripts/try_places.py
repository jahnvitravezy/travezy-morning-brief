"""Smoke test: one real Places call, to confirm the key and billing work."""
import asyncio

from mb.budget import Budget
from mb.sources.places import GooglePlaces


async def main():
    places = GooglePlaces(Budget.from_config())
    info = await places.lookup("Museum of the Future, Sheikh Zayed Road, Dubai", run_id="smoke-places")
    print(info.model_dump_json(indent=2) if info else "No match")
    print(places.budget.status())

asyncio.run(main())