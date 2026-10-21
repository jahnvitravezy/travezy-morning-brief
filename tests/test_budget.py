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
    with pytest.raises(BudgetExceeded, match="Check with Sourabh"):
        budget.check(2.0)                   # would go over


def test_unknown_sku_is_an_error_not_free(budget):
    with pytest.raises(ValueError, match="No price"):
        budget.record_call(run_id="r1", sku="mystery_api")


def test_warns_at_80_percent(budget):
    budget.record(run_id="r1", kind="llm", item="x", cost_usd=8.5)
    assert budget.status()["warning"] is True