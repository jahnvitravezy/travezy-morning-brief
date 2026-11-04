"""Agent 4, the judge: scores a draft brief against a rubric and decides pass, fix or fallback.

Two layers. First the rule checks (mb/judge/rules.py): cheap, in code, no model. Then the model
scores each rubric criterion 0-100 with a one-sentence reason and lists concrete issues. Code
does the rest, so the decision is reproducible and the thresholds live in the rubric:
  weighted score = sum(criterion score x weight)
  badge          = green >= rubric.badge.green, amber >= rubric.badge.amber, else red
  decision       = pass     if green, no rule failure and no serious error
                   fix      if amber/red, a rule failure or a serious error, and a fix attempt is left
                   fallback if still red, failing a rule or with a serious error, with no attempt left
A serious error (a wrong time, place, price or opening hour; a diet mistake; anything unsafe)
fails the brief whatever its total score. A fixed brief is judged again before it is sent.
If the judge model fails, the rule checks still decide, and the brief is never blocked by it.

Code confirms each serious error before it can fail a brief (confirm_serious): it must quote the
brief, the quote must really be in the brief, and it must not be about a fact code computed or
on the prompt's "never serious" list. One unconfirmed flag no longer sinks a good brief; it is
kept as an issue, so the fix loop can still act on it.
"""
from __future__ import annotations

import json
import re

from mb.agents.facts import must_cover, suggested_swap, weather_clashes, weather_summary
from mb.agents.summariser import brief_input
from mb.config import AgentSpec, Rubric
from mb.contracts import Context, JudgeOutput, MorningBrief, Verdict
from mb.judge.rules import run_rule_checks
from mb.llm import LLM, LLMResult, load_prompt


# The prompt's "NOT serious, ever" list, enforced in code (judges don't always follow it).
NEVER_SERIOUS = ("not published", "unpublished", "aren't published", "are not published", "hours unknown",
                 "no rain expected", "check the hours", "check hours", "check opening hours")
QUOTE = re.compile(r"[\"“”‘’']([^\"“”]{6,}?)[\"“”’']")


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", t.replace("’", "'").replace("‘", "'")).strip().lower()


INVENTED_WORDS = ("invent", "not in the input", "not in inputs", "does not exist", "doesn't exist", "made up", "fabricat", "hallucinat")


def confirm_serious(serious: list[str], brief: MorningBrief, ctx: Context) -> tuple[list[str], list[str]]:
    """(confirmed, dropped). A serious error stands only if it quotes the brief word for word and
    the quote isn't a line code computed (weather line, must-report checks)."""
    text = _norm(brief.model_dump_json())
    code_lines = [_norm(weather_summary(ctx))] + [_norm(line) for _, _, line in must_cover(ctx)]
    # Places the brief may name: the plan, Google Places lookups and checked ideas. A judge that
    # doesn't know a real place must not call it invented (blind review, 3 Nov: the Haiku judge).
    verified = {_norm(_n) for _n in ([a.name for a in ctx.day.activities] + [c.name for c in ctx.candidates]
                                     + [p.name for p in ctx.places.values() if p]) if len(_n) > 3}
    confirmed, dropped = [], []
    for s in serious:
        low = _norm(s)
        quotes = [q for q in (_norm(m) for m in QUOTE.findall(s)) if q in text]
        if any(w in low for w in NEVER_SERIOUS):
            dropped.append(f"{s} [not serious: on the never-serious list]")
        elif not quotes:
            dropped.append(f"{s} [not serious: quotes nothing from the brief]")
        elif all(any(q in c for c in code_lines) for q in quotes):
            dropped.append(f"{s} [not serious: about a fact code computed]")
        elif any(w in low for w in INVENTED_WORDS) and any(v in q for q in quotes for v in verified):
            dropped.append(f"{s} [not serious: the place is verified (plan or Google Places)]")
        else:
            confirmed.append(s)
    return confirmed, dropped


def candidate_facts(ctx: Context) -> list[dict]:
    """What we verified about each idea's place: the brief may use these facts."""
    return [{"name": c.name, "slot": f"{(c.open_from or c.window.start):%H:%M}-{c.window.end:%H:%M}",
             "travel_min": c.travel_min, "rating": c.rating, "types": c.types[:5], "source": c.provider}
            for c in ctx.candidates]


def judge_input(ctx: Context, brief: MorningBrief, rubric: Rubric) -> str:
    """What the judge sees: the facts the brief was written from, the brief, and the rubric."""
    shown = brief.model_dump(mode="json", exclude={"meta", "confidence", "brief_id", "actions"})
    return json.dumps({
        "rubric": [{"id": c.id, "weight": c.weight, "asks": c.asks} for c in rubric.criteria],
        "inputs": json.loads(brief_input(ctx)) | {
            "free_day": ctx.day.is_free,
            "free_windows": [f"{w.start:%H:%M}-{w.end:%H:%M}" for w in ctx.free_windows],
            "candidates_for_surprises": candidate_facts(ctx),
        },
        "facts_from_code": {
            "weather_line": weather_summary(ctx), "weather_clashes": weather_clashes(ctx),
            "weather_swap": suggested_swap(ctx), "must_be_reported": [line for _, _, line in must_cover(ctx)],
            "spend_estimate": "computed in code from the plan's costs, per person, flights and hotels excluded",
            "timeline": "copied from the plan by code",
        },
        "brief": shown,
    }, indent=1, ensure_ascii=False, default=str)


def weighted(scores, rubric: Rubric) -> int:
    by_id = {s.id: s.score for s in scores}
    return round(sum(by_id[c.id] * c.weight for c in rubric.criteria))


def badge_for(score: int, rubric: Rubric) -> str:
    return "green" if score >= rubric.badge.green else "amber" if score >= rubric.badge.amber else "red"


def validate_scores(out: JudgeOutput, rubric: Rubric) -> None:
    """Every rubric criterion exactly once, nothing else. Otherwise the judgement is unusable."""
    got = [s.id for s in out.scores]
    want = [c.id for c in rubric.criteria]
    if sorted(got) != sorted(want):
        raise ValueError(f"judge returned criteria {got}, rubric has {want}")


def decide(rule_failures: list[str], score: int | None, rubric: Rubric, attempts_left: int,
           serious: list[str] | None = None) -> str:
    """A rule failure or a serious error fails the brief whatever its total score."""
    blocking = bool(rule_failures) or bool(serious)
    below_green = score is not None and score < rubric.badge.green
    if not blocking and not below_green:
        return "pass"
    if attempts_left > 0:
        return "fix"
    if blocking or (score is not None and score < rubric.badge.amber):
        return "fallback"
    return "pass"                       # amber after the last attempt: send it, with the amber badge


def recommendation(rule_failures: list[str], serious: list[str], score: int | None, rubric: Rubric) -> str | None:
    """The verdict in a reviewer's words (send as is / fix first / don't send), for measuring agreement."""
    if rule_failures or serious:
        return "dont_send"
    if score is None:
        return None
    return "send" if score >= rubric.badge.green else "fix_first" if score >= rubric.badge.amber else "dont_send"


async def judge_brief(ctx: Context, brief: MorningBrief, rubric: Rubric, spec: AgentSpec, llm: LLM,
                      run_id: str, attempts_left: int) -> tuple[Verdict, LLMResult | None]:
    failures = run_rule_checks(brief, ctx, rubric)
    try:
        res = await llm.call(model=spec.model, system=load_prompt(spec.prompt_version),
                             user=judge_input(ctx, brief, rubric), response_model=JudgeOutput,
                             run_id=run_id, agent_id=spec.id, temperature=rubric.temperature,
                             timeout_s=spec.timeout_s)
        validate_scores(res.output, rubric)
    except Exception as e:              # the judge never blocks a brief: rule checks decide alone
        decision = decide(failures, None, rubric, attempts_left)
        return Verdict(agent_id=spec.id, rubric_id=rubric.id, rule_failures=failures, decision=decision,
                       recommendation=recommendation(failures, [], None, rubric),
                       reasons=failures, error=f"{type(e).__name__}: {str(e)[:200]}"), None
    score = weighted(res.output.scores, rubric)
    serious, dropped = confirm_serious(res.output.serious, brief, ctx)
    decision = decide(failures, score, rubric, attempts_left, serious)
    return Verdict(agent_id=spec.id, rubric_id=rubric.id, model=res.model, rule_failures=failures,
                   scores=res.output.scores, score=score, badge=badge_for(score, rubric), serious=serious,
                   serious_dropped=dropped, recommendation=recommendation(failures, serious, score, rubric),
                   decision=decision, reasons=failures + serious + res.output.issues
                   + [d.split(" [not serious")[0] for d in dropped]), res


def stamp(brief: MorningBrief, v: Verdict) -> MorningBrief:
    """Put the verdict on the brief the app receives: score, badge, flags, verdict."""
    b = brief.model_copy(deep=True)
    b.confidence.score = v.score
    b.confidence.badge = v.badge
    b.confidence.flags = ([*b.confidence.flags, *v.rule_failures] + [f"serious: {x}" for x in v.serious]
                          + ([f"judge unavailable: {v.error}"] if v.error else []))
    b.meta.judge_verdict = v.decision
    return b
