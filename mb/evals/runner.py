"""Runs a test set through the full pipeline and stores everything needed to compare runs.

  python -m mb.evals.runner baseline            (add --rebuild after a change to how contexts are built)
  python -m mb.evals.runner cmp-summariser-sonnet --set summariser=anthropic/claude-sonnet-5-5

--set role=model swaps one agent's model for this run only (roles: summariser, surprise_finder,
composer, judge). Comparisons change one role at a time, so a difference has one cause.

Contexts are built once per test day and saved; later runs reuse them, so every model is
compared on exactly the same inputs and no Places search is paid for twice. For each day the
run saves the brief and one record: models, latency, tokens, cost, events and rule-check
failures. Results go to data/runs/<run_id>/.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

from mb.context import build_full_context
from mb.contracts import Context
from mb.evals.testset import TestDay, TestSet, load_testset
from mb.judge.rules import run_rule_checks
from mb.llm import preflight
from mb.pipeline import run_brief
from mb.services import Services

ROOT = Path(__file__).resolve().parents[2]
CONTEXTS = ROOT / "data" / "contexts"
RUNS = ROOT / "data" / "runs"


def context_index(testset: TestSet) -> Path:
    return CONTEXTS / f"{testset.id}.index.json"


async def contexts_for(testset: TestSet, s: Services, rebuild: bool = False) -> dict[str, Context]:
    """Day id -> Context. Built (and paid for) once, then loaded from disk."""
    CONTEXTS.mkdir(parents=True, exist_ok=True)
    idx_path = context_index(testset)
    index = json.loads(idx_path.read_text(encoding="utf-8")) if idx_path.exists() and not rebuild else {}
    out: dict[str, Context] = {}
    for day in testset.days:
        path = CONTEXTS / f"{index.get(day.id, '-')}.json"
        if day.id in index and path.exists():
            out[day.id] = Context.model_validate_json(path.read_text(encoding="utf-8"))
            continue
        trip = s.loader.load(day.trip_id)
        d = next(x for x in trip.days if x.date == day.date)
        ctx = await build_full_context(trip, d.index, s.meteo, day.weather, places=s.places, routes=s.routes,
                                       run_id=f"ctx-{testset.id}", providers=s.providers)
        (CONTEXTS / f"{ctx.ctx_id}.json").write_text(ctx.model_dump_json(indent=1), encoding="utf-8")
        index[day.id] = ctx.ctx_id
        out[day.id] = ctx
        print(f"  context {day.id}: {ctx.ctx_id}  ({len(ctx.candidates)} candidates)")
    idx_path.write_text(json.dumps(index, indent=1), encoding="utf-8")
    return out


async def run_testset(run_id: str, testset: TestSet, s: Services, cfg=None, concurrency: int = 3,
                      rebuild: bool = False) -> list[dict]:
    cfg = cfg or s.cfg
    rubric = next(r for r in cfg.rubrics.values() if r.use_case == testset.use_case)
    out_dir = RUNS / run_id
    (out_dir / "briefs").mkdir(parents=True, exist_ok=True)
    contexts = await contexts_for(testset, s, rebuild)
    sem = asyncio.Semaphore(concurrency)

    async def one(day: TestDay) -> dict:
        async with sem:
            ctx = contexts[day.id]
            run = await run_brief(ctx, cfg, s.llm, run_id=f"{run_id}:{day.id}")
            failures = run_rule_checks(run.brief, ctx, rubric)
            (out_dir / "briefs" / f"{day.id}.json").write_text(run.brief.model_dump_json(indent=1), encoding="utf-8")
            rec = {"run_id": run_id, "day_id": day.id, "trip_id": day.trip_id, "date": day.date.isoformat(),
                   "covers": day.covers, "ctx_id": ctx.ctx_id, "models": run.brief.meta.models,
                   "latency_ms": run.brief.meta.latency_ms, "tokens": run.brief.meta.tokens,
                   "cost_usd": round(run.cost_usd, 5), "surprises": len(run.brief.surprises),
                   "fallback": run.brief.meta.judge_verdict == "fallback", "rule_failures": failures,
                   "agent_failures": [e for e in run.events if "failed" in e and "attempt" not in e],
                   "verdict": run.brief.meta.judge_verdict, "score": run.brief.confidence.score,
                   "badge": run.brief.confidence.badge,
                   "first_score": run.verdicts[0].score if run.verdicts else None,
                   "criteria": {c.id: c.score for c in (run.verdicts[-1].scores if run.verdicts else [])},
                   "judge_issues": run.verdicts[0].reasons if run.verdicts else [],
                   "events": run.events}
            print(f"  {day.id} {day.trip_id:11} {rec['latency_ms']:>6} ms  ${rec['cost_usd']:.4f}  "
                  f"surprises={rec['surprises']}  judge={rec['verdict']} {rec['score']} {rec['badge']}  "
                  f"rules={'ok' if not failures else failures}")
            return rec

    records = await asyncio.gather(*(one(d) for d in testset.days))
    with (out_dir / "records.jsonl").open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return records


def _avg(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 1) if xs else None


def summary(records: list[dict]) -> dict:
    n = len(records)
    scored = [r["score"] for r in records if r.get("score") is not None]
    return {"days": n, "fallbacks": sum(r["fallback"] for r in records),
            "agent_failures": sum(len(r.get("agent_failures", [])) for r in records),
            "verdicts": {v: sum(r.get("verdict") == v for r in records) for v in ("pass", "fix", "fallback")},
            "badges": {b: sum(r.get("badge") == b for r in records) for b in ("green", "amber", "red")},
            "avg_score": round(sum(scored) / len(scored), 1) if scored else None,
            "avg_first_score": _avg([r.get("first_score") for r in records]),
            "judged": len(scored),
            "rule_pass_rate": round(sum(not r["rule_failures"] for r in records) / n, 3),
            "no_surprise_days": sum(r["surprises"] == 0 for r in records),
            "avg_latency_ms": round(sum(r["latency_ms"] for r in records) / n),
            "total_cost_usd": round(sum(r["cost_usd"] for r in records), 4)}


def with_models(cfg, overrides: dict[str, str]):
    """The registry with some agents' models swapped. The judge's rubric follows its agent."""
    agents, rubrics = dict(cfg.agents), dict(cfg.rubrics)
    for role, model in overrides.items():
        if model not in cfg.models:
            raise ValueError(f"{model} is not in models.yaml")
        matches = [a for a in agents.values() if a.id.split(".")[1] == role]
        if len(matches) != 1:
            raise ValueError(f"unknown role '{role}': use one of {[a.id.split('.')[1] for a in agents.values()]}")
        a = matches[0]
        agents[a.id] = a.model_copy(update={"model": model})
        if a.rubric:
            rubrics[a.rubric] = rubrics[a.rubric].model_copy(update={"judge_model": model})
    return cfg.model_copy(update={"agents": agents, "rubrics": rubrics})


async def main(run_id: str, rebuild: bool = False, overrides: dict[str, str] | None = None):
    s = Services()
    cfg = with_models(s.cfg, overrides or {})
    problems = await preflight(s.llm, {a.model for a in cfg.agents.values()})
    if problems:
        for m, p in problems.items():
            print(f"PREFLIGHT FAILED  {m}: {p}")
        print(f"Run '{run_id}' not started: every brief would silently fall back, and the numbers would mean nothing.")
        sys.exit(1)
    started = time.perf_counter()
    records = await run_testset(run_id, load_testset(), s, cfg=cfg, rebuild=rebuild)
    result = summary(records) | {"wall_s": round(time.perf_counter() - started),
                                 "models": {a.id: a.model for a in cfg.agents.values()},
                                 "overrides": overrides or {}}
    (RUNS / run_id / "summary.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    print("\n", result, "\n", s.budget.status())

if __name__ == "__main__":
    argv = sys.argv[1:]
    sets = dict(argv[i + 1].split("=", 1) for i, a in enumerate(argv) if a == "--set")
    args = [a for i, a in enumerate(argv) if not a.startswith("--") and (i == 0 or argv[i - 1] != "--set")]
    asyncio.run(main(args[0] if args else "baseline", rebuild="--rebuild" in argv, overrides=sets))
