"""Judge briefs that already exist, without writing them again.

  python -m mb.evals.judge_run baseline-v5                      # the registry's judge, once
  python -m mb.evals.judge_run baseline-v5 --repeat 3           # consistency: same briefs, 3 times
  python -m mb.evals.judge_run baseline-v5 --model anthropic/claude-haiku-5-5

Reads data/runs/<run>/briefs and the stored contexts, so every judge sees exactly the same
briefs and inputs. Writes data/runs/<run>/judged/<judge-model>.jsonl: one line per brief per
repeat, with the criterion scores, weighted score, badge, issues and cost.
"""
from __future__ import annotations

import asyncio
import json
import sys

from mb.config import Config
from mb.contracts import Context, MorningBrief
from mb.evals.runner import CONTEXTS, RUNS, context_index
from mb.evals.testset import load_testset
from mb.judge.judge import judge_brief
from mb.llm import preflight
from mb.pipeline import agent_for
from mb.services import Services


def with_judge_model(cfg: Config, model: str) -> Config:
    """The same config with a different judge model (the rubric and the agent stay in step)."""
    if model not in cfg.models:
        raise ValueError(f"{model} is not in models.yaml")
    a4 = agent_for(cfg, "out.verdict")
    agents = dict(cfg.agents) | {a4.id: a4.model_copy(update={"model": model})}
    rubrics = dict(cfg.rubrics) | {a4.rubric: cfg.rubrics[a4.rubric].model_copy(update={"judge_model": model})}
    return cfg.model_copy(update={"agents": agents, "rubrics": rubrics})


async def judge_run(run_id: str, s: Services, model: str | None = None, repeat: int = 1,
                    concurrency: int = 4, check: bool = True) -> list[dict]:
    cfg = with_judge_model(s.cfg, model) if model else s.cfg
    a4 = agent_for(cfg, "out.verdict")
    if check and (problems := await preflight(s.llm, {a4.model})):
        raise SystemExit(f"PREFLIGHT FAILED  {a4.model}: {problems[a4.model]}\nNot judging: every judgement would fail.")
    rubric = cfg.rubrics[a4.rubric]
    testset = load_testset()
    index = json.loads(context_index(testset).read_text(encoding="utf-8"))
    run_dir = RUNS / run_id
    sem = asyncio.Semaphore(concurrency)

    async def one(day_id: str, k: int) -> dict:
        async with sem:
            ctx = Context.model_validate_json((CONTEXTS / f"{index[day_id]}.json").read_text(encoding="utf-8"))
            brief = MorningBrief.model_validate_json((run_dir / "briefs" / f"{day_id}.json").read_text(encoding="utf-8"))
            v, res = await judge_brief(ctx, brief, rubric, a4, s.llm, f"judge:{run_id}:{day_id}:{k}", attempts_left=0)
            rec = {"day_id": day_id, "repeat": k, "judge_model": a4.model, "score": v.score, "badge": v.badge,
                   "criteria": {c.id: c.score for c in v.scores}, "issues": v.reasons, "error": v.error,
                   "cost_usd": round(res.cost_usd, 5) if res else 0.0}
            print(f"  {day_id} #{k}  {v.score} {v.badge}  {'; '.join(v.reasons[:1])[:90]}")
            return rec

    days = [d.id for d in testset.days if (run_dir / "briefs" / f"{d.id}.json").exists()]
    records = await asyncio.gather(*(one(d, k) for k in range(1, repeat + 1) for d in days))
    out = run_dir / "judged"
    out.mkdir(exist_ok=True)
    path = out / f"{a4.model.replace('/', '__')}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    scored = [r["score"] for r in records if r["score"] is not None]
    errors = sum(r["score"] is None for r in records)
    if errors:
        print(f"\nWARNING: {errors} of {len(records)} judgements failed; see 'error' in the file.")
    print(f"\n{len(records)} judgements -> {path}\navg score {sum(scored) / max(len(scored), 1):.1f}, "
          f"cost ${sum(r['cost_usd'] for r in records):.4f}\n{s.budget.status()}")
    return records


def _arg(flag: str, default=None):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default


if __name__ == "__main__":
    asyncio.run(judge_run(sys.argv[1], Services(), model=_arg("--model"), repeat=int(_arg("--repeat", 1))))
