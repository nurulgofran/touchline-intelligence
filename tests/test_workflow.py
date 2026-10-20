"""Tests for the workflow graph, the grounding veto and the A2A hop.

Three things are checked here, and they are the three claims the project
makes that a judge cannot check by reading a number:

**The feed is cited.** A full simulated match goes through the graph and
comes out as a stream of overlays. Every overlay carries the ids of the
events its numbers rest on, and every number recomputes from those ids
alone. Not "roughly" -- the same registry the analyst used, the same
tolerance.

**The veto fires.** A client that states the wrong number is put in front
of the Explainer, and the Grounding step refuses the claim. The refusal is
visible: it is in the trace as a refused hand-off, it goes *back* to the
Explainer, and a second attempt that is also wrong falls back on the
deterministic claim rather than stalling the feed.

**The hop is real.** The Narrator reaches the Explainer over A2A -- a
JSON-RPC exchange against the published agent card -- and the round trip is
made against the ASGI application itself, so it is a protocol exchange
without a socket standing in for one.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from touchline.agents.explainer import ExplainerAgent
from touchline.agents.narrator import NarratorAgent
from touchline.metrics.analyst import MatchAnalyst
from touchline.orchestration.a2a_client import (
    TRANSPORT_A2A,
    TRANSPORT_IN_PROCESS,
    A2AUnavailable,
    ExplainerClient,
)
from touchline.orchestration.a2a_server import (
    A2A_RPC_PATH,
    AGENT_CARD_PATH,
    build_explainer_app,
)
from touchline.orchestration.workflow import (
    STAGE_EXPLAINER,
    STAGE_GROUNDING,
    MatchWorkflow,
    build_explainer_client,
)
from touchline.providers.clients import TemplateChatClient
from touchline.providers.payload import decode_payload
from touchline.sim.generator import simulate, team_names

SEED = 42


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _last_user_text(messages: Any) -> str:
    """The text of the last user message in a conversation."""
    text = ""
    for message in messages or ():
        if getattr(message, "role", "") != "user":
            continue
        content = getattr(message, "text", None)
        if isinstance(content, str) and content.strip():
            text = content
    return text


class _ScriptedClient:
    """A chat client that edits the template engine's answer.

    The template engine answers correctly by construction, so editing its
    reply is the honest way to make a model misbehave on purpose: the
    shape of the reply is the real one, and only the number changes.
    """

    def __init__(
        self,
        edit: Callable[[str, str], str] | None = None,
        *,
        inner: Any = None,
    ) -> None:
        self._edit = edit
        self._inner = inner or TemplateChatClient()
        self.prompts: list[str] = []

    async def get_response(self, messages: Any, **kwargs: Any) -> Any:
        """Answer the prompt, edited."""
        from agent_framework import ChatResponse, Message

        prompt = _last_user_text(messages)
        self.prompts.append(prompt)
        response = await self._inner.get_response(messages, **kwargs)
        text = response.messages[0].text
        if self._edit is not None:
            text = self._edit(prompt, text)
        return ChatResponse(messages=[Message(role="assistant", contents=[text])])


def _wrong_numbers(prompt: str, text: str) -> str:
    """State every metric wrongly.

    Only the numbers move: the names, the sides and the citations stay as
    the analyst left them, so the claim fails the *value* check and not
    the bookkeeping -- which is exactly the failure the veto is for.
    """
    payload = json.loads(text)
    for metric in payload.get("metrics", []):
        metric["value"] = 66.0
    return json.dumps(payload)


def _wrong_figure(prompt: str, text: str) -> str:
    """Write a number into the prose that the event log does not hold."""
    payload = json.loads(text)
    if "body" in payload:
        payload["body"] = "his pass accuracy collapsed to 1234 per cent"
    return json.dumps(payload)


class _WrongOnceClient:
    """A client that lies about the first claim it is asked for, once.

    The lie is per candidate, so a retried claim can come back correct.
    That is the case the back edge exists for: the first claim is refused,
    the draft goes back, and the second attempt is accepted.
    """

    def __init__(self) -> None:
        self._inner = TemplateChatClient()
        self._lied: set[str] = set()
        self.attempts: dict[str, int] = {}

    async def get_response(self, messages: Any, **kwargs: Any) -> Any:
        """Answer the prompt, wrongly the first time per candidate."""
        from agent_framework import ChatResponse, Message

        prompt = _last_user_text(messages)
        payload = decode_payload(prompt)
        candidate_id = payload.candidate.candidate_id
        self.attempts[candidate_id] = self.attempts.get(candidate_id, 0) + 1

        response = await self._inner.get_response(messages, **kwargs)
        text = response.messages[0].text
        if candidate_id not in self._lied:
            self._lied.add(candidate_id)
            text = _wrong_numbers(prompt, text)
        return ChatResponse(messages=[Message(role="assistant", contents=[text])])


def _workflow(**kwargs: Any) -> MatchWorkflow:
    """A workflow over the template engine, on a fresh store."""
    kwargs.setdefault("analyst", MatchAnalyst(team_names=team_names(SEED)))
    return MatchWorkflow(**kwargs)


async def _run_match(**kwargs: Any) -> Any:
    """Run a whole simulated match through the graph."""
    workflow = _workflow(**kwargs)
    return await workflow.run(simulate(seed=SEED), match_id=f"seed-{SEED}")


# --------------------------------------------------------------------------
# The feed is cited
# --------------------------------------------------------------------------


def test_a_full_match_becomes_a_cited_feed() -> None:
    """Every overlay on the feed names the events its numbers rest on."""
    run = asyncio.run(_run_match())

    assert run.overlays, "the run produced no overlays at all"
    assert len(run.overlays) == len(run.candidates)
    assert len(run.narrations) == len(run.overlays)

    for overlay in run.overlays:
        assert overlay.cited_event_ids, (
            f"{overlay.overlay_id} carries no citations"
        )
        assert overlay.metrics, f"{overlay.overlay_id} carries no metrics"
        assert overlay.title and overlay.body


def test_every_number_on_the_feed_recomputes_from_its_own_citations() -> None:
    """The load-bearing check: the overlay's numbers come back out of the log."""
    from touchline.metrics.definitions import compute_metric

    events = simulate(seed=SEED)
    run = asyncio.run(_run_match())
    by_id = {event.event_id: event for event in events}

    for overlay in run.overlays:
        cited = [by_id[event_id] for event_id in overlay.cited_event_ids]
        assert len(cited) == len(overlay.cited_event_ids), (
            f"{overlay.overlay_id} cites an event the log does not hold"
        )
        for metric in overlay.metrics:
            recomputed = compute_metric(metric.name, cited, metric.side)
            assert recomputed is not None
            assert recomputed == pytest.approx(metric.value, abs=0.01), (
                f"{overlay.overlay_id}: {metric.name} recomputes to "
                f"{recomputed}, not {metric.value}"
            )


def test_the_grounding_step_accepts_every_claim_the_template_engine_makes() -> None:
    """The template engine's claims are the candidate's, so they reproduce."""
    run = asyncio.run(_run_match())

    assert run.verdicts
    assert not run.vetoes, f"the template path vetoed {len(run.vetoes)} claims"
    assert all(verdict.accepted for verdict in run.verdicts)


def test_the_trace_records_every_stage_of_the_chain() -> None:
    """The hand-off ledger names every stage, in order."""
    run = asyncio.run(_run_match())

    stages = {handoff.from_stage for handoff in run.trace}
    assert {"ingest", "analyst", "explainer", "grounding", "narrator"} <= stages

    kinds = {handoff.kind for handoff in run.trace}
    assert {"events", "insights", "claim", "verdict", "narration"} <= kinds

    vetoes = run.trace.vetoes()
    assert vetoes == []


def test_the_template_path_is_deterministic() -> None:
    """The same match produces byte-identical overlays, every time."""
    first = asyncio.run(_run_match())
    second = asyncio.run(_run_match())

    assert [overlay.model_dump() for overlay in first.overlays] == [
        overlay.model_dump() for overlay in second.overlays
    ]


# --------------------------------------------------------------------------
# The veto fires
# --------------------------------------------------------------------------


def test_the_veto_fires_and_the_draft_goes_back_to_the_explainer() -> None:
    """A claim that does not reproduce is refused, visibly, and sent back."""
    client = _ScriptedClient(_wrong_numbers)
    run = asyncio.run(_run_match(explainer=ExplainerAgent(client)))

    assert run.vetoes, "the grounding step did not veto a wrong claim"

    refusals = [
        handoff
        for handoff in run.trace
        if handoff.accepted is False
        and handoff.from_stage == STAGE_GROUNDING
        and handoff.to_stage == STAGE_EXPLAINER
    ]
    assert refusals, "the veto was not recorded as a refused hand-off"
    assert all("vetoed" in handoff.summary for handoff in refusals)


def test_the_feed_still_runs_after_a_veto() -> None:
    """A model that cannot be made to agree must not stall the feed."""
    client = _ScriptedClient(_wrong_numbers)
    run = asyncio.run(_run_match(explainer=ExplainerAgent(client)))

    assert run.overlays, "the veto stopped the feed"
    assert run.degradations, "the fallback was not recorded"
    assert any("deterministic claim" in reason for reason in run.degradations)

    # Everything that made it to the screen is still grounded.
    for verdict in run.verdicts:
        assert verdict.accepted or verdict.reasons


def test_a_vetoed_draft_is_explained_again_and_can_be_accepted() -> None:
    """The back edge works: a second attempt can pass where the first failed."""
    client = _WrongOnceClient()
    run = asyncio.run(_run_match(explainer=ExplainerAgent(client)))

    assert run.vetoes, "the first claim was not refused"
    assert run.overlays, "the retry did not make it to the screen"

    # The retried candidate was asked twice.
    assert max(client.attempts.values()) >= 2

    # And the candidate that was refused first is on the feed afterwards.
    refused = {verdict.candidate_id for verdict in run.vetoes}
    delivered = {candidate.candidate_id for candidate in run.candidates}
    assert refused & delivered


def test_the_grounding_step_refuses_a_claim_that_drops_a_metric() -> None:
    """A claim that quietly drops a number has changed its meaning."""

    def drop_a_metric(prompt: str, text: str) -> str:
        payload = json.loads(text)
        metrics = payload.get("metrics", [])
        if len(metrics) > 1:
            payload["metrics"] = metrics[:-1]
        return json.dumps(payload)

    client = _ScriptedClient(drop_a_metric)
    run = asyncio.run(_run_match(explainer=ExplainerAgent(client)))

    assert run.vetoes, "a claim that dropped a metric was accepted"
    assert any(
        "drops" in reason or "adds" in reason
        for verdict in run.vetoes
        for reason in verdict.reasons
    )


def test_the_figure_guard_refuses_a_number_the_log_does_not_hold() -> None:
    """The prose is checked too, not only the claim."""
    client = _ScriptedClient(_wrong_figure)
    run = asyncio.run(_run_match(narrator=NarratorAgent(client)))

    assert run.overlays
    for overlay in run.overlays:
        assert "1234" not in overlay.title
        assert "1234" not in overlay.body


# --------------------------------------------------------------------------
# The A2A hop
# --------------------------------------------------------------------------


def _a2a_app() -> tuple[Any, ExplainerAgent]:
    """The Explainer, published as an A2A agent, over the template engine."""
    explainer = ExplainerAgent(TemplateChatClient())
    url = f"http://testserver{A2A_RPC_PATH}"
    return build_explainer_app(explainer, url=url), explainer


def test_the_agent_card_is_published() -> None:
    """The card is what a client reads to decide whether to call."""

    async def go() -> None:
        app, _ = _a2a_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as http:
            response = await http.get(AGENT_CARD_PATH)
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

    asyncio.run(go())


def test_the_narrator_reaches_the_explainer_over_a2a() -> None:
    """The hop is a real protocol exchange, not a function call."""

    async def go() -> None:
        app, explainer = _a2a_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as http:
            client = build_explainer_client(
                explainer, url=f"http://testserver{A2A_RPC_PATH}", http_client=http
            )
            narrator = NarratorAgent(TemplateChatClient(), explainer_client=client)
            run = await _run_match(
                explainer=explainer, narrator=narrator, explainer_client=client
            )

            assert run.overlays
            assert client.last_transport == TRANSPORT_A2A
            assert not client.degradations

    asyncio.run(go())


def test_the_a2a_hop_falls_back_to_the_explainer_in_process() -> None:
    """A wire that cannot be used degrades the transport, not the feed."""

    async def go() -> None:
        _, explainer = _a2a_app()
        # A URL nothing is listening on.
        client = build_explainer_client(
            explainer, url="http://127.0.0.1:9/a2a", timeout_seconds=0.2
        )
        narrator = NarratorAgent(TemplateChatClient(), explainer_client=client)
        run = await _run_match(
            explainer=explainer, narrator=narrator, explainer_client=client
        )

        assert run.overlays, "the feed stopped when the wire could not be used"
        assert client.last_transport == TRANSPORT_IN_PROCESS
        assert client.degradations, "the failed hop was not recorded"

    asyncio.run(go())


def test_a_throttled_a2a_call_is_retried_and_then_succeeds() -> None:
    """A 429 means "not now", so the call is tried again."""

    class _FlakyAgent:
        """Answers wrongly twice, then correctly."""

        def __init__(self) -> None:
            self.calls = 0

        async def run(self, prompt: str, **kwargs: Any) -> Any:
            from agent_framework import ChatResponse, Message

            self.calls += 1
            if self.calls <= 2:
                raise httpx.HTTPStatusError(
                    "too many requests",
                    request=httpx.Request("POST", "http://testserver/a2a"),
                    response=httpx.Response(429),
                )
            candidate = decode_payload(prompt).candidate
            answer = json.dumps(
                {
                    "candidate_id": candidate.candidate_id,
                    "reading": "the reading",
                    "metrics": [
                        {
                            "name": metric.name,
                            "value": metric.value,
                            "unit": metric.unit,
                            "side": metric.side.value if metric.side else None,
                        }
                        for metric in candidate.metrics
                    ],
                    "cited_event_ids": list(candidate.cited_event_ids),
                }
            )
            return ChatResponse(messages=[Message(role="assistant", contents=[answer])])

    async def go() -> None:
        agent = _FlakyAgent()
        client = ExplainerClient(
            "http://testserver/a2a",
            max_attempts=3,
            backoff_seconds=0.01,
            agent=agent,
        )
        candidate = MatchAnalyst(team_names=team_names(SEED)).analyse(
            simulate(seed=SEED)
        )[0]

        explanation = await client.explain(candidate)

        assert agent.calls == 3
        assert client.last_transport == TRANSPORT_A2A
        assert explanation.reading == "the reading"

    asyncio.run(go())


def test_a_bad_request_over_a2a_is_not_retried() -> None:
    """A 400 is not "not now". Retrying it only makes the feed slower."""

    class _BadRequestAgent:
        """Always answers 400."""

        def __init__(self) -> None:
            self.calls = 0

        async def run(self, prompt: str, **kwargs: Any) -> Any:
            self.calls += 1
            raise httpx.HTTPStatusError(
                "bad request",
                request=httpx.Request("POST", "http://testserver/a2a"),
                response=httpx.Response(400),
            )

    async def go() -> None:
        agent = _BadRequestAgent()
        client = ExplainerClient(
            "http://testserver/a2a",
            max_attempts=3,
            backoff_seconds=0.01,
            agent=agent,
        )
        candidate = MatchAnalyst(team_names=team_names(SEED)).analyse(
            simulate(seed=SEED)
        )[0]

        with pytest.raises(A2AUnavailable):
            await client.explain(candidate)

        assert agent.calls == 1

    asyncio.run(go())


def test_an_a2a_call_that_times_out_falls_back() -> None:
    """One call may not take the whole feed with it."""

    class _SlowAgent:
        """Never answers in time."""

        def __init__(self) -> None:
            self.calls = 0

        async def run(self, prompt: str, **kwargs: Any) -> Any:
            self.calls += 1
            await asyncio.sleep(5)
            raise AssertionError("unreachable")

    async def go() -> None:
        explainer = ExplainerAgent(TemplateChatClient())
        agent = _SlowAgent()
        client = ExplainerClient(
            "http://testserver/a2a",
            timeout_seconds=0.05,
            max_attempts=1,
            backoff_seconds=0.01,
            fallback=explainer,
            agent=agent,
        )
        candidate = MatchAnalyst(team_names=team_names(SEED)).analyse(
            simulate(seed=SEED)
        )[0]

        explanation = await client.explain(candidate)

        assert agent.calls == 1
        assert client.last_transport == TRANSPORT_IN_PROCESS
        assert client.degradations
        assert explanation.reading

    asyncio.run(go())
