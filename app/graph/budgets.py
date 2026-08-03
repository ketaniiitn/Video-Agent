from datetime import datetime, timezone
from typing import Mapping


class BudgetExceeded(Exception):
    """Raised before work when any configured hard budget cap is exhausted."""


def check_budget(state: Mapping[str, object]) -> None:
    cap_pairs = (
        ("budget_used_usd", "budget_max_usd"),
        ("budget_used_tokens", "budget_max_tokens"),
        ("budget_used_iterations", "budget_max_iterations"),
    )
    for used_key, max_key in cap_pairs:
        if state[used_key] >= state[max_key]:  # type: ignore[operator]
            raise BudgetExceeded(f"{max_key} exhausted")

    started_at = datetime.fromisoformat(str(state["started_at_iso"]))
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
    if elapsed >= float(state["budget_max_wall_clock_seconds"]):
        raise BudgetExceeded("budget_max_wall_clock_seconds exhausted")
