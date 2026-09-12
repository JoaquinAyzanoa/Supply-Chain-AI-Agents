"""Tests for sc_core.llm.budget."""

import pytest

from sc_core.llm.budget import RunBudget, current_budget, get_budget
from sc_core.shared.errors import BudgetExceeded


def test_records_and_checks() -> None:
    budget = RunBudget(max_input_tokens=100, max_output_tokens=50, max_usd=1.0)
    budget.check()
    budget.record(input_tokens=60, output_tokens=10, usd=0.2)
    budget.check()
    budget.record(input_tokens=60, output_tokens=10, usd=0.2)
    with pytest.raises(BudgetExceeded) as exc:
        budget.check()
    assert exc.value.details == {"used": 120, "limit": 100}
    assert budget.snapshot() == {"calls": 2, "input_tokens": 120, "output_tokens": 20, "usd": 0.4}


def test_cost_limit() -> None:
    budget = RunBudget(max_input_tokens=10**9, max_output_tokens=10**9, max_usd=0.5)
    budget.record(input_tokens=1, output_tokens=1, usd=0.6)
    with pytest.raises(BudgetExceeded, match="cost"):
        budget.check()


def test_context_variable() -> None:
    assert get_budget() is None
    budget = RunBudget(1, 1, 1.0)
    token = current_budget.set(budget)
    try:
        assert get_budget() is budget
    finally:
        current_budget.reset(token)
    assert get_budget() is None
