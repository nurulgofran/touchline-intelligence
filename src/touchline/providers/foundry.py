"""The Microsoft Foundry provider -- the one place a model is called.

Everything else in this project is deterministic Python. The Analyst computes
the numbers; the Explainer and Narrator ask a model for *prose only*. The
citations on a returned overlay are the candidate's citations, carried
forward untouched, so a model cannot talk its way into an unsupported claim
and the grounding executor has something solid to recompute against.

Authentication is keyless on both sides of the deployment: ``DefaultAzureCredential``
resolves the ``az login`` credential on a laptop and the Container App's
system-assigned managed identity in Azure. There is no API key in this path,
and a Foundry project endpoint does not accept one.

The imports of the Azure and Agent Framework packages are lazy, so this module
imports cleanly in an environment that has only ``pydantic`` installed.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from inspect import isawaitable
from typing import Any

from touchline.models.insights import InsightCandidate, InsightKind
from touchline.models.overlay import Overlay, OverlayType
from touchline.providers import status
from touchline.providers.base import (
    NarrativeProvider,
    ProviderError,
    ProviderName,
    ProviderUnavailable,
)

#: How long an overlay stays on screen.
OVERLAY_DURATION_MS = 8_000

#: Which overlay type each insight kind renders as.
_KIND_TO_OVERLAY: dict[InsightKind, OverlayType] = {
    InsightKind.TACTICAL: OverlayType.TACTICAL,
    InsightKind.MOMENTUM_SHIFT: OverlayType.STAT,
    InsightKind.PRESS_BREAK: OverlayType.TACTICAL,
    InsightKind.THREAT: OverlayType.ALERT,
    InsightKind.MILESTONE: OverlayType.STAT,
    InsightKind.CHANCE_CREATED: OverlayType.NARRATIVE,
    InsightKind.SET_PIECE: OverlayType.NARRATIVE,
    InsightKind.DISCIPLINE: OverlayType.ALERT,
}

#: What each audience mode means. The model needs this spelled out; "fan" and
#: "analyst" produce very different sentences from the same numbers.
_MODE_BRIEF: dict[str, str] = {
    "analyst": (
        "a television analyst: precise, tactical, no hedging, short clauses. "
        "Explain what the shape of the play means."
    ),
    "fan": (
        "a supporter watching the match: direct, energetic, one plain "
        "sentence, no jargon. React to what just happened."
    ),
    "player": (
        "a player-centred view: focus on the individual named in the "
        "insight and what it says about their involvement."
    ),
    "my_team": (
        "a supporter of one specific team: write as if this team is the "
        "listener's own, and say whether it is a positive or a negative "
        "development for them."
    ),
    "metric_lens": (
        "a view filtered through one metric: say only what that metric "
        "shows, and name it."
    ),
}

_SYSTEM_PROMPT = (
    "You write short broadcast overlay copy for a football match. "
    "You are given a deterministic insight that has already been computed, "
    "and you turn it into one title and one body of text. "
    "Rules: use only the figures given to you, never invent a number; "
    "never mention ids, data, metrics, code or the words 'insight' or "
    "'JSON'; do not use markdown, quotes or bullet points. "
    "Reply with a JSON object and nothing else: "
    '{"title": "<= 60 characters>", "body": "<= 220 characters"}.'
)

#: Request options for the model call.
#:
#: The Foundry chat client talks to the **Responses API**, not to
#: ``/chat/completions``, and that changes the shape of the options:
#:
#: * ``reasoning_effort`` is not a top-level parameter on this route. It is
#:   ``reasoning={"effort": ...}``. Passing the flat spelling raises
#:   ``AsyncResponses.create() got an unexpected keyword argument
#:   'reasoning_effort'`` before the request is even sent.
#: * ``temperature`` is **rejected** by gpt-5-mini on this route --
#:   ``400 Unsupported parameter: 'temperature'`` -- so it must never be
#:   sent, and no retry should try to add it.
#:
#: "minimal" is not cosmetic. At the default reasoning effort a one-sentence
#: overlay cost 189 output tokens, 128 of them reasoning, and 4.2 seconds.
#: At minimal the same call is 1.8 seconds and the tokens go to the answer.
#: The live feed has to keep up with a football match, so the difference
#: between 4.2 s and 1.8 s per overlay is the difference between a narrated
#: match and a narrated slideshow.
#:
#: The cap is a ceiling, not a budget, and the Explainer needs a lot of
#: room. It asks for a reading *and* the metrics *and* the cited event ids,
#: and a candidate can carry forty metrics and fifty event ids -- copying
#: those accurately is work, and the reasoning it takes counts against the
#: ceiling along with the answer. At 400 the reply was cut off inside the
#: list of event ids; at 4000 it was still cut off, about three thousand
#: tokens into the reasoning and nine hundred into the answer.
#:
#: The ceiling has to clear the reasoning *and* the answer, so it is set
#: well above either. It costs nothing when the reply is short: the model
#: stops when the JSON is finished, not when the ceiling is reached.
_REQUEST_OPTIONS: dict[str, Any] = {
    "reasoning": {"effort": "minimal"},
    "max_output_tokens": 16000,
}

#: The same options under a public name, for the modules that build a chat
#: client rather than calling the provider directly.
REQUEST_OPTIONS: dict[str, Any] = _REQUEST_OPTIONS


def _language_brief(locale: str) -> str:
    """Describe the requested language to the model, by name."""
    names = {
        "en": "English",
        "de": "German",
        "bn": "Bengali",
    }
    return names.get(locale, locale)


def _metric_lines(candidate: InsightCandidate) -> str:
    if not candidate.metrics:
        return "(none)"
    return "\n".join(
        f"- {m.name} = {m.value:g} {m.unit} ({m.label})" for m in candidate.metrics
    )


def _build_prompt(candidate: InsightCandidate, locale: str, mode: str) -> str:
    brief = _MODE_BRIEF.get(mode, _MODE_BRIEF["analyst"])
    language = _language_brief(locale)
    return (
        f"KIND: {candidate.kind.value}\n"
        f"HEADLINE: {candidate.headline}\n"
        f"METRICS:\n{_metric_lines(candidate)}\n"
        f"AUDIENCE MODE: {mode} -- write for {brief}\n"
        f"LANGUAGE: write the title and body in {language}.\n"
        "Return only the JSON object."
    )


def _parse_payload(raw: str) -> tuple[str, str]:
    """Pull ``title`` and ``body`` out of the model's reply.

    The model is asked for JSON, and it usually complies. When it wraps the
    JSON in prose or a code fence, the object is still recovered; if there is
    no object at all, the whole reply is used as the body so that a slightly
    badly-behaved model degrades into plainer copy rather than a hard failure.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    payload: Any = None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if match:
            try:
                payload = json.loads(match.group(0))
            except json.JSONDecodeError:
                payload = None

    if isinstance(payload, dict):
        title = str(payload.get("title", "")).strip()
        body = str(payload.get("body", "")).strip()
        if title and body:
            return title, body
        if body:
            return candidate_fallback_title(payload, body), body

    # No JSON at all. Use the reply as the body, and the deterministic
    # headline as the title. The citations are unaffected either way.
    return "", text


def candidate_fallback_title(payload: dict[str, Any], body: str) -> str:
    """Derive a title when the model returned a body but no usable title."""
    headline = str(payload.get("headline", "")).strip()
    if headline:
        return headline[:60]
    return body[:60].rsplit(" ", 1)[0][:60] if body else "Insight"


def build_foundry_chat_client(
    *,
    project_endpoint: str | None = None,
    model: str | None = None,
    credential: Any = None,
) -> Any:
    """Create the Agent Framework's Foundry chat client.

    This is the one place in the project that knows how to talk to Foundry.
    Both the one-shot provider and the agent-facing chat client go through
    it, so there is a single set of credentials and a single set of request
    options behind both.

    Args:
        project_endpoint: The Foundry project endpoint. Defaults to
            ``FOUNDRY_PROJECT_ENDPOINT``.
        model: The **deployment** name, not the model name. Defaults to
            ``FOUNDRY_MODEL``.
        credential: An Azure credential. Defaults to a
            ``DefaultAzureCredential``, which is keyless both locally and in
            Azure Container Apps.

    Returns:
        The Agent Framework chat client.

    Raises:
        ProviderUnavailable: If the endpoint or the deployment name is
            missing, or the client packages are not installed.
    """
    endpoint = project_endpoint or os.environ.get("FOUNDRY_PROJECT_ENDPOINT", "")
    deployment = model or os.environ.get("FOUNDRY_MODEL", "")
    if not endpoint or not deployment:
        raise ProviderUnavailable(
            "FOUNDRY_PROJECT_ENDPOINT and FOUNDRY_MODEL are not both set."
        )

    try:
        from agent_framework.foundry import FoundryChatClient
    except ImportError:
        try:
            from agent_framework_foundry import FoundryChatClient
        except ImportError as exc:  # pragma: no cover - dependency missing
            raise ProviderUnavailable(
                "agent-framework-foundry is not installed."
            ) from exc

    if credential is None:
        try:
            from azure.identity.aio import DefaultAzureCredential
        except ImportError as exc:  # pragma: no cover - dependency missing
            raise ProviderUnavailable(
                "azure-identity is not installed; the Foundry provider cannot "
                "authenticate."
            ) from exc
        credential = DefaultAzureCredential()

    return FoundryChatClient(
        project_endpoint=endpoint,
        model=deployment,
        credential=credential,
    )


class FoundryProvider:
    """Narrative provider backed by a Microsoft Foundry model deployment.

    The provider is a *narrator*, not an analyst. It receives a fully cited
    :class:`InsightCandidate` and returns an :class:`Overlay` whose
    ``cited_event_ids`` are exactly the candidate's. Every number in the
    output came from the deterministic metrics layer.
    """

    #: The provider id. The display label lives in
    #: :data:`touchline.providers.status.FOUNDRY_PROVIDER` -- the two are
    #: deliberately different so a machine id never reaches the UI.
    name: str = ProviderName.FOUNDRY.value

    def __init__(
        self,
        *,
        project_endpoint: str | None = None,
        model: str | None = None,
        credential: Any = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        """Create the provider.

        Args:
            project_endpoint: The Foundry project endpoint. Defaults to
                ``FOUNDRY_PROJECT_ENDPOINT``.
            model: The **deployment** name, not the model name. Defaults to
                ``FOUNDRY_MODEL``.
            credential: An Azure credential. Defaults to a
                ``DefaultAzureCredential``, which is keyless in both local
                development and Azure Container Apps.
            timeout_seconds: Per-call timeout.
        """
        self.project_endpoint = project_endpoint or os.environ.get(
            "FOUNDRY_PROJECT_ENDPOINT", ""
        )
        self.model = model or os.environ.get("FOUNDRY_MODEL", "")
        self._credential = credential
        self.timeout_seconds = timeout_seconds
        self._client: Any = None

    # -- configuration ----------------------------------------------------

    @property
    def is_configured(self) -> bool:
        """Whether both the endpoint and the deployment name are known."""
        return bool(self.project_endpoint) and bool(self.model)

    def _build_credential(self) -> Any:
        if self._credential is not None:
            return self._credential
        try:
            from azure.identity.aio import DefaultAzureCredential
        except ImportError as exc:  # pragma: no cover - dependency missing
            raise ProviderUnavailable(
                "azure-identity is not installed; the Foundry provider cannot "
                "authenticate."
            ) from exc
        self._credential = DefaultAzureCredential()
        return self._credential

    def _build_client(self) -> Any:
        """Create the Agent Framework chat client, once."""
        if self._client is not None:
            return self._client

        if not self.is_configured:
            raise ProviderUnavailable(
                "FOUNDRY_PROJECT_ENDPOINT and FOUNDRY_MODEL are not both set."
            )

        self._client = build_foundry_chat_client(
            project_endpoint=self.project_endpoint,
            model=self.model,
            credential=self._build_credential(),
        )
        return self._client

    # -- the protocol -----------------------------------------------------

    async def narrate(
        self,
        candidate: InsightCandidate,
        *,
        locale: str = "en",
        mode: str = "analyst",
    ) -> Overlay:
        """Render one candidate into an overlay.

        Args:
            candidate: The deterministic, fully-cited insight.
            locale: Language code, e.g. ``"en"``, ``"de"``, ``"bn"``.
            mode: Audience mode, e.g. ``"analyst"``, ``"fan"``, ``"my_team"``.

        Returns:
            An overlay carrying the candidate's citations.

        Raises:
            ProviderUnavailable: The model could not be reached at all.
            ProviderError: The model was reached but the narrative could not
                be produced.
        """
        client = self._build_client()
        prompt = _build_prompt(candidate, locale, mode)

        try:
            raw = await self._complete(client, prompt)
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderUnavailable(
                f"Microsoft Foundry could not be reached: {exc}"
            ) from exc

        status.record_call(status.FOUNDRY_PROVIDER, live=True)

        title, body = _parse_payload(raw)
        if not body:
            raise ProviderError("The model returned an empty narrative.")

        return Overlay(
            overlay_id=f"ovl-{candidate.candidate_id}",
            type=_KIND_TO_OVERLAY.get(candidate.kind, OverlayType.NARRATIVE),
            start_ms=candidate.match_clock_ms,
            end_ms=candidate.match_clock_ms + OVERLAY_DURATION_MS,
            match_clock_ms=candidate.match_clock_ms,
            title=title or candidate.headline[:60],
            body=body,
            metrics=list(candidate.metrics),
            # The citations come from the deterministic side of the pipeline,
            # never from the model.
            cited_event_ids=list(candidate.cited_event_ids),
            locale=locale,
            confidence=candidate.confidence,
        )

    async def _complete(self, client: Any, prompt: str) -> str:
        """Send the prompt and return the reply text."""
        messages: Any = prompt
        try:
            from agent_framework import ChatMessage  # type: ignore

            messages = [
                ChatMessage(role="system", text=_SYSTEM_PROMPT),
                ChatMessage(role="user", text=prompt),
            ]
        except ImportError:
            # No ChatMessage available: the system rules are prepended to the
            # single user message instead.
            messages = f"{_SYSTEM_PROMPT}\n\n{prompt}"

        get_response = getattr(client, "get_response", None)
        if get_response is None:  # pragma: no cover - API drift
            raise ProviderError(
                "The Foundry client exposes neither get_response nor send."
            )

        response = await self._get_response(get_response, messages)

        text = getattr(response, "text", None)
        if isinstance(text, str) and text.strip():
            return text.strip()

        parts: list[str] = []
        for message in getattr(response, "messages", None) or []:
            content = getattr(message, "text", None)
            if isinstance(content, str):
                parts.append(content)
        joined = "\n".join(p for p in parts if p).strip()
        if not joined:
            raise ProviderError("The model returned an empty response.")
        return joined

    async def _get_response(self, get_response: Any, messages: Any) -> Any:
        """Call the chat client, with the request options when supported.

        The options are what cap the reasoning effort, so they are not
        optional in practice. A client that does not accept them still gets a
        usable answer -- it just pays the default reasoning cost -- so the
        fallback is a retry without the options rather than a failure.

        The call is bounded by the provider's timeout. The Agent Framework's
        Foundry client carries no timeout of its own -- it hands the request
        to the OpenAI transport, whose default is ten minutes -- and a live
        feed cannot afford to wait ten minutes for one overlay. A call that
        runs long becomes a provider failure, and the provider chain falls
        back to the template engine rather than the match standing still.
        """
        try:
            return await asyncio.wait_for(
                get_response(messages, options=_REQUEST_OPTIONS),
                timeout=self.timeout_seconds,
            )
        except TypeError:
            return await asyncio.wait_for(
                get_response(messages), timeout=self.timeout_seconds
            )
        except TimeoutError:
            raise ProviderUnavailable(
                f"Foundry did not answer within {self.timeout_seconds:g}s."
            ) from None

    async def aclose(self) -> None:
        """Release the HTTP session and the credential.

        The Agent Framework client owns an httpx session and an
        ``AIProjectClient``; the credential owns an aiohttp session. Left
        open, they are reported as unclosed client sessions on exit.
        """
        client = self._client
        self._client = None
        if client is not None:
            for attribute in ("client", "project_client"):
                target = getattr(client, attribute, None)
                close = getattr(target, "close", None)
                if close is None:
                    continue
                result = close()
                if isawaitable(result):
                    await result

        credential = self._credential
        self._credential = None
        if credential is not None:
            close = getattr(credential, "close", None)
            if close is not None:
                result = close()
                if isawaitable(result):
                    await result


def is_available() -> bool:
    """Whether the Foundry provider is configured at all.

    Configuration is not the same as availability -- a configured provider can
    still be unreachable -- but an unconfigured one is definitely not usable,
    and the router uses this to start the chain at the template provider.
    """
    return bool(os.environ.get("FOUNDRY_PROJECT_ENDPOINT")) and bool(
        os.environ.get("FOUNDRY_MODEL")
    )


__all__ = ["FoundryProvider", "NarrativeProvider", "is_available"]
