import asyncio
import datetime as dt

import pytest

import mb.pipeline as pipeline
from mb.agents.composer import merge, safe_version
from mb.config import load_config
from mb.context import build_context
from mb.contracts import (ComposerText, Summary, SummaryText, SurpriseItem, Surprises)
from mb.agents.facts import spend, timeline, load_fx
from mb.llm import LLMResult
from mb.sources.itinerary import FileLoader


@pytest.fixture(scope="module")
def ctx():
    return build_context(FileLoader().load("dubai_up01"), 1)


def _summary(ctx):
    text = SummaryText(notification_title="Day 2 in Dubai", notification_body="Old Dubai and the museum",
                       heads_ups=["Be at the Museum of the Future for your 15:00 slot."],
                       weather_summary="Sunny, 31°C", local_tip="Try the karak tea")
    return Summary(agent_id="a1", text=text, timeline=timeline(ctx), spend=spend(ctx, load_fx()))


def _surprises(ctx, name="Arabian Tea House", slot=None):
    w = ctx.free_windows[0]
    return Surprises(agent_id="a2", items=[SurpriseItem(
        name=name, why_you="Veg menu", fits_slot=slot or f"{w.start:%H:%M}-{w.end:%H:%M}", place_id="p1")])


def test_merge_keeps_valid_surprises_and_sets_actions(ctx):
    b = merge(ctx, _summary(ctx), _surprises(ctx))
    assert [s.name for s in b.surprises] == ["Arabian Tea House"]
    assert b.actions == ["add_to_day", "swap", "ask"]
    assert b.spend_estimate.local == "about AED 210 per person"


def test_merge_drops_repeats_and_surprises_outside_free_windows(ctx):
    assert merge(ctx, _summary(ctx), _surprises(ctx, name="Burj Khalifa At the Top")).surprises == []
    assert merge(ctx, _summary(ctx), _surprises(ctx, slot="03:00-04:00")).surprises == []


def test_every_contract_field_is_present(ctx):
    data = merge(ctx, _summary(ctx), None).model_dump(mode="json")
    for key in ("brief_id", "notification", "timeline", "weather", "heads_ups", "spend_estimate",
                "surprises", "local_tip", "confidence", "actions", "meta"):
        assert key in data


def test_safe_version_is_plan_only_with_no_badge(ctx):
    b = safe_version(ctx, "test")
    assert b.surprises == [] and b.confidence.badge is None and b.meta.judge_verdict == "fallback"
    assert len(b.timeline) == 5 and any("15:00" in h for h in b.heads_ups)


class FakeLLM:
    """Answers each agent by its response model; can be told to hang or fail."""
    def __init__(self, hang=(), fail=()):
        self.hang, self.fail = set(hang), set(fail)

    async def call(self, *, response_model, agent_id, **kw):
        if agent_id in self.hang:
            await asyncio.sleep(60)
        if agent_id in self.fail:
            raise RuntimeError("boom")
        name = response_model.__name__
        if name == "SummaryText":
            out = SummaryText(notification_title="Day 2 in Dubai", notification_body="Old Dubai",
                              weather_summary="Sunny", local_tip="Tip")
        elif name == "ComposerText":
            out = ComposerText(notification_title="Day 2 in Dubai", notification_body="Old Dubai, plus 0 ideas",
                               heads_ups=["One"], local_tip="Tip 2")
        else:
            from mb.contracts import SurprisePicks
            out = SurprisePicks()
        return LLMResult(out, "fake-model", 1, 1, 0.001, 5)


def _cfg_with_timeouts(seconds):
    cfg = load_config()
    agents = {k: v.model_copy(update={"timeout_s": seconds}) for k, v in cfg.agents.items()}
    return cfg.model_copy(update={"agents": agents})


async def test_happy_path_uses_the_composer_words(ctx):
    run = await pipeline.run_brief(ctx, load_config(), FakeLLM(), run_id="t")
    assert run.brief.notification.body == "Old Dubai, plus 0 ideas" and run.brief.heads_ups[0] == "One"
    assert any("Museum of the Future" in h for h in run.brief.heads_ups)   # dropped by the model, added by code
    assert set(run.brief.meta.models) >= {"mb.summariser.v1", "mb.composer.v1"}
    assert run.brief.meta.tokens["mb.summariser.v1"] == 2


async def test_surprise_finder_timeout_still_gives_a_brief(ctx):
    from mb.contracts import Candidate
    cand = Candidate(id="places:p1", provider="google_places", name="Tea House", window=ctx.free_windows[0])
    ctx2 = ctx.model_copy(update={"candidates": [cand]})          # so Agent 2 really calls the model
    run = await pipeline.run_brief(ctx2, _cfg_with_timeouts(0.3), FakeLLM(hang={"mb.surprise_finder.v1"}),
                                   run_id="t")
    assert run.brief.timeline and run.brief.meta.judge_verdict != "fallback" and run.brief.surprises == []
    assert any("no surprises" in e for e in run.events)


async def test_composer_failure_falls_back_to_code_merge(ctx):
    run = await pipeline.run_brief(ctx, load_config(), FakeLLM(fail={"mb.composer.v1"}), run_id="t")
    assert run.brief.notification.body == "Old Dubai"          # the Summariser's words
    assert any("code-only merge" in e for e in run.events)


async def test_summariser_failure_gives_the_safe_version(ctx):
    run = await pipeline.run_brief(ctx, load_config(), FakeLLM(fail={"mb.summariser.v1"}), run_id="t")
    assert run.brief.meta.judge_verdict == "fallback" and run.brief.surprises == []


async def test_time_budget_exhausted_gives_the_safe_version(ctx):
    run = await pipeline.run_brief(ctx, load_config(), FakeLLM(hang={"mb.summariser.v1"}), run_id="t",
                                   budget_s=1.5)
    assert run.brief.meta.judge_verdict == "fallback"


def test_agents_are_found_by_output_in_the_registry():
    cfg = load_config()
    assert pipeline.agent_for(cfg, "out.draft").id == "mb.composer.v1"
