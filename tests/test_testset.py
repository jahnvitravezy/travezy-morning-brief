import yaml

from mb.config import CONFIG_DIR
from mb.evals.testset import REQUIRED_COVERAGE, load_testset
from mb.sources.itinerary import FileLoader

TS = load_testset()


def test_at_least_20_days_with_unique_ids():
    assert len(TS.days) >= 20
    assert len({d.id for d in TS.days}) == len(TS.days)


def test_every_required_case_is_covered():
    covered = {c for d in TS.days for c in d.covers}
    assert REQUIRED_COVERAGE <= covered, REQUIRED_COVERAGE - covered
    assert covered <= REQUIRED_COVERAGE, covered - REQUIRED_COVERAGE        # no typos like "free-day"


def test_both_destinations_and_two_no_surprise_days():
    assert {d.trip_id.split("_")[0] for d in TS.days} == {"dubai", "bali"}
    assert sum("no_surprise" in d.covers for d in TS.days) >= 2


def test_trips_dates_and_weather_scenarios_exist():
    scenarios = yaml.safe_load((CONFIG_DIR.parent / "data" / "fixtures" / "weather.yaml").read_text())
    loader = FileLoader()
    for d in TS.days:
        trip = loader.load(d.trip_id)
        assert d.date in {x.date for x in trip.days}, d.id
        assert d.weather in scenarios, d.id
        assert d.weather.split("_")[0] == d.trip_id.split("_")[0], d.id   # Bali weather on a Bali day


def test_case_labels_match_the_itinerary():
    loader = FileLoader()
    for d in TS.days:
        day = next(x for x in loader.load(d.trip_id).days if x.date == d.date)
        assert ("arrival" in d.covers) == day.is_arrival, d.id
        assert ("departure" in d.covers) == day.is_departure, d.id
        assert ("free_day" in d.covers) == day.is_free, d.id
