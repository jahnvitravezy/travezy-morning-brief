"""Orchestration: runs the agents for one trip day, in the order and shape the registry defines.

Agents are found in the registry by the output they produce (out.summary, out.surprises,
out.draft), so swapping a model or an agent is a config change. Agents 1 and 2 run in
parallel. Every step has a timeout and one retry for transient errors, inside an overall
time budget. Failures degrade, never block:
  - Surprise finder fails  -> brief without surprises
  - Composer fails         -> code-only merge of the summary and surprises
  - Summariser fails, or the time budget runs out -> the safe version (plan only)
The judge (Agent 4) plugs in at the marked point.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

import litellm

from mb.agents.composer import merge, run_composer, safe_version
from mb.agents.summariser import run_summariser
from mb.agents.surprise_finder import run_surprise_finder
from mb.config import AgentSpec, Config
from mb.contracts import Context, MorningBrief, Summary, Surprises
from mb.llm import LLM

TRANSIENT = (asyncio.TimeoutError, litellm.exceptions.Timeout, litellm.exceptions.RateLimitError,
             litellm.exceptions.APIConnectionError, litellm.exceptions.ServiceUnavailableError,
             litellm.exceptions.InternalServerError)
DEFAULT_BUDGET_S = 90.0


@dataclass
class BriefRun:
    brief: MorningBrief
    summary: Summary | None = None
    surprises: Surprises | None = None
    events: list[str] = field(default_factory=list)   # what happened, step by step
    cost_usd: float = 0.0
    tokens: dict[str, int] = field(default_factory=dict)


def agent_for(cfg: Config, output: str) -> AgentSpec:
    for a in cfg.agents.values():
        if a.output == output and a.status == "active":
            return a
    raise LookupError(f"No active agent produces {output}")


class _Clock:
    def __init__(self, budget_s: float):
        self.end = time.perf_counter() + budget_s

    def left(self) -> float:
        return self.end - time.perf_counter()


async def _step(name: str, make_coro, timeout_s: float, clock: _Clock, run: BriefRun, retries: int = 1):
    """Run one agent call with a timeout and one retry for transient errors, inside the budget."""
    for attempt in range(retries + 1):
        if clock.left() <= 0.5:
            raise asyncio.TimeoutError(f"{name}: time budget exhausted")
        limit = min(timeout_s, clock.left())
        try:
            out, res = await asyncio.wait_for(make_coro(), timeout=limit)
            if res is not None:
                run.cost_usd += res.cost_usd
                run.tokens[name] = run.tokens.get(name, 0) + res.tokens_in + res.tokens_out
            run.events.append(f"{name}: ok" + (f" ({res.model}, {res.latency_ms} ms)" if res else " (no model call)"))
            return out, res
        except TRANSIENT as e:
            run.events.append(f"{name}: attempt {attempt + 1} failed ({type(e).__name__})")
            if attempt == retries:
                raise
            await asyncio.sleep(min(2 ** attempt, max(clock.left() - 1, 0)))


async def run_brief(ctx: Context, cfg: Config, llm: LLM, run_id: str,
                    budget_s: float = DEFAULT_BUDGET_S) -> BriefRun:
    started, clock = time.perf_counter(), _Clock(budget_s)
    a1, a2, a3 = (agent_for(cfg, o) for o in ("out.summary", "out.surprises", "out.draft"))
    run = BriefRun(brief=safe_version(ctx, "not started"))

    r1, r2 = await asyncio.gather(
        _step(a1.id, lambda: run_summariser(ctx, a1, llm, run_id), a1.timeout_s, clock, run),
        _step(a2.id, lambda: run_surprise_finder(ctx, a2, llm, run_id), a2.timeout_s, clock, run),
        return_exceptions=True)

    if isinstance(r1, BaseException):
        run.events.append(f"{a1.id}: failed ({type(r1).__name__}) -> safe version")
        run.brief = safe_version(ctx, f"summariser failed: {type(r1).__name__}")
        return _finish(run, started, {})
    run.summary = r1[0]
    models = {a1.id: r1[1].model}
    if isinstance(r2, BaseException):
        run.events.append(f"{a2.id}: failed ({type(r2).__name__}) -> no surprises")
        run.surprises = Surprises(agent_id=a2.id, none_reason="Surprises unavailable right now")
    else:
        run.surprises = r2[0]
        if r2[1]:
            models[a2.id] = r2[1].model

    try:
        brief, res = await _step(a3.id, lambda: run_composer(ctx, run.summary, run.surprises, a3, llm, run_id),
                                 a3.timeout_s, clock, run)
        models[a3.id] = res.model
    except Exception as e:  # any Composer failure falls back to the code-only merge
        run.events.append(f"{a3.id}: failed ({type(e).__name__}) -> code-only merge")
        brief = merge(ctx, run.summary, run.surprises)

    # --- Agent 4 (judge) plugs in here: pass -> send; fix -> run_composer(..., fix_reasons) once;
    #     fallback -> safe_version. Until then, the brief goes out unjudged (no badge).
    run.brief = brief
    return _finish(run, started, models)


def _finish(run: BriefRun, started: float, models: dict[str, str]) -> BriefRun:
    run.brief.meta.latency_ms = int((time.perf_counter() - started) * 1000)
    run.brief.meta.models = models or run.brief.meta.models
    run.brief.meta.tokens = dict(run.tokens)
    return run
