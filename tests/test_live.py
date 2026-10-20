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



class _CountingPersonalizer:
    """A Personaliser that records how many rewrites run at once."""

    def __init__(self) -> None:
        self.running = 0
        self.peak = 0
        self.calls = 0

    async def personalise(self, candidate, narration, **kwargs):
        self.running += 1
        self.calls += 1
        self.peak = max(self.peak, self.running)
        try:
            await asyncio.sleep(0.02)
            from touchline.agents.audience import project_candidate
            from touchline.providers import template

            projected = project_candidate(
                candidate,
                kwargs.get("audience"),
                kwargs.get("events", []),
                team_names={},
            )
            return template.build_overlay(
                projected,
                title="t",
                body="b",
                locale=kwargs.get("audience").locale,
                mode=kwargs.get("audience").mode.value,
                revision=kwargs.get("revision", 0),
            )
        finally:
            self.running -= 1


class _SlowPersonalizer(_CountingPersonalizer):
    """A Personaliser that takes its time, like the model does.

    The point of it is the wait: a rewrite against the real model is
    minutes of work, and the tests that follow are about what the
    service does while that work is going on.
    """

    async def personalise(self, candidate, narration, **kwargs):
        await asyncio.sleep(1.0)
        return await super().personalise(candidate, narration, **kwargs)


class _OrderRecordingPersonalizer(_CountingPersonalizer):
    """A Personaliser that records the order the insights came in."""

    def __init__(self) -> None:
        super().__init__()
        self.clocks: list[int] = []

    async def personalise(self, candidate, narration, **kwargs):
        self.clocks.append(candidate.match_clock_ms)
        return await super().personalise(candidate, narration, **kwargs)


def test_a_reader_change_rewrites_the_insights_together() -> None:
    """The rewrites run at the same time, not one after the other.

    A reader change is one model call per insight. Done one at a time, a
    language change took the better part of a minute and the person who
    asked for it was left watching a spinner. They are independent of one
    another, so they run together -- within a limit, because a burst of
    requests is a burst the model deployment may refuse.
    """
    from touchline.live.engine import LiveMatchEngine
    from touchline.models.audience import Audience, AudienceMode

    async def run():
        engine = LiveMatchEngine(seed=SEED, client=TemplateChatClient())
        assert await engine.wait_for(engine.duration_ms, timeout=180)
        counter = _CountingPersonalizer()
        engine._personalizer = counter
        await engine.set_audience(Audience(mode=AudienceMode.FAN, locale="de"))
        # The rewrite runs in the background, so the test waits for it
        # rather than assuming it is done when the change is recorded.
        assert await engine.wait_for_rewrite(timeout=60)
        await engine.close()
        return counter

    counter = asyncio.run(run())
    assert counter.calls > 1, "there was nothing to rewrite"
    assert counter.peak > 1, "the rewrites ran one after the other"


def test_a_reader_change_is_answered_before_the_rewrite_is_finished() -> None:
    """The service says who the feed is for straight away.

    The rewrite is minutes of model time -- one call per insight, and
    there are forty-odd insights. If the change were only recorded when
    the rewrite finished, the studio would show the old language for
    minutes, and a socket that was cut in the meantime would lose the
    change altogether. So the state changes at once and the rewrite
    follows.
    """
    from touchline.live.engine import LiveMatchEngine
    from touchline.models.audience import Audience, AudienceMode

    async def run() -> tuple[str, bool]:
        engine = LiveMatchEngine(seed=SEED, client=TemplateChatClient())
        assert await engine.wait_for(engine.duration_ms, timeout=180)
        engine._personalizer = _SlowPersonalizer()
        await engine.set_audience(Audience(mode=AudienceMode.FAN, locale="de"))
        # The change is recorded before the rewrite is finished, and the
        # rewrite is still running.
        locale = engine.audience.locale
        still_running = engine._rewrite_task is not None and not (
            engine._rewrite_task.done()
        )
        await engine.close()
        return locale, still_running

    locale, still_running = asyncio.run(run())
    assert locale == "de", "the change was not recorded at once"
    assert still_running, "the change waited for the rewrite"


def test_the_language_can_be_changed_back_to_english() -> None:
    """Switching to German and back again leaves the feed in English.

    This is the bug that was reported: the studio asked for English, the
    service went on writing in German, and the request was lost. The
    engine has to follow the request both ways -- and the second change
    must not be swallowed by the first one's rewrite, which is the part
    that used to lose it.
    """
    from touchline.live.engine import LiveMatchEngine
    from touchline.models.audience import Audience, AudienceMode

    async def run() -> list[str]:
        engine = LiveMatchEngine(seed=SEED, client=TemplateChatClient())
        assert await engine.wait_for(engine.duration_ms, timeout=180)
        engine._personalizer = _SlowPersonalizer()
        seen = []
        for locale in ("de", "en", "bn", "en"):
            await engine.set_audience(
                Audience(mode=AudienceMode.ANALYST, locale=locale)
            )
            seen.append(engine.audience.locale)
        await engine.close()
        return seen

    assert asyncio.run(run()) == ["de", "en", "bn", "en"]


def test_a_second_reader_change_is_not_queued_behind_the_first() -> None:
    """The second change is answered while the first is still rewriting.

    The control channel used to wait for the whole rewrite before it read
    the next control, so a studio that asked twice in a row had its
    second request sit in a queue for minutes -- and lose it entirely if
    the socket was cut first.
    """
    from touchline.live.engine import LiveMatchEngine
    from touchline.models.audience import Audience, AudienceMode

    async def run() -> tuple[str, str]:
        engine = LiveMatchEngine(seed=SEED, client=TemplateChatClient())
        assert await engine.wait_for(engine.duration_ms, timeout=180)
        engine._personalizer = _SlowPersonalizer()
        await engine.set_audience(Audience(mode=AudienceMode.FAN, locale="de"))
        await engine.set_audience(Audience(mode=AudienceMode.ANALYST, locale="en"))
        first, second = engine.audience.locale, engine.audience.mode.value
        await engine.close()
        return first, second

    locale, mode = asyncio.run(run())
    assert locale == "en", "the second change did not take effect"
    assert mode == "analyst", "the second change did not take effect"


def test_a_superseded_rewrite_does_not_land() -> None:
    """The copy of a reader nobody is reading any more never reaches the feed.

    The rewrite for the first change is abandoned when the second change
    arrives, so the feed is not left with the language the reader has
    already moved on from.
    """
    from touchline.live.engine import LiveMatchEngine
    from touchline.models.audience import Audience, AudienceMode

    async def run() -> list[str]:
        engine = LiveMatchEngine(seed=SEED, client=TemplateChatClient())
        assert await engine.wait_for(engine.duration_ms, timeout=180)
        engine._personalizer = _SlowPersonalizer()
        await engine.set_audience(Audience(mode=AudienceMode.FAN, locale="de"))
        await engine.set_audience(Audience(mode=AudienceMode.ANALYST, locale="en"))
        assert await engine.wait_for_rewrite(timeout=60)
        locales = [overlay.locale for overlay in engine.overlays]
        await engine.close()
        return locales

    locales = asyncio.run(run())
    assert locales, "the feed is empty"
    assert set(locales) == {"en"}, f"the feed carries {set(locales)}"


def test_the_session_shows_the_rewritten_cards() -> None:
    """The viewer is shown the same moments, written for somebody else.

    A rewritten card keeps its id, so the session has to walk the feed
    again when the rewrite lands. If it did not, the viewer would keep
    looking at the copy they had already seen.
    """
    from touchline.live.engine import LiveMatchEngine
    from touchline.models.audience import Audience, AudienceMode

    async def run() -> tuple[list[str], list[str]]:
        engine = LiveMatchEngine(seed=SEED, client=TemplateChatClient())
        assert await engine.wait_for(engine.duration_ms, timeout=180)
        session = LiveSession(engine=engine)
        session.pause()
        session.seek(engine.duration_ms)

        before = [
            frame["overlay"]["title"]
            for frame in session.tick()
            if frame["type"] == "overlay"
        ]
        await session.set_audience(
            Audience(mode=AudienceMode.ANALYST, locale="de")
        )
        assert await engine.wait_for_rewrite(timeout=60)
        after = [
            frame["overlay"]["title"]
            for frame in session.tick()
            if frame["type"] == "overlay"
        ]
        await engine.close()
        return before, after

    before, after = asyncio.run(run())
    assert before, "the session showed nothing"
    assert after, "the rewritten cards were never shown"
    assert len(after) == len(before), "the feed lost a card"


def test_the_rewrite_starts_where_the_reader_is_looking() -> None:
    """The cards on screen are rewritten first.

    A rewrite of the whole feed is one call per insight, and the
    deployment's rate makes that a matter of minutes rather than seconds.
    So the order is the order the reader is reading in: the moments
    nearest where the reader is looking come first, because those are the
    ones on the screen.
    """
    from touchline.live.engine import LiveMatchEngine
    from touchline.models.audience import Audience, AudienceMode

    async def run() -> list[int]:
        engine = LiveMatchEngine(seed=SEED, client=TemplateChatClient())
        assert await engine.wait_for(engine.duration_ms, timeout=180)
        recorder = _OrderRecordingPersonalizer()
        engine._personalizer = recorder
        await engine.set_audience(
            Audience(mode=AudienceMode.ANALYST, locale="de"), focus_ms=1_200_000
        )
        assert await engine.wait_for_rewrite(timeout=60)
        await engine.close()
        return recorder.clocks

    clocks = asyncio.run(run())
    assert len(clocks) > 2, "there was nothing to rewrite"
    # The nearest moment to the reader's clock is the first one rewritten.
    focus = 1_200_000
    assert clocks[0] == min(clocks, key=lambda c: abs(c - focus)), (
        "the rewrite did not start at the moment the reader is looking at"
    )
    assert clocks[1] == min(
        (c for c in clocks if c != clocks[0]), key=lambda c: abs(c - focus)
    ), "the second rewrite was not the next nearest moment"


def test_the_full_time_card_changes_with_the_reader_at_once() -> None:
    """The recap is written again straight away, not at the end of a rewrite.

    The recap is deterministic -- a summary of the log's numbers -- so it
    costs nothing to write it again. And it is the card on the screen
    once the clock reaches the end of the match, so a reader who changes
    the language should not have to wait minutes for it.
    """
    from touchline.live.engine import LiveMatchEngine
    from touchline.models.audience import Audience, AudienceMode
    from touchline.models.overlay import OverlayType

    async def run() -> tuple[str, str]:
        engine = LiveMatchEngine(seed=SEED, client=TemplateChatClient())
        assert await engine.wait_for(engine.duration_ms, timeout=180)
        engine._personalizer = _SlowPersonalizer()
        before = next(
            overlay.title
            for overlay in engine.overlays
            if overlay.type is OverlayType.RECAP
        )
        await engine.set_audience(Audience(mode=AudienceMode.ANALYST, locale="de"))
        after = next(
            overlay.title
            for overlay in engine.overlays
            if overlay.type is OverlayType.RECAP
        )
        await engine.close()
        return before, after

    before, after = asyncio.run(run())
    assert before != after, "the full-time card did not change with the reader"


def test_asking_for_the_reader_you_are_already_reading_costs_nothing() -> None:
    """A reader change that changes nothing does not rewrite the feed.

    A rewrite is one model call per insight, and at the deployment's
    rate that is minutes of the deployment's quota. It is not spent on
    a reader who asked for the reader they were already reading -- which
    is what the studio's own reset does, and what a click on the
    language that is already in force does.
    """
    from touchline.live.engine import LiveMatchEngine
    from touchline.models.audience import Audience, AudienceMode

    async def run() -> int:
        engine = LiveMatchEngine(seed=SEED, client=TemplateChatClient())
        assert await engine.wait_for(engine.duration_ms, timeout=180)
        counter = _CountingPersonalizer()
        engine._personalizer = counter
        # The reader the feed is already written for.
        await engine.set_audience(
            Audience(mode=AudienceMode.ANALYST, locale="en")
        )
        calls = counter.calls
        await engine.set_audience(
            Audience(mode=AudienceMode.ANALYST, locale="en")
        )
        again = counter.calls
        await engine.close()
        return again - calls

    assert asyncio.run(run()) == 0, (
        "asking for the reader you are already reading rewrote the feed"
    )


def test_the_service_does_not_ask_for_more_than_the_deployment_gives() -> None:
    """The pacer keeps the service inside the request rate.

    A request over the deployment's limit comes back as a 429, and a 429
    that cannot be retried in time is a fallback: the template engine
    writes the card instead of the model, and the studio says so. So the
    service waits its turn, however many things are asking at once.
    """
    from touchline.providers.pacer import CallPacer

    async def run() -> list[float]:
        paced = CallPacer(limit=3, window=0.3)
        times: list[float] = []
        for _ in range(5):
            await paced.acquire()
            times.append(time.monotonic())
        return times

    times = asyncio.run(run())
    assert times[3] - times[0] >= 0.25, (
        "the fourth call went out without waiting for the window"
    )
    assert times[4] - times[1] >= 0.25, (
        "the fifth call went out without waiting for the window"
    )


def test_the_live_feed_is_the_batch_feed(engine: LiveMatchEngine) -> None:
    """The live feed and the whole-match feed carry the same insights.

    The live engine analyses the match one stretch at a time, and each
    stretch sees only the log as it stood at that moment. The batch run
    sees the whole log at once. Both have to produce the same feed --
    same insights, same order, same numbers -- because the analyst only
    ever looks backwards from the moment it reports on.

    The live feed carries one card the batch run does not: the full-time
    recap, which is a summary of the whole match and so cannot exist
    until the match is over.
    """

    async def batch() -> list:
        # The same analyst, with the same two clubs. The only difference
        # between this run and the live one is *when* the log was read.
        workflow = MatchWorkflow(analyst=MatchAnalyst(team_names=team_names(SEED)))
        run = await workflow.run(simulate(seed=SEED))
        return list(run.overlays)

    whole = asyncio.run(batch())
    live = engine.overlays
    recap = [overlay for overlay in live if overlay.overlay_id == "ovl-recap"]
    insights = [overlay for overlay in live if overlay.overlay_id != "ovl-recap"]

    assert live, "the live engine produced no insights at all"
    assert len(insights) == len(whole), (
        f"the live feed has {len(insights)} insights and the batch feed has "
        f"{len(whole)}"
    )
    assert [overlay.overlay_id for overlay in insights] == [
        overlay.overlay_id for overlay in whole
    ]
    assert [_content(overlay) for overlay in insights] == [
        _content(overlay) for overlay in whole
    ]
    assert len(recap) == 1, "the live feed carries no full-time recap"


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
