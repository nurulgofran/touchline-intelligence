"""Overlay models -- the payload the broadcast UI renders.

An overlay is a timed, on-screen piece of narrative with its supporting
metrics and the event ids it was derived from. The JSON Schema in
``schemas/overlay.schema.json`` mirrors these models exactly; a test asserts
the two stay in sync.
"""

from __future__ import annotations

import itertools
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from touchline.models.insights import MetricValue


class OverlayType(str, Enum):
    """What kind of overlay this is, which decides how the UI renders it."""

    TACTICAL = "TACTICAL"
    STAT = "STAT"
    NARRATIVE = "NARRATIVE"
    ALERT = "ALERT"
    RECAP = "RECAP"


class Overlay(BaseModel):
    """A single timed on-screen overlay."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = Field(default="1.0")
    overlay_id: str
    type: OverlayType
    start_ms: int = Field(ge=0, description="Overlay appears at this match clock.")
    end_ms: int = Field(ge=0, description="Overlay disappears at this match clock.")
    match_clock_ms: int = Field(
        ge=0, le=5_400_000, description="Clock the overlay is anchored to."
    )
    title: str
    body: str
    metrics: list[MetricValue] = Field(default_factory=list)
    cited_event_ids: list[str] = Field(default_factory=list)
    locale: str = Field(default="en")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class OverlayBundle(BaseModel):
    """A batch of overlays that are validated as a unit."""

    model_config = ConfigDict(frozen=True)

    overlays: list[Overlay] = Field(default_factory=list)

    def check_timing(self) -> None:
        """Raise ``ValueError`` if any two overlays overlap.

        Overlays occupy the half-open interval ``[start_ms, end_ms)``, so an
        overlay that ends exactly where the next one starts is not an overlap.
        """
        ordered = sorted(self.overlays, key=lambda o: (o.start_ms, o.end_ms))
        for previous, current in itertools.pairwise(ordered):
            if current.start_ms < previous.end_ms:
                raise ValueError(
                    "Overlays overlap: "
                    f"{previous.overlay_id} [{previous.start_ms}, {previous.end_ms}) "
                    f"and {current.overlay_id} [{current.start_ms}, {current.end_ms})"
                )

    def validate(self) -> None:  # type: ignore[override]
        """Validate the bundle as a unit. Alias of :meth:`check_timing`.

        Pydantic's own ``BaseModel.validate`` is a deprecated classmethod; this
        instance method deliberately replaces it with the bundle check.
        """
        self.check_timing()
