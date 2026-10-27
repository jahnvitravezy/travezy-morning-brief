"""Agent 4, the judge: scores a draft brief against a rubric and decides pass, fix or fallback.

Two layers. First the rule checks (mb/judge/rules.py): cheap, in code, no model. Then the model
scores each rubric criterion 0-100 with a one-sentence reason and lists concrete issues. Code
does the rest, so the decision is reproducible and the thresholds live in the rubric:
  weighted score = sum(criterion score x weight)
  badge          = green >= rubric.badge.green, amber >= rubric.badge.amber, else red
  decision       = pass     if green and no rule failures
                   fix      if amber/red or a rule failure, and a fix attempt is left
                   fallback if still red (or failing a rule) with no attempt left
If the judge model fails, the rule checks still decide, and the brief is never blocked by it.
"""
from __future__ import annotations

import json

from mb.agents.facts import must_cover, suggested_swap, weather_clashes, weather_summary
from mb.agents.summariser import brief_input
from mb.config import AgentSpec, Rubric
from mb.contracts import Context, JudgeOutput, MorningBrief, Verdict
from mb.judge.rules import run_rule_checks
from mb.llm import LLM, LLMResult, load_prompt


def judge_input(ctx: Context, brief: MorningBrief, rubric: Rubric) -> str:
    """What the judge sees: the facts the brief was written from, the brief, and the rubric."""
    shown = brief.model_dump(mode="json", exclude={"meta", "confidence", "brief_id", "actions"})
    return json.dumps({
        "rubric": [{"id": c.id, "weight": c.weight, "asks": c.asks} for c in rubric.criteria],
        "inputs": json.loads(brief_input(ctx)) | {
            "free_day": ctx.day.is_free,
            "free_windows": [f"{w.start:%H:%M}-{w.end:%H:%M}" for w in ctx.free_windows],
            "candidates_for_surprises": [c.name for c in ctx.candidates],
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


def decide(rule_failures: list[str], score: int | None, rubric: Rubric, attempts_left: int) -> str:
    below_green = score is not None and score < rubric.badge.green
    if not rule_failures and not below_green:
        return "pass"
    if attempts_left > 0:
        return "fix"
    if rule_failures or (score is not None and score < rubric.badge.amber):
        return "fallback"
    return "pass"                       # amber after the last attempt: send it, with the amber badge


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
                       reasons=failures, error=f"{type(e).__name__}: {str(e)[:200]}"), None
    score = weighted(res.output.scores, rubric)
    decision = decide(failures, score, rubric, attempts_left)
    return Verdict(agent_id=spec.id, rubric_id=rubric.id, model=res.model, rule_failures=failures,
                   scores=res.output.scores, score=score, badge=badge_for(score, rubric),
                   decision=decision, reasons=failures + res.output.issues), res


def stamp(brief: MorningBrief, v: Verdict) -> MorningBrief:
    """Put the verdict on the brief the app receives: score, badge, flags, verdict."""
    b = brief.model_copy(deep=True)
    b.confidence.score = v.score
    b.confidence.badge = v.badge
    b.confidence.flags = [*b.confidence.flags, *v.rule_failures] + ([f"judge unavailable: {v.error}"] if v.error else [])
    b.meta.judge_verdict = v.decision
    return b
