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

from mb.agents.facts import (_covers, _near_duplicate, _same_moment, repeats_code_place, diet_claim, fit_body, plan_body, plan_text, unverified_claims, key_title, ensure_covered, fix_idea_count, safe_tip, spend, suggested_swap, timeline,
                             tip_problems, unknown_times,
                             weather_clashes, weather_summary)
from mb.config import AgentSpec
from mb.contracts import (ComposerText, Context, MorningBrief, Notification, Spend, Summary, Surprises,
                          Weather)
from mb.llm import LLM, LLMResult, load_prompt


def send_at_utc(ctx: Context) -> str:
    """The local send time where the traveller wakes up, as one UTC instant for the backend."""
    if not (ctx.send_at_local and ctx.timezone):
        return ""
    import datetime as _dt
    from zoneinfo import ZoneInfo
    local = _dt.datetime.combine(ctx.date, ctx.send_at_local, tzinfo=ZoneInfo(ctx.timezone))
    return local.astimezone(_dt.timezone.utc).isoformat().replace("+00:00", "Z")


def brief_id(ctx: Context) -> str:
    return f"brief_{ctx.ctx_id.removeprefix('ctx_')}"


def _window_slots(ctx: Context) -> set[str]:
    return {f"{w.start:%H:%M}-{w.end:%H:%M}" for w in ctx.free_windows}


def _inside(slot: str, ctx: Context) -> bool:
    """The surprise's slot lies inside one of the day's free windows."""
    try:
        s, e = slot.split("-")
    except ValueError:
        return False
    return any(f"{w.start:%H:%M}" <= s < e <= f"{w.end:%H:%M}" for w in ctx.free_windows)


def merge(ctx: Context, summary: Summary, surprises: Surprises | None,
          text: ComposerText | None = None) -> MorningBrief:
    """The code-only merge. With `text`, the Composer model's words replace the Summariser's."""
    s = summary.text
    planned = {a.name.lower() for a in ctx.day.activities} | {n.lower() for n in ctx.other_days}
    items = [i for i in (surprises.items if surprises else [])
             if i.name.lower() not in planned and _inside(i.fits_slot, ctx)       # no repeats, no clashes
             and not repeats_code_place(i.name, i.fits_slot, ctx)]          # nor a place code already suggests
    support = plan_text(ctx)
    body = next((b for b in ((text.notification_body if text else None), s.notification_body)
                 if b and not unverified_claims(b, support) and not unknown_times(b, ctx)), None) or plan_body(ctx)
    swap = suggested_swap(ctx)                                              # code picks the time
    clashes = weather_clashes(ctx)
    actions = (["add_to_day"] if items else []) + (["swap"] if items or swap else []) + ["ask"]
    heads = [h for h in (text.heads_ups if text else s.heads_ups)
             if not _names_any(h, items) and not unknown_times(h, ctx) and not diet_claim(h)
             and not unverified_claims(h, support)]                           # nor a detail nothing confirms       # no time the model made up
    heads = ensure_covered(heads, ctx)
    tip = next((t for t in ((text.local_tip if text else None), s.local_tip)
                if t and not _names_any(t, items) and not (swap and _same_advice(t, swap))
                and not tip_problems(t, ctx)                                   # never an invented place
                and not any(_near_duplicate(t, h) or _same_moment(t, h) for h in heads)), "") or safe_tip(ctx)
    return MorningBrief(
        brief_id=brief_id(ctx), trip_id=ctx.trip_id, date=ctx.date,
        send_at_local=f"{ctx.send_at_local:%H:%M}" if ctx.send_at_local else "", send_at_utc=send_at_utc(ctx),
        timezone=ctx.timezone or "",
        notification=Notification(title=key_title(ctx),   # code names the key moments; the model writes the body
                                  body=fit_body(fix_idea_count(body, len(items)))),
        timeline=summary.timeline,
        weather=Weather(summary=weather_summary(ctx), clash=clashes[0] if clashes else None, suggested_swap=swap),
        heads_ups=heads,
        spend_estimate=_spend_text(summary.spend), surprises=items,
        local_tip=tip, actions=actions)


def _names_any(line: str, items) -> bool:
    """Does the line talk about one of the surprises? Those belong in the surprises section only."""
    return any(_covers(line, [w for w in i.name.lower().split() if len(w) >= 4] or [i.name.lower()], [])
               for i in items)


def _same_advice(tip: str, swap: str) -> bool:
    """A tip that just repeats the weather swap ("move X to 08:00") adds nothing."""
    times = {w for w in swap.replace("-", " ").split() if ":" in w}
    return bool(times & set(tip.replace("-", " ").split())) or tip.lower().startswith(("move ", "consider moving"))


def _spend_text(sp: Spend) -> Spend:
    return sp if sp.local else Spend(local="Nothing to pay for in today's plan", home="")


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
    brief = MorningBrief(
        brief_id=brief_id(ctx), trip_id=ctx.trip_id, date=ctx.date,
        send_at_local=f"{ctx.send_at_local:%H:%M}" if ctx.send_at_local else "", send_at_utc=send_at_utc(ctx), timezone=ctx.timezone or "",
        notification=Notification(title=key_title(ctx), body="Your plan for today"),
        timeline=timeline(ctx), heads_ups=ensure_covered([], ctx), weather=Weather(summary=weather_summary(ctx)),
        spend_estimate=_spend_text(spend(ctx, _fx())), actions=["ask"])
    brief.confidence.flags.append(f"safe_version: {reason}")
    brief.meta.judge_verdict = "fallback"
    return brief


def _fx():
    from mb.agents.facts import load_fx
    return load_fx()
