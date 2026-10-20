"""The Explainer, called the way the A2A specification intends: over the wire.

This is the other half of :mod:`touchline.orchestration.a2a_server`. The server
publishes the Explainer as an agent; this module *calls* it, as a client, and
speaks the protocol rather than sharing a Python object with it. The point is
not ceremony: an agent that is reachable only by import is not an agent
another team can use.

Three things make the hop survivable, and all three are needed:

**A timeout.** One call may not take the whole feed with it. A broadcast
overlay that arrives four minutes late is worse than one that never arrives,
because the match has moved on.

**A backoff.** A 429 or a 5xx means "not now", not "never". Those two are
retried with the wait doubling each time; a 400 or a 404 is not retried,
because retrying it only makes the feed slower.

**A fallback.** When the hop cannot be made at all, the Explainer is asked
in-process instead. The answer is the *same* answer -- the Explainer
remembers what it has already said -- so the feed keeps running and the
degradation is recorded rather than hidden.

The last point is what makes the fallback safe. A fallback that invented a
second reading would put two different stories on screen for the same
moment, and only one of them would have been grounded.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from touchline.agents.explainer import ExplanationError, parse_explanation
from touchline.models.claims import Explanation
from touchline.models.insights import InsightCandidate
from touchline.providers.clients import is_retryable, status_code_of
from touchline.providers.payload import TASK_EXPLAIN, encode_payload

logger = logging.getLogger(__name__)

__all__ = [
    "TRANSPORT_A2A",
    "TRANSPORT_IN_PROCESS",
    "A2AUnavailable",
    "ExplainerClient",
]

#: The transport that answered the last call.
TRANSPORT_A2A = "a2a"

#: The transport that answered the last call, when the wire could not.
TRANSPORT_IN_PROCESS = "in-process"


class A2AUnavailable(RuntimeError):
    """Raised when the Explainer could not be reached at all."""


class ExplainerClient:
    """Calls the Explainer over A2A, with a timeout, a backoff and a fallback.

    Example:
        >>> client = ExplainerClient("http://localhost:8000/a2a")
        >>> client.last_transport
        'a2a'
    """

    def __init__(
        self,
        url: str,
        *,
        timeout_seconds: float = 20.0,
        max_attempts: int = 3,
        backoff_seconds: float = 0.5,
        fallback: Any = None,
        http_client: Any = None,
        agent: Any = None,
    ) -> None:
        """Create the client.

        Args:
            url: The Explainer's JSON-RPC endpoint.
            timeout_seconds: How long one attempt may take.
            max_attempts: How many attempts in total, including the first.
            backoff_seconds: The first wait between attempts. Each further
                wait is twice as long.
            fallback: An Explainer to ask in-process when the wire cannot be
                used. It is the same agent the server wraps, so its memory is
                shared and the answer is the same one.
            http_client: An ``httpx.AsyncClient`` to use. The tests pass one
                bound to the ASGI application, so the round trip is a real
                protocol exchange without a socket.
            agent: An ``A2AAgent`` to use instead of building one. Used by
                the tests that need to observe the client's behaviour.
        """
        self.url = url
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max(1, max_attempts)
        self.backoff_seconds = backoff_seconds
        self.fallback = fallback
        self._last_transport = TRANSPORT_A2A
        self._degradations: list[str] = []
        self._agent = agent
        self._http_client = http_client

        if self._agent is None:
            from agent_framework.a2a import A2AAgent

            self._agent = A2AAgent(
                name="explainer",
                description="Calls the Explainer over A2A.",
                url=url,
                http_client=http_client,
                timeout=timeout_seconds,
            )

    # -- what happened ----------------------------------------------------

    @property
    def last_transport(self) -> str:
        """Which transport answered the last call."""
        return self._last_transport

    @property
    def degradations(self) -> list[str]:
        """Every time the wire could not be used, and why."""
        return list(self._degradations)

    # -- the call ----------------------------------------------------------

    async def explain(
        self,
        candidate: InsightCandidate,
        *,
        locale: str = "en",
        mode: str = "analyst",
    ) -> Explanation:
        """Ask the Explainer for a reading, over A2A.

        Args:
            candidate: The deterministic, fully-cited insight.
            locale: Language code for the reading.
            mode: Audience mode.

        Returns:
            The reading and the claim it rests on.

        Raises:
            A2AUnavailable: If the hop failed and there is no fallback.
        """
        prompt = encode_payload(TASK_EXPLAIN, candidate, locale=locale, mode=mode)

        last_error: BaseException | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = await asyncio.wait_for(
                    self._agent.run(prompt),
                    timeout=self.timeout_seconds,
                )
                text = getattr(response, "text", "") or ""
                explanation = parse_explanation(
                    text, candidate_id=candidate.candidate_id
                )
            except TimeoutError as exc:
                last_error = exc
                logger.warning(
                    "the A2A call to the Explainer timed out after %.1fs "
                    "(attempt %d/%d)",
                    self.timeout_seconds,
                    attempt,
                    self.max_attempts,
                )
                if attempt == self.max_attempts:
                    break
            except ExplanationError as exc:
                # The Explainer answered, but the answer was not a claim.
                # Retrying the same prompt is unlikely to help, but the hop
                # itself worked, so this is not a transport failure.
                last_error = exc
                logger.warning("the Explainer's A2A reply was unusable: %s", exc)
                break
            except Exception as exc:
                last_error = exc
                if not is_retryable(exc) or attempt == self.max_attempts:
                    break
                logger.warning(
                    "the A2A call to the Explainer failed with %s "
                    "(attempt %d/%d); retrying",
                    status_code_of(exc),
                    attempt,
                    self.max_attempts,
                )
            else:
                self._last_transport = TRANSPORT_A2A
                return explanation

            await asyncio.sleep(self.backoff_seconds * (2 ** (attempt - 1)))

        reason = f"{type(last_error).__name__}: {last_error}"
        self._degradations.append(reason)
        logger.warning(
            "the Explainer could not be reached over A2A (%s); "
            "asking it in-process instead",
            reason,
        )

        if self.fallback is not None:
            self._last_transport = TRANSPORT_IN_PROCESS
            return await self.fallback.explain(
                candidate, locale=locale, mode=mode
            )

        raise A2AUnavailable(
            f"the Explainer could not be reached over A2A at {self.url}: {reason}"
        )

    async def aclose(self) -> None:
        """Release the HTTP session, if this client owns one."""
        agent = self._agent
        self._agent = None
        if agent is None:
            return
        close = getattr(agent, "__aexit__", None)
        if close is None:
            return
        await close(None, None, None)
