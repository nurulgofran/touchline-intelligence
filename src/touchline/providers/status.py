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

from dataclasses import dataclass, field
from datetime import UTC, datetime

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
        }


@dataclass
class _Registry:
    """Mutable bookkeeping behind the immutable snapshots."""

    last_call_provider: str | None = None
    last_call_state: str | None = None
    last_call_at: str | None = None
    call_count: int = 0
    fallback_count: int = 0
    _lock: object = field(default=None, repr=False)


_REGISTRY = _Registry()


def record_call(provider: str, *, live: bool) -> None:
    """Record which provider served a call.

    Args:
        provider: The provider that actually produced the response.
        live: ``True`` when the model provider served it, ``False`` when the
            no-model fallback did.
    """
    _REGISTRY.last_call_provider = provider
    _REGISTRY.last_call_state = "live" if live else "fallback"
    _REGISTRY.last_call_at = datetime.now(UTC).isoformat()
    _REGISTRY.call_count += 1
    if not live:
        _REGISTRY.fallback_count += 1


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
    )


def reset() -> None:
    """Forget the recorded calls. Used by tests."""
    _REGISTRY.last_call_provider = None
    _REGISTRY.last_call_state = None
    _REGISTRY.last_call_at = None
    _REGISTRY.call_count = 0
    _REGISTRY.fallback_count = 0
