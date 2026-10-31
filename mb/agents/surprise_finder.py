"""Agent 2, the Surprise finder: picks up to three things to add to the day.

Candidates are gathered and filtered in code (mb/sources/candidates.py). The model only
chooses from them and says why each fits. Code then rejects any pick that isn't a real
candidate, and turns picks into SurpriseItems from the candidate's own data, so names,
links, slots and distances can't be invented. With no candidates, the model isn't called.
"""
from __future__ import annotations

import json

from mb.config import AgentSpec
from mb.agents.facts import unverified_claims
from mb.contracts import Context, SurpriseItem, SurprisePicks, Surprises
from mb.llm import LLM, LLMResult, load_prompt

MAX_SURPRISES = 3


def finder_input(ctx: Context) -> str:
    p = ctx.preferences
    return json.dumps({
        "date": ctx.date.isoformat(), "destination": ctx.region,
        "travellers": {"who": p.who, "diet": p.diet.summary, "pace": p.pace.summary,
                       "children": bool(p.party.children_ages), "interests": p.interests, "budget": p.budget},
        "todays_plan": [f"{a.start:%H:%M} {a.name}" for a in ctx.day.activities],
        "candidates": [{"id": c.id, "name": c.name, "slot": f"{(c.open_from or c.window.start):%H:%M}-{c.window.end:%H:%M}",
                        "travel_min": c.travel_min, "rating": c.rating, "types": c.types,
                        "source": c.provider, "snippet": c.snippet or None} for c in ctx.candidates],
        "max_picks": MAX_SURPRISES,
    }, indent=1, ensure_ascii=False)


def plain_why(c) -> str:
    """A description built only from verified facts, when the model's overstates the place."""
    kind = next((t.replace("_", " ") for t in c.types if t not in ("point_of_interest", "establishment")), "place")
    bits = [f"A {kind}"] + ([f"rated {c.rating:.1f}"] if c.rating else [])
    return ", ".join(bits) + (f", {c.travel_min} min away" if c.travel_min else "") + ", open in this slot."


def validate(ctx: Context, picks: SurprisePicks, agent_id: str) -> Surprises:
    """Code checks on the model's choices: real candidate, one per slot, at most three."""
    by_id = {c.id: c for c in ctx.candidates}
    items, dropped, used_slots = [], [], set()
    for pick in picks.picks:
        c = by_id.get(pick.candidate_id)
        if c is None:
            dropped.append(f"{pick.candidate_id}: not in the candidate list")
            continue
        slot = f"{c.window.start:%H:%M}"                # one idea per free window (each idea's end differs)
        if slot in used_slots:
            dropped.append(f"{c.name}: a surprise is already in {slot}")
            continue
        if len(items) == MAX_SURPRISES:
            dropped.append(f"{c.name}: more than {MAX_SURPRISES} surprises")
            continue
        used_slots.add(slot)
        shown = f"{(c.open_from or c.window.start):%H:%M}-{c.window.end:%H:%M}"   # from when it opens
        why = pick.why_you.strip()
        if unverified_claims(why, support=f"{c.name} {' '.join(c.types)} {c.snippet}"):
            why = plain_why(c)                           # "rooftop dining" for a cafe: say only what we know
        items.append(SurpriseItem(name=c.name, why_you=why, fits_slot=shown,
                                  distance_min=c.travel_min, source_url=c.source_url, place_id=c.place_id))
    none_reason = picks.none_reason if not items else None
    return Surprises(agent_id=agent_id, items=items, none_reason=none_reason, dropped=dropped)


async def run_surprise_finder(ctx: Context, spec: AgentSpec, llm: LLM,
                              run_id: str) -> tuple[Surprises, LLMResult | None]:
    if not ctx.candidates:
        return Surprises(agent_id=spec.id, none_reason="No free window with a verifiable option today"), None
    res = await llm.call(model=spec.model, system=load_prompt(spec.prompt_version), user=finder_input(ctx),
                         response_model=SurprisePicks, run_id=run_id, agent_id=spec.id,
                         temperature=spec.temperature, timeout_s=spec.timeout_s)
    return validate(ctx, res.output, spec.id), res
