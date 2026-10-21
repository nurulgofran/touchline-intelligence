"""Accurate provider and degradation reporting.

Three things are being checked:

* every provider result is recorded with its stage, candidate and
  audience revision, from request-local context, so concurrent rewrites
  cannot mix attribution;
* prose-validation substitutions are recorded separately from
  model-call failures, and never move the model-call counts;
* when validated prose is rejected, the card shows deterministic copy in
  the requested audience and language -- not an earlier stage's copy --
  and the card says so about itself.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from touchline.agents.audience import build_recap
from touchline.live.engine import LiveMatchEngine
from touchline.metrics.analyst import MatchAnalyst
from touchline.models.audience import Audience, AudienceMode
from touchline.models.claims import Narration
from touchline.models.overlay import OverlayType
from touchline.orchestration.workflow import MatchWorkflow
from touchline.providers import attribution, template
from touchline.providers import status as provider_status
from touchline.providers.clients import TemplateChatClient
from touchline.sim.generator import simulate, team_names

SEED = 42


class _BadFigureClient:
    """Answers with prose carrying a figure the log does not hold.

    It records the call exactly like the real clients do, so the test
    can tell the call count apart from the substitution count.
    """

    name = "template"

    def __init__(self, task: str = "personalise") -> None:
        self.task = task

    async def get_response(self, messages: Any, **kwargs: Any) -> Any:
        from touchline.providers.clients import _assistant_response

        provider_status.record_call(provider_status.TEMPLATE_PROVIDER, live=False)
        if self.task == "personalise":
            return _assistant_response(
                json.dumps(
                    {
                        "title": "Unbelievable",
                        # 99 is not a figure the candidate carries.
                        "body": "A staggering 99 goals in a single half!",
                    }
                )
            )
        return _assistant_response(
            json.dumps(
                {
                    "title": "99 wasted chances",
                    "body": "99 shots, none of them on target.",
                    "commentary": "99 and out.",
                }
            )
        )


def _match() -> tuple[Any, dict, Any, list]:
    """A real match, its clubs, one insight, and the cited events."""
    events = simulate(seed=SEED)
    names = team_names(SEED)
    analyst = MatchAnalyst(team_names=names)
    feed = analyst.select_for_feed(analyst.analyse(events))
    candidate = feed[3]
    by_id = {event.event_id: event for event in events}
    cited = [by_id[event_id] for event_id in candidate.cited_event_ids]
    return events, names, candidate, cited


def _narration(candidate: Any) -> Narration:
    return Narration(
        candidate_id=candidate.candidate_id,
        title="The press drops off",
        body="Riverside United pressed hard for an hour, then sat off.",
        commentary="The press drops off.",
    )


# -- the request-local context ---------------------------------------------


def test_the_request_context_is_task_local() -> None:
    """Concurrent requests do not mix attribution."""

    async def worker(
        stage: str, candidate_id: str, revision: int, out: list
    ) -> None:
        with attribution.request_context(
            stage=stage, candidate_id=candidate_id, revision=revision
        ):
            # Interleave, so a global would mix the three up.
            await asyncio.sleep(0.01)
            provider_status.record_call("Template engine", live=False)
            out.append(
                (
                    (stage, candidate_id, revision),
                    provider_status.last_call_for(
                        stage=stage, candidate_id=candidate_id, revision=revision
                    ),
                )
            )

    async def run() -> list:
        provider_status.reset()
        out: list = []
        await asyncio.gather(
            worker("personalizer", "c-1", 1, out),
            worker("personalizer", "c-2", 2, out),
            worker("narrator", "c-1", 1, out),
        )
        return out

    for wanted, call in asyncio.run(run()):
        assert call is not None, f"no record for {wanted}"
        assert (call.stage, call.candidate_id, call.revision) == wanted, (
            f"attribution mixed up: wanted {wanted}, got "
            f"{(call.stage, call.candidate_id, call.revision)}"
        )


def test_a_narrowed_context_inherits_the_revision() -> None:
    """An agent narrows stage and candidate; the revision comes along."""
    with attribution.request_context(
        revision=7
    ), attribution.request_context(stage="narrator", candidate_id="c-9"):
        context = attribution.current()
    assert context.stage == "narrator"
    assert context.candidate_id == "c-9"
    assert context.revision == 7


# -- substitutions, separately ----------------------------------------------


def test_a_rejected_personalisation_is_a_substitution_not_a_failure() -> None:
    """The figure check's rejection is recorded apart from call failures."""
    from touchline.agents.personalizer import PersonalizerAgent

    _, names, candidate, cited = _match()

    async def run() -> PersonalizerAgent:
        provider_status.reset()
        agent = PersonalizerAgent(_BadFigureClient(), team_names=names)
        overlay = await agent.personalise(
            candidate,
            _narration(candidate),
            audience=Audience(mode=AudienceMode.ANALYST, locale="en"),
            events=cited,
        )
        return agent, overlay

    agent, _overlay = asyncio.run(run())
    assert len(agent.substitutions) == 1, "the rejection was not recorded"
    assert agent.degradations == [], (
        "the rejection was recorded as a call failure"
    )
    state = provider_status.current(False)
    assert state.substitution_count == 1
    # The call was served -- once -- and the substitution did not move the
    # model-call counts.
    assert state.call_count == 1, state.call_count
    assert state.fallback_count == 1, state.fallback_count


def test_rejected_prose_is_replaced_in_the_requested_language() -> None:
    """A rejected German card is rewritten deterministically, in German.

    The old behaviour fell back on the narration's copy from an earlier
    stage -- English, when the rewrite was for German. The card now shows
    deterministic copy in the requested audience and language, and says
    so about itself.
    """
    from touchline.agents.audience import project_candidate
    from touchline.agents.personalizer import PersonalizerAgent

    _, names, candidate, cited = _match()
    audience = Audience(mode=AudienceMode.ANALYST, locale="de")

    async def run() -> Any:
        provider_status.reset()
        agent = PersonalizerAgent(_BadFigureClient(), team_names=names)
        return await agent.personalise(
            candidate, _narration(candidate), audience=audience, events=cited
        )

    overlay = asyncio.run(run())
    assert overlay.prose_substituted is True
    assert overlay.locale == "de"
    narration = _narration(candidate)
    assert overlay.title != narration.title, (
        "the card fell back on the earlier stage's English copy"
    )
    assert overlay.body != narration.body, (
        "the card fell back on the earlier stage's English copy"
    )
    projected = project_candidate(candidate, audience, cited, team_names=names)
    assert overlay.title == template.render_title(
        projected, locale="de", mode="analyst", audience=audience
    )


def test_the_narrator_guard_is_a_substitution_not_a_failure() -> None:
    """The Narrator's figure check is recorded apart from the A2A hop."""
    from touchline.agents.narrator import NarratorAgent

    _, _names, candidate, _cited = _match()

    async def run() -> NarratorAgent:
        from touchline.models.claims import Explanation

        provider_status.reset()
        agent = NarratorAgent(_BadFigureClient(task="narrate"))
        explanation = Explanation(
            candidate_id=candidate.candidate_id,
            reading="a reading",
            cited_event_ids=list(candidate.cited_event_ids),
            metrics=[],
            baseline=None,
        )
        await agent.narrate(
            candidate, explanation, locale="en", mode="analyst",
            ask_explainer=False,
        )
        return agent

    agent = asyncio.run(run())
    assert len(agent.substitutions) == 1, "the rejection was not recorded"
    assert agent.degradations == [], (
        "the rejection was recorded as a call failure"
    )
    assert provider_status.current(False).substitution_count == 1


def test_substitutions_do_not_move_the_model_call_counts() -> None:
    provider_status.reset()
    before = provider_status.current(False)
    provider_status.record_call(provider_status.TEMPLATE_PROVIDER, live=False)
    provider_status.record_substitution()
    provider_status.record_substitution()
    after = provider_status.current(False)
    assert after.call_count == before.call_count + 1
    assert after.fallback_count == before.fallback_count + 1
    assert after.substitution_count == before.substitution_count + 2
    assert after.as_dict()["substitution_count"] == after.substitution_count


# -- provenance on the payloads ---------------------------------------------


def test_the_recap_is_labelled_deterministic() -> None:
    """The full-time card says about itself that no model wrote it."""
    events, names, _, _ = _match()
    recap = build_recap(
        events, Audience(mode=AudienceMode.ANALYST, locale="en"), team_names=names
    )
    assert recap.type is OverlayType.RECAP
    assert recap.deterministic_prose is True
    assert recap.provenance_provider is None
    assert recap.prose_substituted is False


def test_handoffs_carry_the_provider_that_served_them() -> None:
    """The claim, narration and overlay hand-offs name their provider."""

    async def run() -> list:
        provider_status.reset()
        workflow = MatchWorkflow(analyst=MatchAnalyst(team_names=team_names(SEED)))
        run = await workflow.run(simulate(seed=SEED))
        assert run.overlays, "the workflow produced nothing"
        return workflow._trace.as_list()

    entries = asyncio.run(run())
    for kind in ("claim", "narration", "overlay"):
        matching = [e for e in entries if e.kind == kind]
        assert matching, f"no {kind} hand-off was recorded"
        assert all(
            e.provider == provider_status.TEMPLATE_PROVIDER for e in matching
        ), f"a {kind} hand-off does not name its provider"


def test_the_engine_stamps_its_cards_with_their_provider() -> None:
    """Analysis cards name the provider that wrote their prose."""

    async def run() -> list:
        provider_status.reset()
        engine = LiveMatchEngine(seed=SEED, client=TemplateChatClient())
        assert await engine.wait_for(engine.duration_ms, timeout=180)
        overlays = list(engine.overlays)
        await engine.close()
        return overlays

    overlays = asyncio.run(run())
    assert overlays, "the engine produced nothing"
    insights = [o for o in overlays if o.type is not OverlayType.RECAP]
    assert insights
    assert all(
        o.provenance_provider == provider_status.TEMPLATE_PROVIDER
        for o in insights
    ), "an analysis card does not name its provider"
    recaps = [o for o in overlays if o.type is OverlayType.RECAP]
    assert len(recaps) == 1
    assert recaps[0].deterministic_prose is True


def test_a_rewrite_stamps_its_cards_with_their_provider() -> None:
    """Rewritten cards name the provider that rewrote them, per revision."""

    async def run() -> list:
        provider_status.reset()
        engine = LiveMatchEngine(seed=SEED, client=TemplateChatClient())
        assert await engine.wait_for(engine.duration_ms, timeout=180)
        await engine.set_audience(Audience(mode=AudienceMode.FAN, locale="de"))
        assert await engine.wait_for_rewrite(timeout=60)
        overlays = list(engine.overlays)
        await engine.close()
        return overlays

    overlays = asyncio.run(run())
    insights = [o for o in overlays if o.type is not OverlayType.RECAP]
    assert insights
    assert all(o.revision == 1 for o in insights)
    assert all(
        o.provenance_provider == provider_status.TEMPLATE_PROVIDER
        for o in insights
    )
    # The ledger kept the two revisions apart.
    revisions = {
        call.revision
        for call in provider_status.calls(
            stage="personalizer", candidate_id=insights[0].overlay_id[4:]
        )
    }
    assert revisions == {0, 1}, f"the ledger mixed the revisions: {revisions}"


def test_the_overlay_payload_carries_provenance() -> None:
    """The socket/REST overlay shape includes the additive fields."""
    _, names, candidate, cited = _match()

    async def run() -> Any:
        from touchline.agents.personalizer import PersonalizerAgent

        agent = PersonalizerAgent(TemplateChatClient(), team_names=names)
        return await agent.personalise(
            candidate,
            _narration(candidate),
            audience=Audience(mode=AudienceMode.ANALYST, locale="en"),
            events=cited,
            revision=3,
        )

    overlay = asyncio.run(run())
    payload = overlay.model_dump(mode="json")
    assert payload["revision"] == 3
    assert payload["prose_substituted"] is False
    assert payload["deterministic_prose"] is False
    assert "provenance_provider" in payload
