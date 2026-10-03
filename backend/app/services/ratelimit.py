"""A small in-memory limiter for failed logins (single process, so no shared store needed)."""

import time
from collections import defaultdict, deque
from collections.abc import Callable


class FailureLimiter:
    """Allows `limit` failures per key within `window` seconds."""

    def __init__(
        self, limit: int = 10, window: float = 600, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.limit = limit
        self.window = window
        self._clock = clock
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    def _prune(self, key: str) -> deque[float]:
        failures = self._failures[key]
        cutoff = self._clock() - self.window
        while failures and failures[0] <= cutoff:
            failures.popleft()
        if not failures:
            self._failures.pop(key, None)
        return failures

    def retry_after(self, key: str) -> int:
        """Seconds until another attempt is allowed; 0 if the key isn't blocked."""
        failures = self._prune(key)
        if len(failures) < self.limit:
            return 0
        return max(1, int(failures[0] + self.window - self._clock()) + 1)

    def record_failure(self, key: str) -> None:
        self._prune(key)
        self._failures[key].append(self._clock())

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)
