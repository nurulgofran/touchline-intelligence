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

from touchline.models.events import TeamSide


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
    side: TeamSide | None = Field(
        default=None,
        description=(
            "Which side the number describes, when the insight is about one "
            "side. The grounding step recomputes the metric over the cited "
            "events for this side."
        ),
    )
    subject: str | None = Field(
        default=None,
        description=(
            "The person the number is about, when it is about a player "
            "rather than a side. The player-focus audience counts one "
            "player's actions inside the cited window; the subject says "
            "whose they are."
        ),
    )


class BaselineComparison(BaseModel):
    """The earlier value a recent metric is compared against.

    An insight in this project is a *deviation*, never a restated statistic:
    "84% pass accuracy" says nothing on its own, "84% against 66% fifteen
    minutes earlier" does. The baseline carries its own citations, so both
    halves of the comparison are independently recomputable from the log.
    """

    model_config = ConfigDict(frozen=True)

    metric_name: str = Field(description="The metric being compared.")
    value: float = Field(description="The earlier value.")
    unit: str = Field(description="Unit of the metric.")
    side: TeamSide | None = Field(default=None, description="Whose value this is.")
    delta: float = Field(description="Recent value minus this baseline value.")
    window_ms: int = Field(ge=0, description="Length of the baseline window.")
    cited_event_ids: list[str] = Field(
        default_factory=list,
        description="The events the baseline was computed over.",
    )


class InsightCandidate(BaseModel):
    """A deterministic, fully-cited insight awaiting a narrative.

    Attributes:
        candidate_id: Stable identifier for this candidate.
        kind: What sort of insight this is; drives template selection.
        match_clock_ms: Match clock at which the insight is anchored.
        side: The side the insight is about, when it is about one side.
        headline: A short, factual summary (not prose -- prose is the
            provider's job).
        metrics: The numbers the insight is built on.
        baseline: The earlier value the insight deviates from.
        cited_event_ids: The event ids that support the claim. This is the
            field the grounding executor verifies against the event log.
        confidence: Model-independent confidence in [0, 1].
        tags: Free-form labels used for filtering and personalisation.
    """

    model_config = ConfigDict(frozen=True)

    candidate_id: str
    kind: InsightKind
    match_clock_ms: int = Field(ge=0, le=5_400_000)
    side: TeamSide | None = Field(
        default=None, description="The side the insight is about, if any."
    )
    headline: str
    metrics: list[MetricValue] = Field(default_factory=list)
    baseline: BaselineComparison | None = Field(
        default=None,
        description="The earlier value the insight deviates from, if any.",
    )
    cited_event_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Event ids this claim rests on. The grounding step recomputes every "
            "cited metric from the event log and vetoes the claim on a mismatch."
        ),
    )
    confidence: float = Field(ge=0.0, le=1.0)
    tags: list[str] = Field(default_factory=list)
