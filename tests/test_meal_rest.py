import datetime as dt

from mb.contracts import Activity, Candidate, Window
from mb.sources.candidates import VISIT_MIN, meal_and_rest_filter

T = dt.time
LUNCH = Activity(id="l", kind="eat", start=T(12, 30), end=T(13, 30), name="Lunch at Arabian Tea House")


def cand(name, start, end, types=()):
    return Candidate(id=name, provider="google_places", name=name, window=Window(start=start, end=end), types=list(types))


def test_no_second_lunch_straight_after_lunch():
    out = meal_and_rest_filter([cand("Cafe Nero", T(14), T(17), ["cafe"])], [LUNCH], rest_after_lunch=False)
    assert out == []


def test_food_idea_later_in_the_day_is_fine():
    out = meal_and_rest_filter([cand("Dessert bar", T(16, 30), T(19), ["dessert"])], [LUNCH], False)
    assert [c.name for c in out] == ["Dessert bar"]


def test_idea_moves_out_of_the_rest_break():
    out = meal_and_rest_filter([cand("Aquarium", T(13, 30), T(18), ["aquarium"])], [LUNCH], rest_after_lunch=True)
    assert out[0].open_from == T(15, 0) and out[0].window.end == T(18)       # rest 13:30-15:00 kept free


def test_idea_only_in_the_rest_break_is_dropped():
    short = cand("Park", T(13, 30), T(15, 0), ["park"])
    assert meal_and_rest_filter([short], [LUNCH], rest_after_lunch=True) == []
    assert VISIT_MIN <= 90
