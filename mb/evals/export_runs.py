"""Every evaluation run as one table, for the Brief 1 insight tool.

Reads data/runs/<run>/records.jsonl and summary.json for every run and writes
data/runs/brief_runs.csv: one row per brief per run, with its scores, verdict,
response time, tokens and cost, and which model the run was testing.

Brief 1 loads this file into analytics.brief_runs (scripts/load_morning_brief.py
in that repo), so questions like "Which model failed most often on arrival
days?" can be asked in plain English.

    python -m mb.evals.export_runs            # writes data/runs/brief_runs.csv
"""
import csv
import json
import sys
from pathlib import Path

RUNS = Path("data/runs")
OUT = RUNS / "brief_runs.csv"
WRITERS = ("summariser", "surprise_finder", "composer")

# Runs whose numbers don't measure the model. Kept in the table, marked invalid,
# and left out of the view the insight tool reads (see docs/build_log.md, 27 Oct).
INVALID_RUNS = {
    "cmp-summariser-gemini": "Gemini free-tier quota: its calls failed, so the model was never tested",
    "cmp-composer-gemini": "Gemini free-tier quota: its calls failed, so the model was never tested",
    "cmp-surprise_finder-gemini": "Gemini free-tier quota: its calls failed, so the model was never tested",
}

COLUMNS = [
    "run_id", "valid_run", "day_id", "trip_id", "region", "brief_date", "day_kind", "day_types",
    "model_tested", "role_tested",
    "summariser_model", "surprise_finder_model", "composer_model", "judge_model",
    "first_score", "final_score", "badge", "verdict",
    "fallback", "agent_failures", "rule_failures", "serious_errors", "failed",
    "latency_ms", "tokens_total", "tokens_summariser", "tokens_surprise_finder", "tokens_composer",
    "cost_usd",
]


def _by_role(per_agent: dict) -> dict:
    """{'mb.summariser.v1': x} -> {'summariser': x}."""
    return {k.split(".")[1]: v for k, v in (per_agent or {}).items() if k.count(".") >= 2}


def model_tested(writers: dict, overrides: dict) -> tuple[str, str]:
    """Which model this run was testing, and in which role.

    A comparison run swaps one role (overrides), so that model is the one under
    test. A run with no swap tests whatever writes every part, when all three
    writers share one model; otherwise the mix is named as it is.
    """
    if overrides:
        models = set(overrides.values())
        if len(overrides) == 1:
            return next(iter(overrides.items()))[::-1]
        if len(models) == 1:
            return models.pop(), "all writers" if set(overrides) >= set(WRITERS) else "several"
        return " + ".join(sorted(models)), "several"
    models = {writers.get(r) for r in WRITERS} - {None}
    if len(models) == 1:
        return models.pop(), "all writers"
    return " + ".join(sorted(models)) or "unknown", "mixed"


def rows_for_run(run_dir: Path) -> list[dict]:
    recs = run_dir / "records.jsonl"
    if not recs.exists():
        return []
    summary = {}
    if (run_dir / "summary.json").exists():
        summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    run_models = _by_role(summary.get("models", {}))
    overrides = summary.get("overrides") or {}
    out = []
    for line in recs.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        writers = run_models | _by_role(r.get("models"))
        judge = run_models.get("judge")
        tested, role = model_tested(writers, overrides)
        tokens = _by_role(r.get("tokens"))
        covers = r.get("covers") or []
        # Older records have no agent_failures field: read it from the events, as the runner does.
        fails = r.get("agent_failures")
        if fails is None:
            fails = [e for e in r.get("events") or [] if "failed" in e and "attempt" not in e]
        agent_fail = len(fails)
        rule_fail = len(r.get("rule_failures") or [])
        fallback = bool(r.get("fallback")) or r.get("verdict") == "fallback"
        out.append({
            "run_id": r["run_id"], "valid_run": run_dir.name not in INVALID_RUNS, "day_id": r["day_id"], "trip_id": r["trip_id"],
            "region": r["trip_id"].split("_")[0].capitalize(),
            "brief_date": r["date"],
            # The test set lists the kind of day first (arrival, sightseeing,
            # departure, free_day); the rest are the cases the day also covers.
            "day_kind": covers[0] if covers else "unknown",
            "day_types": ",".join(covers),
            "model_tested": tested, "role_tested": role,
            "summariser_model": writers.get("summariser"),
            "surprise_finder_model": writers.get("surprise_finder"),
            "composer_model": writers.get("composer"),
            # Older runs were not judged; their judge columns stay empty.
            "judge_model": judge if r.get("verdict") else None,
            "first_score": r.get("first_score"), "final_score": r.get("score"),
            "badge": r.get("badge"), "verdict": r.get("verdict") or "not judged",
            "fallback": fallback, "agent_failures": agent_fail, "rule_failures": rule_fail,
            "serious_errors": len(r.get("serious") or []),
            # Failed: the traveller got the safe version, an agent failed, or a rule check failed.
            "failed": fallback or agent_fail > 0 or rule_fail > 0,
            "latency_ms": r.get("latency_ms"),
            "tokens_total": sum(tokens.values()),
            "tokens_summariser": tokens.get("summariser"),
            "tokens_surprise_finder": tokens.get("surprise_finder"),
            "tokens_composer": tokens.get("composer"),
            "cost_usd": r.get("cost_usd"),
        })
    return out


def export(runs: Path = RUNS, out: Path | None = None) -> tuple[Path, int, int]:
    out = out or runs / "brief_runs.csv"
    rows, n_runs = [], 0
    # Folders starting with "_" are scratch copies, not runs.
    for d in sorted(p for p in runs.iterdir() if p.is_dir() and not p.name.startswith("_")):
        got = rows_for_run(d)
        n_runs += bool(got)
        rows += got
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for row in rows:
            w.writerow({k: ("" if v is None else v) for k, v in row.items()})
    return out, n_runs, len(rows)


if __name__ == "__main__":
    path, n_runs, n_rows = export(Path(sys.argv[1]) if len(sys.argv) > 1 else RUNS)
    print(f"{n_rows} briefs from {n_runs} runs -> {path}")
