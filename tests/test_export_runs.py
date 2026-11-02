import csv
import json

from mb.evals.export_runs import COLUMNS, export, model_tested

M = {"mb.summariser.v1": "openai/gpt-4o", "mb.surprise_finder.v1": "openai/gpt-4o",
     "mb.composer.v1": "openai/gpt-4o"}


def _rec(**kw):
    r = {"run_id": "r", "day_id": "td.01", "trip_id": "dubai_up01", "date": "2026-11-20",
         "covers": ["arrival", "diet"], "models": M, "latency_ms": 9000,
         "tokens": {"mb.summariser.v1": 100, "mb.surprise_finder.v1": 50, "mb.composer.v1": 25},
         "cost_usd": 0.013, "fallback": False, "rule_failures": [], "agent_failures": [],
         "verdict": "pass", "score": 90, "first_score": 88, "badge": "green", "serious": []}
    return r | kw


def _run(tmp_path, name, records, summary):
    d = tmp_path / name
    d.mkdir()
    (d / "records.jsonl").write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    (d / "summary.json").write_text(json.dumps(summary), encoding="utf-8")


def test_model_tested():
    w = {"summariser": "a", "surprise_finder": "a", "composer": "a"}
    assert model_tested(w, {}) == ("a", "all writers")
    assert model_tested(w, {"surprise_finder": "b"}) == ("b", "surprise_finder")
    assert model_tested(w | {"composer": "c"}, {}) == ("a + c", "mixed")
    assert model_tested(w, {r: "s" for r in w}) == ("s", "all writers")


def test_export_one_row_per_brief(tmp_path):
    judge = M | {"mb.judge.v1": "openai/gpt-4o-mini"}
    _run(tmp_path, "base", [_rec(), _rec(day_id="td.06", covers=["departure"], fallback=True,
                                         verdict="fallback", badge=None, score=None)],
         {"models": judge, "overrides": {}})
    swapped = M | {"mb.composer.v1": "anthropic/claude-haiku-5-5"}
    _run(tmp_path, "cmp", [_rec(run_id="cmp", models=swapped, rule_failures=["too long"])],
         {"models": swapped | {"mb.judge.v1": "openai/gpt-4o-mini"},
          "overrides": {"composer": "anthropic/claude-haiku-5-5"}})
    _run(tmp_path, "_scratch", [_rec()], {})
    path, n_runs, n_rows = export(tmp_path)
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    assert (n_runs, n_rows) == (2, 3) and list(rows[0]) == COLUMNS
    base, dep, cmp = rows
    assert base["day_kind"] == "arrival" and base["region"] == "Dubai" and base["failed"] == "False"
    assert base["tokens_total"] == "175" and base["judge_model"] == "openai/gpt-4o-mini"
    assert dep["day_kind"] == "departure" and dep["failed"] == "True" and dep["final_score"] == ""
    assert cmp["model_tested"] == "anthropic/claude-haiku-5-5" and cmp["role_tested"] == "composer"
    assert cmp["failed"] == "True" and cmp["rule_failures"] == "1"


def test_old_unjudged_runs_still_export(tmp_path):
    old = _rec()
    for k in ("verdict", "score", "first_score", "badge", "serious", "agent_failures"):
        old.pop(k)
    _run(tmp_path, "old", [old], {})
    rows = list(csv.DictReader(export(tmp_path)[0].open(encoding="utf-8")))
    assert rows[0]["verdict"] == "not judged" and rows[0]["judge_model"] == ""
    assert rows[0]["model_tested"] == "openai/gpt-4o"


def test_agent_failures_read_from_events_and_invalid_runs_marked(tmp_path):
    old = _rec(run_id="cmp-composer-gemini",
               events=["mb.composer.v1: failed (InstructorRetryException) -> code-only merge"])
    old.pop("agent_failures")
    _run(tmp_path, "cmp-composer-gemini", [old], {"overrides": {"composer": "gemini/x"}})
    row = list(csv.DictReader(export(tmp_path)[0].open(encoding="utf-8")))[0]
    assert row["agent_failures"] == "1" and row["failed"] == "True"
    assert row["valid_run"] == "False"
