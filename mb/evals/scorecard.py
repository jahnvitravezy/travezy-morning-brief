"""The scorecard: every run and every judge, side by side.

  python -m mb.evals.scorecard            -> data/runs/scorecard.html (+ a table in the terminal)

Part 1, agent models: one row per run. The comparison metric is the FIRST judge score (before any
fix), from the same judge on the same 22 contexts, so a difference comes from the model being
tried, not from the fix loop. Also: badges, fixes, fallbacks, rule pass rate, latency, cost.
Part 2, judge models: for each judge, on the same stored briefs: average score, how much its score
moves between repeats of the same brief (consistency), how often its badge stays the same, cost.
"""
from __future__ import annotations

import html
import json
import statistics as st
from collections import defaultdict
from pathlib import Path

from mb.evals.runner import RUNS

ROLES = ("summariser", "surprise_finder", "composer", "judge")


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def agent_rows(runs_dir: Path = RUNS) -> list[dict]:
    rows = []
    for d in sorted(p for p in runs_dir.iterdir() if (p / "summary.json").exists()):
        s = json.loads((d / "summary.json").read_text(encoding="utf-8"))
        if s.get("judged") is None:
            continue                                     # runs before the judge existed
        recs = _jsonl(d / "records.jsonl")
        models = {k.split(".")[1]: v for k, v in s.get("models", {}).items()}
        if not models:                                   # older runs: read the models off the records
            for r in recs:
                models |= {k.split(".")[1]: v for k, v in r["models"].items()}
        rows.append({"run": d.name, **{r: models.get(r, "") for r in ROLES},
                     "first_score": s.get("avg_first_score"), "final_score": s.get("avg_score"),
                     "green": s["badges"]["green"], "amber": s["badges"]["amber"], "red": s["badges"]["red"],
                     "fixes": s["verdicts"]["fix"], "fallbacks": s["fallbacks"],
                     "rules": f"{s['rule_pass_rate']:.0%}", "latency_s": round(s["avg_latency_ms"] / 1000, 1),
                     "cost_per_brief": round(s["total_cost_usd"] / s["days"], 4)})
    return rows


def judge_rows(runs_dir: Path = RUNS) -> list[dict]:
    rows = []
    for f in sorted(runs_dir.glob("*/judged/*.jsonl")):
        recs = [r for r in _jsonl(f) if r["score"] is not None]
        if not recs:
            continue
        by_day = defaultdict(list)
        for r in recs:
            by_day[r["day_id"]].append(r)
        spreads = [max(x["score"] for x in v) - min(x["score"] for x in v) for v in by_day.values() if len(v) > 1]
        same_badge = [len({x["badge"] for x in v}) == 1 for v in by_day.values() if len(v) > 1]
        rows.append({"briefs": f.parent.parent.name, "judge": recs[0]["judge_model"],
                     "repeats": max(r["repeat"] for r in recs), "avg_score": round(st.mean(r["score"] for r in recs), 1),
                     "avg_spread": round(st.mean(spreads), 1) if spreads else None,
                     "max_spread": max(spreads) if spreads else None,
                     "same_badge": f"{sum(same_badge) / len(same_badge):.0%}" if same_badge else None,
                     "green": sum(r["badge"] == "green" for r in recs), "red": sum(r["badge"] == "red" for r in recs),
                     "errors": sum(1 for r in _jsonl(f) if r["score"] is None),
                     "cost_per_judgement": round(st.mean(r["cost_usd"] for r in recs), 5)})
    return rows


def _table(rows: list[dict]) -> str:
    if not rows:
        return "<p>No runs yet.</p>"
    head = "".join(f"<th>{html.escape(k)}</th>" for k in rows[0])
    body = "".join("<tr>" + "".join(f"<td>{html.escape(str(v if v is not None else '-'))}</td>" for v in r.values())
                   + "</tr>" for r in rows)
    return f"<table><tr>{head}</tr>{body}</table>"


def _print(rows: list[dict]) -> None:
    if not rows:
        return
    keys = list(rows[0])
    w = {k: max(len(k), *(len(str(r[k])) for r in rows)) for k in keys}
    print("  ".join(k.ljust(w[k]) for k in keys))
    for r in rows:
        print("  ".join(str(r[k] if r[k] is not None else "-").ljust(w[k]) for k in keys))


def main():
    a, j = agent_rows(), judge_rows()
    css = ("body{font:14px system-ui;margin:24px}table{border-collapse:collapse;margin:8px 0 24px}"
           "td,th{border:1px solid #ddd;padding:4px 8px;text-align:left}th{background:#1F3864;color:#fff}")
    page = (f"<!doctype html><meta charset=utf-8><title>Morning Brief scorecard</title><style>{css}</style>"
            "<h1>Morning Brief scorecard</h1><h2>Agent models</h2>"
            "<p>first_score: the judge's score before any fix, same judge and same 22 contexts for every run.</p>"
            f"{_table(a)}<h2>Judge models</h2><p>Same stored briefs for every judge. avg_spread: how far a "
            "judge's score moves between repeats of the same brief (lower is more consistent).</p>" + _table(j))
    out = RUNS / "scorecard.html"
    out.write_text(page, encoding="utf-8")
    print("AGENT MODELS"); _print(a); print("\nJUDGE MODELS"); _print(j); print(f"\n-> {out}")


if __name__ == "__main__":
    main()
