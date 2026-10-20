"""The messages that travel along the workflow graph.

Every edge of the graph carries a typed message, and the type is the
contract: the Analyst cannot send a verdict, the Grounding step cannot send
a candidate, and the framework checks the pairing when the graph is built.
That check is worth having -- a hand-off whose types do not line up is a
hand-off that silently drops work at run time.

The five stages of the pipeline are:

===============  ==========================  ==============================
Stage            Message in                  Message out
===============  ==========================  ==============================
Ingest           ``MatchIngested``           ``MatchLoaded``
Analyst          ``MatchLoaded``             ``InsightsAnalysed``
Explainer        ``InsightsAnalysed``        ``InsightExplained``
Grounding        ``InsightExplained``        ``GroundingVerdict``
Narrator         ``GroundingVerdict``        ``InsightNarrated``
Personalizer     ``InsightNarrated``         ``InsightPersonalised``
===============  ==========================  ==============================

The Grounding step is the odd one out: it does not only move the work
onwards. A veto goes *backwards*, to the Explainer, on the same edge
structure, and the draft is explained again. That is what "recover from
failures" looks like in a multi-agent system.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from touchline.models.claims import Explanation, Narration
from touchline.models.events import MatchEvent
from touchline.models.insights import InsightCandidate
from touchline.models.overlay import Overlay
from touchline.orchestration.grounding import GroundingVerdict

__all__ = [
    "GroundingVerdict",
    "InsightExplained",
    "InsightNarrated",
    "InsightPersonalised",
    "InsightsAnalysed",
    "MatchIngested",
    "MatchLoaded",
]


class MatchIngested(BaseModel):
    """A match has arrived: the raw event log."""

    model_config = ConfigDict(frozen=True)

    match_id: str = Field(description="Which match this is.")
    events: list[MatchEvent] = Field(default_factory=list)


class MatchLoaded(BaseModel):
    """The event log is in the shared state and ready to be read."""

    model_config = ConfigDict(frozen=True)

    match_id: str
    event_count: int = Field(ge=0)


class InsightsAnalysed(BaseModel):
    """The analyst has found the insights worth showing."""

    model_config = ConfigDict(frozen=True)

    candidates: list[InsightCandidate] = Field(default_factory=list)


class InsightExplained(BaseModel):
    """One insight, explained, with the claim it rests on."""

    model_config = ConfigDict(frozen=True)

    candidate: InsightCandidate
    explanation: Explanation
    attempt: int = Field(
        default=1,
        description="How many times this candidate has been explained so far.",
    )
    feedback: str = Field(
        default="",
        description="Why the last attempt was vetoed, for the agent to read.",
    )


class InsightNarrated(BaseModel):
    """One insight, written for the screen."""

    model_config = ConfigDict(frozen=True)

    candidate: InsightCandidate
    narration: Narration


class InsightPersonalised(BaseModel):
    """One insight, finished: the overlay that goes on screen."""

    model_config = ConfigDict(frozen=True)

    candidate: InsightCandidate
    overlay: Overlay
