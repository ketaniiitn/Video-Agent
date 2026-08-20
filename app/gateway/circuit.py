from collections import deque
from time import monotonic

from app.pipeline.constants import CIRCUIT_FAILURE_THRESHOLD, CIRCUIT_WINDOW_SECONDS


class CircuitBreaker:
    """Per-dependency breaker: open after N failures in a sliding window."""

    def __init__(
        self,
        *,
        threshold: int = CIRCUIT_FAILURE_THRESHOLD,
        window_seconds: float = CIRCUIT_WINDOW_SECONDS,
    ):
        self.threshold = threshold
        self.window_seconds = window_seconds
        self._failures: deque[float] = deque()
        self._state = "closed"
        self._opened_at: float | None = None
        self._half_open_probe = False

    @property
    def state(self) -> str:
        return self._state

    def allow(self) -> bool:
        now = monotonic()
        if self._state == "closed":
            return True
        if self._state == "open":
            if self._opened_at is not None and now - self._opened_at >= self.window_seconds:
                self._state = "half_open"
                self._half_open_probe = True
                return True
            return False
        # half_open: one probe at a time
        if self._half_open_probe:
            return False
        self._half_open_probe = True
        return True

    def record_success(self) -> None:
        self._failures.clear()
        self._state = "closed"
        self._opened_at = None
        self._half_open_probe = False

    def record_failure(self) -> None:
        now = monotonic()
        self._failures.append(now)
        cutoff = now - self.window_seconds
        while self._failures and self._failures[0] < cutoff:
            self._failures.popleft()
        self._half_open_probe = False
        if self._state == "half_open" or len(self._failures) >= self.threshold:
            self._state = "open"
            self._opened_at = now
