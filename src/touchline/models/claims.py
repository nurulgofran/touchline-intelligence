"""What an agent *claims* about an insight.

The analyst produces facts. An agent turns them into a sentence, and a
sentence can go wrong in two ways: it can state a number that was never
computed, or it can point at events that do not support it. Both are claims,
and both have to be checkable.

So the Explainer does not just return prose. It returns a *claim*: the
numbers it is resting on, the events it is resting on them for, and the
reading it drew from them. The grounding step then recomputes every number
from the cited events and compares. A claim that does not reproduce is
vetoed, and the draft goes back to the Explainer.

These models live in :mod:`touchline.models` rather than in the orchestration
package because both the providers (which build the claim) and the grounding
step (which checks it) need them, and neither should have to import the
other.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from touchline.models.events import TeamSide
from touchline.models.insights import BaselineComparison, InsightCandidate, MetricValue

__all__ = [
    "ClaimedBaseline",
    "ClaimedMetric",
    "Explanation",
    "Narration",
]


class ClaimedMetric(BaseModel):
    """A number an agent says it is resting on."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="The metric's machine name.")
    value: float = Field(description="The value the agent states.")
    unit: str = Field(default="", description="The unit the agent states.")
    side: TeamSide | None = Field(
        default=None, description="Whose value this is, when it is about one side."
    )


class ClaimedBaseline(BaseModel):
    """The earlier value an agent says the claim deviates from."""

    model_config = ConfigDict(frozen=True)

    metric_name: str
    value: float
    unit: str = ""
    side: TeamSide | None = None
    cited_event_ids: list[str] = Field(default_factory=list)


class Explanation(BaseModel):
    """The Explainer's output: a reading, and the claim it rests on.

    The ``metrics`` and ``cited_event_ids`` here are the agent's *own*
    statement of what the claim rests on -- not a copy of the candidate's.
    That is the whole point: the grounding step compares the agent's account
    with the log, and an agent that quietly dropped a metric or invented an
    event id is caught.
    """

    model_config = ConfigDict(frozen=True)

    candidate_id: str = Field(description="Which candidate this explains.")
    reading: str = Field(description="Why the insight matters, in prose.")
    metrics: list[ClaimedMetric] = Field(
        default_factory=list, description="The numbers the claim rests on."
    )
    cited_event_ids: list[str] = Field(
        default_factory=list, description="The events the claim rests on."
    )
    baseline: ClaimedBaseline | None = Field(
        default=None, description="The earlier value the claim deviates from."
    )

    @classmethod
    def from_candidate(
        cls, candidate: InsightCandidate, reading: str
    ) -> Explanation:
        """Build the claim that a candidate *exactly* supports.

        This is the no-model path, and it is also the reference the grounding
        step compares against: every number and every citation comes from the
        deterministic side of the pipeline, so the claim reproduces by
        construction.
        """
        baseline: ClaimedBaseline | None = None
        if candidate.baseline is not None:
            baseline = ClaimedBaseline(
                metric_name=candidate.baseline.metric_name,
                value=candidate.baseline.value,
                unit=candidate.baseline.unit,
                side=candidate.baseline.side,
                cited_event_ids=list(candidate.baseline.cited_event_ids),
            )
        return cls(
            candidate_id=candidate.candidate_id,
            reading=reading,
            metrics=[
                ClaimedMetric(
                    name=metric.name,
                    value=metric.value,
                    unit=metric.unit,
                    side=metric.side,
                )
                for metric in candidate.metrics
            ],
            cited_event_ids=list(candidate.cited_event_ids),
            baseline=baseline,
        )

    def to_metric_values(self) -> list[MetricValue]:
        """Render the claimed metrics as display values.

        The labels are not part of a claim -- a claim is a number, a unit and
        a side -- so the labels come back empty and the renderer fills them
        in.
        """
        return [
            MetricValue(
                name=metric.name,
                value=metric.value,
                unit=metric.unit,
                label=metric.name,
                side=metric.side,
            )
            for metric in self.metrics
        ]

    def baseline_comparison(self) -> BaselineComparison | None:
        """Render the claimed baseline as a comparison, if there is one."""
        if self.baseline is None:
            return None
        return BaselineComparison(
            metric_name=self.baseline.metric_name,
            value=self.baseline.value,
            unit=self.baseline.unit,
            side=self.baseline.side,
            delta=0.0,
            window_ms=0,
            cited_event_ids=list(self.baseline.cited_event_ids),
        )


class Narration(BaseModel):
    """The Narrator's output: the words, and nothing else.

    The numbers and the citations are *not* part of a narration. They travel
    with the candidate and are attached to the overlay afterwards, so a
    model cannot talk its way into an unsupported claim -- it can only write
    sentences about the claim that was already checked.
    """

    model_config = ConfigDict(frozen=True)

    candidate_id: str
    title: str = Field(description="The overlay's title, at most 60 characters.")
    body: str = Field(description="The overlay's body, at most 220 characters.")
    commentary: str = Field(
        default="", description="The one-line call a commentator would make."
    )
