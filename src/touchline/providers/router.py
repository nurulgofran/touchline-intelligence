"""Provider routing: ``foundry -> template``.

The chain has exactly two links and no more:

1. **Microsoft Foundry** -- the one model provider. Used by the deployed demo
   and by local development alike.
2. **Template engine** -- no model at all. It renders a complete, grounded
   insight deterministically, and it is what keeps the live feed running when
   inference is unavailable or the quota is exhausted.

The router is also where the truth about "who answered" is recorded, so the UI
badge can never claim Foundry produced a response the template engine
produced. See :mod:`touchline.providers.status`.
"""

from __future__ import annotations

import logging
from typing import Any

from touchline.models.insights import InsightCandidate
from touchline.models.overlay import Overlay
from touchline.providers import status
from touchline.providers.base import (
    NarrativeProvider,
    ProviderError,
    ProviderName,
    ProviderUnavailable,
)

logger = logging.getLogger(__name__)

#: Machine provider id -> (display label, is the model provider).
_DISPLAY: dict[str, tuple[str, bool]] = {
    ProviderName.FOUNDRY.value: (status.FOUNDRY_PROVIDER, True),
    ProviderName.TEMPLATE.value: (status.TEMPLATE_PROVIDER, False),
}


def _record(provider: Any) -> None:
    """Record which provider actually served a call.

    The provider id is a machine name ("foundry", "template"); the label the
    UI shows is a display name ("Microsoft Foundry", "Template engine"). They
    are mapped here so no machine id ever reaches the badge.
    """
    provider_id = getattr(provider, "name", "")
    label, live = _DISPLAY.get(provider_id, (status.TEMPLATE_PROVIDER, False))
    status.record_call(label, live=live)


def _template_provider() -> Any:
    """Build the no-model provider.

    Imported lazily so that the module imports in an environment where the
    Agent Framework packages are not installed.
    """
    from touchline.providers.template import TemplateProvider

    return TemplateProvider()


def build_provider() -> NarrativeProvider:
    """Return the provider the chain starts at.

    If Foundry is configured, the chain starts at Foundry and falls back to
    the template engine on failure. If it is not configured, the chain starts
    at the template engine directly -- the build must never block on Azure
    credentials arriving.
    """
    from touchline.providers import foundry

    if foundry.is_available():
        return foundry.FoundryProvider()
    return _template_provider()


async def narrate(
    candidate: InsightCandidate,
    *,
    locale: str = "en",
    mode: str = "analyst",
    provider: NarrativeProvider | None = None,
) -> Overlay:
    """Narrate one candidate, falling back to the template engine.

    Args:
        candidate: The deterministic, fully-cited insight.
        locale: Language code for the rendered prose.
        mode: Audience mode.
        provider: The provider to start at. Defaults to :func:`build_provider`.

    Returns:
        An overlay with the candidate's citations.

    Raises:
        ProviderError: If even the template engine could not produce an
            overlay, which means the pipeline itself is broken.
    """
    primary = provider if provider is not None else build_provider()

    try:
        overlay = await primary.narrate(candidate, locale=locale, mode=mode)
    except (ProviderUnavailable, ProviderError) as exc:
        logger.warning(
            "provider %s failed (%s); falling back to the template engine",
            getattr(primary, "name", primary),
            exc,
        )
        fallback = _template_provider()
        try:
            overlay = await fallback.narrate(candidate, locale=locale, mode=mode)
        except Exception as fallback_exc:
            raise ProviderError(
                f"both providers failed: {exc}; fallback: {fallback_exc}"
            ) from fallback_exc
        _record(fallback)
        return overlay

    # The provider records its own call, but a caller may have supplied a
    # provider that does not; make sure the badge is never stale.
    if status.current(model_configured=True).last_call_provider is None:
        _record(primary)

    return overlay


__all__ = ["build_provider", "narrate"]
