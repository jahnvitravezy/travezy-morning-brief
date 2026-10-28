"""Self-preference test: does a judge score writing from its own model family higher?

  python -m mb.evals.self_preference cmp-baseline-gpt4o all-sonnet

Each run's briefs were written by one family (all three writing agents from it). Every judge
scores every run's briefs (judge_run, beforehand). For each judge we compare its average on
briefs from its own family with its average on the other family's. A judge can just be lenient
or strict, so the test is the DIFFERENCE between judges: if the OpenAI judges prefer the OpenAI
briefs by much more than the Anthropic judge does, that gap is self-preference.
Briefs that fell back to the safe version (code, not a model) are left out.
"""
from __future__ import annotations

import json
import statistics as st
import sys

from mb.config import load_config
from mb.evals.runner import RUNS


def writer_family(run: str, families: dict[str, str]) -> str:
    models = json.loads((RUNS / run / "summary.json").read_text(encoding="utf-8"))["models"]
    writers = {families[m] for k, m in models.items() if "judge" not in k}
    if len(writers) != 1:
        raise ValueError(f"{run}: writers come from several families {writers}; use a single-family run")
    return writers.pop()


def scores(run: str) -> dict[str, dict[str, float]]:
    """judge model -> {day_id: average score over repeats}, model-written briefs only."""
    recs = [json.loads(l) for l in (RUNS / run / "records.jsonl").read_text(encoding="utf-8").splitlines()]
    model_written = {r["day_id"] for r in recs if r.get("verdict") != "fallback"}
    out = {}
    for f in (RUNS / run / "judged").glob("*.jsonl"):
        rows = [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines()]
        by_day: dict[str, list[int]] = {}
        for r in rows:
            if r["score"] is not None and r["day_id"] in model_written:
                by_day.setdefault(r["day_id"], []).append(r["score"])
        if by_day:
            out[rows[0]["judge_model"]] = {d: st.mean(v) for d, v in by_day.items()}
    return out


def table(runs: list[str]) -> list[dict]:
    fam = {m: s.family for m, s in load_config().models.items()}
    data = {r: (writer_family(r, fam), scores(r)) for r in runs}
    judges = sorted(set.intersection(*(set(s) for _, s in data.values())))
    rows = []
    for j in judges:
        common = set.intersection(*(set(s[j]) for _, s in data.values()))    # same days for both runs
        avg = {wf: round(st.mean(s[j][d] for d in common), 1) for wf, s in data.values()}
        own = avg.get(fam[j])
        other = [v for wf, v in avg.items() if wf != fam[j]]
        rows.append({"judge": j, "judge_family": fam[j], **{f"on_{wf}_briefs": v for wf, v in avg.items()},
                     "own_minus_other": round(own - st.mean(other), 1) if own is not None and other else None,
                     "days": len(common)})
    return rows


def main(runs: list[str]):
    rows = table(runs)
    keys = list(dict.fromkeys(k for r in rows for k in r))
    w = {k: max(len(k), *(len(str(r.get(k, "-"))) for r in rows)) for k in keys}
    print("  ".join(k.ljust(w[k]) for k in keys))
    for r in rows:
        print("  ".join(str(r.get(k, "-")).ljust(w[k]) for k in keys))
    print("\nown_minus_other > 0: the judge prefers its own family's briefs. Compare judges with each other:\n"
          "a gap shared by every judge is about the briefs; a gap only the same-family judges show is self-preference.")
    (RUNS / "self_preference.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1:] or ["cmp-baseline-gpt4o", "all-sonnet"])
