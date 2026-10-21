"""Read-only evidence for one insight.

This is the service half of the studio's "Inspect evidence" drawer: a
judge picks a key moment and wants to follow it from the explanation back
to the events and forward to the numbers, without reading source code.

The route only reads what the engine has already computed and recomputes
the metrics with the deterministic functions. It makes no model calls and
it touches nothing the audience pickers share: opening the drawer cannot
change who the overlays are written for, and it cannot schedule anything.

Unknown candidates answer 404. Missing evidence -- a candidate record the
engine never kept, an event id the log does not hold, a metric that will
not recompute -- is labelled as missing. It is never reconstructed as if
it were historical evidence.
"""

from __future__ import annotations

import math
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from touchline.api.live import get_engine, overlay_candidate_id
from touchline.metrics.definitions import METRICS, MetricScope, compute_metric
from touchline.models.events import MatchEvent, TeamSide
from touchline.models.insights import BaselineComparison, InsightCandidate, MetricValue
from touchline.models.overlay import Overlay

__all__ = ["router"]

router = APIRouter(prefix="/api/evidence", tags=["evidence"])

#: The prose is interpretation, not verification. It is said once here and
#: rendered by the drawer verbatim, so the copy cannot drift.
_PROSE_NOTE = (
    "The title and body are model interpretation (or deterministic copy, "
    "when the card says so). Only the numbers and their citations are "
    "recomputed; the semantic prose is not formally verified."
)

#: The grounding rule, in the same terms the grounding step uses: a claim
#: that sits within half a reported unit of the recomputed value is the
#: same number said more roughly. Everything else is compared almost
#: exactly.
_TOLERANCES = ("%", "m")


def _close(claimed: float, recomputed: float, unit: str) -> bool:
    """Whether a displayed value is the recomputed one, near enough."""
    tolerance = 0.5 if unit in _TOLERANCES else 0.01
    return math.isclose(claimed, recomputed, rel_tol=1e-3, abs_tol=tolerance)


def _side_value(side: TeamSide | None) -> str | None:
    """The side, as the wire spells it."""
    return side.value if side is not None else None


def _metric_definition(name: str, side: TeamSide | None) -> str | None:
    """What a metric measures, from the registry that defines every number.

    Returns ``None`` for a metric name the registry does not know.
    """
    spec = METRICS.get(name)
    if spec is None:
        return None
    if spec.scope is MetricScope.FILTER:
        if side is None:
            return (
                f"{spec.label}: side-filtered -- the cited event set is "
                "used whole, with no side restriction."
            )
        return (
            f"{spec.label}: side-filtered -- the cited events are restricted "
            f"to the {side.value} side first, then the number is computed."
        )
    if side is None:
        return (
            f"{spec.label}: side-attributed -- computed over the whole cited "
            "event set, reporting the home side's share."
        )
    return (
        f"{spec.label}: side-attributed -- computed over the whole cited "
        f"event set, reporting the {side.value} side's share."
    )


def _event_payload(event: MatchEvent) -> dict[str, Any]:
    """One cited event, with the details a judge needs to find it."""
    return {
        "event_id": event.event_id,
        "found": True,
        "type": event.type.value,
        "match_clock_ms": event.match_clock_ms,
        "period": event.period,
        "side": _side_value(event.side),
        "player_name": event.player_name,
        "target_player_name": event.target_player_name,
        "outcome": event.outcome,
        "score_home": event.score_home,
        "score_away": event.score_away,
        "x": event.x,
        "y": event.y,
    }


def _missing_event(event_id: str) -> dict[str, Any]:
    """The label for a cited event id the log does not hold."""
    return {
        "event_id": event_id,
        "found": False,
        "note": (
            "This event id is cited by the insight but is not in the event "
            "log. It is shown as missing; it is not reconstructed."
        ),
    }


def _recompute(
    name: str,
    cited: list[MatchEvent],
    side: TeamSide | None,
    subject: str | None,
) -> tuple[float | None, str | None]:
    """Recompute one displayed value with the deterministic function.

    Returns the recomputed value and an optional note. A player-subject
    metric is recomputed over the cited events that involve the subject;
    when the metric name is unknown, or no cited event involves the
    subject, the value is left empty and the note says why.
    """
    if name not in METRICS:
        return None, f"unknown metric {name!r}: no definition to recompute with"
    events: list[MatchEvent] = cited
    if subject is not None:
        events = [
            event
            for event in cited
            if event.player_name == subject or event.player_id == subject
        ]
        if not events:
            return (
                None,
                (
                    f"no cited event involves {subject!r}, so the value "
                    "cannot be recomputed from the cited set"
                ),
            )
    try:
        return compute_metric(name, events, side), None
    except ValueError as exc:
        return None, str(exc)


def _metric_evidence(
    metric: MetricValue,
    cited: list[MatchEvent],
) -> dict[str, Any]:
    """The definition, the displayed value and the recomputed value."""
    definition = _metric_definition(metric.name, metric.side)
    recomputed, note = _recompute(metric.name, cited, metric.side, metric.subject)
    return {
        "name": metric.name,
        "label": metric.label,
        "unit": metric.unit,
        "side": _side_value(metric.side),
        "subject": metric.subject,
        "definition": definition
        if definition is not None
        else f"no definition is registered for {metric.name!r}",
        "displayed_value": metric.value,
        "recomputed_value": recomputed,
        "match": (
            _close(metric.value, recomputed, metric.unit)
            if recomputed is not None
            else False
        ),
        "note": note,
    }


def _baseline_evidence(
    baseline: BaselineComparison,
    events_by_id: dict[str, MatchEvent],
) -> dict[str, Any]:
    """The earlier half of the comparison, recomputed from its own citations."""
    cited: list[MatchEvent] = []
    missing: list[str] = []
    for event_id in baseline.cited_event_ids:
        event = events_by_id.get(event_id)
        if event is None:
            missing.append(event_id)
        else:
            cited.append(event)
    recomputed, note = _recompute(
        baseline.metric_name, cited, baseline.side, subject=None
    )
    if missing:
        note = (
            note
            or f"{len(missing)} of the baseline's cited events are not in the "
            "log; the recomputation used only the events that were found"
        )
    definition = _metric_definition(baseline.metric_name, baseline.side)
    return {
        "present": True,
        "metric_name": baseline.metric_name,
        "definition": definition
        if definition is not None
        else f"no definition is registered for {baseline.metric_name!r}",
        "unit": baseline.unit,
        "side": _side_value(baseline.side),
        "earlier_value": baseline.value,
        "recomputed_value": recomputed,
        "match": (
            _close(baseline.value, recomputed, baseline.unit)
            if recomputed is not None
            else False
        ),
        "delta": baseline.delta,
        "delta_note": (
            "The recent value minus the earlier value, as recorded when the "
            "insight was computed."
        ),
        "window_ms": baseline.window_ms,
        "cited_event_ids": list(baseline.cited_event_ids),
        "missing_event_ids": missing,
        "note": note,
    }


def _grounding_evidence(
    candidate_id: str, handoffs: list[dict[str, Any]]
) -> dict[str, Any]:
    """What the grounding step said about this insight, as the trace holds it.

    The verdict is read from the recorded hand-offs, not re-derived: a
    veto is a hand-off the grounding step refused, an acceptance is the
    grounding verdict it sent on. When the trace holds neither, that is
    said out loud -- the full-time recap, for instance, never visits the
    grounding step.
    """
    vetoed = any(
        entry.get("accepted") is False and entry.get("from_stage") == "grounding"
        for entry in handoffs
    )
    grounded = any(
        entry.get("accepted") is True and entry.get("from_stage") == "grounding"
        for entry in handoffs
    )
    if vetoed:
        status = "vetoed"
    elif grounded:
        status = "accepted"
    else:
        status = "not_recorded"
    return {
        "candidate_id": candidate_id,
        "status": status,
        "status_note": {
            "accepted": (
                "The grounding step verified this claim against the event "
                "log and accepted it."
            ),
            "vetoed": (
                "The grounding step refused this claim. Its reasons are in "
                "the hand-offs below."
            ),
            "not_recorded": (
                "The trace holds no grounding verdict for this insight -- "
                "not every overlay passes through the grounding step."
            ),
        }[status],
        "handoffs": handoffs,
    }


def _find_overlay(engine: Any, candidate_id: str) -> Overlay | None:
    """The overlay behind a candidate id, or ``None``.

    The studio may pass the candidate id or the full overlay id; both
    resolve, because the point is to find the card, not to enforce a
    spelling.
    """
    for overlay in engine.overlays:
        if (
            overlay_candidate_id(overlay.overlay_id) == candidate_id
            or overlay.overlay_id == candidate_id
        ):
            return overlay
    return None


def _find_candidate(engine: Any, candidate_id: str) -> InsightCandidate | None:
    """The deterministic insight the card came from, or ``None``.

    The full-time recap is deterministic end to end: it has an overlay but
    no candidate, and the drawer says so rather than inventing one.
    """
    for result in engine.results:
        for candidate in result.candidates:
            if candidate.candidate_id == candidate_id:
                return candidate
    return None


@router.get("/{candidate_id}")
async def evidence(
    candidate_id: str, seed: int = Query(default=42)
) -> dict[str, Any]:
    """The evidence behind one insight, recomputed from the event log.

    The payload separates what was *calculated* -- the deterministic
    headline, the metrics, the baseline -- from what was *interpreted* --
    the prose the model wrote. Every metric carries its definition, the
    value on the card, and the value recomputed over the cited events, so
    a judge can check the number without reading source code.

    This route only reads: it starts no model call and it changes no
    audience state. Unknown candidates answer 404.
    """
    engine = get_engine(seed)
    overlay = _find_overlay(engine, candidate_id)
    if overlay is None:
        raise HTTPException(
            status_code=404,
            detail=f"unknown candidate {candidate_id!r} for seed {seed}",
        )
    resolved_id = overlay_candidate_id(overlay.overlay_id)
    candidate = _find_candidate(engine, resolved_id)

    events_by_id = {event.event_id: event for event in engine.events}
    cited_ids = (
        list(candidate.cited_event_ids)
        if candidate is not None
        else list(overlay.cited_event_ids)
    )
    cited_events = [
        _event_payload(events_by_id[event_id])
        if event_id in events_by_id
        else _missing_event(event_id)
        for event_id in cited_ids
    ]
    present_events = [
        events_by_id[event_id] for event_id in cited_ids if event_id in events_by_id
    ]

    if candidate is not None:
        calculated: dict[str, Any] = {
            "present": True,
            "headline": candidate.headline,
            "kind": candidate.kind.value,
            "match_clock_ms": candidate.match_clock_ms,
            "side": _side_value(candidate.side),
            "confidence": candidate.confidence,
            "cited_event_ids": cited_ids,
        }
    else:
        calculated = {
            "present": False,
            "note": (
                "No deterministic candidate record was kept for this overlay. "
                "Its card is written deterministically, so the calculated "
                "facts below come from the overlay itself rather than from "
                "a separate insight record."
            ),
            "cited_event_ids": cited_ids,
        }

    baseline = candidate.baseline if candidate is not None else None
    baseline_evidence: dict[str, Any] = (
        _baseline_evidence(baseline, events_by_id)
        if baseline is not None
        else {
            "present": False,
            "note": "This insight carries no baseline comparison.",
        }
    )

    handoffs = [
        entry.model_dump(mode="json")
        for entry in engine.trace.for_candidate(resolved_id)
    ]

    return {
        "candidate_id": resolved_id,
        "seed": seed,
        "calculated": calculated,
        "interpretation": {
            "title": overlay.title,
            "body": overlay.body,
            "type": overlay.type.value,
            "match_clock_ms": overlay.match_clock_ms,
            "provenance_provider": overlay.provenance_provider,
            "prose_substituted": overlay.prose_substituted,
            "deterministic_prose": overlay.deterministic_prose,
            "note": _PROSE_NOTE,
        },
        "cited_events": cited_events,
        "metrics": [
            _metric_evidence(metric, present_events) for metric in overlay.metrics
        ],
        "baseline": baseline_evidence,
        "grounding": _grounding_evidence(resolved_id, handoffs),
    }
