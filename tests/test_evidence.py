"""The evidence inspector's read-only endpoint.

The drawer a judge opens with "Inspect evidence" has to be trustworthy:
it recomputes the numbers with the deterministic functions, it answers
404 for candidates that do not exist, it labels missing evidence instead
of inventing it, and it makes no model calls and changes no audience
state. These tests run over the template engine, so every recompute is
fully deterministic.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from touchline.api.main import app
from touchline.metrics.definitions import compute_metric
from touchline.models.events import TeamSide

#: How long to wait for the whole match to be analysed. Over the template
#: engine the whole match takes about ten seconds; the waits below are
#: generous because a loaded machine can be slower.
STARTUP_TIMEOUT = 90.0


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    """The service, with the match already being analysed."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def completed(client: TestClient) -> None:
    """Wait until the analysis has reached the end of the match.

    Provider counters are only stable once the analysis is finished, so
    the no-model-call test needs the whole match, not just the first
    insight.
    """
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        summary = client.get("/api/match").json()
        if summary["computed_ms"] >= summary["duration_ms"]:
            return
        time.sleep(0.5)
    raise AssertionError("the engine did not finish the match in time")


@pytest.fixture(scope="module")
def insights(client: TestClient, completed: None) -> list[dict]:
    """Every insight the engine computed, in clock order."""
    return client.get("/api/match/insights").json()["insights"]


# -- unknown candidates --------------------------------------------------------

# An id that was never issued, and one that looks almost right. Neither
# may leak anything about the rest of the match.
@pytest.mark.parametrize("candidate_id", ["does-not-exist", "cand-9999"])
def test_unknown_candidates_answer_404(
    client: TestClient, completed: None, candidate_id: str
) -> None:
    """A candidate id the engine never produced is a 404, not an empty page."""
    response = client.get(f"/api/evidence/{candidate_id}")
    assert response.status_code == 404
    assert candidate_id in response.json()["detail"]


# -- the happy path ------------------------------------------------------------


def test_the_full_overlay_id_spelling_also_resolves(
    client: TestClient, insights: list[dict]
) -> None:
    """The studio may pass the overlay id; the point is the card, not the spelling."""
    candidate_id = insights[0]["candidate_id"]
    overlay_id = insights[0]["overlay_id"]
    assert overlay_id != candidate_id
    payload = client.get(f"/api/evidence/{overlay_id}").json()
    assert payload["candidate_id"] == candidate_id


def test_the_endpoint_separates_calculated_facts_from_interpretation(
    client: TestClient, insights: list[dict]
) -> None:
    """The calculated headline and the model-written prose are shown apart."""
    candidate_id = insights[0]["candidate_id"]
    payload = client.get(f"/api/evidence/{candidate_id}").json()

    assert payload["candidate_id"] == candidate_id
    assert payload["seed"] == 42

    calculated = payload["calculated"]
    assert calculated["present"] is True
    assert calculated["headline"], "the deterministic headline is the calculated fact"

    interpretation = payload["interpretation"]
    assert interpretation["title"]
    assert interpretation["body"]
    assert "not formally verified" in interpretation["note"]

    events = payload["cited_events"]
    assert events, "the evidence names the events the insight rests on"
    assert all(entry["found"] for entry in events)
    assert all(entry["match_clock_ms"] >= 0 for entry in events)


def test_every_metric_carries_definition_displayed_and_recomputed(
    client: TestClient, insights: list[dict]
) -> None:
    """A judge can read what the number means and check it."""
    candidate_id = insights[0]["candidate_id"]
    payload = client.get(f"/api/evidence/{candidate_id}").json()

    metrics = payload["metrics"]
    assert metrics
    for metric in metrics:
        assert metric["name"]
        assert metric["definition"], f"no definition for {metric['name']}"
        assert isinstance(metric["displayed_value"], float)
        assert isinstance(metric["recomputed_value"], float)
        assert metric["match"] is True, (
            f"{metric['name']}: displayed {metric['displayed_value']} does not "
            f"match recomputed {metric['recomputed_value']}"
        )
        assert metric["note"] is None


def test_the_recomputed_values_use_the_deterministic_functions(
    client: TestClient, insights: list[dict]
) -> None:
    """The endpoint is not a second implementation: it calls compute_metric.

    The test recomputes independently, over the cited events, with the
    same subject rule the endpoint documents, and expects the same
    number.
    """
    candidate_id = insights[0]["candidate_id"]
    payload = client.get(f"/api/evidence/{candidate_id}").json()

    cited_ids = [entry["event_id"] for entry in payload["cited_events"]]
    assert cited_ids

    from touchline.api.live import get_engine

    engine = get_engine(42)
    by_id = {event.event_id: event for event in engine.events}
    for metric in payload["metrics"]:
        events_for_metric = [by_id[event_id] for event_id in cited_ids]
        if metric["subject"] is not None:
            events_for_metric = [
                event
                for event in events_for_metric
                if event.player_name == metric["subject"]
                or event.player_id == metric["subject"]
            ]
        side = TeamSide(metric["side"]) if metric["side"] else None
        expected = compute_metric(metric["name"], events_for_metric, side)
        assert metric["recomputed_value"] == pytest.approx(expected)


def test_the_grounding_verdict_comes_from_the_trace(
    client: TestClient, insights: list[dict]
) -> None:
    """The verdict is the grounding step's own verdict, as recorded."""
    candidate_id = insights[0]["candidate_id"]
    payload = client.get(f"/api/evidence/{candidate_id}").json()

    grounding = payload["grounding"]
    assert grounding["status"] == "accepted"
    assert grounding["status_note"]
    handoffs = grounding["handoffs"]
    assert handoffs
    verdicts = [entry for entry in handoffs if entry["from_stage"] == "grounding"]
    assert any(entry["accepted"] is True for entry in verdicts)
    # The related hand-offs ride along, so the drawer can show them.
    assert all(entry["candidate_id"] == candidate_id for entry in handoffs)


def test_an_insight_with_a_baseline_shows_the_earlier_evidence(
    client: TestClient, insights: list[dict]
) -> None:
    """The baseline is recomputed from its own cited events."""
    baselined = next(
        insight
        for insight in insights
        if client.get(f"/api/evidence/{insight['candidate_id']}").json()[
            "baseline"
        ]["present"]
    )
    payload = client.get(f"/api/evidence/{baselined['candidate_id']}").json()
    baseline = payload["baseline"]
    assert baseline["metric_name"]
    assert isinstance(baseline["earlier_value"], float)
    assert isinstance(baseline["recomputed_value"], float)
    assert baseline["match"] is True
    assert baseline["missing_event_ids"] == []
    assert isinstance(baseline["delta"], float)

    without = client.get(f"/api/evidence/{insights[0]['candidate_id']}").json()[
        "baseline"
    ]
    assert without["present"] is False
    assert "no baseline" in without["note"]


# -- missing evidence ----------------------------------------------------------


def test_the_deterministic_recap_labels_its_missing_candidate_record(
    client: TestClient, insights: list[dict]
) -> None:
    """The full-time recap has an overlay but no insight behind it.

    The drawer must say so -- labelling the gap -- instead of treating
    the overlay as if a candidate had been recorded.
    """
    recap = next(
        insight for insight in insights if insight["candidate_id"] == "recap"
    )
    assert recap["metrics"], "the test only makes sense if the recap has numbers"
    payload = client.get("/api/evidence/recap").json()

    assert payload["calculated"]["present"] is False
    assert "note" in payload["calculated"]
    assert payload["metrics"], "the recap's own numbers are still shown"
    assert all(metric["match"] is True for metric in payload["metrics"])
    assert payload["grounding"]["status"] == "not_recorded"


# -- read-only guarantees -------------------------------------------------------


def test_the_drawer_changes_no_audience_state(
    client: TestClient, insights: list[dict]
) -> None:
    """Opening evidence must not move the reader: the audience is service state."""
    before = client.get("/api/match/audience").json()["audience"]
    for insight in insights[:3]:
        response = client.get(f"/api/evidence/{insight['candidate_id']}")
        assert response.status_code == 200
    after = client.get("/api/match/audience").json()["audience"]
    assert after == before


def test_the_drawer_makes_no_model_calls(
    client: TestClient, insights: list[dict]
) -> None:
    """The endpoint reads and recomputes; it never asks a provider.

    The provider counters are only stable once the analysis is finished,
    which the `insights` fixture guarantees.
    """
    before = client.get("/healthz").json()["provider"]
    for insight in insights[:5]:
        response = client.get(f"/api/evidence/{insight['candidate_id']}")
        assert response.status_code == 200
    after = client.get("/healthz").json()["provider"]
    assert after["call_count"] == before["call_count"]
    assert after["fallback_count"] == before["fallback_count"]
    assert after["substitution_count"] == before["substitution_count"]
