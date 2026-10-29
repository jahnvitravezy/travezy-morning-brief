"""Exchange rates for the spend line: live once a day, cached, or no home figure at all."""
import datetime as dt

import pytest

from mb.context import build_context
from mb.sources import fx
from mb.sources.itinerary import FileLoader

DAY = dt.date(2026, 10, 30)


def boom():
    raise ConnectionError("offline")


def test_live_rate_is_fetched_once_a_day(tmp_path):
    calls = []
    def fetch():
        calls.append(1)
        return {"USD": 1, "INR": 95.22, "AED": 3.6725}
    r1 = fx.live(DAY, fetch, tmp_path)
    r2 = fx.live(DAY, fetch, tmp_path)
    assert r1["INR"] == 95.22 and r1.source == "live" and r1.as_of == DAY
    assert r2["INR"] == 95.22 and len(calls) == 1          # second brief of the day: from the cache


def test_source_down_uses_a_recent_rate(tmp_path):
    fx.live(DAY, lambda: {"USD": 1, "INR": 95.0}, tmp_path)
    r = fx.live(DAY + dt.timedelta(days=2), boom, tmp_path)
    assert r.source == "cached" and r.as_of == DAY and r.trusted


def test_no_recent_rate_means_unverified(tmp_path):
    fx.live(DAY, lambda: {"USD": 1, "INR": 95.0}, tmp_path)
    r = fx.live(DAY + dt.timedelta(days=3), boom, tmp_path)
    assert r.source == "unverified" and not r.trusted


@pytest.fixture(scope="module")
def dubai_day2():
    return build_context(FileLoader().load("dubai_up01"), 1)


def test_unverified_rate_drops_the_home_figure(dubai_day2):
    from mb.agents.facts import spend
    rates = fx.fixed()
    trusted = spend(dubai_day2, rates)
    unsure = spend(dubai_day2, fx.Rates(rates, "unverified"))
    assert trusted.home.startswith("about ₹") and trusted.local
    assert unsure.home == "" and unsure.local == trusted.local
