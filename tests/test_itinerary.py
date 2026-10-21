from datetime import time

import pytest

from mb.context import build_context, free_windows
from mb.sources.itinerary import FileLoader, parse_cost, parse_duration, parse_trip


@pytest.fixture(scope="module")
def loader():
    return FileLoader()


def test_both_itineraries_parse(loader):
    assert len(loader.load("dubai_up01").days) == 4
    assert len(loader.load("bali_up04").days) == 5


def test_booked_and_timed_entry(loader):
    museum = next(a for a in loader.load("dubai_up01").days[1].activities
                  if a.name == "Museum of the Future")
    assert museum.booked and museum.timed_at == time(15, 0)


def test_free_day(loader):
    day = loader.load("bali_up04").days[2]
    assert day.is_free and day.activities == []


@pytest.mark.parametrize("text,amount,currency,per", [
    ("$280 per person", 280, "USD", "person"),
    ("AED 169 per person", 169, "AED", "person"),
    ("IDR 250,000 per person", 250000, "IDR", "person"),
    ("IDR 600,000 for the day", 600000, "IDR", "day"),
    ("AED 90", 90, "AED", "total"),
    ("Free", 0, None, "total"),
])
def test_costs(text, amount, currency, per):
    m = parse_cost(text)
    assert (m.amount, m.currency, m.per) == (amount, currency, per)


def test_durations():
    assert parse_duration("1 hour 30 minutes") == 90
    assert parse_duration("3 hours") == 180
    assert parse_duration("45 minutes") == 45


def test_wake_location_carries_forward(loader):
    days = loader.load("dubai_up01").days
    assert days[0].wake_location == "Mumbai"
    assert days[1].wake_location == "Rove Downtown"
    assert loader.load("bali_up04").days[0].wake_location == "Chennai"


def test_overnight_flight_is_capped_at_end_of_day(loader):
    flight = loader.load("bali_up04").days[4].activities[-1]
    assert flight.kind == "move" and flight.end == time(23, 59)


def test_free_windows_match_the_mockup(loader):
    trip = loader.load("dubai_up01")
    w = [(x.start, x.end) for x in free_windows(trip.days[1], trip.preferences)]
    assert (time(14, 0), time(15, 0)) in w
    assert (time(17, 0), time(20, 0)) in w


def test_no_windows_after_leaving_for_the_airport(loader):
    trip = loader.load("dubai_up01")
    assert all(x.end <= time(15, 0) for x in free_windows(trip.days[3], trip.preferences))


def test_family_is_back_by_8pm(loader):
    trip = loader.load("dubai_up02")
    assert all(x.end <= time(20, 0) for x in free_windows(trip.days[1], trip.preferences))


def test_unknown_activity_type_fails_loudly(loader):
    prefs = loader.load("dubai_up01").preferences
    raw = {"plan": [{"dt": "2026-11-20", "act_ttl": "x", "acts": [{"tm": "09:00 AM", "type": "teleport"}]}]}
    with pytest.raises(ValueError, match="unknown activity type"):
        parse_trip("t", "t", raw, prefs)


def test_ctx_id_is_stable_and_input_sensitive(loader):
    a = build_context(loader.load("dubai_up01"), 1)
    b = build_context(loader.load("dubai_up01"), 1)
    c = build_context(loader.load("dubai_up02"), 1)
    assert a.ctx_id == b.ctx_id != c.ctx_id
    assert a.freshness["places"] == "not_fetched"


def test_other_days_prevent_repeats(loader):
    ctx = build_context(loader.load("dubai_up01"), 1)
    assert "Burj Khalifa At the Top" in ctx.other_days
    assert "Museum of the Future" not in ctx.other_days