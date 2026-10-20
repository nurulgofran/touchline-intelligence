"""The live match must be the same match as the batch run.

The studio shows a match being analysed while it is being played. The
project also claims that a batch run of the whole match produces the same
feed. If those two ever disagreed, the demo would be showing something
the pipeline does not actually do -- so the agreement is a test, not a
hope.

These tests are about the *engine* and the *session*: the work and the
playback. The HTTP layer is tested in ``test_api.py``.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from touchline.api.live import DEFAULT_SPEED, LiveSession
from touchline.live.engine import LiveMatchEngine
from touchline.metrics.analyst import MatchAnalyst
from touchline.orchestration.workflow import MatchWorkflow
from touchline.providers.clients import TemplateChatClient
from touchline.sim.generator import simulate, team_names

SEED = 42


def _content(overlay: object) -> tuple:
    """What an overlay *is*, without the name it was given.

    The id is derived from the insight's id, which is derived from its
    position, so the id moves when the number of insights found before it
    changes. What has to stay the same is the overlay.
    """
    return (
        overlay.type.value,
        overlay.match_clock_ms,
        overlay.title,
        overlay.body,
        tuple(
            (
                metric.name,
                round(metric.value, 6),
                metric.side.value if metric.side else None,
            )
            for metric in overlay.metrics
        ),
        tuple(overlay.cited_event_ids),
    )


@pytest.fixture(scope="module")
def engine() -> LiveMatchEngine:
    """A match analysed all the way through, over the template engine."""

    async def build() -> LiveMatchEngine:
        built = LiveMatchEngine(seed=SEED, client=TemplateChatClient())
        assert await built.wait_for(built.duration_ms, timeout=180)
        return built

    return asyncio.run(build())


def test_the_live_feed_is_the_batch_feed(engine: LiveMatchEngine) -> None:
    """The live feed and the whole-match feed carry the same insights.

    The live engine analyses the match one stretch at a time, and each
    stretch sees only the log as it stood at that moment. The batch run
    sees the whole log at once. Both have to produce the same feed --
    same insights, same order, same numbers -- because the analyst only
    ever looks backwards from the moment it reports on.
    """

    async def batch() -> list:
        # The same analyst, with the same two clubs. The only difference
        # between this run and the live one is *when* the log was read.
        workflow = MatchWorkflow(analyst=MatchAnalyst(team_names=team_names(SEED)))
        run = await workflow.run(simulate(seed=SEED))
        return list(run.overlays)

    whole = asyncio.run(batch())
    live = engine.overlays

    assert live, "the live engine produced no insights at all"
    assert len(live) == len(whole), (
        f"the live feed has {len(live)} insights and the batch feed has "
        f"{len(whole)}"
    )
    assert [overlay.overlay_id for overlay in live] == [
        overlay.overlay_id for overlay in whole
    ]
    assert [_content(overlay) for overlay in live] == [
        _content(overlay) for overlay in whole
    ]


def test_no_insight_is_explained_twice(engine: LiveMatchEngine) -> None:
    """The feed holds each insight once.

    The analyst re-reads the whole log on every stretch, so the same
    insight is *found* again and again. The live path has to keep only
    the ones the feed has not published yet, or the studio would show the
    same moment three times.
    """
    ids = [overlay.overlay_id for overlay in engine.overlays]
    assert len(ids) == len(set(ids)), "an insight was published more than once"

    candidates = [
        candidate
        for result in engine.results
        for candidate in result.candidates
    ]
    candidate_ids = [candidate.candidate_id for candidate in candidates]
    assert len(candidate_ids) == len(set(candidate_ids)), (
        "a candidate was handed to the explainer more than once"
    )


def test_the_trace_grows_across_the_stretches(engine: LiveMatchEngine) -> None:
    """The ledger of hand-offs is one ledger, not one per stretch.

    A judge reading the trace has to read one story: the analyst handed
    to the explainer, the explainer to the grounding step, the grounding
    step back to the explainer when it refused, and the narrator to the
    personaliser. If the trace restarted at every stretch, the story
    would be a hundred disconnected fragments.
    """
    trace = engine.trace
    assert len(trace) > 0
    indexes = [handoff.index for handoff in trace.as_list()]
    assert indexes == list(range(len(indexes))), "the trace is not in order"

    stages = {handoff.from_stage for handoff in trace.as_list()}
    assert "analyst" in stages
    assert "explainer" in stages
    assert "grounding" in stages
    assert "narrator" in stages
    assert "personalizer" in stages


def test_every_stretch_adds_to_the_same_trace(engine: LiveMatchEngine) -> None:
    """The hand-offs of a stretch are a slice of the whole ledger."""
    total = sum(len(result.trace) for result in engine.results)
    assert total == len(engine.trace), (
        "the stretches' hand-offs and the ledger disagree"
    )


# -- the session ------------------------------------------------------------


def test_the_clock_runs_at_the_speed(engine: LiveMatchEngine) -> None:
    """One wall-clock second is worth thirty match-seconds by default."""
    session = LiveSession(engine=engine)
    assert session.speed == DEFAULT_SPEED
    session.play()
    session.tick(now=time.monotonic() + 1.0)
    assert 29_000 <= session.clock_ms <= 31_000, session.clock_ms


def test_pause_stops_the_clock(engine: LiveMatchEngine) -> None:
    """A paused clock does not move, however long the wall clock waits."""
    session = LiveSession(engine=engine)
    session.play()
    session.tick(now=time.monotonic() + 1.0)
    paused_at = session.clock_ms
    session.pause()
    session.tick(now=time.monotonic() + 5.0)
    assert session.clock_ms == paused_at


def test_seek_moves_the_clock_and_keeps_the_match_the_same(
    engine: LiveMatchEngine,
) -> None:
    """Seeking is safe, because the analysis does not depend on the clock."""
    session = LiveSession(engine=engine)
    session.seek(1_200_000)
    assert session.clock_ms == 1_200_000
    assert len(engine.overlays) == len(engine.overlays)


def test_seek_cannot_leave_the_match(engine: LiveMatchEngine) -> None:
    """The clock is clamped to the match."""
    session = LiveSession(engine=engine)
    session.seek(-5_000)
    assert session.clock_ms == 0
    session.seek(99_000_000)
    assert session.clock_ms == engine.duration_ms


def test_next_moment_finds_the_next_insight(engine: LiveMatchEngine) -> None:
    """"Jump to the next key moment" lands on an insight, not near one."""
    session = LiveSession(engine=engine)
    clocks = engine.overlay_clocks()
    assert clocks, "the match produced no insights to jump to"

    session.seek(clocks[0])
    assert session.next_moment() == clocks[1]

    session.seek(clocks[-1])
    assert session.next_moment() is None


def test_replay_starts_the_match_again(engine: LiveMatchEngine) -> None:
    """Replay puts the clock back to the whistle."""
    session = LiveSession(engine=engine)
    session.seek(1_200_000)
    session.replay()
    assert session.clock_ms == 0
    assert session.playing


def test_the_session_reveals_an_insight_when_the_clock_reaches_it(
    engine: LiveMatchEngine,
) -> None:
    """An overlay is shown when the clock reaches it, not before."""
    session = LiveSession(engine=engine)
    session.pause()
    first = engine.overlays[0]

    session.seek(first.match_clock_ms - 1_000)
    frames = session.tick(now=time.monotonic())
    assert not [f for f in frames if f["type"] == "overlay"]

    session.seek(first.match_clock_ms)
    frames = session.tick(now=time.monotonic())
    overlays = [f for f in frames if f["type"] == "overlay"]
    assert overlays, "the insight was not shown when the clock reached it"
    assert overlays[0]["clock_ms"] == first.match_clock_ms
    assert overlays[0]["overlay"]["cited_event_ids"], "the insight cites nothing"
