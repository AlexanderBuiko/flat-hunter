"""
Per-IP rate limiter — a fixed-window counter, in memory, stdlib only.

The gateway is meant to be exposed (the Day-5 partner red-teams it over the
network), so a single caller must not be able to drive unbounded cost. This is
deliberately the simplest thing that works: one deque of recent hit timestamps
per client, pruned to the last ``window_s`` seconds. No Redis, no dependency —
a single-process proxy does not need a distributed limiter, and a real
deployment would put one at the load balancer anyway.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    """Allow at most ``max_per_window`` hits per client per ``window_s`` seconds."""

    def __init__(self, max_per_window: int, window_s: float = 60.0) -> None:
        self.max_per_window = max_per_window
        self.window_s = window_s
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, client: str, *, now: float | None = None) -> bool:
        """Record a hit for ``client`` and return whether it is within the limit.

        ``now`` is injectable so tests need no sleeping.
        """
        now = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits[client]
            cutoff = now - self.window_s
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self.max_per_window:
                return False
            hits.append(now)
            return True

    def retry_after(self, client: str, *, now: float | None = None) -> float:
        """Seconds until ``client``'s oldest in-window hit expires (0 if free)."""
        now = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits[client]
            if len(hits) < self.max_per_window or not hits:
                return 0.0
            return max(0.0, hits[0] + self.window_s - now)
