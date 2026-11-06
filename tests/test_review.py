"""Review export, agreement with the reviewer, cost report. No network, no cost."""
import json

import pytest

import mb.evals.agreement as ag
import mb.evals.cost_report as cr
import mb.evals.review_export as rx


def test_weighted_kappa():
    assert ag.weighted_kappa(["send", "fix_first", "dont_send"], ["send", "fix_first", "dont_send"]) == 1.0
    # a near miss costs less than a far miss
    near = ag.weighted_kappa(["send", "send", "fix_first", "dont_send"], ["fix_first", "send", "fix_first", "dont_send"])
    far = ag.weighted_kappa(["send", "send", "fix_first", "dont_send"], ["dont_send", "send", "fix_first", "dont_send"])
    assert 0 < far < near < 1


def test_reviewer_wording_is_understood():
    for text, want in [("Send as is", "send"), ("fix first", "fix_first"), ("Don't send", "dont_send"),
                       ("do not send", "dont_send"), ("FIX_FIRST", "fix_first")]:
        assert ag.norm_decision(text) == want
    with pytest.raises(ValueError):
        ag.norm_decision("maybe")


def _run(tmp_path):
    run = tmp_path / "r"
    (run / "judged").mkdir(parents=True)
    (run / "pack_key.json").write_text(json.dumps({"codes": {"B01": {"day_id": "td.01"}, "B02": {"day_id": "td.02"},
                                                              "B03": {"day_id": "td.03"}}}))
    rows = [("td.01", 92, []), ("td.02", 70, []), ("td.03", 90, ["wrong opening time"])]
    (run / "judged" / "j.jsonl").write_text("\n".join(json.dumps(
        {"day_id": d, "repeat": 1, "judge_model": "openai/gpt-4o-mini", "score": s, "badge": "green",
         "recommendation": None, "serious": ser, "serious_dropped": [], "criteria": {"accuracy": s}, "prompt": "judge-v3"}) for d, s, ser in rows))
    return run


def test_agreement_with_reviewer(tmp_path, monkeypatch):
    monkeypatch.setattr(ag, "RUNS", tmp_path)
    monkeypatch.setattr(rx, "RUNS", tmp_path)
    _run(tmp_path)
    reviewer = {"B01": {"decision": "send", "unsafe": False}, "B02": {"decision": "fix_first", "unsafe": False},
                "B03": {"decision": "dont_send", "unsafe": True}}
    row = ag.compare("r", reviewer)[0]
    assert row["agreement"] == "100%" and row["kappa"] == 1.0
    assert row["unsafe_caught"] == "1/1" and row["false_alarms"] == 0


def test_cost_report_measures_models_and_data(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    (runs / "x").mkdir(parents=True)
    (runs / "x" / "records.jsonl").write_text("\n".join(json.dumps({"cost_usd": c, "latency_ms": l})
                                                         for c, l in [(0.01, 8000), (0.02, 12000)]))
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("\n".join(json.dumps(r) for r in [
        {"run_id": "ctx-testset", "kind": "api", "item": "google_places_text_search", "cost_usd": 2.2},
        {"run_id": "batch-dubai", "kind": "api", "item": "google_places_text_search", "cost_usd": 0.1},
        {"run_id": "batch-ctx_ab", "kind": "llm", "item": "mb.summariser.v1:openai/gpt-4o", "cost_usd": 0.01}]))
    m = cr.measure("x", ledger, runs)
    assert m["model_usd"] == pytest.approx(0.015) and m["data_cold_usd"] == pytest.approx(0.1)   # 2.2 / 22 days
    assert m["data_batch_usd"] == pytest.approx(0.1) and m["p50_s"] == 12.0


def test_a_judge_with_failed_judgements_is_left_out(tmp_path, monkeypatch):
    monkeypatch.setattr(rx, "RUNS", tmp_path)
    run = _run(tmp_path)
    (run / "judged" / "g.jsonl").write_text("\n".join(json.dumps(r) for r in [
        {"day_id": "td.01", "repeat": 1, "judge_model": "gemini/x", "score": 90, "badge": "green", "criteria": {}},
        {"day_id": "td.02", "repeat": 1, "judge_model": "gemini/x", "score": None, "badge": None, "criteria": {}}]))
    assert set(rx.judge_table("r")) == {"openai/gpt-4o-mini"}


def test_judge_and_reviewer_see_what_we_verified_about_each_idea():
    from mb.context import build_context
    from mb.contracts import Candidate
    from mb.judge.judge import candidate_facts
    from mb.sources.itinerary import FileLoader
    c = build_context(FileLoader().load("dubai_up01"), 1)
    w = c.free_windows[0]
    c = c.model_copy(update={"candidates": [Candidate(id="places:p", provider="google_places", name="Shri Krishna Temple",
                                                       window=w, rating=4.7, travel_min=32, types=["hindu_temple"])]})
    f = candidate_facts(c)[0]
    assert f["rating"] == 4.7 and f["travel_min"] == 32 and f["types"] == ["hindu_temple"]


def test_reviewer_contract_and_prompt():
    from mb.config import load_config
    from mb.contracts import ReviewerVerdict
    from mb.llm import load_prompt
    v = ReviewerVerdict(decision="fix_first", wrong_or_unsafe=False, reasons=["Heads-up 2 is vague"])
    assert v.decision == "fix_first"
    assert "independent" in load_prompt("reviewer-v1")
    assert "anthropic/claude-opus-5-5" in load_config().models


def test_two_reviewers_agreement():
    a = {"B01": {"decision": "send", "unsafe": False}, "B02": {"decision": "fix_first", "unsafe": True},
         "B03": {"decision": "send", "unsafe": False}}
    b = {"B01": {"decision": "send", "unsafe": False}, "B02": {"decision": "fix_first", "unsafe": False},
         "B03": {"decision": "fix_first", "unsafe": True}}
    r = ag.between(a, b)
    assert r["briefs"] == 3 and r["agreement"] == "67%"
    assert r["unsafe_only_a"] == ["B02"] and r["unsafe_only_b"] == ["B03"]
    assert r["decision_splits"] == ["B03: send vs fix_first"]


def test_reviewer_scores_are_found_on_any_sheet(tmp_path):
    from openpyxl import Workbook
    wb = Workbook()
    wb.active.title = "How to score"
    wb.active.append(["Morning Brief scoring sheet"])
    ws = wb.create_sheet("Scores")
    ws.append(["code", "accuracy", "decision", "wrong_or_unsafe"])
    ws.append(["B01", 5, "send", "no"])
    ws.append(["B18", 2, "dont_send", "yes"])
    wb.save(tmp_path / "t.xlsx")
    got = ag.read_reviewer(tmp_path / "t.xlsx")
    assert got["B01"] == {"decision": "send", "unsafe": False}
    assert got["B18"]["unsafe"] is True and got["B18"]["decision"] != "send"
