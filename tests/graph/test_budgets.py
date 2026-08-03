from datetime import datetime, timedelta, timezone

import pytest

from app.graph.budgets import BudgetExceeded, check_budget


def _state(**overrides):
    state = {
        "budget_used_usd": 0.0,
        "budget_max_usd": 1.0,
        "budget_used_tokens": 0,
        "budget_max_tokens": 100,
        "budget_used_iterations": 0,
        "budget_max_iterations": 10,
        "started_at_iso": datetime.now(timezone.utc).isoformat(),
        "budget_max_wall_clock_seconds": 600,
    }
    state.update(overrides)
    return state


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("budget_used_usd", 1.0),
        ("budget_used_tokens", 100),
        ("budget_used_iterations", 10),
    ],
)
def test_budget_exceeded_at_hard_cap(field, value):
    with pytest.raises(BudgetExceeded):
        check_budget(_state(**{field: value}))


def test_budget_exceeded_on_wall_clock():
    started_at = datetime.now(timezone.utc) - timedelta(seconds=601)

    with pytest.raises(BudgetExceeded):
        check_budget(_state(started_at_iso=started_at.isoformat()))


def test_budget_allows_usage_below_caps():
    check_budget(_state())
