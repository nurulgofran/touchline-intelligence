"""Insight candidates -- the hand-off between the deterministic analyst and
the narrative providers.

The load-bearing field of this project is ``cited_event_ids``. Every narrative
claim must carry the ids of the events it rests on, and a grounding step later
recomputes each cited metric from the event log and vetoes any claim that does
not reproduce. A claim without citations is treated as unsupported and is
rejected; a claim with citations that do not recompute is treated as a
hallucination and is vetoed.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class InsightKind(str, Enum):
    """The category of a tactical or statistical insight."""

    TACTICAL = "TACTICAL"
    MOMENTUM_SHIFT = "MOMENTUM_SHIFT"
    PRESS_BREAK = "PRESS_BREAK"
    THREAT = "THREAT"
    MILESTONE = "MILESTONE"
    CHANCE_CREATED = "CHANCE_CREATED"
    SET_PIECE = "SET_PIECE"
    DISCIPLINE = "DISCIPLINE"


class MetricValue(BaseModel):
    """A single number shown next to an insight, with its unit and a label."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Machine name of the metric, e.g. 'pass_accuracy'.")
    value: float
    unit: str = Field(description="Unit of measurement, e.g. '%' or 'm'.")
    label: str = Field(description="Human-readable label, translated per locale.")


class InsightCandidate(BaseModel):
    """A deterministic, fully-cited insight awaiting a narrative.

    Attributes:
        candidate_id: Stable identifier for this candidate.
        kind: What sort of insight this is; drives template selection.
        match_clock_ms: Match clock at which the insight is anchored.
        headline: A short, factual summary (not prose -- prose is the
            provider's job).
        metrics: The numbers the insight is built on.
        cited_event_ids: The event ids that support the claim. This is the
            field the grounding executor verifies against the event log.
        confidence: Model-independent confidence in [0, 1].
        tags: Free-form labels used for filtering and personalisation.
    """

    model_config = ConfigDict(frozen=True)

    candidate_id: str
    kind: InsightKind
    match_clock_ms: int = Field(ge=0, le=5_400_000)
    headline: str
    metrics: list[MetricValue] = Field(default_factory=list)
    cited_event_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Event ids this claim rests on. The grounding step recomputes every "
            "cited metric from the event log and vetoes the claim on a mismatch."
        ),
    )
    confidence: float = Field(ge=0.0, le=1.0)
    tags: list[str] = Field(default_factory=list)
