"""The chat-client seam: the one place an agent may reach a model.

An agent in this project is an Agent Framework ``Agent``, and an Agent
Framework ``Agent`` talks to a *chat client* -- an object with a
``get_response`` method. This module supplies the clients, and there are
only ever two of them:

``TemplateChatClient``
    No model at all. It reads the payload out of the prompt and renders the
    answer deterministically from the candidate. This is the path the project
    develops against before any Azure credentials exist, and the path that
    keeps the feed running when inference is unavailable.

``FoundryChatClient``
    Microsoft Foundry, with a per-call timeout and an exponential backoff on
    the two failures that are worth retrying -- 429 and 5xx.

On top of those sits ``FallbackChatClient``, which is the same chain the
one-shot provider uses: **Foundry, then Template**. A judge watching the
feed sees a fallback event when the model is down, which is the point.

Every client records which one *actually* served the call, so the badge in
the UI can never claim Foundry produced a response the template engine
produced. See :mod:`touchline.providers.status`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from touchline.models.insights import InsightCandidate
from touchline.providers import status
from touchline.providers.base import ProviderError, ProviderName, ProviderUnavailable
from touchline.providers.payload import (
    TASK_EXPLAIN,
    TASK_NARRATE,
    TASK_PERSONALISE,
    Payload,
    decode_payload,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ClientError",
    "ClientUnavailable",
    "FallbackChatClient",
    "FoundryChatClient",
    "TemplateChatClient",
    "build_client",
    "is_retryable",
    "status_code_of",
]


class ClientError(ProviderError):
    """Raised when a chat client fails to produce an answer."""


class ClientUnavailable(ProviderUnavailable):
    """Raised when a chat client cannot be reached at all."""


def _last_user_text(messages: Any) -> str:
    """The text of the last user message in a conversation."""
    text = ""
    for message in messages or ():
        if getattr(message, "role", "") != "user":
            continue
        content = getattr(message, "text", None)
        if isinstance(content, str) and content.strip():
            text = content
    return text


def _assistant_response(text: str) -> Any:
    """Build a chat response carrying one assistant message."""
    from agent_framework import ChatResponse, Message

    return ChatResponse(messages=[Message(role="assistant", contents=[text])])


class TemplateChatClient:
    """A chat client with no model behind it.

    It reads the payload out of the prompt and answers it from the candidate
    alone. The same candidate always produces the same answer, byte for
    byte, which is what makes the whole pipeline testable without a network.
    """

    #: The provider id. The display label lives in
    #: :data:`touchline.providers.status.TEMPLATE_PROVIDER`.
    name: str = ProviderName.TEMPLATE.value

    def __init__(self, *, record: bool = True) -> None:
        """Create the client.

        Args:
            record: Whether to record the call in the provider status. Tests
                that assert on the status turn this off to stay independent
                of each other.
        """
        self._record = record

    async def get_response(
        self, messages: Any, **kwargs: Any
    ) -> Any:
        """Answer a prompt, without a model.

        Args:
            messages: The conversation so far. The payload is read out of
                the last user message.
            **kwargs: Ignored. The Agent Framework passes options, streaming
                flags and compaction settings; none of them change what the
                template engine produces.

        Returns:
            A chat response whose single message carries the answer.
        """
        prompt = _last_user_text(messages)
        try:
            payload = decode_payload(prompt)
        except ValueError as exc:
            raise ClientError(f"the prompt carries no usable payload: {exc}") from exc

        answer = self._answer(payload)
        if self._record:
            status.record_call(status.TEMPLATE_PROVIDER, live=False)
        return _assistant_response(answer)

    def _answer(self, payload: Payload) -> str:
        """Render the answer for one payload."""
        from touchline.providers import template

        candidate = payload.candidate
        if payload.task == TASK_EXPLAIN:
            reading = template.render_reading(candidate, locale=payload.locale)
            return json.dumps(
                {
                    "candidate_id": candidate.candidate_id,
                    "reading": reading,
                    "metrics": [
                        {
                            "name": metric.name,
                            "value": metric.value,
                            "unit": metric.unit,
                            "side": metric.side.value if metric.side else None,
                        }
                        for metric in candidate.metrics
                    ],
                    "cited_event_ids": list(candidate.cited_event_ids),
                    "baseline": (
                        {
                            "metric_name": candidate.baseline.metric_name,
                            "value": candidate.baseline.value,
                            "unit": candidate.baseline.unit,
                            "side": (
                                candidate.baseline.side.value
                                if candidate.baseline.side
                                else None
                            ),
                            "cited_event_ids": list(
                                candidate.baseline.cited_event_ids
                            ),
                        }
                        if candidate.baseline is not None
                        else None
                    ),
                },
                ensure_ascii=False,
            )

        if payload.task == TASK_PERSONALISE:
            # The body is the interpretation only. The personaliser puts the
            # analyst's facts in front of it and the template engine's
            # closing line after it, so the numbers on screen are never the
            # model's to restate. The reading is rendered in the audience's
            # register, so the no-model path reads like the model path.
            return json.dumps(
                {
                    "title": template.render_title(candidate, locale=payload.locale),
                    "body": template.render_reading(
                        candidate, locale=payload.locale, mode=payload.mode
                    ),
                },
                ensure_ascii=False,
            )

        if payload.task == TASK_NARRATE:
            narration = template.render_narration(
                candidate, locale=payload.locale, mode=payload.mode
            )
            return json.dumps(narration, ensure_ascii=False)

        raise ClientError(f"unknown task {payload.task!r}")


def status_code_of(exc: BaseException) -> int | None:
    """The HTTP status behind an exception, if there is one.

    The Agent Framework's Foundry client is built on the OpenAI transport, so
    a throttling or server error arrives as an SDK exception carrying a
    status code. The code is read by duck-typing rather than by importing
    the SDK, so this module does not care which transport is underneath.
    """
    for attribute in ("status_code", "http_status", "code"):
        value = getattr(exc, attribute, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    if isinstance(value, int):
        return value
    return None


def is_retryable(exc: BaseException) -> bool:
    """Whether an error is worth another attempt.

    429 is the service asking for less load, and 5xx is the service failing.
    Both are transient. Everything else -- a bad request, a missing
    credential, a timeout -- is not, because retrying it only makes the feed
    slower.
    """
    status_code = status_code_of(exc)
    if status_code is None:
        return False
    return status_code == 429 or status_code >= 500


class FoundryChatClient:
    """Microsoft Foundry, with a timeout and an exponential backoff.

    The two settings are not decoration. A live football feed cannot wait
    forever for one overlay, and it cannot afford to give up on a request
    that was merely throttled. So: a call that takes longer than
    ``timeout_seconds`` is abandoned, and a call that comes back 429 or 5xx
    is retried with the wait doubling each time, up to ``max_attempts``
    times in total.
    """

    #: The provider id. The display label lives in
    #: :data:`touchline.providers.status.FOUNDRY_PROVIDER`.
    name: str = ProviderName.FOUNDRY.value

    def __init__(
        self,
        *,
        project_endpoint: str | None = None,
        model: str | None = None,
        credential: Any = None,
        timeout_seconds: float = 90.0,
        max_attempts: int = 3,
        backoff_seconds: float = 0.5,
        inner: Any = None,
    ) -> None:
        """Create the client.

        Args:
            project_endpoint: The Foundry project endpoint.
            model: The **deployment** name, not the model name.
            credential: An Azure credential. Defaults to a
                ``DefaultAzureCredential``.
            timeout_seconds: How long one attempt may take. The Explainer's
                reply is the long one -- it restates forty metrics and fifty
                event ids -- and on the deployed container it can take thirty
                seconds, so the ceiling is set above that. A call that runs
                long is abandoned and the provider chain falls back; the
                analysis keeps going either way.
            max_attempts: How many attempts in total, including the first.
            backoff_seconds: The first wait between attempts. Each further
                wait is twice as long.
            inner: The chat client to wrap. Used by the tests; in production
                this is the Agent Framework's Foundry client.
        """
        from touchline.providers import foundry

        self._project_endpoint = project_endpoint
        self._model = model
        self._credential = credential
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max(1, max_attempts)
        self.backoff_seconds = backoff_seconds
        self._inner = inner
        self._factory = foundry.build_foundry_chat_client

    def _client(self) -> Any:
        """The wrapped client, created once."""
        if self._inner is None:
            self._inner = self._factory(
                project_endpoint=self._project_endpoint,
                model=self._model,
                credential=self._credential,
            )
        return self._inner

    async def get_response(self, messages: Any, **kwargs: Any) -> Any:
        """Send the conversation to Foundry, with a timeout and a backoff.

        Args:
            messages: The conversation so far.
            **kwargs: Passed through to the wrapped client, except for
                ``options``, which is merged with this project's own request
                options -- the reasoning effort and the token cap are what
                keep a call to a second and a half.

        Returns:
            The chat response.

        Raises:
            ClientUnavailable: If Foundry could not be reached at all.
            ClientError: If the call failed for a reason worth no retry.
        """
        from touchline.providers import foundry

        options = dict(kwargs.pop("options", None) or {})
        options.update(foundry.REQUEST_OPTIONS)
        kwargs["options"] = options

        last_error: BaseException | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = await asyncio.wait_for(
                    self._call(self._client(), messages, **kwargs),
                    timeout=self.timeout_seconds,
                )
            except TimeoutError as exc:
                last_error = exc
                logger.warning(
                    "foundry call timed out after %.1fs (attempt %d/%d)",
                    self.timeout_seconds,
                    attempt,
                    self.max_attempts,
                )
                if attempt == self.max_attempts:
                    break
            except Exception as exc:
                last_error = exc
                if not is_retryable(exc) or attempt == self.max_attempts:
                    raise ClientUnavailable(
                        f"Microsoft Foundry could not be reached: {exc}"
                    ) from exc
                logger.warning(
                    "foundry call failed with %s (attempt %d/%d); retrying",
                    status_code_of(exc),
                    attempt,
                    self.max_attempts,
                )
            else:
                status.record_call(status.FOUNDRY_PROVIDER, live=True)
                return response

            await asyncio.sleep(self.backoff_seconds * (2 ** (attempt - 1)))

        raise ClientUnavailable(
            f"Microsoft Foundry did not answer within {self.max_attempts} attempts: "
            f"{last_error}"
        )

    @staticmethod
    async def _call(client: Any, messages: Any, **kwargs: Any) -> Any:
        """Call the wrapped client, dropping the options if it cannot take them.

        The options are what cap the reasoning effort, so they are not
        optional in practice. A client that does not accept them still gets
        a usable answer -- it just pays the default reasoning cost -- so the
        fallback is a retry without the options rather than a failure.
        """
        get_response = getattr(client, "get_response", None)
        if get_response is None:  # pragma: no cover - API drift
            raise ClientError("The Foundry client exposes no get_response.")
        try:
            return await get_response(messages, **kwargs)
        except TypeError:
            kwargs.pop("options", None)
            return await get_response(messages, **kwargs)

    async def aclose(self) -> None:
        """Release the wrapped client's HTTP session."""
        client = self._inner
        self._inner = None
        if client is None:
            return
        for attribute in ("client", "project_client"):
            target = getattr(client, attribute, None)
            close = getattr(target, "close", None)
            if close is None:
                continue
            result = close()
            if hasattr(result, "__await__"):
                await result


class FallbackChatClient:
    """One client, with another behind it.

    This is the provider chain -- **Foundry, then Template** -- in the shape
    the agents use. The primary is tried first; if it fails for any reason
    the fallback answers, and the fallback records itself as a fallback, so
    the badge in the UI shows what actually happened.
    """

    def __init__(self, primary: Any, fallback: Any) -> None:
        """Create the chain.

        Args:
            primary: The client to try first.
            fallback: The client to use when the primary fails.
        """
        self.primary = primary
        self.fallback = fallback

    @property
    def name(self) -> str:
        """The id of the client in front."""
        return getattr(self.primary, "name", "unknown")

    async def get_response(self, messages: Any, **kwargs: Any) -> Any:
        """Try the primary, and fall back if it fails."""
        try:
            return await self.primary.get_response(messages, **kwargs)
        except (TimeoutError, ProviderError, OSError) as exc:
            logger.warning(
                "chat client %s failed (%s); falling back to %s",
                getattr(self.primary, "name", "?"),
                exc,
                getattr(self.fallback, "name", "?"),
            )
            return await self.fallback.get_response(messages, **kwargs)

    async def aclose(self) -> None:
        """Release both clients."""
        for client in (self.primary, self.fallback):
            close = getattr(client, "aclose", None)
            if close is not None:
                await close()


def build_client() -> Any:
    """Return the chat client the chain starts at.

    If Foundry is configured, the chain starts at Foundry and falls back to
    the template engine on failure. If it is not configured, the chain starts
    at the template engine directly -- the build must never block on Azure
    credentials arriving.
    """
    from touchline.providers import foundry

    if foundry.is_available():
        return FallbackChatClient(FoundryChatClient(), TemplateChatClient())
    return TemplateChatClient()


def candidate_payload(
    task: str,
    candidate: InsightCandidate,
    *,
    locale: str = "en",
    mode: str = "analyst",
    reading: str = "",
) -> str:
    """Build the prompt text for one task. A thin wrapper over the payload."""
    from touchline.providers.payload import encode_payload

    return encode_payload(
        task, candidate, locale=locale, mode=mode, reading=reading
    )
