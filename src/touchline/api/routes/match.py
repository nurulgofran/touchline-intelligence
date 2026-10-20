"""The match, over HTTP.

The WebSocket is the live path: it is what the studio watches. These
routes are the *rest* of it — what a page load needs before the socket
opens, what a judge might paste into a browser to see the feed as JSON,
and what the tests read.

Every route reports **what has been computed so far**, never what is
scheduled. The engine analyses the match as fast as the model allows and
the clock may be ahead of it or behind it; a route that reported the
schedule would be claiming an insight that has not been computed yet.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from touchline.api.live import (
    get_engine,
    list_seeds,
    overlay_candidate_id,
    provider_payload,
)

__all__ = ["router"]

router = APIRouter(prefix="/api/match", tags=["match"])


@router.get("")
async def match(seed: int = Query(default=42)) -> dict[str, Any]:
    """The match, in brief.

    This is what the studio fetches before it opens the socket: the two
    clubs, the provider state, and how much of the match has been
    analysed.
    """
    engine = get_engine(seed)
    overlays = engine.overlays
    return {
        "match_id": engine.match_id,
        "seed": engine.seed,
        "teams": engine.teams,
        "duration_ms": engine.duration_ms,
        "stretch_ms": engine.stretch_ms,
        "computed_ms": engine.computed_ms,
        "insights": len(overlays),
        "events": len(engine.events),
        "provider": provider_payload(),
        "seeds": list_seeds(),
    }


@router.get("/insights")
async def insights(seed: int = Query(default=42)) -> dict[str, Any]:
    """The insights computed so far, in clock order.

    Each one carries its metrics and the ids of the events it was
    computed from, so a reader can check the number against the log.
    """
    engine = get_engine(seed)
    overlays = engine.overlays
    return {
        "count": len(overlays),
        "computed_ms": engine.computed_ms,
        "insights": [
            {
                "candidate_id": overlay_candidate_id(overlay.overlay_id),
                "overlay_id": overlay.overlay_id,
                "type": overlay.type.value,
                "match_clock_ms": overlay.match_clock_ms,
                "title": overlay.title,
                "body": overlay.body,
                "metrics": [
                    metric.model_dump(mode="json") for metric in overlay.metrics
                ],
                "cited_event_ids": list(overlay.cited_event_ids),
                "confidence": overlay.confidence,
            }
            for overlay in overlays
        ],
    }


@router.get("/momentum")
async def momentum(seed: int = Query(default=42)) -> dict[str, Any]:
    """The momentum curve, for the chart in the studio."""
    engine = get_engine(seed)
    points = engine.momentum
    return {
        "count": len(points),
        "points": [
            {
                "match_clock_ms": point.match_clock_ms,
                "home": point.home,
                "away": point.away,
            }
            for point in points
        ],
    }


@router.get("/trace")
async def trace(seed: int = Query(default=42)) -> dict[str, Any]:
    """The ledger of hand-offs.

    This is the record of who passed what to whom: the analyst to the
    explainer, the explainer to the grounding step, the grounding step
    back to the explainer when it refuses a claim, and the narrator to the
    personaliser.
    """
    engine = get_engine(seed)
    entries = engine.trace.as_list()
    return {
        "count": len(entries),
        "vetoes": len(engine.trace.vetoes()),
        "entries": [entry.model_dump(mode="json") for entry in entries],
    }
