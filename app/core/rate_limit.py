import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from functools import lru_cache

from app.config import get_settings

UNIT_SECONDS = {"second": 1, "minute": 60, "hour": 3600}
PRUNE_EVERY = 1000


def parse_rate(spec: str) -> tuple[int, int]:
    """Parse '10/minute' into (10, 60)."""
    count, _, unit = spec.partition("/")
    return int(count.strip()), UNIT_SECONDS[unit.strip().lower()]


class SlidingWindowLimiter:
    """In-memory sliding-window limiter. Single-process only (documented limitation)."""

    def __init__(self, limit: int, window_seconds: int, clock: Callable[[], float] = time.monotonic):
        self.limit = limit
        self.window = window_seconds
        self._clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        self._calls = 0

    def hit(self, key: str) -> float | None:
        """Record a request. Returns None if allowed, else seconds until a slot frees up."""
        now = self._clock()
        with self._lock:
            self._calls += 1
            if self._calls % PRUNE_EVERY == 0:
                self._prune(now)
            window = self._hits[key]
            while window and window[0] <= now - self.window:
                window.popleft()
            if len(window) >= self.limit:
                return max(window[0] + self.window - now, 0.0)
            window.append(now)
            return None

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()

    def _prune(self, now: float) -> None:
        stale = [key for key, window in self._hits.items() if not window or window[-1] <= now - self.window]
        for key in stale:
            del self._hits[key]


@lru_cache
def ip_limiter() -> SlidingWindowLimiter:
    return SlidingWindowLimiter(*parse_rate(get_settings().rate_limit_per_ip))


@lru_cache
def widget_limiter() -> SlidingWindowLimiter:
    return SlidingWindowLimiter(*parse_rate(get_settings().rate_limit_per_widget))


def reset_all_limiters() -> None:
    ip_limiter().reset()
    widget_limiter().reset()