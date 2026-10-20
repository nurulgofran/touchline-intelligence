"""Grounding: the step that decides whether a claim is allowed on screen.

This is the differentiator of the whole project, and it is deliberately
*boring*. The Explainer is a model, and a model can be wrong. So the
Explainer does not get to assert a number; it gets to *claim* one, and this
module checks the claim against the event log:

1. Every cited event id has to exist in the log. An id that does not exist
   is a claim about something that never happened.
2. Every claimed metric has to **recompute** from the cited events. The
   metric registry is the same one the analyst used, so a number that does
   not come back out is a number the agent made up.
3. The set of claimed metrics has to be the candidate's set. An agent that
   quietly drops a metric has changed the meaning of the claim, and one that
   adds a metric the candidate never had is making a second claim.
4. The baseline, if there is one, has to recompute from **its own**
   citations, so both halves of "84% against 66% fifteen minutes earlier"
   are independently checkable.

Anything that fails is a **veto**. The veto is not an exception and not a
silent drop: it is a verdict, it carries its reasons, and the workflow
routes the draft back to the Explainer so the claim can be made again. The
reasons are shown in the trace, because a grounding step a judge cannot see
is a grounding step a judge cannot believe.

The tolerance is small but not zero. A model that restates ``83.7142857`` as
``83.7`` has not hallucinated; a model that restates it as ``66`` has. The
check is therefore "close enough to be the same number", not "the same
bits".
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from touchline.models.claims import Explanation
from touchline.state.store import MatchStateStore

__all__ = [
    "GroundingCheck",
    "GroundingVerdict",
    "ground_claim",
]


class GroundingCheck(BaseModel):
    """One number, and what came back when it was recomputed."""

    model_config = ConfigDict(frozen=True)

    metric_name: str
    claimed: float
    recomputed: float | None = Field(
        default=None, description="None when the cited ids are not in the log."
    )
    ok: bool
    reason: str = Field(default="", description="Why the check failed, if it did.")


class GroundingVerdict(BaseModel):
    """The grounding step's answer: allowed, or vetoed."""

    model_config = ConfigDict(frozen=True)

    candidate_id: str
    accepted: bool
    reasons: list[str] = Field(
        default_factory=list, description="Every reason the claim was rejected."
    )
    checks: list[GroundingCheck] = Field(default_factory=list)

    @property
    def summary(self) -> str:
        """A one-line description, for the trace."""
        if self.accepted:
            return f"{self.candidate_id}: grounded"
        return f"{self.candidate_id}: vetoed -- {'; '.join(self.reasons)}"


def _tolerance(unit: str, value: float) -> float:
    """How far a claimed value may sit from the recomputed one.

    Percentages and metres are the two units the project reports, and both
    are rounded for display, so a claim that differs by less than half a
    unit is the same number said more roughly. Everything else is compared
    almost exactly, with a little room for a floating-point reordering.
    """
    if unit in ("%", "m"):
        return 0.5
    return 0.01


def _close(claimed: float, recomputed: float, unit: str) -> bool:
    """Whether a claimed value is the recomputed one, near enough."""
    return math.isclose(
        claimed,
        recomputed,
        rel_tol=1e-3,
        abs_tol=_tolerance(unit, recomputed),
    )


def _metric_names(explanation: Explanation) -> list[str]:
    """The metric names a claim rests on, in order."""
    return [metric.name for metric in explanation.metrics]


def ground_claim(
    store: MatchStateStore,
    candidate_id: str,
    explanation: Explanation,
    *,
    expected_metric_names: Sequence[str] = (),
) -> GroundingVerdict:
    """Check a claim against the event log.

    Args:
        store: The shared state, holding the event log.
        candidate_id: The candidate the claim is about.
        explanation: The claim, as the agent stated it.
        expected_metric_names: The metric names the candidate itself carries.
            When given, the claim has to rest on exactly these -- no fewer,
            no more. This is what stops an agent from dropping a number it
            found inconvenient.

    Returns:
        The verdict. ``accepted`` is ``True`` only when every check passed.
    """
    reasons: list[str] = []
    checks: list[GroundingCheck] = []

    cited = list(explanation.cited_event_ids)
    if not cited:
        reasons.append("the claim cites no events")

    for metric in explanation.metrics:
        recomputed = store.recompute(metric.name, cited, metric.side)
        if recomputed is None:
            checks.append(
                GroundingCheck(
                    metric_name=metric.name,
                    claimed=metric.value,
                    recomputed=None,
                    ok=False,
                    reason="the cited events are not in the log",
                )
            )
            reasons.append(
                f"{metric.name}: the cited events are not in the log"
            )
            continue

        ok = _close(metric.value, recomputed, metric.unit)
        checks.append(
            GroundingCheck(
                metric_name=metric.name,
                claimed=metric.value,
                recomputed=recomputed,
                ok=ok,
                reason=(
                    ""
                    if ok
                    else f"claimed {metric.value:g}, recomputed {recomputed:g}"
                ),
            )
        )
        if not ok:
            reasons.append(
                f"{metric.name}: claimed {metric.value:g}, "
                f"recomputed {recomputed:g}"
            )

    if expected_metric_names:
        claimed_names = _metric_names(explanation)
        missing = [name for name in expected_metric_names if name not in claimed_names]
        extra = [name for name in claimed_names if name not in expected_metric_names]
        if missing:
            reasons.append(f"the claim drops {', '.join(missing)}")
        if extra:
            reasons.append(f"the claim adds {', '.join(extra)}")

    baseline = explanation.baseline
    if baseline is not None:
        recomputed = store.recompute(
            baseline.metric_name, list(baseline.cited_event_ids), baseline.side
        )
        if recomputed is None:
            reasons.append(
                f"baseline {baseline.metric_name}: its cited events are not in the log"
            )
        elif not _close(baseline.value, recomputed, baseline.unit):
            reasons.append(
                f"baseline {baseline.metric_name}: claimed {baseline.value:g}, "
                f"recomputed {recomputed:g}"
            )

    return GroundingVerdict(
        candidate_id=candidate_id,
        accepted=not reasons,
        reasons=reasons,
        checks=checks,
    )
