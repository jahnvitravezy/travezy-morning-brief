"""Audit every brief in a run against the facts it was written from.

    python -m mb.evals.audit baseline-v13

Independent of the judge: each check is a rule a reviewer would apply, run in code.
- Notification within its limits; title names the day (not "Day N in ...").
- Every idea is a checked candidate, at most 3, inside its checked slot, and that slot doesn't
  overlap a planned stop, a family's rest break, a meal (for food), rain or heat (outdoors), or a
  short walking limit (seated only).
- No time in the AI-written text that isn't in the plan, the free windows or code's own lines.
- No unsupported claim or risky advice in the tip.
- Every must-say fact (closures, delays, diet, access, walking, safety, airport) is covered.
- No near-duplicate heads-ups.
- A weather clash gets a swap when code can offer one.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from mb.agents.facts import (INDOOR_WORDS, plan_text, ensure_covered, suggested_swap, tip_problems, unknown_times,
                             unverified_claims, HOT_C, RAIN_PROB)
from mb.config import load_config
from mb.contracts import Context, MorningBrief
from mb.evals.runner import CONTEXTS, RUNS, context_index
from mb.evals.testset import load_testset
from mb.sources.candidates import AFTER_MEAL_MIN, FOOD_WORDS, REST_MIN, SEATED_IDEA

SLOT = re.compile(r"(\d{1,2}):(\d{2})\s*[-–]\s*(\d{1,2}):(\d{2})")


def _m(t) -> int:
    return t.hour * 60 + t.minute


def _words(s: str) -> set[str]:
    return set(re.findall(r"[a-z]{4,}", s.lower()))


def audit_brief(ctx: Context, b: MorningBrief, limits: dict) -> list[str]:
    out = []
    n = b.notification
    if len(n.title) > limits.get("notification_title_max_chars", 40):
        out.append(f"title {len(n.title)} chars")
    if len(n.body) > limits.get("notification_body_max_chars", 90):
        out.append(f"body {len(n.body)} chars")
    if re.match(r"^Day \d+ in ", n.title):
        out.append(f"generic title '{n.title}'")
    if b.confidence.applies_to != ["heads_ups", "surprises", "local_tip"]:
        out.append("badge covers more than the AI parts")

    # ideas
    cands = {c.name: c for c in ctx.candidates}
    acts = [a for a in ctx.day.activities if a.kind in ("do", "eat")]
    meals = [a for a in ctx.day.activities if a.kind == "eat"]
    bad = {h.hour.hour for h in (ctx.weather.hours if ctx.weather else []) if h.rain_prob >= RAIN_PROB or h.temp_c >= HOT_C}
    km = ctx.preferences.pace.max_walk_km
    if len(b.surprises) > 3:
        out.append(f"{len(b.surprises)} ideas")
    for s in b.surprises:
        c = cands.get(s.name)
        if c is None:
            out.append(f"idea '{s.name}' is not a checked candidate")
            continue
        m = SLOT.search(s.fits_slot or "")
        if not m:
            out.append(f"idea '{s.name}' has no slot")
            continue
        a, z = int(m[1]) * 60 + int(m[2]), int(m[3]) * 60 + int(m[4])
        lo, hi = _m(c.open_from or c.window.start), _m(c.window.end)
        if a < lo - 1 or z > hi + 1:
            out.append(f"idea '{s.name}' {s.fits_slot} outside its checked slot {lo//60:02d}:{lo%60:02d}-{hi//60:02d}:{hi%60:02d}")
        for p in acts:
            ps, pe = _m(p.start), _m(p.end or p.start)
            if a < pe and ps < z and pe > ps:
                out.append(f"idea '{s.name}' overlaps planned '{p.name}'")
        text = f"{c.name} {' '.join(c.types)}".lower().replace("_", " ")
        if ctx.preferences.pace.rest_after_lunch:
            lunch = next((x for x in meals if 11 * 60 <= _m(x.start) <= 15 * 60), None)
            r0 = _m(lunch.end) if lunch and lunch.end else 13 * 60 + 30
            if a < r0 + REST_MIN and r0 < z and not (z - (r0 + REST_MIN) >= 45 or r0 - a >= 45):
                out.append(f"idea '{s.name}' sits in the rest break")
        if any(w in text for w in FOOD_WORDS):
            for x in meals:
                if _m(x.start) - 30 <= a < _m(x.end or x.start) + AFTER_MEAL_MIN:
                    out.append(f"food idea '{s.name}' right after '{x.name}'")
        outdoor = not any(w in text for w in INDOOR_WORDS)
        if outdoor and bad & set(range(a // 60, (z - 1) // 60 + 1)):
            out.append(f"outdoor idea '{s.name}' in rain or heat")
        if km and km <= 3 and not any(w in text for w in SEATED_IDEA):
            out.append(f"walking idea '{s.name}' for a {km:g} km limit")

    # AI-written text
    for h in b.heads_ups:
        for t in unknown_times(h, ctx):
            out.append(f"unknown time {t} in heads-up '{h[:60]}'")
    for t in unknown_times(b.local_tip or "", ctx):
        out.append(f"unknown time {t} in tip")
    support = " ".join(f"{c.name} {c.snippet} {' '.join(c.types)}" for c in ctx.candidates) + " " + plan_text(ctx)
    for w in unverified_claims(b.local_tip or "", support):
        out.append(f"unsupported claim '{w}' in tip")
    for p in tip_problems(b.local_tip or "", ctx):
        out.append(f"tip: {p}")

    # must-say facts
    missing = [x for x in ensure_covered(b.heads_ups, ctx) if x not in b.heads_ups]   # lines code would still add
    out += [f"must-say not covered: {x[:80]}" for x in missing]

    # duplicates
    hs = b.heads_ups
    for i in range(len(hs)):
        for j in range(i + 1, len(hs)):
            wi, wj = _words(hs[i]), _words(hs[j])
            legs = all(re.search(r" to .+: about \d+ min", h) for h in (hs[i], hs[j]))   # two legs of a journey
            dinner = all("dinner" in h.lower() for h in (hs[i], hs[j]))
            if not legs and (dinner or (wi and wj and len(wi & wj) / len(wi | wj) >= 0.6)):
                out.append(f"near-duplicate heads-ups: '{hs[i][:50]}' / '{hs[j][:50]}'")

    # weather swap
    if b.weather.clash and not b.weather.suggested_swap and suggested_swap(ctx):
        out.append("weather clash without the swap code can offer")
    return out


def audit(run: str, runs: Path = RUNS) -> dict[str, list[str]]:
    ts = load_testset()
    index = json.loads(context_index(ts).read_text(encoding="utf-8"))
    rubrics = load_config().rubrics
    rubric = rubrics.get("rubric.morning_brief.v1") or next(iter(rubrics.values()))
    limits = dict(rubric.rule_checks)
    result = {}
    for day in ts.days:
        bp = runs / run / "briefs" / f"{day.id}.json"
        if not bp.exists():
            continue
        raw = json.loads(bp.read_text(encoding="utf-8"))
        b = MorningBrief.model_validate(raw.get("brief", raw))
        ctx = Context.model_validate_json((CONTEXTS / f"{index[day.id]}.json").read_text(encoding="utf-8"))
        result[day.id] = audit_brief(ctx, b, limits)
    return result


if __name__ == "__main__":
    run = sys.argv[1] if len(sys.argv) > 1 else "baseline-v13"
    res = audit(run)
    issues = sum(len(v) for v in res.values())
    for d, v in res.items():
        print(f"{d}: {'ok' if not v else ''}")
        for x in v:
            print(f"   - {x}")
    print(f"\n{len(res)} briefs, {issues} issues, {sum(1 for v in res.values() if not v)} clean")
