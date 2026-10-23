"""Agent 3, the Composer: merges out.summary and out.surprises into the output contract.

Code does the merge: timeline, weather, spend, surprises (re-checked against free windows),
actions and meta. The model only writes the final words: the notification (which now knows
how many ideas there are), de-duplicated heads-ups and the tip; and, on a fix request from
the judge, rewrites them to address the judge's reasons. If the model fails, the code-only
merge is used, so a Composer failure never blocks a brief.

safe_version() is the brief's fallback: the day's plan only, no surprises, no badge, built
entirely in code.
"""
from __future__ import annotations

import json

from mb.agents.facts import spend, timeline
from mb.config import AgentSpec
from mb.contracts import (ComposerText, Context, MorningBrief, Notification, Summary, Surprises,
                          Weather)
from mb.llm import LLM, LLMResult, load_prompt


def brief_id(ctx: Context) -> str:
    return f"brief_{ctx.ctx_id.removeprefix('ctx_')}"


def _window_slots(ctx: Context) -> set[str]:
    return {f"{w.start:%H:%M}-{w.end:%H:%M}" for w in ctx.free_windows}


def merge(ctx: Context, summary: Summary, surprises: Surprises | None,
          text: ComposerText | None = None) -> MorningBrief:
    """The code-only merge. With `text`, the Composer model's words replace the Summariser's."""
    s = summary.text
    planned = {a.name.lower() for a in ctx.day.activities} | {n.lower() for n in ctx.other_days}
    slots = _window_slots(ctx)
    items = [i for i in (surprises.items if surprises else [])
             if i.name.lower() not in planned and i.fits_slot in slots]     # no repeats, no clashes
    actions = (["add_to_day"] if items else []) + (["swap"] if items or s.suggested_swap else []) + ["ask"]
    return MorningBrief(
        brief_id=brief_id(ctx), trip_id=ctx.trip_id, date=ctx.date,
        send_at_local=f"{ctx.send_at_local:%H:%M}" if ctx.send_at_local else "",
        timezone=ctx.timezone or "",
        notification=Notification(title=(text or s).notification_title, body=(text or s).notification_body),
        timeline=summary.timeline,
        weather=Weather(summary=s.weather_summary, clash=s.weather_clash, suggested_swap=s.suggested_swap),
        heads_ups=(text.heads_ups if text else s.heads_ups),
        spend_estimate=summary.spend, surprises=items,
        local_tip=(text.local_tip if text and text.local_tip else s.local_tip),
        actions=actions)


def composer_input(brief: MorningBrief, fix_reasons: list[str] | None) -> str:
    return json.dumps({
        "draft": {"notification_title": brief.notification.title, "notification_body": brief.notification.body,
                  "heads_ups": brief.heads_ups, "local_tip": brief.local_tip,
                  "weather_clash": brief.weather.clash, "suggested_swap": brief.weather.suggested_swap},
        "surprises": [{"name": i.name, "slot": i.fits_slot, "why_you": i.why_you} for i in brief.surprises],
        "timeline": [f"{t.time} {t.name}{' (booked)' if t.booked else ''}" for t in brief.timeline],
        "fix_reasons": fix_reasons or [],
    }, indent=1, ensure_ascii=False)


async def run_composer(ctx: Context, summary: Summary, surprises: Surprises | None, spec: AgentSpec,
                       llm: LLM, run_id: str,
                       fix_reasons: list[str] | None = None) -> tuple[MorningBrief, LLMResult]:
    draft = merge(ctx, summary, surprises)
    res = await llm.call(model=spec.model, system=load_prompt(spec.prompt_version),
                         user=composer_input(draft, fix_reasons), response_model=ComposerText,
                         run_id=run_id, agent_id=spec.id, temperature=spec.temperature, timeout_s=spec.timeout_s)
    return merge(ctx, summary, surprises, res.output), res


def safe_version(ctx: Context, reason: str) -> MorningBrief:
    """Fallback: the day's plan only, no surprises, no badge. Code only, so it cannot fail."""
    must = [f"{a.name}: {'pickup' if 'pickup' in a.note.lower() else 'timed entry'} at {a.timed_at:%H:%M}"
            for a in ctx.day.activities if a.timed_at]
    title = f"Day {ctx.day.index + 1} in {ctx.region}" if ctx.region else f"Day {ctx.day.index + 1}"
    brief = MorningBrief(
        brief_id=brief_id(ctx), trip_id=ctx.trip_id, date=ctx.date,
        send_at_local=f"{ctx.send_at_local:%H:%M}" if ctx.send_at_local else "", timezone=ctx.timezone or "",
        notification=Notification(title=title[:40], body="Your plan for today"),
        timeline=timeline(ctx), heads_ups=must[:4], spend_estimate=spend(ctx, _fx()), actions=["ask"])
    brief.confidence.flags.append(f"safe_version: {reason}")
    brief.meta.judge_verdict = "fallback"
    return brief


def _fx():
    from mb.agents.facts import load_fx
    return load_fx()
