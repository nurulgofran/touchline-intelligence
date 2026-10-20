"""The HTTP and WebSocket surface the studio talks to.

Three things are being checked here, and they are the three things a
judge will touch:

* the **health** endpoint answers, and says which provider is really
  serving -- the Azure Container Apps probe depends on it;
* the **agent card** is published at the path the A2A specification fixes,
  because the card is what a client reads to decide whether to call;
* the **socket** carries the match: the clock, the events, the insights
  with their citations, and the controls that move the clock.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from touchline.api.main import app
from touchline.orchestration.a2a_server import A2A_RPC_PATH, AGENT_CARD_PATH

#: How long to wait for the engine to finish analysing the match. Over the
#: template engine the whole match takes a few seconds.
STARTUP_TIMEOUT = 60.0


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    """The service, with the match already being analysed."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def analysed(client: TestClient) -> list[dict]:
    """The insights the engine has computed by the time the tests run."""
    deadline = time.monotonic() + STARTUP_TIMEOUT
    insights: list[dict] = []
    while time.monotonic() < deadline:
        payload = client.get("/api/match/insights").json()
        insights = payload["insights"]
        if insights:
            return insights
        time.sleep(0.2)
    raise AssertionError("the engine produced no insights in time")


# -- health ----------------------------------------------------------------


def test_the_health_endpoint_answers(client: TestClient) -> None:
    """The probe's endpoint, with the provider state attached."""
    response = client.get("/healthz")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "ok"
    assert "provider" in body
    assert body["provider"]["configured_provider"]
    assert "match" in body
    assert body["match"]["seed"]


def test_the_provider_badge_never_contradicts_itself(client: TestClient) -> None:
    """The badge may not claim a provider that did not answer.

    The rules are exact: before any model is configured the badge reads
    "Template engine (model not configured)" and carries **no** chip. A
    badge that said "fallback" next to that would be saying two
    contradictory things at once -- that the model was never there *and*
    that something failed.
    """
    provider = client.get("/healthz").json()["provider"]
    if not provider["model_configured"]:
        assert provider["last_call_state"] is None, (
            "the badge shows a fallback chip when no model was ever configured"
        )
        assert provider["configured_provider"] == (
            "Template engine (model not configured)"
        )
    else:
        assert provider["last_call_state"] in {"live", "fallback", None}
        if provider["last_call_provider"]:
            assert provider["last_call_provider"] in {
                "Microsoft Foundry",
                "Template engine",
            }


# -- the A2A card -----------------------------------------------------------


def test_the_agent_card_is_published(client: TestClient) -> None:
    """The card is what a client reads to decide whether to call."""
    response = client.get(AGENT_CARD_PATH)
    assert response.status_code == 200

    card = response.json()
    assert card["name"]
    assert card["skills"], "the card publishes no skill"
    skill = card["skills"][0]
    assert skill["id"]
    assert skill["description"]
    assert skill["inputModes"]
    assert skill["outputModes"]

    interface = card["supportedInterfaces"][0]
    assert interface["protocolBinding"] == "JSONRPC"
    assert interface["url"].endswith(A2A_RPC_PATH)


# -- the match over REST ---------------------------------------------------


def test_the_match_route_describes_the_match(client: TestClient) -> None:
    """What the studio fetches before it opens the socket."""
    body = client.get("/api/match").json()

    assert body["seed"]
    assert body["duration_ms"] == 5_400_000
    assert body["stretch_ms"] == 90_000
    assert body["events"] > 0
    assert body["teams"]["home"]["name"]
    assert body["teams"]["away"]["name"]
    assert len(body["teams"]["home"]["primary_colour"]) == 3


def test_the_insights_carry_their_citations(
    client: TestClient, analysed: list[dict]
) -> None:
    """Every insight says which events it was computed from.

    This is what makes the claim checkable, and it is the part of the
    product the grounding step exists to protect.
    """
    for insight in analysed:
        assert insight["cited_event_ids"], (
            f"{insight['overlay_id']} cites nothing"
        )
        assert insight["title"]
        assert insight["body"]
        assert insight["metrics"], f"{insight['overlay_id']} reports no metric"


def test_the_trace_shows_the_a2a_hop(
    client: TestClient, analysed: list[dict]
) -> None:
    """The ledger records the hop from the narrator to the explainer.

    A multi-agent system is only as convincing as its hand-offs, and the
    A2A hop is the one a judge cannot see by looking at a pipeline
    diagram. It has to be in the trace.
    """
    body = client.get("/api/match/trace").json()
    kinds = {entry["kind"] for entry in body["entries"]}
    assert "a2a" in kinds, "the trace records no A2A hop"
    assert "claim" in kinds
    assert "verdict" in kinds

    hop = next(e for e in body["entries"] if e["kind"] == "a2a")
    assert hop["from_stage"] == "narrator"
    assert hop["to_stage"] == "explainer"
    assert "a2a" in hop["summary"]


def test_the_momentum_curve_is_served(client: TestClient) -> None:
    """The chart's points, one per minute of match time."""
    body = client.get("/api/match/momentum").json()
    assert body["count"] > 0
    clocks = [point["match_clock_ms"] for point in body["points"]]
    assert clocks == sorted(clocks)


# -- the live socket -------------------------------------------------------


def test_the_socket_greets_with_the_match(client: TestClient) -> None:
    """The first frame says what the studio is looking at."""
    with client.websocket_connect("/ws/match?seed=42") as socket:
        hello = socket.receive_json()

    assert hello["type"] == "hello"
    assert hello["seed"] == 42
    assert hello["duration_ms"] == 5_400_000
    assert hello["speed"] == 30.0
    assert hello["provider"]["configured_provider"]
    assert hello["teams"]["home"]["name"]
    assert hello["team_names"]["away"]


def test_the_socket_sends_the_clock_and_the_events(client: TestClient) -> None:
    """The clock runs and the events arrive with it."""
    with client.websocket_connect("/ws/match?seed=42") as socket:
        socket.receive_json()  # hello
        frames = [socket.receive_json() for _ in range(12)]

    kinds = {frame["type"] for frame in frames}
    assert "clock" in kinds
    assert "events" in kinds

    clock = next(f for f in frames if f["type"] == "clock")
    assert clock["clock_ms"] >= 0
    assert "score_home" in clock
    assert "complete" in clock


def test_the_controls_move_the_clock(
    client: TestClient, analysed: list[dict]
) -> None:
    """Pause stops the clock; seek and next move it.

    The controls are the demo's pacing: a judge who wants to see the
    next big moment must not have to wait for the clock to get there.
    """
    with client.websocket_connect("/ws/match?seed=42") as socket:
        socket.receive_json()  # hello

        socket.send_json({"type": "control", "action": "pause"})
        socket.send_json({"type": "control", "action": "seek", "value": 1_200_000})
        time.sleep(0.4)

        clocks = []
        for _ in range(10):
            frame = socket.receive_json()
            if frame["type"] == "clock":
                clocks.append(frame["clock_ms"])
        assert clocks, "no clock frame arrived"
        assert clocks[-1] == 1_200_000, "the seek did not land"

        # Next moment: the clock jumps to the following insight. The frames
        # already in flight carry the old clock, so the test waits for the
        # new one rather than looking at the first frame that arrives.
        target = next(
            insight["match_clock_ms"]
            for insight in analysed
            if insight["match_clock_ms"] > 1_200_000
        )
        socket.send_json({"type": "control", "action": "next"})
        time.sleep(0.4)
        seen = []
        for _ in range(20):
            frame = socket.receive_json()
            if frame["type"] == "clock":
                seen.append(frame["clock_ms"])
                if frame["clock_ms"] == target:
                    break
        assert target in seen, f"the clock never reached {target}: {seen}"


def test_the_socket_shows_an_insight_when_the_clock_reaches_it(
    client: TestClient, analysed: list[dict]
) -> None:
    """The insight arrives with its metrics and its citations."""
    target = analysed[0]["match_clock_ms"]
    with client.websocket_connect("/ws/match?seed=42") as socket:
        socket.receive_json()  # hello
        socket.send_json({"type": "control", "action": "pause"})
        socket.send_json({"type": "control", "action": "seek", "value": target})
        time.sleep(0.4)

        overlay = None
        for _ in range(40):
            frame = socket.receive_json()
            if frame["type"] == "overlay":
                overlay = frame
                break
            if frame["type"] == "clock" and frame["clock_ms"] > target + 60_000:
                break

    assert overlay is not None, "the insight was never shown"
    assert overlay["clock_ms"] == target
    assert overlay["overlay"]["title"]
    assert overlay["overlay"]["body"]
    assert overlay["overlay"]["cited_event_ids"]
    assert overlay["overlay"]["metrics"]


def test_the_socket_survives_a_bad_control(client: TestClient) -> None:
    """A nonsense control does not kill the feed."""
    with client.websocket_connect("/ws/match?seed=42") as socket:
        socket.receive_json()  # hello
        socket.send_json({"type": "control", "action": "wobble"})
        socket.send_json({"type": "control", "action": "speed", "value": "fast"})
        time.sleep(0.4)
        frame = socket.receive_json()
    assert frame["type"] in {"clock", "events", "momentum", "trace", "provider"}
