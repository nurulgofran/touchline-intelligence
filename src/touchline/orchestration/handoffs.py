"""The handoff trace: the record of who passed what to whom.

A multi-agent system is only as convincing as its hand-offs. This is the
ledger of them: every message that crosses an edge of the workflow graph is
recorded here, with the stage it came from, the stage it went to, and a
one-line summary of what was handed over.

The trace is what the agent-trace view in the UI renders, and it is what a
judge reads to see that the Explainer really did hand its claim to the
Grounding step, that the Grounding step really did veto it, and that the
veto really did send the draft back to the Explainer rather than quietly
dropping it.

It is deliberately plain data. A trace that cannot be serialised is a trace
that cannot be shown.
"""

from __future__ import annotations

from collections.abc import Iterator

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["Handoff", "HandoffTrace"]


class Handoff(BaseModel):
    """One hand-off between two stages."""

    model_config = ConfigDict(frozen=True)

    index: int = Field(description="Position in the trace, from zero.")
    from_stage: str = Field(description="The stage that handed over.")
    to_stage: str = Field(description="The stage that received it.")
    kind: str = Field(description="What sort of hand-off this was.")
    summary: str = Field(description="One line about what was handed over.")
    candidate_id: str | None = Field(
        default=None, description="The insight this hand-off was about."
    )
    accepted: bool | None = Field(
        default=None,
        description=(
            "Whether the receiving stage accepted the hand-off. A grounding "
            "veto records False here."
        ),
    )


class HandoffTrace:
    """An ordered record of hand-offs.

    The trace is mutable while a run is in progress and read-only once it is
    finished; nothing in the system edits a recorded hand-off.
    """

    def __init__(self) -> None:
        self._handoffs: list[Handoff] = []

    def record(
        self,
        from_stage: str,
        to_stage: str,
        kind: str,
        summary: str,
        *,
        candidate_id: str | None = None,
        accepted: bool | None = None,
    ) -> Handoff:
        """Record one hand-off.

        Args:
            from_stage: The stage handing over.
            to_stage: The stage receiving it.
            kind: What sort of hand-off this is.
            summary: One line about what was handed over.
            candidate_id: The insight this is about.
            accepted: Whether the receiving stage accepted it.

        Returns:
            The recorded hand-off.
        """
        handoff = Handoff(
            index=len(self._handoffs),
            from_stage=from_stage,
            to_stage=to_stage,
            kind=kind,
            summary=summary,
            candidate_id=candidate_id,
            accepted=accepted,
        )
        self._handoffs.append(handoff)
        return handoff

    def as_list(self) -> list[Handoff]:
        """The hand-offs, in order."""
        return list(self._handoffs)

    def vetoes(self) -> list[Handoff]:
        """The hand-offs that were refused."""
        return [handoff for handoff in self._handoffs if handoff.accepted is False]

    def for_candidate(self, candidate_id: str) -> list[Handoff]:
        """The hand-offs about one insight."""
        return [
            handoff
            for handoff in self._handoffs
            if handoff.candidate_id == candidate_id
        ]

    def __len__(self) -> int:
        return len(self._handoffs)

    def __iter__(self) -> Iterator[Handoff]:
        return iter(self._handoffs)
