import pytest

from mb.budget import Budget, BudgetExceeded


@pytest.fixture
def budget(tmp_path):
    return Budget(limit_usd=10.0, prices={"places": 0.5}, ledger=tmp_path / "ledger.jsonl")


def test_starts_empty(budget):
    assert budget.spent_usd() == 0


def test_records_and_sums(budget):
    budget.record(run_id="r1", kind="llm", item="openai/gpt-4o", cost_usd=1.25)
    budget.record_call(run_id="r1", sku="places", n=2)
    assert budget.spent_usd() == pytest.approx(2.25)


def test_blocks_a_run_that_would_exceed(budget):
    budget.record(run_id="r1", kind="llm", item="x", cost_usd=9.0)
    budget.check(0.5)                       # fits
    with pytest.raises(BudgetExceeded, match="monthly_budget_inr"):
        budget.check(2.0)                   # would go over


def test_unknown_sku_is_an_error_not_free(budget):
    with pytest.raises(ValueError, match="No price"):
        budget.record_call(run_id="r1", sku="mystery_api")


def test_warns_at_80_percent(budget):
    budget.record(run_id="r1", kind="llm", item="x", cost_usd=8.5)
    assert budget.status()["warning"] is True

def test_alerts_fire_once_at_50_and_80_percent(budget, caplog):
    budget.record(run_id="r1", kind="llm", item="x", cost_usd=4.0)
    assert budget.alerts == []
    budget.record(run_id="r1", kind="llm", item="x", cost_usd=1.5)     # 55%
    budget.record(run_id="r1", kind="llm", item="x", cost_usd=1.0)     # 65%: no new alert
    budget.record(run_id="r1", kind="llm", item="x", cost_usd=2.0)     # 85%
    assert len(budget.alerts) == 2 and "50%" in budget.alerts[0] and "80%" in budget.alerts[1]
    assert budget.status()["alerts_crossed"] == [50, 80] and not budget.status()["stopped"]


def test_hard_stop_at_100_percent(budget):
    budget.record(run_id="r1", kind="llm", item="x", cost_usd=10.0)
    assert budget.status()["stopped"]
    with pytest.raises(BudgetExceeded):
        budget.check(0.01)


def test_only_this_month_counts(tmp_path):
    import json, time
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(json.dumps({"ts": time.time() - 40 * 86400, "run_id": "old", "kind": "llm",
                                  "item": "x", "cost_usd": 9.0, "units": {}}) + "\n", encoding="utf-8")
    b = Budget(limit_usd=10.0, prices={}, ledger=ledger)
    assert b.spent_usd() == 0


def test_alert_thresholds_come_from_config():
    from mb.config import load_config
    a = load_config().app
    assert a.budget_alert_pct == [50, 80] and a.budget_stop_pct == 100
