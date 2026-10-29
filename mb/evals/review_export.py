"""Everything the Travezy team asked for, for their independent review of the 20 pack briefs.

  python -m mb.evals.review_export baseline-v5

Writes data/runs/<run>/review/:
  briefs.html          the 20 briefs beside their inputs (as sent before), no model names
  briefs.json          the same 20, by code (B01..B20): the inputs the brief was written from and
                       the brief itself, with model names, scores and badges removed
  judge_scores.xlsx    every judge's verdict on every brief, by code: score, badge,
                       recommendation (send / fix first / don't send), serious errors, criteria
  cost_and_time.md     cost per brief in rupees, time per brief, monthly cost at 50 and 100 a day
The private code -> test day key stays in pack_key.json and is not part of the export.
"""
from __future__ import annotations

import json
import shutil
import statistics as st
import sys
from collections import Counter, defaultdict

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

from mb.agents.summariser import brief_input
from mb.judge.judge import candidate_facts
from mb.contracts import Context, MorningBrief
from mb.evals.cost_report import report as cost_report
from mb.evals.runner import CONTEXTS, RUNS, context_index
from mb.evals.testset import load_testset

HIDDEN = {"meta", "confidence", "brief_id"}


def _jsonl(path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def judge_table(run_id: str) -> dict[str, dict[str, dict]]:
    """judge model -> day_id -> verdict (repeats merged: mean score, majority recommendation)."""
    out: dict[str, dict[str, dict]] = {}
    for f in sorted((RUNS / run_id / "judged").glob("*.jsonl")):
        rows_all = _jsonl(f)
        if not rows_all or any(r.get("score") is None for r in rows_all):
            continue                    # a judge with failed judgements (quota, errors) isn't comparable
        by_day = defaultdict(list)
        for r in rows_all:
            by_day[r["day_id"]].append(r)
        model = next(iter(by_day.values()))[0]["judge_model"]
        out[model] = {}
        for day, rows in by_day.items():
            recs = [r.get("recommendation") for r in rows if r.get("recommendation")]
            out[model][day] = {
                "score": round(st.mean(r["score"] for r in rows)),
                "badge": Counter(r["badge"] for r in rows).most_common(1)[0][0],
                "recommendation": Counter(recs).most_common(1)[0][0] if recs else None,
                "serious": sorted({s for r in rows for s in r.get("serious") or []}),
                "criteria": {k: round(st.mean(r["criteria"][k] for r in rows)) for k in rows[0]["criteria"]},
                "repeats": len(rows), "prompt": rows[0].get("prompt")}
    return out


def main(run_id: str):
    run_dir = RUNS / run_id
    key = json.loads((run_dir / "pack_key.json").read_text(encoding="utf-8"))["codes"]
    index = json.loads(context_index(load_testset()).read_text(encoding="utf-8"))
    out = run_dir / "review"
    out.mkdir(exist_ok=True)
    shutil.copy(run_dir / "pack" / "briefs.html", out / "briefs.html")

    briefs = []
    for code, k in key.items():
        ctx = Context.model_validate_json((CONTEXTS / f"{index[k['day_id']]}.json").read_text(encoding="utf-8"))
        b = MorningBrief.model_validate_json((run_dir / "briefs" / f"{k['day_id']}.json").read_text(encoding="utf-8"))
        briefs.append({"code": code, "inputs": json.loads(brief_input(ctx)) | {
                           "free_windows": [f"{w.start:%H:%M}-{w.end:%H:%M}" for w in ctx.free_windows],
                           "candidates_for_ideas": candidate_facts(ctx)},
                       "brief": b.model_dump(mode="json", exclude=HIDDEN)})
    (out / "briefs.json").write_text(json.dumps(briefs, indent=1, ensure_ascii=False), encoding="utf-8")

    judges = judge_table(run_id)
    wb = Workbook()
    ws = wb.active
    ws.title = "Verdicts"
    head = ["code"] + [f"{m} | {c}" for m in judges for c in ("score", "badge", "recommendation", "serious errors")]
    ws.append(head)
    for code, k in key.items():
        row = [code]
        for m, days in judges.items():
            v = days.get(k["day_id"], {})
            row += [v.get("score"), v.get("badge"), v.get("recommendation"), "; ".join(v.get("serious", []))]
        ws.append(row)
    crit = wb.create_sheet("Criteria")
    names = next(iter(next(iter(judges.values())).values()))["criteria"] if judges else {}
    crit.append(["code"] + [f"{m} | {c}" for m in judges for c in names])
    for code, k in key.items():
        crit.append([code] + [judges[m].get(k["day_id"], {}).get("criteria", {}).get(c) for m in judges for c in names])
    about = wb.create_sheet("How to read")
    for line in ["Scores: the judge's rubric score out of 100 (100 nothing to fix, 80 small slip, 60 one real problem,",
                 "40 several problems or misleading, 20 wrong). Not an accuracy percentage.",
                 "recommendation: send (score >= 85), fix_first (60-84), dont_send (below 60, a rule failure, or a",
                 "serious error). 85 and 60 are placeholders, to be set once the judge is chosen.",
                 "serious errors: anything that makes the brief wrong or unsafe; any one fails the brief.",
                 f"Judge prompts: {', '.join(sorted({d['prompt'] or '?' for j in judges.values() for d in j.values()}))}.",
                 "Each judge scored each brief " + ", ".join(f"{m}: {next(iter(d.values()))['repeats']}x" for m, d in judges.items())
                 + "; repeats are averaged (score) and majority-voted (badge, recommendation)."]:
        about.append([line])
    for sheet in (ws, crit):
        for c in sheet[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="1F3864")
        sheet.freeze_panes = "B2"
    wb.save(out / "judge_scores.xlsx")

    (out / "cost_and_time.md").write_text(cost_report(), encoding="utf-8-sig")   # BOM: ₹ shows in Windows tools
    print(f"{len(briefs)} briefs, {len(judges)} judges -> {out}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "baseline-v5")
