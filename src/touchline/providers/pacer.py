"""The service's share of the model deployment's request rate.

The deployment this project runs on allows a fixed number of requests a
minute -- ten, as it happens, which is the number that matters here. A
request over that limit comes back as a 429, and a 429 that cannot be
retried in time becomes a **fallback**: the template engine writes the
card instead of the model, and the studio says so on the badge.

That is the right behaviour when the model is down. It is the wrong
behaviour when the service simply asked for too much too quickly: the
model was there, the service did not wait its turn, and the demo shows
an amber "fallback" chip for a reason that has nothing to do with the
model being broken. A reader change rewrites the whole feed -- one call
per insight, forty-odd of them -- and the analysis is asking for the
same deployment's attention at the same time. The two together ask for
more than the deployment gives.

So the service waits its turn. **One pacer, shared by everything that
talks to the model** -- the analysis, the rewrites, whatever else -- so
the limit is kept whatever the service is doing, and the feed is served
by the model rather than by the fallback.

The limit is configurable (``TOUCHLINE_MODEL_RPM``) because it belongs
to the deployment, not to the code, and a deployment with a different
quota should not need a code change to be served properly.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import deque

logger = logging.getLogger(__name__)

__all__ = ["CallPacer", "pacer", "requests_per_minute"]

#: How many requests a minute the service asks for by default. The
#: deployment allows ten; eight leaves room for the retry of a call that
#: was throttled anyway.
DEFAULT_REQUESTS_PER_MINUTE = 8

#: The window the limit is counted over. It matches the deployment's own
#: window, so the service's arithmetic and the deployment's arithmetic
#: are the same arithmetic.
WINDOW_SECONDS = 60.0


def requests_per_minute() -> int:
    """How many requests a minute the service may make.

    Read from the environment so that a deployment with a different quota
    can be served without a code change.
    """
    raw = os.environ.get("TOUCHLINE_MODEL_RPM", "").strip()
    if not raw:
        return DEFAULT_REQUESTS_PER_MINUTE
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "TOUCHLINE_MODEL_RPM is not a number (%r); using the default",
            raw,
        )
        return DEFAULT_REQUESTS_PER_MINUTE
    return max(1, value)


class CallPacer:
    """Keeps the service inside the deployment's request rate.

    A sliding window, not a token bucket: the deployment counts the
    requests in the last minute, so the service counts them the same way
    and stays one request short of the limit.
    """

    def __init__(
        self, limit: int = DEFAULT_REQUESTS_PER_MINUTE, window: float = WINDOW_SECONDS
    ) -> None:
        """Create the pacer.

        Args:
            limit: How many requests are allowed in the window.
            window: How long the window is, in seconds.
        """
        self._limit = max(1, int(limit))
        self._window = float(window)
        self._calls: deque[float] = deque()
        self._lock = asyncio.Lock()
        self._held = 0

    @property
    def limit(self) -> int:
        """How many requests are allowed in the window."""
        return self._limit

    @property
    def held(self) -> int:
        """How many calls are waiting for their turn."""
        return self._held

    async def acquire(self) -> None:
        """Wait for the service's turn to make a request.

        Returns as soon as the window has room. A caller that is turned
        away waits until the oldest request in the window has expired,
        which is exactly how long the deployment needs.
        """
        while True:
            async with self._lock:
                now = time.monotonic()
                while self._calls and now - self._calls[0] >= self._window:
                    self._calls.popleft()
                if len(self._calls) < self._limit:
                    self._calls.append(now)
                    self._held = max(0, self._held - 1)
                    return
                wait = self._window - (now - self._calls[0])
                self._held += 1
            await asyncio.sleep(max(0.05, wait))

    def reset(self) -> None:
        """Forget the requests in the window. Used by the tests."""
        self._calls.clear()
        self._held = 0


#: The one pacer for the whole process. Everything that talks to the
#: model goes through it.
pacer = CallPacer(limit=requests_per_minute())
