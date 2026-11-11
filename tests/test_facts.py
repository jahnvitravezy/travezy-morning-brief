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
    assert line == "Long day: you finish around 21:15 at Al Ustad Special Kebab, later than your usual 20:00 return; leave Al Ustad Special Kebab earlier to be back by 20:00"
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
                    window=Window(start=dt.time(18), end=dt.time(20)), types=["vegetarian_restaurant"], travel_min=8)
    lines = diet_checks(c.model_copy(update={"candidates": [alt]}))
    assert lines, "this day has a planned meal not marked vegetarian"
    assert "or try Rangoli Veg Kitchen (18:00-20:00, 8 min away)" in lines[0]


def _with_prefs(c, **update):
    p = c.preferences
    parts = {k: v for k, v in update.items() if k in ("diet", "pace", "party")}
    return c.model_copy(update={"preferences": p.model_copy(update=parts | {k: v for k, v in update.items() if k == "who"})})


def test_jain_travellers_get_a_vegetarian_alternative_with_a_jain_note():
    from mb.contracts import Candidate, Diet, Window
    c = ctx_for("dubai_up02", 1)
    c = _with_prefs(c, diet=Diet(summary="Jain", restrictions=["jain", "vegetarian"]))
    veg = Candidate(id="places:v", provider="google_places", name="Rangoli Veg Kitchen",
                    window=Window(start=dt.time(18), end=dt.time(20)), types=["vegetarian_restaurant"])
    line = diet_checks(c.model_copy(update={"candidates": [veg]}))[0]
    assert "or try Rangoli Veg Kitchen" in line and "ask for Jain preparation" in line


def test_walking_limit_gets_a_caution():
    from mb.agents.facts import walk_checks
    c = ctx_for("dubai_up01", 1)
    c = _with_prefs(c, pace=c.preferences.pace.model_copy(update={"max_walk_km": 2}))
    walky = [a for a in c.day.activities if any(w in f"{a.name} {a.note}".lower() for w in ("walk", "souk", "old town", "heritage", "mall"))]
    lines = walk_checks(c)
    # one line for all of them, not one per stop (scoring sheet, 10 Nov: B11 repeated it)
    assert len(lines) == 1 and "2 km limit" in lines[0] and all(a.name in lines[0] for a in walky)


def test_a_beach_walk_gets_no_taxi_advice():
    from mb.agents.facts import walk_checks
    from mb.contracts import Activity
    c = ctx_for("dubai_up01", 1)
    c = _with_prefs(c, pace=c.preferences.pace.model_copy(update={"max_walk_km": 2}))
    walk = Activity(id="w", kind="do", start=dt.time(17), name="JBR Beach walk", note="Stroll on the beach.")
    c = c.model_copy(update={"day": c.day.model_copy(update={"activities": [walk]})})
    [line] = walk_checks(c)
    assert "taxi" not in line and "turn back" in line


def test_monkeys_caution_for_children_and_dune_bashing_for_older_travellers():
    from mb.agents.facts import safety_checks
    from mb.contracts import Activity, Party
    c = ctx_for("bali_up02", 1)
    monkey = Activity(id="m", kind="do", start=dt.time(16), name="Sacred Monkey Forest Sanctuary")
    kids = _with_prefs(c, party=Party(adults=2, children_ages=[6, 10]))
    kids = kids.model_copy(update={"day": kids.day.model_copy(update={"activities": [monkey]})})
    assert "keep the children (6 and 10) close" in safety_checks(kids)[0]
    dunes = Activity(id="d", kind="do", start=dt.time(15), name="Desert safari", note="Dune bashing and camp dinner")
    older = _with_prefs(c, who="Couple in their 60s", party=Party(adults=2))
    older = older.model_copy(update={"day": older.day.model_copy(update={"activities": [dunes]})})
    assert "dune bashing is a rough" in safety_checks(older)[0]
    young = _with_prefs(c, who="Couple in their 20s", party=Party(adults=2))
    young = young.model_copy(update={"day": young.day.model_copy(update={"activities": [dunes]})})
    assert safety_checks(young) == []


def test_weather_clash_with_no_dry_slot_offers_an_indoor_idea():
    from mb.contracts import Candidate, Window
    c = ctx_for("bali_up04", 1, rain=range(6, 23))
    museum = Candidate(id="places:m", provider="google_places", name="ARMA Museum",
                       window=Window(start=dt.time(6), end=dt.time(23)), types=["museum"])
    swap = suggested_swap(c.model_copy(update={"candidates": [museum]}))
    assert swap and "ARMA Museum" in swap and swap.startswith("Rain up to") and "weather turns" not in swap


def test_indoor_swap_must_be_open_during_the_clash_and_not_already_planned():
    """Scoring sheet, 10 Nov: B12 got a swap starting after the beach slot ended; B14 a planned place."""
    from mb.agents.facts import weather_clashes
    from mb.contracts import Candidate, Window
    c = ctx_for("bali_up04", 1, rain=range(6, 23))
    name = weather_clashes(c)[0].split(" ")[0]
    a = next(x for x in c.day.activities if x.name.startswith(name))
    end = a.end or a.start
    late = Candidate(id="places:l", provider="google_places", name="Late Museum",
                     window=Window(start=end, end=dt.time(23, 30)), types=["museum"])
    planned = Candidate(id="places:p", provider="google_places", name=c.day.activities[-1].name,
                        window=Window(start=dt.time(6), end=dt.time(23)), types=["museum"])
    assert suggested_swap(c.model_copy(update={"candidates": [late, planned]})) is None


def test_judge_cannot_call_a_verified_place_invented():
    from mb.judge.judge import confirm_serious
    c = ctx_for("dubai_up01", 1)
    place = next(a.name for a in c.day.activities if a.kind == "do")
    from mb.agents.composer import safe_version
    brief = safe_version(c, "t")
    brief.heads_ups.append(f"Visit {place} early")
    ok, dropped = confirm_serious([f'"Visit {place} early" names a place not in the inputs: invented'], brief, c)
    assert ok == [] and "verified" in dropped[0]



def test_model_diet_line_is_replaced_when_code_has_an_alternative():
    from mb.contracts import Candidate, Window
    from mb.agents.facts import ensure_covered
    c = ctx_for("dubai_up01", 1)
    alt = Candidate(id="places:v", provider="google_places", name="Rangoli Veg Kitchen",
                    window=Window(start=dt.time(18), end=dt.time(20)), types=["vegetarian_restaurant"])
    c = c.model_copy(update={"candidates": [alt]})
    meal = next(a.name for a in c.day.activities if a.kind == "eat" and "veg" not in f"{a.name} {a.note}".lower())
    out = ensure_covered([f"You're vegetarian: {meal}, ask for veg options first."], c)
    veg = [h for h in out if "veg" in h.lower()]
    assert len(veg) == 1 and "Rangoli Veg Kitchen" in veg[0]


def test_monkey_caution_is_added_even_when_another_line_names_the_forest():
    from mb.agents.facts import ensure_covered
    from mb.contracts import Activity, Party
    c = ctx_for("bali_up02", 1)
    monkey = Activity(id="m", kind="do", start=dt.time(16), end=dt.time(17, 30), name="Sacred Monkey Forest Sanctuary")
    c = _with_prefs(c, party=Party(adults=2, children_ages=[6, 10]))
    c = c.model_copy(update={"day": c.day.model_copy(update={"activities": [monkey]})})
    out = ensure_covered(["Leave Sacred Monkey Forest Sanctuary by 17:00: it closes before your planned finish."], c)
    assert any("keep the children (6 and 10) close" in h for h in out)



def test_models_own_monkey_caution_is_not_doubled():
    from mb.agents.facts import ensure_covered
    from mb.contracts import Activity
    c = ctx_for("bali_up01", 1)
    monkey = Activity(id="m", kind="do", start=dt.time(16), end=dt.time(17, 30), name="Sacred Monkey Forest Sanctuary")
    c = c.model_copy(update={"day": c.day.model_copy(update={"activities": [monkey]})})
    out = ensure_covered(["Monkeys grab bags and food; keep everyone close, hold bags tightly."], c)
    assert sum("monkey" in h.lower() for h in out) == 1



def test_temperature_in_a_tip_is_not_a_place():
    from mb.agents.facts import tip_problems
    c = ctx_for("dubai_up01", 1)
    assert not [p for p in tip_problems("Carry water and sun cover: it reaches 31°C today.", c) if "names" in p]


def test_code_dinner_line_replaces_the_models_vaguer_one():
    from mb.agents.facts import ensure_covered, pace_checks
    for trip, day in [("bali_up04", d) for d in range(5)] + [("dubai_up04", d) for d in range(4)]:
        c = ctx_for(trip, day)
        if any("usual dinner" in l for l in pace_checks(c)):
            out = ensure_covered(["Dinner at the restaurant is later than your usual time."], c)
            assert sum("dinner" in h.lower() for h in out) == 1
            return
    raise AssertionError("no test day with a late dinner")


# ---------- Scoring sheet, 10 Nov (handover points) ----------
def _forest_flag(c):
    from mb.contracts import Flag
    forest = next(a for a in c.day.activities if "Monkey" in a.name)
    return c.model_copy(update={"flags": [Flag(code="closes_during_visit", activity_id=forest.id,
                                               message=f"{forest.name}: closes at 17:00, before the planned end at 17:30")]})


def test_closing_time_comes_only_from_the_flag():
    """B19: the model's "leave by 16:00 due to early closing" stood beside a 17:00 close."""
    c = _forest_flag(ctx_for("bali_up01", 1))
    out = ensure_covered(["Leave Sacred Monkey Forest by 16:00 due to early closing."], c)
    assert not any("16:00" in h for h in out)
    assert any(h.startswith("Leave Sacred Monkey Forest Sanctuary by 17:00") for h in out)


def test_heads_ups_are_capped_without_dropping_code_lines():
    """B11 had 9 heads-ups. Code's must-say lines stay; the model's extras fill up to the cap, no repeats."""
    from mb.agents.facts import MAX_HEADS_UPS, must_cover
    c = _forest_flag(ctx_for("bali_up01", 1))
    must = len(must_cover(c))
    model = ["Pack a light jacket for the evening.", "Pack a light jacket for the evening, it gets cool.",
             "Bring sunscreen.", "Try the local coffee.", "Keep small change for parking.", "Charge your phone."]
    out = ensure_covered(model, c)
    assert len(out) == max(MAX_HEADS_UPS, must)
    assert sum("jacket" in h for h in out) <= 1
    assert any(h.startswith("Leave Sacred Monkey Forest Sanctuary") for h in out)


def test_camp_dinner_counts_as_a_meal_for_diet_checks():
    """B07, B10: the desert safari's camp dinner got no vegetarian check."""
    from mb.contracts import Activity, Diet
    c = _with_prefs(ctx_for("dubai_up01", 1), diet=Diet(summary="Vegetarian", restrictions=["vegetarian"]))
    safari = Activity(id="s", kind="do", start=dt.time(15), end=dt.time(21), name="Desert Safari",
                      note="Dune drive and camp dinner with BBQ.")
    c = c.model_copy(update={"day": c.day.model_copy(update={"activities": [safari]}), "candidates": []})
    assert "Desert Safari" in diet_checks(c)[0]


def test_diet_alternative_is_never_a_planned_place():
    """B14: "or try Zest" on a day that already had Zest Ubud."""
    from mb.contracts import Activity, Candidate, Diet, Window
    c = _with_prefs(ctx_for("dubai_up02", 1), diet=Diet(summary="Vegetarian", restrictions=["vegetarian"]))
    meal = Activity(id="e", kind="eat", start=dt.time(19, 30), name="Zest Ubud")
    zest = Candidate(id="places:z", provider="google_places", name="Zest",
                     window=Window(start=dt.time(17), end=dt.time(19)), types=["vegetarian_restaurant"])
    c = c.model_copy(update={"day": c.day.model_copy(update={"activities": [meal]}), "candidates": [zest]})
    assert "or try" not in diet_checks(c)[0]


def test_an_idea_cannot_repeat_the_diet_alternative_or_a_second_meal_in_its_window():
    """B02: the idea was the diet alternative again; B04: two lunch places for the same afternoon."""
    from mb.agents.facts import repeats_code_place
    from mb.contracts import Activity, Candidate, Diet, Window
    c = _with_prefs(ctx_for("dubai_up02", 1), diet=Diet(summary="Vegetarian", restrictions=["vegetarian"]))
    meal = Activity(id="e", kind="eat", start=dt.time(12), name="Ravi Restaurant")
    eco = Candidate(id="places:e", provider="google_places", name="Eco Mind Cafe",
                    window=Window(start=dt.time(13, 30), end=dt.time(16)), types=["vegetarian_restaurant", "cafe"])
    moon = Candidate(id="places:w", provider="google_places", name="Wild & The Moon",
                     window=Window(start=dt.time(13, 45), end=dt.time(16, 30)), types=["restaurant"])
    gallery = Candidate(id="places:g", provider="google_places", name="Art Gallery",
                        window=Window(start=dt.time(14), end=dt.time(16)), types=["art_gallery"])
    c = c.model_copy(update={"day": c.day.model_copy(update={"activities": [meal]}), "candidates": [eco, moon, gallery]})
    assert repeats_code_place("Eco Mind Cafe", "14:00-15:00", c)
    assert repeats_code_place("Wild & The Moon", "13:45-15:00", c)
    assert not repeats_code_place("Art Gallery", "14:00-15:00", c)


def test_diet_alternative_matches_the_meal_it_replaces():
    """Travezy, 11 Nov: a camp dinner got a breakfast cafe (08:00-09:18) as its alternative."""
    from mb.contracts import Activity, Candidate, Diet, Window
    c = _with_prefs(ctx_for("dubai_up01", 1), diet=Diet(summary="Vegetarian", restrictions=["vegetarian"]))
    safari = Activity(id="s", kind="do", start=dt.time(15), end=dt.time(21), name="Evening Desert Safari",
                      note="Dune drive and camp dinner with BBQ.")
    cafe = Candidate(id="places:b", provider="google_places", name="Morning Veg Cafe",
                     window=Window(start=dt.time(8), end=dt.time(9, 15)), types=["vegetarian_restaurant"])
    late = Candidate(id="places:d", provider="google_places", name="Evening Veg Kitchen",
                     window=Window(start=dt.time(18), end=dt.time(20)), types=["vegetarian_restaurant"])
    day = c.day.model_copy(update={"activities": [safari]})
    assert "or try" not in diet_checks(c.model_copy(update={"day": day, "candidates": [cafe]}))[0]
    assert "or try Evening Veg Kitchen" in diet_checks(c.model_copy(update={"day": day, "candidates": [cafe, late]}))[0]


# ---------- Model switch, 11 Nov: what cost gpt-5-mini and gpt-4o a brief ----------
def test_body_is_cut_to_its_limit_at_a_clause():
    from mb.agents.facts import fit_body
    long = "Rice terraces this morning, lunch at Clear Cafe, then the Monkey Forest before a late dinner in Ubud"
    out = fit_body(long)
    assert len(long) > 90 and len(out) <= 90 and long.startswith(out) and not out.endswith((",", " "))
    assert fit_body("Old Dubai and the souks") == "Old Dubai and the souks"


def test_a_model_cannot_say_the_diet_is_catered_for():
    from mb.agents.facts import diet_claim
    assert diet_claim("Penida Colada: vegetarian options available.")
    assert diet_claim("A vegan-friendly cafe by the beach.")
    assert not diet_claim("Vegetarian food: check Penida Colada has vegetarian dishes for you.")


def test_judge_cannot_call_a_repeated_code_line_a_contradiction():
    from mb.agents.composer import safe_version
    from mb.judge.judge import confirm_serious
    c = _forest_flag(ctx_for("bali_up01", 1))
    brief = safe_version(c, "t")
    brief.notification.body = "Rice terraces, then leave Monkey Forest by 17:00"
    ok, dropped = confirm_serious(["The brief states 'leave Monkey Forest by 17:00', but the planned visit ends at 17:30."], brief, c)
    assert not ok and dropped
    brief.notification.body = "Rice terraces, then leave Monkey Forest by 16:00"
    ok, _ = confirm_serious(["The brief states 'leave Monkey Forest by 16:00', but it closes at 17:00."], brief, c)
    assert ok


def test_idea_count_leaves_no_stray_dash():
    from mb.agents.facts import fix_idea_count
    assert fix_idea_count("Dubai Mall 10:00; Desert Safari 15:00 — plus 1 idea for you", 1) == "Dubai Mall 10:00; Desert Safari 15:00, plus 1 idea for you"
    assert fix_idea_count("Rain 13:00-17:00, dinner tonight +1 ideas for you", 1) == "Rain 13:00-17:00, dinner tonight, plus 1 idea for you"
    assert fix_idea_count("Dubai Mall, then the safari — plus 2 ideas for you", 0) == "Dubai Mall, then the safari"


def test_details_nothing_confirms_are_claims_unless_the_plan_says_them():
    from mb.agents.facts import unverified_claims
    assert unverified_claims("Photograph textured murals in the courtyard mid-morning.")
    assert unverified_claims("Book rooftop dinner for the evening.")
    assert not unverified_claims("Bring a light layer for the evening.")
    assert not unverified_claims("Dinner at the rooftop.", support="Level 43 Rooftop Lounge")


def test_same_timed_thing_is_said_once():
    from mb.agents.facts import _same_moment
    assert _same_moment("Evening Desert Safari: pickup at 15:00.", "Pickup at 15:00 from your hotel — be ready early.")
    assert not _same_moment("Evening Desert Safari: pickup at 15:00.", "Be at Burj Khalifa for 17:00.")
