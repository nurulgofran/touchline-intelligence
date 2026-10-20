"""The provider contract: one place where a model may be called.

The rest of the pipeline is deterministic Python. A provider is the single
seam through which a language model can contribute prose, and it always
receives a fully cited :class:`InsightCandidate` and always returns an
:class:`Overlay` that carries the candidate's citations forward.
"""

from __future__ import annotations

from enum import Enum
from typing import Protocol, runtime_checkable

from touchline.models.insights import InsightCandidate
from touchline.models.overlay import Overlay


class ProviderName(str, Enum):
    """The identifiers used to select a provider."""

    FOUNDRY = "foundry"
    TEMPLATE = "template"


class ProviderError(Exception):
    """Raised when a provider fails to produce a narrative."""


class ProviderUnavailable(ProviderError):
    """Raised when a provider cannot be reached at all (no credentials, down)."""


@runtime_checkable
class NarrativeProvider(Protocol):
    """Turns a cited insight candidate into a timed overlay."""

    name: str

    async def narrate(
        self,
        candidate: InsightCandidate,
        *,
        locale: str = "en",
        mode: str = "analyst",
    ) -> Overlay:
        """Render one candidate.

        Args:
            candidate: The deterministic, fully-cited insight to narrate.
            locale: Language code for the rendered prose, e.g. ``"en"``.
            mode: The audience mode, e.g. ``"analyst"``, ``"fan"`` or
                ``"player"``.

        Returns:
            An overlay whose ``cited_event_ids`` are the candidate's.

        Raises:
            ProviderError: If the narrative could not be produced.
        """
        ...
