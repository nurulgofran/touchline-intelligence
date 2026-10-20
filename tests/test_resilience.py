"""The live analysis must survive a stretch it cannot explain.

Two failures killed the deployed match, and both are tested here.

The first was a *truncated* reply. The Explainer asks for a reading, the
metrics and the cited event ids; a candidate can carry fifty event ids, and
the ids alone are a couple of thousand characters. When the reply was cut
off by the output-token ceiling it was not JSON, the parser refused it, and
the exception ended the whole analysis at minute nineteen -- the studio
showed a match that had stopped.

The second was a model that never answered. The Agent Framework's Foundry
client carries no timeout of its own, so a request that hung held the
analysis with it, for as long as the container lived.

So: the output ceiling has to leave room for the longest reply the prompts
ask for, a call that runs long has to become a provider failure rather than
a wait, and a stretch that fails has to be skipped rather than fatal.
"""

from __future__ import annotations

import asyncio

import pytest

from touchline.live.engine import LiveMatchEngine
from touchline.providers import foundry
from touchline.providers.base import ProviderUnavailable
from touchline.providers.clients import TemplateChatClient

SEED = 42


def test_the_output_ceiling_leaves_room_for_the_longest_reply() -> None:
    """The ceiling has to fit a reply carrying a candidate's event ids.

    A candidate can cite fifty events. The model writes them out, and the
    ids are a couple of thousand characters on their own -- at roughly four
    characters a token, that is a thousand tokens before the reading and
    the metrics are counted. A ceiling below that cuts the reply off
    mid-sentence, and a cut-off reply is not JSON.
    """
    ceiling = foundry.REQUEST_OPTIONS["max_output_tokens"]
    assert ceiling >= 4000, (
        f"the output ceiling is {ceiling}; a candidate carrying fifty cited "
        "event ids does not fit in that, and the reply is cut off mid-JSON"
    )


def test_the_ceiling_does_not_change_the_reasoning_effort() -> None:
    """The ceiling is a ceiling; the effort is what keeps a call quick."""
    assert foundry.REQUEST_OPTIONS["reasoning"] == {"effort": "minimal"}


def test_a_call_that_never_answers_becomes_a_provider_failure() -> None:
    """A model that does not answer must not hold the match.

    The Agent Framework's Foundry client has no timeout of its own, so this
    is the only place the wait is bounded.
    """

    class HangingClient:
        """A chat client that never answers."""

        async def get_response(self, messages: object, **kwargs: object) -> object:
            """Wait for ever."""
            await asyncio.sleep(3600)
            raise AssertionError("unreachable")

    provider = foundry.FoundryProvider(
        project_endpoint="https://example.invalid/api/projects/p",
        model="gpt-5-mini",
        credential=object(),
        timeout_seconds=0.2,
    )

    with pytest.raises(ProviderUnavailable):
        asyncio.run(provider._complete(HangingClient(), "say something"))


def test_one_failed_stretch_does_not_stop_the_match() -> None:
    """A stretch that cannot be analysed is skipped, and the match goes on.

    The live analysis runs stretch by stretch. One stretch raising used to
    end the analysis there, so a single unreadable reply left the studio
    showing a match that had stopped at minute nineteen. The rest of the
    match is still analysed, and the loss is recorded in the trace.
    """

    async def build() -> LiveMatchEngine:
        built = LiveMatchEngine(seed=SEED, client=TemplateChatClient())
        original = built._analyse_stretch

        async def failing(
            index: int, start_ms: int, end_ms: int, events: list
        ) -> None:
            if index == 3:
                raise RuntimeError("the model replied with something unreadable")
            await original(index, start_ms, end_ms, events)

        built._analyse_stretch = failing
        built.start()
        assert await built.wait_for(built.duration_ms, timeout=180)
        return built

    engine = asyncio.run(build())

    assert engine.computed_ms == engine.duration_ms, (
        "the analysis stopped at the first failing stretch"
    )

    kinds = [entry.kind for entry in engine.trace.as_list()]
    assert "degradation" in kinds, "the lost stretch was not recorded in the trace"


def test_a_reply_that_was_cut_off_is_repaired() -> None:
    """A reply that ran out of output is read for what it says.

    The reading is written first, so a reply that was cut off still has it;
    only the tail is missing. Closing the open containers recovers it.
    """
    from touchline.providers.payload import parse_json_object

    cut = (
        '{"reading":"Riverside completed 15 passes.",'
        '"metrics":[{"name":"passes","value":15.0,"unit":"","side":"HOME"}],'
        '"cited_event_ids":["ev-000133","ev-000134","ev-00017'
    )
    payload = parse_json_object(cut)

    assert payload.get("reading") == "Riverside completed 15 passes."
    assert payload.get("cited_event_ids") == ["ev-000133", "ev-000134"]
    assert payload.get("metrics") == [
        {"name": "passes", "value": 15.0, "unit": "", "side": "HOME"}
    ]


def test_a_half_written_value_is_dropped_not_kept() -> None:
    """Half an event id is not an event id.

    The grounding step recomputes every number from the cited ids, so a
    half-written id would either be refused -- or worse, name a different
    event. Dropping it is the safe answer.
    """
    from touchline.providers.payload import parse_json_object

    cut = '{"reading":"ok","cited_event_ids":["ev-000133","ev-0001'
    payload = parse_json_object(cut)

    assert payload.get("cited_event_ids") == ["ev-000133"]


def test_a_reply_that_is_not_json_at_all_is_still_refused() -> None:
    """Repairing a reply must not invent one."""
    from touchline.providers.payload import parse_json_object

    assert parse_json_object("not json at all") == {}
    assert parse_json_object("") == {}
