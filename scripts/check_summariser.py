"""Repeatable check of the Summariser on two known days.

Runs each day several times and scores the output against fixed, checkable expectations,
so a prompt change is judged on evidence across runs, not on one lucky (or unlucky) run.
"""
import asyncio
import re
import sys

from mb.agents.summariser import run_summariser
from mb.budget import Budget
from mb.config import load_config
from mb.context import build_full_context
from mb.llm import LLM
from mb.sources.itinerary import FileLoader
from mb.sources.openmeteo import OpenMeteo
from mb.sources.places import GooglePlaces
from mb.sources.routes import GoogleRoutes

BEFORE_1PM = re.compile(r"\b(0?[89]|1[0-2])(:\d\d)?\s*(am)?\b|\bbefore (lunch|the rain|13)", re.I)


def checks(trip_id, t):
    common = {
        "title <= 40 chars": len(t.notification_title) <= 40,
        "body <= 90 chars": len(t.notification_body) <= 90,
        "<= 4 heads-ups": len(t.heads_ups) <= 4,
        "no vague wording": not any(re.search(r"plan accordingly|be aware|may be challenging", h, re.I)
                                    for h in t.heads_ups),
    }
    if trip_id == "dubai_up01":
        return common | {
            "kebab flagged for vegetarians": any("Ustad" in c for c in t.conflicts),
            "kebab in a heads-up": any("Ustad" in h or "kebab" in h.lower() for h in t.heads_ups),
            "15:00 museum slot": any("15:00" in h for h in t.heads_ups),
            "abra cash": any("abra" in h.lower() and "cash" in h.lower() for h in t.heads_ups),
            "no weather clash invented": t.weather_clash is None,
        }
    return common | {
        "title says Bali": "Bali" in t.notification_title,
        "clash is Monkey Forest": bool(t.weather_clash) and "Monkey Forest" in t.weather_clash,
        "swap into a dry window": bool(t.suggested_swap) and bool(BEFORE_1PM.search(t.suggested_swap)),
        "late dinner flagged": any("Zest" in c for c in t.conflicts),
        "late finish flagged": any("22:30" in c for c in t.conflicts),
    }


async def main(repeats: int):
    cfg, budget, loader = load_config(), Budget.from_config(), FileLoader()
    meteo, places, routes, llm = OpenMeteo(), GooglePlaces(budget), GoogleRoutes(budget), LLM(budget)
    spec = cfg.agents["mb.summariser.v1"]
    total = passed = 0
    for trip_id, day, scenario in [("dubai_up01", 1, "dubai_clear_warm"), ("bali_up04", 1, "bali_rain_afternoon")]:
        ctx = await build_full_context(loader.load(trip_id), day, meteo, scenario,
                                       places=places, routes=routes, run_id="check-summariser")
        results = []
        for _ in range(repeats):
            summary, _ = await run_summariser(ctx, spec, llm, run_id="check-summariser")
            results.append(checks(trip_id, summary.text))
        print(f"\n== {trip_id} day {day}  ({spec.prompt_version}, {repeats} runs)")
        for name in results[0]:
            ok = sum(r[name] for r in results)
            total += repeats
            passed += ok
            print(f"  {'PASS' if ok == repeats else 'FAIL'}  {ok}/{repeats}  {name}")
    print(f"\nScore: {passed}/{total} = {100 * passed / total:.0f}%   {budget.status()}")

asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 3))
