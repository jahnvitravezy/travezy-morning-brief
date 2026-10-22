"""Agent 1, the Summariser: turns one trip day's context into out.summary.

Code computes the facts (timeline, travel, spend). The model writes the words
(notification, heads-ups, weather advice, tip), grounded only in the context.
Weather clashes are found in code; the model only words them and suggests a swap.
"""
from __future__ import annotations

import json

from mb.agents.facts import load_fx, spend, timeline, weather_clashes
from mb.config import AgentSpec
from mb.contracts import Context, Summary, SummaryText
from mb.llm import LLM, LLMResult, load_prompt


def _weather_at(ctx: Context, a) -> str | None:
    """Worst rain and heat during the activity, computed in code so the model can't misalign hours."""
    if not ctx.weather or not ctx.weather.hours:
        return None
    end = (a.end or a.start).hour
    hrs = [h for h in ctx.weather.hours if a.start.hour <= h.hour.hour <= end]
    if not hrs:
        return None
    return f"rain up to {max(h.rain_prob for h in hrs)}%, up to {max(h.temp_c for h in hrs):.0f}C"


def brief_input(ctx: Context) -> str:
    """The compact, task-only view of the context the model sees. No personal data beyond the task."""
    p = ctx.preferences
    acts = ctx.day.activities
    names = {a.id: a.name for a in acts}
    must = [f"{a.name}: {'pickup' if 'pickup' in a.note.lower() else 'timed entry'} at {a.timed_at:%H:%M}"
            for a in acts if a.timed_at]
    must += [f"{a.name}: {a.note}" for a in acts if "cash" in a.note.lower()]
    return json.dumps({
        "date": ctx.date.isoformat(), "weekday": f"{ctx.date:%A}", "day_title": ctx.day.title,
        "destination": ctx.region, "destination_day": ctx.day.index + 1,
        "arrival_day": ctx.day.is_arrival, "departure_day": ctx.day.is_departure,
        "travellers": {"who": p.who, "diet": p.diet.summary, "pace": p.pace.summary,
                       "children": bool(p.party.children_ages), "interests": p.interests},
        "plan": [{"time": f"{a.start:%H:%M}-{(a.end or a.start):%H:%M}", "kind": a.kind, "name": a.name,
                  "booked": a.booked, "note": a.note, "cost": a.cost_text, "weather": _weather_at(ctx, a)}
                 for a in acts],
        "travel_between_stops": [{"from": names[l.from_id], "to": names[l.to_id], "minutes": l.minutes}
                                 for l in ctx.legs],
        "weather_overall": ctx.weather.hours and f"{min(h.temp_c for h in ctx.weather.hours):.0f}-"
                           f"{max(h.temp_c for h in ctx.weather.hours):.0f}C" if ctx.weather else None,
        "weather_clashes": weather_clashes(ctx),
        "must_mention": must,
        "data_flags": [f.message for f in ctx.flags],
    }, indent=1, ensure_ascii=False)


async def run_summariser(ctx: Context, spec: AgentSpec, llm: LLM, run_id: str) -> tuple[Summary, LLMResult]:
    res = await llm.call(model=spec.model, system=load_prompt(spec.prompt_version), user=brief_input(ctx),
                         response_model=SummaryText, run_id=run_id, agent_id=spec.id,
                         temperature=spec.temperature, timeout_s=spec.timeout_s)
    summary = Summary(agent_id=spec.id, text=res.output, timeline=timeline(ctx), spend=spend(ctx, load_fx()))
    return summary, res