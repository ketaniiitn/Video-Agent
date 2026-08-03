from types import SimpleNamespace

import pytest

from app.domain.schemas import JobStatus
from app.jobs.runner import _OUTCOME_TO_STATUS, _has_checkpoint


def test_outcome_to_status_maps_failed_no_progress():
    """FAILED_NO_PROGRESS is a valid harness terminal outcome
    (.cursor/rules/03-harness-loop-and-termination.mdc) even though no node
    currently emits it; the mapping must exist so a future node that does
    isn't silently swallowed by ``_OUTCOME_TO_STATUS.get(...)`` returning
    ``None`` and leaving the job stuck at RUNNING.
    """
    assert _OUTCOME_TO_STATUS["FAILED_NO_PROGRESS"] is JobStatus.FAILED_NO_PROGRESS


@pytest.mark.asyncio
async def test_has_checkpoint_false_when_no_checkpointer_on_graph():
    graph = SimpleNamespace()
    assert await _has_checkpoint(graph, {"configurable": {"thread_id": "x"}}) is False


@pytest.mark.asyncio
async def test_has_checkpoint_false_when_checkpointer_returns_none():
    class _NoCheckpoint:
        async def aget_tuple(self, config):
            return None

    graph = SimpleNamespace(checkpointer=_NoCheckpoint())
    assert await _has_checkpoint(graph, {"configurable": {"thread_id": "x"}}) is False


@pytest.mark.asyncio
async def test_has_checkpoint_true_when_checkpointer_returns_a_tuple():
    class _HasCheckpoint:
        async def aget_tuple(self, config):
            return object()

    graph = SimpleNamespace(checkpointer=_HasCheckpoint())
    assert await _has_checkpoint(graph, {"configurable": {"thread_id": "x"}}) is True
