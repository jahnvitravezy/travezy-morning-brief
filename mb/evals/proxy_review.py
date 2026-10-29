"""A stand-in for the Travezy team's independent reviewer, until their verdicts arrive.

  python -m mb.evals.proxy_review baseline-v5 anthropic/claude-opus-5-5
  python -m mb.evals.proxy_review baseline-v5 openai/gpt-5
  python -m mb.evals.agreement baseline-v5 data/runs/baseline-v5/review/proxy__anthropic__claude-opus-5-5.xlsx

A stronger model reads each of the 20 pack briefs with the facts it was written from
(review/briefs.json) and answers the team's questions: send / fix first / don't send, and
whether anything is wrong or unsafe. Writes a sheet in the format the agreement script reads.
Labelled as a stand-in everywhere: it chooses nothing on its own. Using one stand-in from each
model family shows whether a judge only agrees with reviewers from its own family.
"""
from __future__ import annotations

import asyncio
import json
import sys

from openpyxl import Workbook

from mb.contracts import ReviewerVerdict
from mb.evals.runner import RUNS
from mb.llm import load_prompt, preflight
from mb.services import Services


async def review(run_id: str, model: str, s: Services, concurrency: int = 4) -> list[dict]:
    if model not in s.cfg.models:
        raise SystemExit(f"{model} is not in models.yaml")
    if problems := await preflight(s.llm, {model}):
        raise SystemExit(f"PREFLIGHT FAILED  {model}: {problems[model]}")
    briefs = json.loads((RUNS / run_id / "review" / "briefs.json").read_text(encoding="utf-8"))
    sem = asyncio.Semaphore(concurrency)

    async def one(b: dict) -> dict:
        async with sem:
            try:
                res = await s.llm.call(model=model, system=load_prompt("reviewer-v1"),
                                       user=json.dumps({"inputs": b["inputs"], "brief": b["brief"]}, ensure_ascii=False),
                                       response_model=ReviewerVerdict, run_id=f"proxy:{run_id}:{b['code']}",
                                       agent_id="proxy_reviewer", timeout_s=90)
                v = res.output
                row = {"code": b["code"], "decision": v.decision, "wrong_or_unsafe": "yes" if v.wrong_or_unsafe else "no",
                       "reasons": "; ".join(v.reasons), "cost_usd": round(res.cost_usd, 4)}
            except Exception as e:
                row = {"code": b["code"], "decision": None, "wrong_or_unsafe": None, "reasons": f"ERROR {type(e).__name__}",
                       "cost_usd": 0}
            print(f"  {row['code']}  {row['decision']!s:10} unsafe={row['wrong_or_unsafe']}  {row['reasons'][:90]}")
            return row

    rows = await asyncio.gather(*(one(b) for b in briefs))
    ok = [r for r in rows if r["decision"]]
    wb = Workbook()
    ws = wb.active
    ws.title = "Verdicts"
    ws.append(["code", "decision", "wrong_or_unsafe", "reasons"])
    for r in ok:
        ws.append([r["code"], r["decision"], r["wrong_or_unsafe"], r["reasons"]])
    path = RUNS / run_id / "review" / f"proxy__{model.replace('/', '__')}.xlsx"
    wb.save(path)
    print(f"\n{len(ok)}/{len(rows)} reviewed by {model} (stand-in) -> {path}\ncost ${sum(r['cost_usd'] for r in rows):.3f}"
          f"\n{s.budget.status()}")
    return rows


if __name__ == "__main__":
    asyncio.run(review(sys.argv[1], sys.argv[2], Services()))
