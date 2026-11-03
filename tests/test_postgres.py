"""The Postgres side (ledger, alerts, cache, places table, outbox schema).

Runs only when TEST_DATABASE_URL points at a throwaway database, e.g.
    $env:TEST_DATABASE_URL = "postgresql://mb:<password>@localhost:5433/mb_test"
"""
import os

import pytest

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")


@pytest.fixture
def pg(monkeypatch):
    from mb import db
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("MB_DB_SCHEMA", "mb_test")
    db._conn = None
    c = db.conn()
    for t in ("usage_ledger", "budget_alerts", "cache", "places", "place_searches", "outbox"):
        c.execute(f"TRUNCATE {t}")
    yield c
    db._conn = None


def test_ledger_is_shared_and_each_alert_fires_once(pg):
    from mb.budget import Budget
    a, b = Budget(10.0, {}), Budget(10.0, {})          # two copies of the service
    a.record(run_id="r", kind="llm", item="x", cost_usd=6.0)
    b.record(run_id="r", kind="llm", item="x", cost_usd=0.1)
    assert b.spent_usd() == pytest.approx(6.1)
    assert len(a.alerts) == 1 and b.alerts == []


def test_places_table_round_trip(pg):
    from mb.sources.places import PgPlaceTable
    t = PgPlaceTable()
    t.put_place({"id": "P1", "displayName": {"text": "Museum"}, "location": {"latitude": 1, "longitude": 2}})
    t.put_search("text:museum", {"text": "museum"}, ["P1"])
    assert t.get_place("P1")[0]["id"] == "P1" and t.get_search("text:museum")[0] == ["P1"]


def test_cache_is_shared(pg):
    from mb.sources.cache import get_cache
    get_cache("w").set({"k": 1}, {"v": 2})
    assert get_cache("w").get({"k": 1}) == {"v": 2}
