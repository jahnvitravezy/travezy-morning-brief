"""Batch runner: local send times in the right time zone, idempotent outbox. No network, no cost."""
import datetime as dt
import os

import pytest

import mb.batch as batch
from mb.agents.composer import safe_version
from mb.config import load_config
from mb.context import build_context
from mb.pipeline import BriefRun
from mb.sources.itinerary import FileLoader
from mb.sources.openmeteo import Geo
from mb.store import FileStore, OutboxItem

TZ = {"dubai": "Asia/Dubai", "mumbai": "Asia/Kolkata", "bali": "Asia/Makassar", "denpasar": "Asia/Makassar",
      "chennai": "Asia/Kolkata"}


class Meteo:
    async def geocode(self, name):
        tz = next(v for k, v in TZ.items() if k in name.lower())
        return Geo(name=name, lat=0, lon=0, timezone=tz)


class S:
    def __init__(self):
        self.cfg, self.loader, self.meteo = load_config(), FileLoader(), Meteo()
        self.places = self.routes = self.llm = None
        self.providers = []


def utc(text):
    return dt.datetime.fromisoformat(text).replace(tzinfo=dt.timezone.utc)


async def test_send_times_follow_where_the_traveller_wakes_up():
    due = await batch.due_briefs(S(), utc("2026-11-20T00:00"), 24 * 60)
    up01 = {d.date: d for d in due if d.trip_id == "dubai_up01"}
    arrival = up01[dt.date(2026, 11, 20)]
    assert arrival.timezone == "Asia/Kolkata"                     # wakes up in Mumbai
    assert arrival.send_utc == utc("2026-11-20T02:00")            # 07:30 IST
    day2 = {d.date: d for d in await batch.due_briefs(S(), utc("2026-11-21T00:00"), 24 * 60)
            if d.trip_id == "dubai_up01"}[dt.date(2026, 11, 21)]
    assert day2.timezone == "Asia/Dubai" and day2.send_utc == utc("2026-11-21T03:30")   # 07:30 GST


async def test_early_start_sends_an_hour_before_and_can_cross_the_utc_date():
    # Bali day 4: driver at 07:00, so the brief goes at 06:00 local = 22:00 UTC the day before
    due = await batch.due_briefs(S(), utc("2026-12-12T21:00"), 120)
    d = next(x for x in due if x.trip_id == "bali_up01")
    assert d.date == dt.date(2026, 12, 13) and f"{d.send_local:%H:%M}" == "06:00"
    assert d.send_utc == utc("2026-12-12T22:00")


async def test_batch_is_idempotent(tmp_path, monkeypatch):
    async def ctx(trip, index, *a, **k):
        return build_context(trip, index)

    async def run(ctx, cfg, llm, run_id, **k):
        return BriefRun(brief=safe_version(ctx, "test"))

    monkeypatch.setattr(batch, "build_full_context", ctx)
    monkeypatch.setattr(batch, "run_brief", run)
    store = FileStore(tmp_path)
    at = utc("2026-11-21T03:00")
    first = await batch.run_batch(S(), at, 60, store)
    assert first and all(r["status"].startswith("ready") for r in first)
    assert len(store.for_date("2026-11-21")) == len(first)
    again = await batch.run_batch(S(), at, 60, store)
    assert all(r["status"] == "already in outbox" for r in again)
    plan = await batch.run_batch(S(), at, 60, FileStore(tmp_path / "x"), plan_only=True)
    assert all(r["status"] == "due" for r in plan) and not (tmp_path / "x").exists()


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="needs a Postgres (TEST_DATABASE_URL)")
def test_postgres_store_round_trip():
    from mb.store import PostgresStore
    s = PostgresStore(os.environ["TEST_DATABASE_URL"])
    s.conn.execute("DELETE FROM outbox WHERE trip_id='t_pg'")
    item = OutboxItem("t_pg", "2026-11-21", "Asia/Dubai", "07:30", "2026-11-21T03:30:00+00:00", "pass", 90, "green",
                      {"x": 1})
    s.save(item)
    s.save(item)                                                  # same trip and date: replaced, not duplicated
    got = [i for i in s.for_date("2026-11-21") if i.trip_id == "t_pg"]
    assert len(got) == 1 and got[0].brief == {"x": 1} and got[0].send_at_utc.startswith("2026-11-21T03:30")
    assert s.has("t_pg", "2026-11-21")


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="needs a Postgres (TEST_DATABASE_URL)")
def test_two_stores_starting_together_do_not_crash():
    from concurrent.futures import ThreadPoolExecutor
    from mb.store import PostgresStore
    url = os.environ["TEST_DATABASE_URL"]
    PostgresStore(url).conn.execute("DROP TABLE IF EXISTS outbox")
    with ThreadPoolExecutor(6) as ex:
        stores = list(ex.map(lambda _: PostgresStore(url), range(6)))
    assert all(s.has("nobody", "2026-01-01") is False for s in stores)
