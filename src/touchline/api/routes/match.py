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

from touchline.agents.audience import LENS_METRICS
from touchline.api.live import (
    get_engine,
    list_seeds,
    overlay_candidate_id,
    provider_payload,
)
from touchline.metrics.definitions import METRICS
from touchline.models.audience import AudienceMode
from touchline.providers.template import SUPPORTED_LOCALES

__all__ = ["router"]

router = APIRouter(prefix="/api/match", tags=["match"])

#: The audiences, in the order the studio offers them, with the label it
#: puts on each one. The chrome is English; the overlays are not.
_MODE_LABELS: dict[str, str] = {
    "analyst": "Analyst",
    "fan": "Fan",
    "player": "Player focus",
    "my_team": "My team",
    "metric_lens": "Metric lens",
}

#: The languages, with the label the studio puts on each one.
_LOCALE_LABELS: dict[str, str] = {
    "en": "English",
    "de": "Deutsch",
    "bn": "বাংলা",
}


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
                "locale": overlay.locale,
                "revision": overlay.revision,
                "provenance_provider": overlay.provenance_provider,
                "prose_substituted": overlay.prose_substituted,
                "deterministic_prose": overlay.deterministic_prose,
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


@router.get("/tracking")
async def tracking(seed: int = Query(default=42)) -> dict[str, Any]:
    """The vision tracker's cross-validation report.

    The tracker is a secondary ingest path: it watches a synthetic feed
    built from the same match, and it is scored against the truth that
    feed was rendered from. The report is produced in the background, so
    it may not be ready the first time it is asked for -- ``ready`` says
    which it is.
    """
    engine = get_engine(seed)
    report = engine.tracking
    if report is None:
        return {"ready": False, "reason": "the tracker is still working"}
    payload = report.model_dump(mode="json")
    payload["ready"] = True
    for key in ("ball", "home", "away", "overall"):
        scores = payload[key]
        scores["precision"] = round(scores["precision"], 4)
        scores["recall"] = round(scores["recall"], 4)
        scores["f1"] = round(scores["f1"], 4)
        scores["mean_error_m"] = round(scores["mean_error_m"], 4)
    return payload


@router.get("/audience")
async def audience(seed: int = Query(default=42)) -> dict[str, Any]:
    """Who the overlays are written for, and who they could be written for.

    The studio's pickers need the five audiences, the three languages, the
    two squads and the metrics a lens can look through. The squad is the
    same one the log carries, so the audience the studio builds is one the
    event log can answer.
    """
    engine = get_engine(seed)
    short_names: dict[str, str] = {}
    for side, payload in engine.teams.items():
        if isinstance(payload, dict):
            short_names[side] = str(payload.get("short_name", ""))
    return {
        "audience": engine.audience.describe(),
        "rewriting": engine.rewriting,
        "rewrite_error": engine.rewrite_error,
        "modes": [
            {"id": mode.value, "label": _MODE_LABELS[mode.value]}
            for mode in AudienceMode
        ],
        "locales": [
            {"id": locale, "label": _LOCALE_LABELS.get(locale, locale)}
            for locale in SUPPORTED_LOCALES
        ],
        "teams": [
            {
                "side": side.value,
                "name": name,
                "short_name": short_names.get(side.value, ""),
            }
            for side, name in engine.team_names.items()
        ],
        "players": engine.roster,
        "metrics": [
            {
                "name": name,
                "label": METRICS[name].label,
                "unit": METRICS[name].unit,
            }
            for name in LENS_METRICS
        ],
    }
