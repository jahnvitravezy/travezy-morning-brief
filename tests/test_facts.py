"""Facts and checks computed in code, from the baseline review (25 Oct)."""
import datetime as dt

from mb.agents.composer import merge
from mb.agents.facts import (diet_checks, ensure_covered, access_checks, airport_checks, suggested_swap,
                             swap_slots, weather_summary)
from mb.context import build_context, free_windows
from mb.contracts import (HourWeather, Summary, SummaryText, SurpriseItem, Surprises, WeatherDay)
from mb.sources.itinerary import FileLoader

L = FileLoader()


def wx(rain=(), temp=30, hot=()):
    return WeatherDay(location="x", source="fixture", hours=[
        HourWeather(hour=dt.time(h), temp_c=38 if h in hot else temp, rain_prob=85 if h in rain else 5, rain_mm=0)
        for h in range(6, 24)])


def ctx_for(trip, day, **weather):
    c = build_context(L.load(trip), day)
    return c.model_copy(update={"weather": wx(**weather)}) if weather else c


def test_weather_line_is_one_format_and_names_rain_hours():
    c = ctx_for("bali_up05", 2, rain=(14, 15, 16))
    assert weather_summary(c) == "30-30°C, rain likely 14:00-17:00"
    assert weather_summary(ctx_for("dubai_up01", 1, hot=(11, 12))).endswith("no rain expected, hot 11:00-13:00")


def test_swap_never_lands_on_another_plan():
    # Monkey Forest 16:00-17:30 in rain; free windows 13:45-16:00 (rainy) and 17:30-19:30 (dry, but too late
    # for a place that closes at 17:00 is checked when hours are known). Here: no dry free slot -> no swap.
    c = ctx_for("bali_up01", 1, rain=(13, 14, 15, 16, 17))
    forest = next(a for a in c.day.activities if "Monkey" in a.name)
    for slot in swap_slots(c, forest):
        s, e = slot.split("-")
        assert any(f"{w.start:%H:%M}" <= s and e <= f"{w.end:%H:%M}" for w in c.free_windows)
    hot = ctx_for("dubai_up05", 3, hot=(11, 12, 13, 14))           # JBR Beach clash, only free time is hot too
    assert suggested_swap(hot) is None


def test_departure_day_ends_three_hours_before_the_flight():
    c = build_context(L.load("bali_up02"), 4)                   # flight 16:00, no transfer planned
    assert all(w.end <= dt.time(13, 0) for w in c.free_windows)   # 12:30-13:00 is too short to use
    assert airport_checks(c) == ["Leave for the airport by 13:00: your flight is at 16:00"]
    assert airport_checks(build_context(L.load("dubai_up01"), 3)) == [      # taxi planned: say when to be ready
        "Airport transfer (Taxi) at 15:00 for your 18:00 flight: be packed and ready by 14:30"]


def test_arrival_day_has_no_free_time_before_check_in():
    c = build_context(L.load("dubai_up01"), 0)
    assert c.free_windows[0].start >= dt.time(13, 30)


def test_jain_meals_and_steep_paths_for_children():
    c = build_context(L.load("bali_up02"), 1)                   # Jain family, Clear Cafe and Zest
    assert "Clear Cafe and Zest Ubud" in diet_checks(c)[0]
    veg = diet_checks(build_context(L.load("bali_up01"), 1))         # vegetarian couple: every meal checked
    assert veg and veg[0].startswith("Vegetarian food: check") and veg[0].endswith("for you")
    assert diet_checks(build_context(L.load("dubai_up03"), 1)) == []  # eats everything
    k = build_context(L.load("bali_up02"), 3)                   # Kelingking: steep path, kids 6 and 10
    assert "children (6 and 10)" in access_checks(k)[0]


def test_dropped_checks_are_added_back_and_kept_when_present():
    c = build_context(L.load("dubai_up02"), 1)                  # Jain family, kebab dinner, back by 20:00
    added = ensure_covered([], c)
    assert any("Jain" in h for h in added) and any("20:00" in h for h in added)
    assert any("Museum of the Future" in h for h in added)
    kept = ensure_covered(["Be at the Museum of the Future for your 15:00 slot."], c)
    assert sum("Museum" in h for h in kept) == 1


def test_surprise_not_repeated_in_heads_ups_or_tip():
    c = build_context(L.load("bali_up04"), 2)                   # free day
    w = c.free_windows[0]
    text = SummaryText(notification_title="Day 3 in Bali", notification_body="Free day",
                       heads_ups=["Ocean Grand Beach Bali: great beach club."], weather_summary="",
                       local_tip="Go to Ocean Grand Beach Bali early.")
    summary = Summary(agent_id="a1", text=text, timeline=[], spend=__import__("mb.contracts").contracts.Spend())
    item = SurpriseItem(name="Ocean Grand Beach Bali", why_you="beach club", fits_slot=f"{w.start:%H:%M}-{w.end:%H:%M}")
    b = merge(c, summary, Surprises(agent_id="a2", items=[item]))
    assert not any("Ocean" in h for h in b.heads_ups) and "Ocean" not in b.local_tip
    assert b.spend_estimate.local == "Nothing to pay for in today's plan"


def test_one_jain_line_is_enough_and_delays_count_as_late():
    c = build_context(L.load("bali_up02"), 3)
    out = ensure_covered(["Ask Penida Colada for Jain-friendly dishes.",
                          "Expect delays reaching Penida Colada and Crystal Bay."], c)
    assert sum("Jain" in h for h in out) == 1
    assert not any("min late" in h for h in out)
    assert all(h.endswith(".") for h in out)


def test_rain_on_a_free_day_becomes_a_heads_up():
    from mb.agents.facts import free_day_weather
    c = ctx_for("bali_up05", 2, rain=(14, 15, 16))
    assert free_day_weather(c) == ["Rain likely 14:00-17:00: keep that time for something indoors"]
    assert any("Rain likely" in h for h in ensure_covered([], c))


def test_cash_only_must_be_mentioned():
    c = build_context(L.load("dubai_up02"), 1)
    assert any("cash" in h.lower() for h in ensure_covered([], c))
    assert sum("cash" in h.lower() for h in ensure_covered(["Carry AED 1 cash each for the abra."], c)) == 1


def test_tip_with_an_unknown_place_or_risky_advice_is_replaced():
    from mb.agents.facts import safe_tip, tip_problems
    c = build_context(L.load("dubai_up02"), 1)
    assert tip_problems("At Al Fahidi, visit the Coffee Museum for a unique experience.", c)
    assert tip_problems("At Gold and Spice Souks, look for saffron.", c) == []
    assert tip_problems("Take the steep path down for a closer view.", c)
    assert safe_tip(c)


def test_closing_time_becomes_a_leave_by_action():
    c = ctx_for("bali_up01", 1)
    from mb.contracts import Flag
    forest = next(a for a in c.day.activities if "Monkey" in a.name)
    c = c.model_copy(update={"flags": [Flag(code="closes_during_visit", activity_id=forest.id,
                                            message=f"{forest.name}: closes at 17:00, before the planned end at 17:30")]})
    out = ensure_covered(["Sacred Monkey Forest closes at 17:00, adjust your visit time."], c)
    assert any(h.startswith("Leave Sacred Monkey Forest Sanctuary by 17:00") for h in out)
    assert not any("adjust your visit" in h for h in out)


def test_late_finish_line_reads_like_a_heads_up():
    """The code's own line must read naturally if it lands in the brief (B02 showed raw check text)."""
    import datetime as dt
    from types import SimpleNamespace as NS
    from mb.agents.facts import pace_checks
    act = NS(kind="eat", name="Al Ustad Special Kebab", start=dt.time(20, 15), end=dt.time(21, 15), move_mode=None)
    pace = NS(dinner_around=None, start_not_before=None, back_by=dt.time(20, 0))
    ctx = NS(preferences=NS(pace=pace), day=NS(activities=[act]))
    [line] = pace_checks(ctx)
    assert line == "Long day: you finish around 21:15 at Al Ustad Special Kebab, later than your usual 20:00 return"
    assert "after 20:00" not in line


def test_tip_with_an_unverifiable_safety_claim_is_replaced():
    """B18 (stand-in review): 'calm waters perfect for children to swim safely' is a promise no source supports."""
    from mb.agents.facts import safe_tip, tip_problems
    ctx = build_context(FileLoader().load("bali_up01"), 2)
    bad = "Enjoy the calm waters, perfect for children to swim safely."
    assert any("safety claim" in p for p in tip_problems(bad, ctx))
    assert not any("safety claim" in p for p in tip_problems(safe_tip(ctx), ctx))


def test_ideas_end_before_the_last_pickup_not_at_it():
    """B18: an idea ran 14:04-15:00 with the airport taxi at 15:00. Windows now end 30 min before it."""
    trip = FileLoader().load("dubai_up01")
    day = trip.days[-1]                              # ... JBR Beach 11:00, Taxi 15:00, Flight 18:00
    assert [w.end for w in free_windows(day, trip.preferences)] == [dt.time(14, 30)]



def test_a_dinner_line_does_not_count_as_the_long_day_line():
    """B11: "later than your usual dinner" contains "late", so the missing long-day line was never added."""
    ctx = build_context(FileLoader().load("bali_up04"), 1)
    heads = ensure_covered(["Zest Ubud at 19:30: later than your usual dinner around 19:00"], ctx)
    assert any(h.startswith("Long day") for h in heads)


def test_a_time_the_model_made_up_is_dropped():
    """B01 (v7): "Leave Kelingking by 11:16" was the model's own (wrong) arithmetic."""
    from mb.agents.facts import tip_problems, unknown_times
    ctx = build_context(L.load("bali_up04"), 3)                  # Nusa Penida: Kelingking 10:00-12:00, lunch 12:30
    assert unknown_times("Leave Kelingking Beach Viewpoint by 11:16 to reach Penida Colada on time.", ctx) == ["11:16"]
    assert unknown_times("Lunch at Penida Colada is at 12:30.", ctx) == []
    assert any("time nothing gave it" in p for p in tip_problems("Leave Kelingking by 11:16 for lunch.", ctx))


def test_unverifiable_place_claims_are_caught():
    from mb.agents.facts import unverified_claims
    assert unverified_claims("Close by, great for rooftop dining") == ["rooftop"]
    assert unverified_claims("Great rooftop views", support="Sky Rooftop Bar") == []
    assert unverified_claims("A quiet garden near your stop") == []


def test_the_idea_count_in_the_notification_is_a_fact():
    from mb.agents.facts import fix_idea_count
    assert fix_idea_count("Explore Kelingking, plus 0 ideas for you.", 0) == "Explore Kelingking."
    assert fix_idea_count("Explore Ubud, plus 3 ideas for you.", 2) == "Explore Ubud, plus 2 ideas for you."
    assert fix_idea_count("Explore Ubud, plus 1 idea for you.", 1) == "Explore Ubud, plus 1 idea for you."
    assert fix_idea_count("Your day in Ubud.", 2) == "Your day in Ubud."


def test_a_dinner_line_with_the_planned_time_covers_the_pace_check():
    """B11 (v7): "Book dinner for 19:00 instead of 19:30" plus the code's own line said the same thing twice."""
    ctx = build_context(L.load("bali_up04"), 1)
    heads = ensure_covered(["Book dinner for 19:00 instead of 19:30 to keep to your usual time."], ctx)
    assert not any(h.startswith("Zest Ubud at 19:30") for h in heads)


def test_uneven_paths_are_a_heads_up_for_children_and_stair_avoiders():
    """B11, B14 (v8): the plan says Tegallalang has "uneven paths"; only "steep/stairs/steps" were checked."""
    kids = access_checks(build_context(L.load("bali_up02"), 1))
    assert any(l.startswith("Tegallalang Rice Terrace: uneven paths, keep the children") for l in kids)
    couple = access_checks(build_context(L.load("bali_up04"), 1))
    assert any(l.startswith("Tegallalang Rice Terrace: uneven paths, take it slowly") for l in couple)


# --- blind review, 3 Nov ------------------------------------------------------

def test_title_names_the_days_key_moments_not_day_n():
    from mb.agents.facts import key_title
    c = ctx_for("dubai_up01", 1)
    t = key_title(c)
    assert not t.startswith("Day ") and len(t) <= 40 and any(ch.isdigit() for ch in t)
    names = [a.name.split(" ")[0] for a in c.day.activities if a.kind in ("do", "eat")]
    assert any(n in t for n in names)


def test_free_day_title_says_free_day():
    from mb.agents.facts import key_title
    c = ctx_for("bali_up05", 2)          # 12 Dec: free day
    assert key_title(c).startswith("Free day")


def test_diet_clash_offers_a_checked_alternative():
    from mb.contracts import Candidate, Window
    c = ctx_for("dubai_up01", 1)
    alt = Candidate(id="places:v", provider="google_places", name="Rangoli Veg Kitchen",
                    window=Window(start=dt.time(14), end=dt.time(16)), types=["vegetarian_restaurant"], travel_min=8)
    lines = diet_checks(c.model_copy(update={"candidates": [alt]}))
    if lines:                                   # only when a planned meal isn't marked vegetarian
        assert "or try Rangoli Veg Kitchen (14:00-16:00, 8 min away)" in lines[0]


def test_judge_cannot_call_a_verified_place_invented():
    from mb.judge.judge import confirm_serious
    c = ctx_for("dubai_up01", 1)
    place = next(a.name for a in c.day.activities if a.kind == "do")
    from mb.agents.composer import safe_version
    brief = safe_version(c, "t")
    brief.heads_ups.append(f"Visit {place} early")
    ok, dropped = confirm_serious([f'"Visit {place} early" names a place not in the inputs: invented'], brief, c)
    assert ok == [] and "verified" in dropped[0]
