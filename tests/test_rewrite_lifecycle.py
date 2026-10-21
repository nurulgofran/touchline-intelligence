"""The audience rewrite lifecycle: duplicates, supersession, retry.

A reader change is answered at once and rewritten in the background --
one model call per insight. This file is about the lifecycle of that
background work:

* an identical request must not invalidate the rewrite it duplicates;
* requested, active and completed are three different facts, and
  completion is only marked when every required card has landed;
* an identical request retries a rewrite that never finished;
* a superseded rewrite is cancelled, awaited, and its stale cards are
  rejected;
* a failed rewrite is exposed as one line plus a retry action, on the
  engine, the session, the socket and the REST audience state.
"""

from __future__ import annotations

import asyncio
from typing import Any

from touchline.api.live import LiveSession
from touchline.live.engine import LiveMatchEngine
from touchline.models.audience import Audience, AudienceMode
from touchline.providers.clients import TemplateChatClient

SEED = 42

DE = Audience(mode=AudienceMode.ANALYST, locale="de")
EN = Audience(mode=AudienceMode.ANALYST, locale="en")


async def _analysed_engine(personalizer: Any = None) -> LiveMatchEngine:
    """A match analysed all the way through, over the template engine."""
    engine = LiveMatchEngine(seed=SEED, client=TemplateChatClient())
    assert await engine.wait_for(engine.duration_ms, timeout=180)
    if personalizer is not None:
        engine._personalizer = personalizer
    return engine


class _SlowPersonalizer:
    """A Personaliser that takes its time, like the model does."""

    def __init__(self, delay: float = 0.3) -> None:
        self.delay = delay
        self.calls = 0

    async def personalise(self, candidate: Any, narration: Any, **kwargs: Any) -> Any:
        from touchline.agents.audience import project_candidate
        from touchline.providers import template

        self.calls += 1
        await asyncio.sleep(self.delay)
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


class _GatedPersonalizer(_SlowPersonalizer):
    """A Personaliser whose first card waits for a release.

    The rewrite cannot finish before the gate opens, so the test can look
    at the lifecycle mid-flight deterministically.
    """

    def __init__(self) -> None:
        super().__init__(delay=0.01)
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def personalise(self, candidate: Any, narration: Any, **kwargs: Any) -> Any:
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return await super().personalise(candidate, narration, **kwargs)


class _FailOncePersonalizer(_SlowPersonalizer):
    """A Personaliser that fails until it is disarmed."""

    def __init__(self) -> None:
        super().__init__(delay=0.01)
        self.armed = True

    async def personalise(self, candidate: Any, narration: Any, **kwargs: Any) -> Any:
        if self.armed:
            raise RuntimeError("the model is down")
        return await super().personalise(candidate, narration, **kwargs)


def test_a_duplicate_request_does_not_invalidate_the_active_rewrite() -> None:
    """Asking for German twice during a rewrite ends in German.

    The identical request used to bump the revision before checking what
    was asked for, which abandoned the rewrite that was running: the
    second request killed the first one's work, and the feed never
    finished in German. Now the duplicate is checked first, the revision
    does not move, and the rewrite runs to completion.
    """

    async def run() -> tuple[int, set[str]]:
        engine = await _analysed_engine(_SlowPersonalizer())
        await engine.set_audience(DE)
        revision_after_first = engine._revision
        # The duplicate: the same audience, while its rewrite is running.
        await engine.set_audience(DE)
        assert engine._revision == revision_after_first, (
            "the duplicate request moved the revision"
        )
        assert await engine.wait_for_rewrite(timeout=60)
        locales = {overlay.locale for overlay in engine.overlays}
        await engine.close()
        return revision_after_first, locales

    revision, locales = asyncio.run(run())
    assert revision == 1, f"the first request did not start revision 1, got {revision}"
    assert locales == {"de"}, f"the feed did not finish in German: {locales}"


def test_an_identical_request_retries_a_failed_rewrite() -> None:
    """A rewrite that never finished is retried, not mistaken for done."""

    async def run() -> tuple[str | None, set[str], str | None]:
        personalizer = _FailOncePersonalizer()
        engine = await _analysed_engine(personalizer)
        await engine.set_audience(DE)
        assert not await engine.wait_for_rewrite(timeout=60)
        error = engine.rewrite_error
        assert error, "the failed rewrite recorded no error"
        assert "RuntimeError" in error

        # The identical request retries the failed rewrite.
        personalizer.armed = False
        await engine.set_audience(DE)
        assert await engine.wait_for_rewrite(timeout=60)
        locales = {overlay.locale for overlay in engine.overlays}
        cleared = engine.rewrite_error
        await engine.close()
        return error, locales, cleared

    error, locales, cleared = asyncio.run(run())
    assert error, "no error was recorded"
    assert locales == {"de"}, f"the retried rewrite did not finish: {locales}"
    assert cleared is None, "the error was not cleared by the retry"


def test_rapid_en_de_en_finishes_in_english() -> None:
    """Rapid reader changes end with the last reader asked for."""

    async def run() -> tuple[set[str], set[int], str | None]:
        engine = await _analysed_engine(_SlowPersonalizer(delay=0.2))
        await engine.set_audience(DE)
        await engine.set_audience(EN)
        await engine.set_audience(DE)
        await engine.set_audience(EN)
        assert await engine.wait_for_rewrite(timeout=120)
        overlays = engine.overlays
        locales = {overlay.locale for overlay in overlays}
        revisions = {overlay.revision for overlay in overlays}
        completed = engine._completed_key
        await engine.close()
        return locales, revisions, completed

    locales, revisions, completed = asyncio.run(run())
    assert locales == {"en"}, f"the feed did not finish in English: {locales}"
    assert revisions == {4}, f"stale cards survived supersession: {revisions}"
    assert completed == EN.key, "completion was not marked for the last reader"


def test_completion_is_marked_only_after_all_cards_publish() -> None:
    """The rewrite is not complete until every required card has landed."""

    async def run() -> tuple[bool, bool]:
        personalizer = _GatedPersonalizer()
        engine = await _analysed_engine(personalizer)
        await engine.set_audience(DE)
        # The rewrite has started a card but the gate is shut: nothing
        # has been published yet.
        await asyncio.wait_for(personalizer.started.wait(), timeout=30)
        await asyncio.sleep(0.2)
        mid_flight = engine._completed_key == DE.key
        rewriting = engine.rewriting
        personalizer.release.set()
        assert await engine.wait_for_rewrite(timeout=60)
        done = engine._completed_key == DE.key
        await engine.close()
        return mid_flight, rewriting and done

    marked_early, finished = asyncio.run(run())
    assert not marked_early, "completion was marked before the cards landed"
    assert finished, "completion was not marked after the cards landed"


def test_a_superseded_rewrite_is_cancelled_and_awaited() -> None:
    """The old rewrite is dead before the new one is recorded as active."""

    async def run() -> tuple[bool, set[str]]:
        engine = await _analysed_engine(_SlowPersonalizer(delay=0.5))
        await engine.set_audience(DE)
        first_task = engine._rewrite_task
        await engine.set_audience(EN)
        # Cancelled *and* awaited: the new request only returns once the
        # old rewrite is gone.
        old_dead = first_task is not None and first_task.done()
        assert await engine.wait_for_rewrite(timeout=120)
        locales = {overlay.locale for overlay in engine.overlays}
        await engine.close()
        return old_dead, locales

    old_dead, locales = asyncio.run(run())
    assert old_dead, "the superseded rewrite was still running"
    assert locales == {"en"}, f"the superseded rewrite landed: {locales}"


def test_retry_restarts_a_failed_rewrite_and_nothing_else() -> None:
    """The retry action restarts a failed rewrite, and is a no-op otherwise."""

    async def run() -> tuple[bool, bool, bool]:
        personalizer = _FailOncePersonalizer()
        engine = await _analysed_engine(personalizer)
        await engine.set_audience(DE)
        assert not await engine.wait_for_rewrite(timeout=60)

        personalizer.armed = False
        retried = engine.retry_rewrite()
        assert await engine.wait_for_rewrite(timeout=60)

        # Nothing to retry now: the rewrite finished.
        again = engine.retry_rewrite()
        await engine.close()
        return retried, again, {o.locale for o in engine.overlays} == {"de"}

    retried, again, german = asyncio.run(run())
    assert retried, "the retry did not restart the failed rewrite"
    assert not again, "the retry restarted a finished rewrite"
    assert german, "the retried rewrite did not finish in German"


def test_retry_is_a_no_op_while_a_rewrite_runs() -> None:
    """The retry does not disturb a rewrite that is still working."""

    async def run() -> tuple[bool, int, set[str]]:
        engine = await _analysed_engine(_SlowPersonalizer(delay=0.3))
        await engine.set_audience(DE)
        retried = engine.retry_rewrite()
        revision = engine._revision
        assert await engine.wait_for_rewrite(timeout=60)
        locales = {overlay.locale for overlay in engine.overlays}
        await engine.close()
        return retried, revision, locales

    retried, revision, locales = asyncio.run(run())
    assert not retried, "the retry disturbed a running rewrite"
    assert revision == 1, "the retry moved the revision of a running rewrite"
    assert locales == {"de"}


def test_a_reconnect_resend_is_a_duplicate() -> None:
    """The client's reconnect resend does not restart the rewrite.

    When the socket is cut mid-rewrite, the studio re-sends the audience
    it asked for when the socket opens again. That resend is an identical
    request, and it must not invalidate the rewrite that is still
    running -- otherwise every reconnect would restart minutes of work.
    """

    async def run() -> tuple[int, set[str]]:
        engine = await _analysed_engine(_SlowPersonalizer())
        session = LiveSession(engine=engine)
        await session.set_audience(DE)
        # What the studio does when the socket opens again: ask for the
        # same audience it already asked for.
        await session.set_audience(DE)
        revision = engine._revision
        assert await engine.wait_for_rewrite(timeout=60)
        locales = {overlay.locale for overlay in engine.overlays}
        await engine.close()
        return revision, locales

    revision, locales = asyncio.run(run())
    assert revision == 1, f"the reconnect resend restarted the rewrite: rev {revision}"
    assert locales == {"de"}, f"the feed did not finish in German: {locales}"


def test_the_session_exposes_the_rewrite_error() -> None:
    """The hello frame and the audience frame carry the one-line error."""

    async def run() -> tuple[dict, list[dict], bool]:
        personalizer = _FailOncePersonalizer()
        engine = await _analysed_engine(personalizer)
        session = LiveSession(engine=engine)
        await session.set_audience(DE)
        assert not await engine.wait_for_rewrite(timeout=60)

        hello = session.hello()
        frames = session.tick()
        audience_frames = [f for f in frames if f["type"] == "audience"]
        # The session's retry delegates to the engine.
        personalizer.armed = False
        retried = session.retry_audience()
        assert await engine.wait_for_rewrite(timeout=60)
        await engine.close()
        return hello, audience_frames, retried

    hello, audience_frames, retried = asyncio.run(run())
    assert hello["rewrite_error"], "the hello frame carries no rewrite error"
    assert "RuntimeError" in str(hello["rewrite_error"])
    assert audience_frames, "no audience frame was sent after the failure"
    assert audience_frames[-1]["rewrite_error"], (
        "the audience frame carries no rewrite error"
    )
    assert audience_frames[-1]["rewriting"] is False
    assert retried, "the session retry did not restart the failed rewrite"


def test_the_audience_rest_state_carries_the_rewrite_error() -> None:
    """GET /api/match/audience reports the rewrite lifecycle."""

    async def run() -> dict:
        from fastapi.testclient import TestClient

        from touchline.api.live import get_engine
        from touchline.api.main import app

        # A fresh engine, so no other test's reader changes leak in.
        engine = get_engine(seed=424242, start=False)
        engine.start()
        assert await engine.wait_for(engine.duration_ms, timeout=180)
        # The rewrite runs against the failing double; the analysis used
        # the real personalizer, so the feed has cards to rewrite.
        engine._personalizer = _FailOncePersonalizer()
        await engine.set_audience(DE)
        assert not await engine.wait_for_rewrite(timeout=60)
        with TestClient(app) as client:
            body = client.get("/api/match/audience?seed=424242").json()
        await engine.close()
        return body

    body = asyncio.run(run())
    assert "rewrite_error" in body, "the audience state has no rewrite_error"
    assert body["rewrite_error"], "the failed rewrite was not reported"
    assert "rewriting" in body
    assert body["rewriting"] is False
