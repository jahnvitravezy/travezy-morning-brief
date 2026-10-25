import datetime as dt

import pytest

from mb.config import load_config
from mb.context import build_context
from mb.contracts import Candidate, MorningBrief, Notification, SurpriseItem, TimelineItem
from mb.judge.rules import run_rule_checks, validate_rules
from mb.sources.itinerary import FileLoader

CFG = load_config()
RUBRIC = CFG.rubrics["rubric.morning_brief.v1"]


@pytest.fixture(scope="module")
def ctx():
    c = build_context(FileLoader().load("dubai_up01"), 1)
    w = c.free_windows[0]
    return c.model_copy(update={"candidates": [Candidate(id="places:p1", provider="google_places", name="Tea House",
                                                         window=w, place_id="p1")]})


def brief(**kw):
    base = dict(brief_id="b", trip_id="dubai_up01", date=dt.date(2026, 11, 21), send_at_local="07:30",
                notification=Notification(title="Day 2 in Dubai", body="Old Dubai, then the museum at 15:00"),
                timeline=[TimelineItem(time="09:00", name="Al Fahidi")])
    return MorningBrief(**(base | kw))


def s(name="Tea House", place_id="p1", url=None):
    return SurpriseItem(name=name, why_you="veg", fits_slot="11:00-13:00", place_id=place_id, source_url=url)


def test_good_brief_passes(ctx):
    assert run_rule_checks(brief(surprises=[s()]), ctx, RUBRIC) == []


def test_title_and_body_limits(ctx):
    out = run_rule_checks(brief(notification=Notification(title="x" * 41, body="y" * 91)), ctx, RUBRIC)
    assert len(out) == 2 and "41 characters" in out[0]


def test_too_many_surprises(ctx):
    assert any("4 surprises" in m for m in run_rule_checks(brief(surprises=[s()] * 4), ctx, RUBRIC))


def test_surprise_must_resolve_to_a_gathered_place_or_have_a_link(ctx):
    out = run_rule_checks(brief(surprises=[s("Invented", "zzz"), s("Nothing", None)]), ctx, RUBRIC)
    assert len(out) == 2
    assert run_rule_checks(brief(surprises=[s("Blog", None, "https://x.com")]), ctx, RUBRIC) == []


def test_empty_title_is_caught(ctx):
    assert any("notification.title" in m for m in run_rule_checks(brief(notification=Notification()), ctx, RUBRIC))


def test_unknown_rule_check_is_an_error():
    bad = RUBRIC.model_copy(update={"rule_checks": RUBRIC.rule_checks | {"no_emoji": True}})
    with pytest.raises(ValueError, match="no_emoji"):
        validate_rules(bad)
