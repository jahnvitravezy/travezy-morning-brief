"""Agent 4, the judge: weighting, badge, decisions, the fix loop and failure paths. No network."""
import datetime as dt

import pytest

import mb.pipeline as pipeline
from mb.config import load_config
from mb.context import build_context
from mb.contracts import CriterionScore, JudgeOutput
from mb.judge.judge import badge_for, decide, judge_input, validate_scores, weighted
from mb.llm import load_prompt
from mb.sources.itinerary import FileLoader
from tests.test_pipeline import FakeLLM

CFG = load_config()
RUBRIC = CFG.rubrics["rubric.morning_brief.v1"]
CRIT = [c.id for c in RUBRIC.criteria]


@pytest.fixture(scope="module")
def ctx():
    c = build_context(FileLoader().load("dubai_up01"), 1)
    return c.model_copy(update={"send_at_local": dt.time(7, 30), "timezone": "Asia/Dubai"})


def scores(**over):
    return [CriterionScore(id=c, score=over.get(c, 90), reason="r") for c in CRIT]


def test_weighted_score_uses_rubric_weights():
    # accuracy .35, relevance .25, feasibility .20, format .10, tone .10
    assert weighted(scores(accuracy=60), RUBRIC) == round(60 * .35 + 90 * .65)
    assert weighted(scores(), RUBRIC) == 90


def test_badge_thresholds_come_from_the_rubric():
    assert badge_for(85, RUBRIC) == "green" and badge_for(84, RUBRIC) == "amber"
    assert badge_for(60, RUBRIC) == "amber" and badge_for(59, RUBRIC) == "red"


def test_decisions():
    assert decide([], 90, RUBRIC, attempts_left=1) == "pass"
    assert decide([], 70, RUBRIC, attempts_left=1) == "fix"
    assert decide([], 70, RUBRIC, attempts_left=0) == "pass"          # amber after the fix: send, amber badge
    assert decide([], 40, RUBRIC, attempts_left=0) == "fallback"
    assert decide(["title too long"], 95, RUBRIC, attempts_left=1) == "fix"
    assert decide(["title too long"], 95, RUBRIC, attempts_left=0) == "fallback"
    assert decide([], None, RUBRIC, attempts_left=1) == "pass"        # judge down, rules fine


def test_judge_must_score_every_criterion_exactly_once():
    validate_scores(JudgeOutput(scores=scores()), RUBRIC)
    with pytest.raises(ValueError):
        validate_scores(JudgeOutput(scores=scores()[:-1]), RUBRIC)


def test_judge_sees_inputs_brief_and_rubric_but_not_its_own_badge(ctx):
    from mb.agents.composer import safe_version
    text = judge_input(ctx, safe_version(ctx, "x"), RUBRIC)
    assert '"rubric"' in text and '"inputs"' in text and '"brief"' in text
    assert "confidence" not in text and "judge_verdict" not in text
    assert '"facts_from_code"' in text and '"free_day"' in text      # judge-v2: code facts are given as facts
    assert load_prompt("judge-v1") and "PLAN IS FIXED" in load_prompt("judge-v2")


async def test_green_brief_passes_with_badge(ctx):
    run = await pipeline.run_brief(ctx, CFG, FakeLLM(judge_scores=[95]), run_id="t")
    assert run.brief.meta.judge_verdict == "pass"
    assert run.brief.confidence.badge == "green" and run.brief.confidence.score == 95
    assert "mb.judge.v1" in run.brief.meta.models


async def test_amber_brief_is_fixed_once_and_kept_when_better(ctx):
    llm = FakeLLM(judge_scores=[70, 90])
    run = await pipeline.run_brief(ctx, CFG, llm, run_id="t")
    assert llm.calls.count("mb.composer.v1") == 2 and llm.calls.count("mb.judge.v1") == 2
    assert run.brief.meta.judge_verdict == "fix" and run.brief.confidence.score == 90


async def test_fix_that_is_worse_is_thrown_away(ctx):
    run = await pipeline.run_brief(ctx, CFG, FakeLLM(judge_scores=[70, 50]), run_id="t")
    assert run.brief.confidence.score == 70 and run.brief.confidence.badge == "amber"
    assert run.brief.meta.judge_verdict == "pass"


async def test_red_after_fix_falls_back_to_safe_version(ctx):
    run = await pipeline.run_brief(ctx, CFG, FakeLLM(judge_scores=[40, 45]), run_id="t")
    assert run.brief.meta.judge_verdict == "fallback"
    assert run.brief.surprises == [] and run.brief.confidence.badge is None      # the safe version carries no badge


async def test_judge_failure_never_blocks(ctx):
    run = await pipeline.run_brief(ctx, CFG, FakeLLM(fail={"mb.judge.v1"}), run_id="t")
    assert run.brief.meta.judge_verdict == "pass" and run.brief.confidence.badge is None
    assert any("judge unavailable" in f for f in run.brief.confidence.flags)


def test_a_serious_error_fails_the_brief_whatever_the_score():
    from mb.judge.judge import recommendation
    assert decide([], 95, RUBRIC, attempts_left=1, serious=["wrong opening time"]) == "fix"
    assert decide([], 95, RUBRIC, attempts_left=0, serious=["wrong opening time"]) == "fallback"
    assert recommendation([], ["diet mistake"], 95, RUBRIC) == "dont_send"
    assert recommendation([], [], 90, RUBRIC) == "send"
    assert recommendation([], [], 70, RUBRIC) == "fix_first"
    assert recommendation([], [], 40, RUBRIC) == "dont_send"
    assert "WRONG OR UNSAFE" in load_prompt("judge-v3")


async def test_serious_error_is_fixed_and_checked_again(ctx):
    class Serious(FakeLLM):
        def __init__(self):
            super().__init__(judge_scores=[95])
            self.judged = 0

        async def call(self, *, response_model, agent_id, **kw):
            res = await super().call(response_model=response_model, agent_id=agent_id, **kw)
            if response_model.__name__ == "JudgeOutput":
                self.judged += 1
                if self.judged == 1:
                    res.output.serious = ['Body says "Old Dubai, plus 0 ideas" but gives the wrong opening time']
            return res

    llm = Serious()
    run = await pipeline.run_brief(ctx, CFG, llm, run_id="t")
    assert llm.judged == 2                                     # the fixed brief was judged again
    assert run.brief.meta.judge_verdict == "fix" and run.brief.confidence.badge == "green"


async def test_serious_error_that_survives_the_fix_sends_the_safe_version(ctx):
    class AlwaysSerious(FakeLLM):
        async def call(self, *, response_model, agent_id, **kw):
            res = await super().call(response_model=response_model, agent_id=agent_id, **kw)
            if response_model.__name__ == "JudgeOutput":
                res.output.serious = ['"Old Dubai, plus 0 ideas" sends a Jain family to a kebab house with no warning']
            return res

    run = await pipeline.run_brief(ctx, CFG, AlwaysSerious(judge_scores=[95]), run_id="t")
    assert run.brief.meta.judge_verdict == "fallback" and run.brief.surprises == []
    assert any(f.startswith("serious:") for f in run.brief.confidence.flags)


def test_every_brief_carries_its_send_time_in_utc(ctx):
    from mb.agents.composer import safe_version
    assert safe_version(ctx, "x").send_at_utc == "2026-11-21T03:30:00Z"     # 07:30 in Dubai


def test_second_rubric_changes_the_judge_without_code():
    from mb.contracts import MorningBrief, Notification
    from mb.evals.judge_run import with_rubric
    from mb.judge.rules import run_rule_checks
    cfg = with_rubric(CFG, "rubric.morning_brief_launch.v1")
    launch = cfg.rubrics["rubric.morning_brief_launch.v1"]
    assert cfg.agents["mb.judge.v1"].rubric == launch.id and launch.judge_model == cfg.agents["mb.judge.v1"].model
    assert badge_for(88, RUBRIC) == "green" and badge_for(88, launch) == "amber"         # stricter cut-offs
    assert weighted(scores(accuracy=50), launch) < weighted(scores(accuracy=50), RUBRIC)   # accuracy weighs more
    c = build_context(FileLoader().load("dubai_up01"), 1)
    b = MorningBrief(brief_id="b", trip_id="t", date=c.date, send_at_local="07:30",
                     notification=Notification(title="Day 2 in Dubai: the old town, museum", body="x"),
                     timeline=[{"time": "09:00", "name": "x"}])
    assert run_rule_checks(b, c, RUBRIC) == [] and run_rule_checks(b, c, launch)        # 36 characters: passes at 40, fails at 35



def test_unconfirmed_serious_errors_do_not_fail_a_brief(ctx):
    """The live false alarm of 29 Oct: no quote, and about hours the venue hasn't published."""
    from mb.agents.composer import safe_version
    from mb.judge.judge import confirm_serious
    brief = safe_version(ctx, "test")
    brief.notification.body = "Old Dubai and the souks"
    real = '"Old Dubai and the souks" sends a Jain family to a kebab house: diet is Jain'
    no_quote = "Heads-up 4 says to check the Gold and Spice Souks' hours, which could confuse"
    unpublished = '"Old Dubai and the souks" does not say the souks\' hours are not published'
    made_up = '"Visit the Burj at dawn" is not in the inputs'
    ok, dropped = confirm_serious([real, no_quote, unpublished, made_up], brief, ctx)
    assert ok == [real]
    assert len(dropped) == 3 and all("[not serious:" in d for d in dropped)
