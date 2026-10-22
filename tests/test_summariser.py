import json

import pytest

from mb.agents.facts import load_fx, spend, timeline
from mb.agents.summariser import brief_input, run_summariser
from mb.config import load_config
from mb.context import build_context
from mb.contracts import SummaryText
from mb.llm import LLMResult, load_prompt
from mb.sources.itinerary import FileLoader

FX = {"USD": 1.0, "AED": 3.6725, "INR": 88.0, "IDR": 16500, "SGD": 1.30}


@pytest.fixture(scope="module")
def dubai_day2():
    return build_context(FileLoader().load("dubai_up01"), 1)


def test_spend_is_computed_in_code(dubai_day2):
    s = spend(dubai_day2, FX)            # abra 1 + museum 149 + kebab 60, per person
    assert s.local == "about AED 210 per person"
    assert s.home.startswith("about ₹5,")


def test_timeline_marks_bookings(dubai_day2):
    t = timeline(dubai_day2)
    assert len(t) == 5 and next(i for i in t if i.name == "Museum of the Future").booked


def test_every_registry_prompt_exists():
    for spec in load_config().agents.values():
        if spec.id == "mb.summariser.v1":
            assert load_prompt(spec.prompt_version)


def test_model_input_is_json_and_task_only(dubai_day2):
    data = json.loads(brief_input(dubai_day2))
    assert data["destination_day"] == 2 and "plan" in data
    assert "ctx_id" not in data and "trip_id" not in data


async def test_summary_combines_model_words_with_code_facts(dubai_day2):
    class FakeLLM:
        async def call(self, **kw):
            return LLMResult(SummaryText(notification_title="Day 2 in Dubai",
                                         notification_body="Old Dubai, the souks and the Museum of the Future",
                                         weather_summary="Sunny, 31°C", local_tip="Bargain at the souks"),
                             "fake", 10, 5, 0.0, 1)
    spec = load_config().agents["mb.summariser.v1"]
    summary, _ = await run_summariser(dubai_day2, spec, FakeLLM(), run_id="t")
    assert summary.text.notification_title == "Day 2 in Dubai"
    assert summary.spend.local == spend(dubai_day2, load_fx()).local
    assert len(summary.timeline) == 5

def test_model_gets_weather_per_activity_and_must_mention():
    from mb.context import build_context
    from mb.contracts import HourWeather, WeatherDay
    import datetime as dt
    trip = FileLoader().load("bali_up04")
    ctx = build_context(trip, 1)
    rain = [HourWeather(hour=dt.time(h), temp_c=28, rain_prob=85 if 13 <= h < 17 else 5, rain_mm=0)
            for h in range(6, 23)]
    ctx = ctx.model_copy(update={"weather": WeatherDay(location="Bali", source="fixture", hours=rain)})
    plan = {p["name"]: p for p in json.loads(brief_input(ctx))["plan"]}
    assert plan["Sacred Monkey Forest Sanctuary"]["weather"].startswith("rain up to 85%")
    assert plan["Tegallalang Rice Terrace"]["weather"].startswith("rain up to 5%")


def test_region_and_transport_names():
    dubai = FileLoader().load("dubai_up01")
    assert dubai.region == "Dubai" and FileLoader().load("bali_up04").region == "Bali"
    names = [a.name for a in dubai.days[1].activities]
    assert "Dubai Creek abra" in names and "Flight EK501" in [a.name for a in dubai.days[0].activities]


def test_timed_entries_are_must_mention(dubai_day2):
    must = json.loads(brief_input(dubai_day2))["must_mention"]
    assert any("Museum of the Future" in m and "15:00" in m for m in must)
    assert any("cash" in m.lower() for m in must)

def test_weather_clash_is_found_in_code_for_outdoor_items_only():
    import datetime as dt
    from mb.agents.facts import is_outdoor, weather_clashes
    from mb.contracts import HourWeather, WeatherDay
    trip = FileLoader().load("bali_up04")
    ctx = build_context(trip, 1)
    rain = [HourWeather(hour=dt.time(h), temp_c=28, rain_prob=85 if 13 <= h < 17 else 5, rain_mm=0)
            for h in range(6, 23)]
    ctx = ctx.model_copy(update={"weather": WeatherDay(location="Bali", source="fixture", hours=rain)})
    clashes = weather_clashes(ctx)
    assert len(clashes) == 1 and clashes[0].startswith("Sacred Monkey Forest Sanctuary 16:00")
    by_name = {a.name: a for a in ctx.day.activities}
    assert not is_outdoor(by_name["Clear Cafe"]) and is_outdoor(by_name["Tegallalang Rice Terrace"])


def test_museum_is_indoor():
    from mb.agents.facts import is_outdoor
    trip = FileLoader().load("dubai_up01")
    museum = next(a for a in trip.days[1].activities if a.name == "Museum of the Future")
    assert not is_outdoor(museum)
