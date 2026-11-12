"""In-memory sliding-window rate limits for POST /guesses and POST /hints (PRD T-34).

Kept in the process: enough for a single app instance. With several instances
each one counts separately, so the effective limit grows with the instances.
"""

import time
from collections import deque
from collections.abc import Callable

from fastapi import Request

_MAX_KEYS = 10_000


class RateLimiter:
    def __init__(
        self, limit: int, window_seconds: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.limit = limit
        self.window = window_seconds
        self.clock = clock
        self._hits: dict[str, deque[float]] = {}

    def hit(self, key: str) -> bool:
        """Count one request for `key`. False when the limit was already reached."""
        now = self.clock()
        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] <= now - self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(now)
        if len(self._hits) > _MAX_KEYS:
            self._prune(now)
        return True

    def _prune(self, now: float) -> None:
        stale = [k for k, h in self._hits.items() if not h or h[-1] <= now - self.window]
        for key in stale:
            del self._hits[key]

    def reset(self) -> None:
        self._hits.clear()


# Per user and per IP. A guess "attempt" includes unknown or repeated words,
# which do not use up the daily guess, so they need a limit of their own.
GUESSES_PER_USER = RateLimiter(limit=20, window_seconds=60)
GUESSES_PER_IP = RateLimiter(limit=60, window_seconds=60)
HINTS_PER_USER = RateLimiter(limit=5, window_seconds=60)
HINTS_PER_IP = RateLimiter(limit=30, window_seconds=60)

ALL_LIMITERS = (GUESSES_PER_USER, GUESSES_PER_IP, HINTS_PER_USER, HINTS_PER_IP)


def client_ip(request: Request) -> str:
    # Behind a proxy, run uvicorn with --proxy-headers so this is the real client.
    return request.client.host if request.client else "unknown"


def allow(request: Request, user_id: object, per_user: RateLimiter, per_ip: RateLimiter) -> bool:
    return per_user.hit(str(user_id)) and per_ip.hit(client_ip(request))
