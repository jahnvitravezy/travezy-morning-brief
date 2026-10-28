"""The batch runner: prepares each traveller's Morning Brief before their local send time.

  python -m mb.batch --at 2026-11-21T02:00:00Z --window 120 --plan     # who is due, no cost
  python -m mb.batch --at 2026-11-21T02:00:00Z --window 120            # generate and store them

Run it on a schedule (every 15 minutes, say). Each run looks at every trip, works out where the
traveller wakes up that day (home on an arrival morning, the destination otherwise), turns the
local send time (07:30, or an hour before an early first activity) into UTC, and prepares every
brief due within the next `window` minutes. A brief already in the outbox is skipped, so a run
can be repeated or overlap safely. Sending the notification is the app's job; when and how it may
interrupt the traveller is covered in the design note.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from mb.budget import BudgetExceeded
from mb.context import build_full_context, send_time, wake_timezone
from mb.evals.testset import load_testset
from mb.pipeline import run_brief
from mb.services import Services
from mb.store import BriefStore, OutboxItem, get_store


@dataclass
class Due:
    trip_id: str
    index: int
    date: dt.date
    timezone: str
    send_local: dt.time
    send_utc: dt.datetime


async def due_briefs(s: Services, at: dt.datetime, window_min: int) -> list[Due]:
    """Every trip day whose local send time falls in [at, at + window)."""
    end = at + dt.timedelta(minutes=window_min)
    out = []
    for trip_id in s.loader.trip_ids():
        trip = s.loader.load(trip_id)
        for day in trip.days:
            tz, _, _ = await wake_timezone(trip, day, s.meteo)
            if not tz:
                continue
            local = send_time(day, lead_min=s.cfg.app.send_lead_min,
                              default=dt.time.fromisoformat(s.cfg.app.default_send_local))
            utc = dt.datetime.combine(day.date, local, tzinfo=ZoneInfo(tz)).astimezone(dt.timezone.utc)
            if at <= utc < end:
                out.append(Due(trip_id, day.index, day.date, tz, local, utc))
    return sorted(out, key=lambda d: (d.send_utc, d.trip_id))


def _scenarios() -> dict[tuple[str, dt.date], str]:
    """Stored weather for test-set days (trip dates are beyond the forecast range)."""
    return {(d.trip_id, d.date): d.weather for d in load_testset().days}


async def run_batch(s: Services, at: dt.datetime, window_min: int, store: BriefStore,
                    plan_only: bool = False, force: bool = False, use_scenarios: bool = True,
                    concurrency: int = 3) -> list[dict]:
    due = await due_briefs(s, at, window_min)
    scen = _scenarios() if use_scenarios else {}
    sem = asyncio.Semaphore(concurrency)
    results = []

    async def one(d: Due) -> dict:
        row = {"trip_id": d.trip_id, "date": d.date.isoformat(), "timezone": d.timezone,
               "send_local": f"{d.send_local:%H:%M}", "send_utc": d.send_utc.isoformat()}
        if store.has(d.trip_id, d.date.isoformat()) and not force:
            return row | {"status": "already in outbox"}
        if plan_only:
            return row | {"status": "due"}
        async with sem:
            try:
                trip = s.loader.load(d.trip_id)
                ctx = await build_full_context(trip, d.index, s.meteo, scen.get((d.trip_id, d.date)),
                                               places=s.places, routes=s.routes, run_id=f"batch-{d.trip_id}-{d.date}",
                                               providers=s.providers)
                run = await run_brief(ctx, s.cfg, s.llm, run_id=f"batch-{ctx.ctx_id}")
            except BudgetExceeded as e:
                return row | {"status": f"skipped: budget reached ({e})"}
            b = run.brief
            store.save(OutboxItem(d.trip_id, d.date.isoformat(), d.timezone, f"{d.send_local:%H:%M}",
                                  d.send_utc.isoformat(), b.meta.judge_verdict, b.confidence.score,
                                  b.confidence.badge, b.model_dump(mode="json")))
            return row | {"status": f"ready ({b.meta.judge_verdict}, {b.confidence.score} {b.confidence.badge})"}

    results = await asyncio.gather(*(one(d) for d in due))
    for r in results:
        print(f"  {r['send_utc'][:16]}Z  {r['send_local']} {r['timezone']:15} {r['trip_id']:11} {r['date']}  {r['status']}")
    print(f"\n{len(results)} due between {at:%Y-%m-%d %H:%M}Z and +{window_min} min")
    return results


def _parse_at(text: str | None) -> dt.datetime:
    if not text:
        return dt.datetime.now(dt.timezone.utc)
    t = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--at", help="start of the window, UTC (default: now)")
    p.add_argument("--window", type=int, default=60, help="minutes ahead to prepare (default 60)")
    p.add_argument("--plan", action="store_true", help="only list who is due; no cost")
    p.add_argument("--force", action="store_true", help="regenerate briefs already in the outbox")
    a = p.parse_args()
    s = Services()
    asyncio.run(run_batch(s, _parse_at(a.at), a.window, get_store(), plan_only=a.plan, force=a.force))


if __name__ == "__main__":
    main()
