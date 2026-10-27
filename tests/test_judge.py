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
    assert run.brief.surprises == [] and run.brief.confidence.badge == "red"


async def test_judge_failure_never_blocks(ctx):
    run = await pipeline.run_brief(ctx, CFG, FakeLLM(fail={"mb.judge.v1"}), run_id="t")
    assert run.brief.meta.judge_verdict == "pass" and run.brief.confidence.badge is None
    assert any("judge unavailable" in f for f in run.brief.confidence.flags)
