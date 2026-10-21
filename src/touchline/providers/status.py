"""Who actually answered?

The whole point of the provider decision is that the UI never claims Microsoft
Foundry produced a response the Template provider produced. That claim needs a
single source of truth: the provider of the **last call**, recorded at the
moment the call completes, not the provider that happens to be configured.

Two facts are tracked, and they are deliberately separate:

``configured_provider``
    What the service *intends* to use -- "Microsoft Foundry" once an endpoint
    and a deployment name are present, otherwise the template engine.

``last_call_provider``
    What actually served the most recent narrative request. This is what the
    badge in the UI shows, next to a ``live`` or ``fallback`` chip.

The ``/healthz`` endpoint reports both, so a failing probe can be told apart
from a provider that is merely degraded.

This module holds no state that matters across a restart, and it holds no
credentials.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime

from touchline.providers import attribution

#: The name of the one model provider this project is allowed to use.
FOUNDRY_PROVIDER = "Microsoft Foundry"

#: The name of the no-model fallback.
TEMPLATE_PROVIDER = "Template engine"

#: Shown before any provider is configured at all.
NOT_CONFIGURED_PROVIDER = "Template engine (model not configured)"


@dataclass(frozen=True)
class ProviderState:
    """An immutable snapshot of the provider status."""

    configured_provider: str
    model_configured: bool
    last_call_provider: str | None
    last_call_state: str | None
    last_call_at: str | None
    call_count: int
    fallback_count: int
    #: How many times validated prose was rejected and replaced with
    #: deterministic copy. This is **not** a model-call count: the call
    #: was served (and counted above), but its words failed the figure
    #: check, so it is kept strictly separate from ``call_count`` and
    #: ``fallback_count``.
    substitution_count: int

    def as_dict(self) -> dict[str, object]:
        """Render for JSON."""
        return {
            "configured_provider": self.configured_provider,
            "model_configured": self.model_configured,
            "last_call_provider": self.last_call_provider,
            "last_call_state": self.last_call_state,
            "last_call_at": self.last_call_at,
            "call_count": self.call_count,
            "fallback_count": self.fallback_count,
            "substitution_count": self.substitution_count,
        }


@dataclass(frozen=True)
class ProviderCall:
    """One provider result, with the request it belonged to.

    The stage, the candidate and the audience revision come from the
    request-local context in force when the call was recorded -- see
    :mod:`touchline.providers.attribution` -- so concurrent rewrites for
    different audiences cannot mix attribution.
    """

    provider: str
    live: bool
    stage: str | None
    candidate_id: str | None
    revision: int | None
    at: str


@dataclass
class _Registry:
    """Mutable bookkeeping behind the immutable snapshots."""

    last_call_provider: str | None = None
    last_call_state: str | None = None
    last_call_at: str | None = None
    call_count: int = 0
    fallback_count: int = 0
    substitution_count: int = 0
    _lock: object = field(default=None, repr=False)


_REGISTRY = _Registry()

#: Every provider result, newest last, with its request attribution. It is
#: bounded, because the service runs for hours and the ledger is for
#: attribution, not archaeology.
_CALLS: deque[ProviderCall] = deque(maxlen=500)


def record_call(provider: str, *, live: bool) -> None:
    """Record which provider served a call.

    The call is stamped with the request-local context in force -- the
    stage, the candidate and the audience revision -- so the record says
    which request the result belonged to.

    Args:
        provider: The provider that actually produced the response.
        live: ``True`` when the model provider served it, ``False`` when the
            no-model fallback did.
    """
    now = datetime.now(UTC).isoformat()
    _REGISTRY.last_call_provider = provider
    _REGISTRY.last_call_state = "live" if live else "fallback"
    _REGISTRY.last_call_at = now
    _REGISTRY.call_count += 1
    if not live:
        _REGISTRY.fallback_count += 1
    context = attribution.current()
    _CALLS.append(
        ProviderCall(
            provider=provider,
            live=live,
            stage=context.stage,
            candidate_id=context.candidate_id,
            revision=context.revision,
            at=now,
        )
    )


def record_substitution() -> None:
    """Record a prose-validation substitution.

    The model's call was served -- and counted as a call -- but its words
    carried a figure the event log does not hold, so deterministic copy
    was used instead. That is not a model-call failure, and it must not
    move ``call_count`` or ``fallback_count``: it has its own counter.
    """
    _REGISTRY.substitution_count += 1


def calls(
    *,
    stage: str | None = None,
    candidate_id: str | None = None,
    revision: int | None = None,
) -> list[ProviderCall]:
    """The recorded provider results, oldest first.

    Args:
        stage: Only calls made for this pipeline stage.
        candidate_id: Only calls about this insight.
        revision: Only calls written for this audience revision.

    Returns:
        The matching records. A filter left as ``None`` matches
        everything, including records made with no context at all.
    """
    return [
        call
        for call in _CALLS
        if (stage is None or call.stage == stage)
        and (candidate_id is None or call.candidate_id == candidate_id)
        and (revision is None or call.revision == revision)
    ]


def last_call_for(
    *,
    stage: str | None = None,
    candidate_id: str | None = None,
    revision: int | None = None,
) -> ProviderCall | None:
    """The most recent provider result matching the request.

    This is what stamps a card or a hand-off with the provider that
    actually served it: it is read straight after the call, in the same
    task, so the last matching record is that call's.

    Args:
        stage: The pipeline stage that made the call.
        candidate_id: The insight the call was about.
        revision: The audience revision the call was written for.

    Returns:
        The matching record, or ``None`` when nothing matches.
    """
    for call in reversed(_CALLS):
        if (
            (stage is None or call.stage == stage)
            and (candidate_id is None or call.candidate_id == candidate_id)
            and (revision is None or call.revision == revision)
        ):
            return call
    return None


def current(model_configured: bool) -> ProviderState:
    """Return the current snapshot.

    Args:
        model_configured: Whether a Foundry endpoint and deployment name are
            configured. Computed by the caller from the environment, so this
            module does not have to read it.
    """
    # The template engine is not a *fallback* when there was never a model
    # to fall back from. The chip is what the badge in the UI shows, and a
    # badge that says "Template engine (model not configured)" next to an
    # amber "fallback" chip would say two contradictory things at once.
    state = _REGISTRY.last_call_state
    if not model_configured and state == "fallback":
        state = None

    return ProviderState(
        configured_provider=(
            FOUNDRY_PROVIDER if model_configured else NOT_CONFIGURED_PROVIDER
        ),
        model_configured=model_configured,
        last_call_provider=_REGISTRY.last_call_provider,
        last_call_state=state,
        last_call_at=_REGISTRY.last_call_at,
        call_count=_REGISTRY.call_count,
        fallback_count=_REGISTRY.fallback_count,
        substitution_count=_REGISTRY.substitution_count,
    )


def reset() -> None:
    """Forget the recorded calls. Used by tests."""
    _REGISTRY.last_call_provider = None
    _REGISTRY.last_call_state = None
    _REGISTRY.last_call_at = None
    _REGISTRY.call_count = 0
    _REGISTRY.fallback_count = 0
    _REGISTRY.substitution_count = 0
    _CALLS.clear()
