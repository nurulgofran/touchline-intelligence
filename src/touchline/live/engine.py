"""The live match engine: the graph, driven by the match clock.

The batch run answers a question about a match that has already been
played. The live run answers the same question about a match that is still
being played, and the two have to agree.

**Why they agree.** The analyst is *causal*: it only ever looks backwards
from the moment it is reporting on, so analysing the log as it stands gives
the same answer as analysing the whole log at once, stretch for stretch.
That is a tested property (``tests/test_analyst.py``), not a hope, and it
is what makes this module possible. The consequence is that a stretch of
the match can be analysed **ahead of the clock** -- at 08:00 of a match
that is being shown at 03:00 -- and the answer will be exactly the answer
the live feed would have given at 08:00.

**Why the engine exists at all.** A whole match is about forty insights on
the feed, and each one costs three model calls. Against the template
engine that is 0.3 s for the whole match. Against Microsoft Foundry it is
about 1.8 s per call, so the whole match is a few minutes of model time.
A demo cannot run the match and *then* show it, and it cannot show the
model stalling. So the engine does the two halves separately:

* the **engine** computes stretches as fast as the model allows, and keeps
  the answers;
* the **studio** plays the match back on its own clock, and shows each
  insight when the clock reaches it.

The computation is real and the pacing is a presentation choice, which is
how a broadcast works anyway: the analysis is prepared ahead and cued to
the clock.

**One engine, many viewers.** The engine is shared. Two judges watching
the same match do not cause two hundred model calls -- they watch the same
computed match, each on their own clock and their own speed. That is both
cheaper and truer: the match is the same match.

The engine runs the graph once per stretch. The graph is rebuilt for each
stretch -- the Agent Framework's own guidance is that a workflow instance
keeps its state between runs, and independent runs want separate instances
-- but the **store**, the **trace**, the **agents** and the set of
**already-forwarded insights** are shared, so the event log grows, the
ledger of hand-offs grows, and no insight is explained twice.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from typing import Any

from touchline.agents.audience import build_recap
from touchline.agents.explainer import ExplainerAgent
from touchline.agents.narrator import NarratorAgent
from touchline.agents.personalizer import PersonalizerAgent
from touchline.metrics.analyst import MatchAnalyst
from touchline.models.audience import Audience, AudienceMode
from touchline.models.claims import Narration
from touchline.models.events import MATCH_DURATION_MS, MatchEvent, TeamSide
from touchline.models.insights import InsightCandidate
from touchline.models.overlay import Overlay, OverlayType
from touchline.orchestration.handoffs import Handoff, HandoffTrace
from touchline.orchestration.workflow import FeedCursor, MatchWorkflow
from touchline.providers import foundry
from touchline.providers import status as provider_status
from touchline.sim.generator import simulate, team_names
from touchline.sim.teams import build_teams
from touchline.state.store import MatchStateStore

logger = logging.getLogger(__name__)

__all__ = [
    "LiveMatchEngine",
    "StretchResult",
    "build_teams_payload",
]


@dataclass(frozen=True)
class StretchResult:
    """What one stretch of match time produced.

    Attributes:
        index: Which stretch this was, from zero.
        start_ms: The match clock at the start of the stretch.
        end_ms: The match clock at the end of the stretch.
        overlays: The overlays the stretch produced, in clock order.
        candidates: The insights the stretch put on the feed.
        narrations: The copy that was written for each insight. Kept so the
            overlays can be rewritten for another audience without asking the
            Explainer and the Narrator again.
        trace: The hand-offs the stretch made.
        degradations: The times something fell back on something else.
        provider: The provider state after the stretch, so the badge in the
            studio shows what actually served this stretch.
    """

    index: int
    start_ms: int
    end_ms: int
    overlays: list[Overlay] = field(default_factory=list)
    candidates: list[InsightCandidate] = field(default_factory=list)
    narrations: list[Narration] = field(default_factory=list)
    trace: list[Handoff] = field(default_factory=list)
    degradations: list[str] = field(default_factory=list)
    provider: dict[str, object] = field(default_factory=dict)

    @property
    def overlay_clocks(self) -> list[int]:
        """The match clock of each overlay, in order."""
        return [overlay.match_clock_ms for overlay in self.overlays]

    def narration_for(self, candidate_id: str) -> Narration | None:
        """The copy written for one insight, if there is any."""
        for narration in self.narrations:
            if narration.candidate_id == candidate_id:
                return narration
        return None


def build_teams_payload(seed: int = 42) -> dict[str, object]:
    """The two fictional clubs, in the shape the studio draws them.

    The colours come from the generator, so the pitch in the browser is
    painted in the clubs' own colours without a single real kit, crest or
    badge anywhere in the project.

    Args:
        seed: Any integer. The same seed always yields the same two clubs.

    Returns:
        A mapping with the home and away club's details.
    """
    home, away = build_teams(seed)
    return {
        "home": {
            "name": home.name,
            "short_name": home.short_name,
            "primary_colour": list(home.primary_colour),
            "secondary_colour": list(home.secondary_colour),
        },
        "away": {
            "name": away.name,
            "short_name": away.short_name,
            "primary_colour": list(away.primary_colour),
            "secondary_colour": list(away.secondary_colour),
        },
    }


class LiveMatchEngine:
    """One match, analysed stretch by stretch as it is played.

    Example:
        >>> engine = LiveMatchEngine(seed=42)          # doctest: +SKIP
        >>> engine.start()                             # doctest: +SKIP
        >>> await engine.wait_for(180_000)             # doctest: +SKIP
        >>> len(engine.results)                       # doctest: +SKIP
        2
    """

    def __init__(
        self,
        *,
        seed: int = 42,
        match_id: str | None = None,
        client: Any | None = None,
        explainer_client: Any | None = None,
        locale: str = "en",
        mode: str = "analyst",
        audience: Audience | None = None,
        stretch_ms: int | None = None,
        rewrite_concurrency: int = 4,
    ) -> None:
        """Create the engine.

        Args:
            seed: Which match to play. The same seed is the same match.
            match_id: The id the match is known by.
            client: The chat client the agents use. The provider chain
                (Foundry, then the template engine) is used when absent.
            explainer_client: The A2A client the Narrator uses to reach the
                Explainer over the wire.
            locale: Language code for the prose.
            mode: Audience mode.
            audience: Who the overlays are written for. The mode and the
                locale above are used when absent.
            stretch_ms: How much match time one stretch covers. The
                analyst's own feed interval is used when absent, so the
                stretches line up with the feed exactly.
            rewrite_concurrency: How many insights are rewritten at once
                when the reader changes. Four is enough to bring a
                rewrite that took four minutes down to one, without
                turning into a burst of requests the model deployment
                would refuse.
        """
        from touchline.providers.clients import build_client

        self._seed = seed
        self._match_id = match_id or f"seed-{seed}"
        self._audience = audience or Audience(
            mode=AudienceMode(mode)
            if mode in {m.value for m in AudienceMode}
            else AudienceMode.ANALYST,
            locale=locale,
        )
        self._locale = self._audience.locale
        self._mode = self._audience.mode.value
        # The reader the feed is written for. The analysis writes in the
        # audience it is given, so the feed starts written for this one.
        self._written_for = self._audience.key

        self._events: list[MatchEvent] = sorted(
            simulate(seed=seed), key=lambda e: (e.match_clock_ms, e.event_id)
        )
        self._team_names = team_names(seed)

        self._analyst = MatchAnalyst(team_names=self._team_names)
        self._stretch_ms = stretch_ms or self._analyst.thresholds.feed_interval_ms
        self._rewrite_concurrency = max(1, int(rewrite_concurrency))

        # The shared state of one match: the event log grows, the ledger of
        # hand-offs grows, and the set of insights already handed to the
        # Explainer stops the same moment being explained twice.
        self._store = MatchStateStore()
        self._trace = HandoffTrace()
        self._cursor = FeedCursor()

        chat_client = client if client is not None else build_client()
        if client is None:
            self._owns_client = chat_client
        else:
            self._owns_client = None
        self._client = chat_client

        self._explainer = ExplainerAgent(chat_client)
        self._narrator = NarratorAgent(
            chat_client, explainer_client=explainer_client
        )
        self._personalizer = PersonalizerAgent(
            chat_client,
            mode=self._mode,
            locale=self._locale,
            team_names=self._team_names,
        )

        self._results: list[StretchResult] = []
        self._overlays: list[Overlay] = []
        self._recap: Overlay | None = None
        self._revision = 0
        self._feed_version = 0
        self._computed_ms = 0
        self._task: asyncio.Task[None] | None = None
        self._rewrite_task: asyncio.Task[None] | None = None
        self._advanced = asyncio.Event()
        self._lock = asyncio.Lock()
        self._tracking: Any | None = None
        self._tracking_task: asyncio.Task[None] | None = None

    # -- what the match is -------------------------------------------------

    @property
    def seed(self) -> int:
        """Which match this is."""
        return self._seed

    @property
    def match_id(self) -> str:
        """The id the match is known by."""
        return self._match_id

    @property
    def events(self) -> list[MatchEvent]:
        """The whole event log. The generator is deterministic, so the log
        is known before the match starts -- the *analysis* is what waits
        for the clock, not the data."""
        return list(self._events)

    @property
    def duration_ms(self) -> int:
        """How long the match is."""
        return MATCH_DURATION_MS

    @property
    def stretch_ms(self) -> int:
        """How much match time one stretch of analysis covers."""
        return self._stretch_ms

    @property
    def team_names(self) -> dict[TeamSide, str]:
        """The two fictional clubs, by side."""
        return dict(self._team_names)

    @property
    def teams(self) -> dict[str, object]:
        """The two fictional clubs, with their colours."""
        return build_teams_payload(self._seed)

    @property
    def roster(self) -> list[dict[str, object]]:
        """The two squads, for the studio's player picker.

        Every player is fictional and every name is invented, and the
        picker needs the same names the log carries so that the audience
        it builds is one the log can answer.
        """
        home, away = build_teams(self._seed)
        players: list[dict[str, object]] = []
        for side, club in ((TeamSide.HOME, home), (TeamSide.AWAY, away)):
            for player in club.squad:
                players.append(
                    {
                        "player_id": player.player_id,
                        "name": player.name,
                        "number": player.number,
                        "position": player.position,
                        "side": side.value,
                        "club": club.name,
                    }
                )
        return players

    @property
    def explainer(self) -> ExplainerAgent:
        """The Explainer agent. The A2A server serves *this* object, so the
        memory it keeps is the pipeline's memory."""
        return self._explainer

    @property
    def narrator(self) -> NarratorAgent:
        """The Narrator agent."""
        return self._narrator

    @property
    def trace(self) -> HandoffTrace:
        """The ledger of hand-offs, shared across the stretches."""
        return self._trace

    @property
    def store(self) -> MatchStateStore:
        """The shared state, shared across the stretches."""
        return self._store

    def attach_explainer_client(self, client: Any) -> None:
        """Give the Narrator the A2A client it will reach the Explainer
        with.

        The agent is built before the A2A server is mounted -- the server
        serves that agent -- so the client that speaks to the server is
        attached once the application exists.

        Args:
            client: The A2A client.
        """
        self._narrator.set_explainer_client(client)

    @property
    def momentum(self) -> list[Any]:
        """The momentum curve for the studio's chart.

        It is computed over the whole log, because the log is known before
        the match starts; the studio reveals it a point at a time as the
        clock reaches it, so the future is never on screen.
        """
        return self._analyst.momentum_curve(self._events, interval_ms=60_000)

    # -- what has been computed -------------------------------------------

    @property
    def results(self) -> list[StretchResult]:
        """The stretches computed so far, in clock order."""
        return list(self._results)

    @property
    def computed_ms(self) -> int:
        """How far the analysis has got, in match time."""
        return self._computed_ms

    @property
    def audience(self) -> Audience:
        """Who the overlays are written for.

        The audience is a property of the **analysis**, not of the viewer:
        one engine, one match, one audience at a time. Changing it rewrites
        the overlays that have already been computed.
        """
        return self._audience

    @property
    def overlays(self) -> list[Overlay]:
        """Every overlay computed so far, in clock order."""
        return list(self._overlays)

    @property
    def feed_version(self) -> int:
        """How many times the feed has been rewritten.

        The session walks the feed from the start whenever this changes,
        because a rewritten card keeps its id: the reader sees the same
        moment again, written for somebody else.
        """
        return self._feed_version

    @property
    def rewriting(self) -> bool:
        """Whether the feed is being rewritten at this moment.

        A rewrite is one model call per insight, and the deployment's
        rate makes it a matter of minutes. The studio says so, because a
        feed that is still being rewritten is not a finished feed and
        the reader should know which one they are looking at.
        """
        task = self._rewrite_task
        return task is not None and not task.done()

    def overlay_clocks(self) -> list[int]:
        """The match clock of every overlay computed so far, in order."""
        return [overlay.match_clock_ms for overlay in self.overlays]

    # -- running -----------------------------------------------------------

    def start(self) -> None:
        """Start analysing. Returns at once; the work happens in the
        background so that the studio can play the match while the model
        is still thinking."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(
            self._compute(), name=f"touchline-{self._match_id}"
        )

    async def close(self) -> None:
        """Stop analysing and release the chat client."""
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        rewrite, self._rewrite_task = self._rewrite_task, None
        if rewrite is not None:
            rewrite.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await rewrite
        owns = self._owns_client
        self._owns_client = None
        if owns is not None:
            close = getattr(owns, "aclose", None)
            if close is not None:
                try:
                    await close()
                except Exception:
                    logger.debug("closing the chat client failed", exc_info=True)

    async def wait_for(self, clock_ms: int, timeout: float | None = None) -> bool:
        """Wait until the analysis has reached a point in the match.

        Args:
            clock_ms: How far into the match to wait for.
            timeout: How long to wait, in seconds. ``None`` waits as long
                as it takes.

        Returns:
            Whether the analysis got there.
        """
        self.start()
        while self._computed_ms < clock_ms:
            self._advanced.clear()
            if self._computed_ms >= clock_ms:
                break
            try:
                await asyncio.wait_for(self._advanced.wait(), timeout=timeout)
            except TimeoutError:
                return False
        return self._computed_ms >= clock_ms

    # -- the audience ------------------------------------------------------

    async def set_audience(
        self, audience: Audience, focus_ms: int | None = None
    ) -> list[Overlay]:
        """Change who the overlays are written for, and rewrite them.

        The change is applied **at once**. The audience is the service's
        state, and a control that says "write it in English" has to be
        answered with English -- not with an English card four minutes
        later, and not with the picker snapping back to the old language
        because the model was busy. So the state changes before the lock
        is even taken: the analysis holds that lock for a whole stretch,
        and a reader should not have to wait for a stretch to be finished
        to be answered.

        The rewrite is the slow half -- one model call per insight -- and
        it happens in the background, so the control channel is never held
        up by it. That matters more than it looks: a rewrite is minutes of
        model time, the studio asks for the next reader in between, and a
        channel that waits for the rewrite would answer the studio's
        questions minutes late, or not at all if the socket was cut first.

        Only the Personaliser runs again. The Explainer's claim and the
        Narrator's copy are kept, because neither of them is a property of
        the audience: the claim is a fact about the match, and the copy is
        the reading that has already been checked against the log.

        Args:
            audience: Who the overlays are written for.
            focus_ms: Where in the match the reader is looking. The
                rewrite starts at the moments nearest there, because those
                are the ones on the screen. ``None`` starts at the
                beginning of the match.

        Returns:
            The overlays, as they stand. The rewritten ones are published
            later, with a higher revision, and the session walks the feed
            again when they arrive.
        """
        self._audience = audience
        self._locale = audience.locale
        self._mode = audience.mode.value
        self._revision += 1
        revision = self._revision

        # The full-time card is deterministic -- a summary of the log's
        # numbers -- so it costs nothing to write it again and it is the
        # card a reader sees once the clock reaches the end. It changes
        # with the reader, at once, like the rest of the state.
        self._replace_recap()

        # A reader who asks for the reader they are already reading gets
        # the feed they already have. A rewrite is one model call per
        # insight, and at the deployment's rate that is minutes of work
        # and the deployment's quota: it is not spent on nothing.
        if audience.key == self._written_for:
            return list(self._overlays)
        self._written_for = audience.key

        # A reader change supersedes the one before it. The rewrite that
        # is running is for somebody who is no longer reading, so it is
        # abandoned rather than allowed to finish: letting it land would
        # put a language on the screen that the reader has already moved
        # on from.
        running = self._rewrite_task
        if running is not None and not running.done():
            running.cancel()

        self._rewrite_task = asyncio.create_task(
            self._rewrite(audience, revision, focus_ms),
            name=f"touchline-rewrite-{self._match_id}",
        )
        return list(self._overlays)

    async def wait_for_rewrite(self, timeout: float | None = None) -> bool:
        """Wait for the rewrite that is running to finish.

        Args:
            timeout: How long to wait, in seconds. ``None`` waits as long
                as it takes.

        Returns:
            Whether the rewrite finished.
        """
        task = self._rewrite_task
        if task is None:
            return True
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except TimeoutError:
            return False
        except asyncio.CancelledError:
            return False
        return True

    async def _rewrite(
        self, audience: Audience, revision: int, focus_ms: int | None = None
    ) -> None:
        """Rewrite the overlays for one audience, in the background.

        The work is one model call per insight, and the calls are
        independent of one another -- each is one insight, one audience,
        one call -- so they run together rather than one after the other.
        Done one at a time, a language change took four minutes.

        The order is the order the reader is reading in: the moments
        nearest where the reader is looking are rewritten first, because
        those are the ones on the screen. The whole feed is rewritten --
        the deployment's rate is what makes that a matter of minutes
        rather than seconds, and a card that is still in the old language
        three minutes later is a card the reader has moved past.

        The lock is held for the whole rewrite, so the analysis cannot add
        an insight half way through and leave the feed with a card in the
        old language. Each card is published as it is written, and only
        while the reader who asked for it is still the reader.

        Args:
            audience: Who the overlays are written for.
            revision: The revision this rewrite is for. A rewrite whose
                revision is no longer the current one is stale.
            focus_ms: Where in the match the reader is looking.
        """
        try:
            async with self._lock:
                jobs: list[tuple[Any, Any]] = []
                for result in self._results:
                    for candidate in result.candidates:
                        narration = result.narration_for(candidate.candidate_id)
                        if narration is None:
                            continue
                        jobs.append((candidate, narration))

                focus = 0 if focus_ms is None else int(focus_ms)
                jobs.sort(
                    key=lambda job: abs(job[0].match_clock_ms - focus)
                )

                limit = asyncio.Semaphore(self._rewrite_concurrency)

                async def rewrite(candidate: Any, narration: Any) -> Overlay:
                    async with limit:
                        return await self._personalizer.personalise(
                            candidate,
                            narration,
                            audience=audience,
                            events=self._cited_events(candidate),
                            revision=revision,
                        )

                # Each card goes on the screen as it is written, rather
                # than the whole feed at the end. A rewrite is one call
                # per insight at the deployment's rate -- minutes, not
                # seconds -- and a reader waiting four minutes to see the
                # moment they are looking at change would think nothing
                # had happened at all.
                #
                # The tasks are made in the order the reader is reading
                # in, so the calls go out in that order too: the nearest
                # moments are the ones being asked for while the rest
                # wait their turn at the deployment's rate.
                tasks = [
                    asyncio.create_task(rewrite(c, n), name="touchline-rewrite")
                    for c, n in jobs
                ]
                try:
                    pending = set(tasks)
                    while pending:
                        done, pending = await asyncio.wait(
                            pending, return_when=asyncio.FIRST_COMPLETED
                        )
                        for task in done:
                            overlay = task.result()
                            if self._revision != revision:
                                # Somebody else has been asked for in the
                                # meantime. This copy is for a reader who
                                # is no longer reading.
                                return
                            self._publish(overlay)
                finally:
                    for task in tasks:
                        if not task.done():
                            task.cancel()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "the rewrite for %s could not be finished", audience.key
            )

    def _publish(self, overlay: Overlay) -> None:
        """Put one rewritten card on the screen, where it was.

        The card keeps its id -- it is the same moment, written for
        somebody else -- so it goes back where it was rather than at the
        end of the feed.

        Args:
            overlay: The rewritten card.
        """
        for index, current in enumerate(self._overlays):
            if current.overlay_id == overlay.overlay_id:
                self._overlays[index] = overlay
                self._feed_version += 1
                return
        self._overlays.append(overlay)
        self._feed_version += 1

    def _replace_recap(self) -> None:
        """Write the full-time card again, for the reader who is here now.

        The recap is deterministic, so it costs nothing to write and it
        is the card on the screen once the clock reaches the end of the
        match. It is written again as soon as the reader changes, rather
        than at the end of a rewrite that takes minutes.
        """
        recap = self._build_recap()
        if recap is None:
            return
        self._recap = recap
        for index, overlay in enumerate(self._overlays):
            if overlay.type is OverlayType.RECAP:
                self._overlays[index] = recap
                self._feed_version += 1
                return
        self._overlays.append(recap)
        self._feed_version += 1

    def _cited_events(self, candidate: InsightCandidate) -> list[MatchEvent]:
        """The events one candidate cites, as the shared state holds them."""
        events: list[MatchEvent] = []
        for event_id in candidate.cited_event_ids:
            event = self._store.get(event_id)
            if event is not None:
                events.append(event)
        return events

    # -- the vision tracker ------------------------------------------------

    def start_tracking(self) -> None:
        """Start the vision tracker's cross-validation in the background.

        The tracker is a **secondary** ingest path: the match analysis does
        not depend on it and the demo does not wait for it. It is started
        alongside the analysis and its report is ready whenever it is
        ready.
        """
        if self._tracking_task is not None:
            return
        self._tracking_task = asyncio.create_task(
            self._compute_tracking(), name=f"touchline-tracking-{self._match_id}"
        )

    async def _compute_tracking(self) -> None:
        """Run the tracker over the synthetic feed and measure the result."""
        from touchline.vision.validate import build_report

        try:
            self._tracking = await asyncio.to_thread(build_report, self._seed)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("the vision tracker's cross-validation failed")

    @property
    def tracking(self) -> Any | None:
        """The tracker's report, if it has finished.

        ``None`` means it is still running, or that it failed; the studio
        shows the panel as "working" either way.
        """
        return self._tracking

    def _build_recap(self) -> Overlay | None:
        """The full-time card, for the current audience.

        The recap is deterministic -- it is a summary of the log's numbers,
        and the numbers are the log's -- so it needs no model and no wait.
        """
        if not self._results:
            return None
        recap = build_recap(
            self._events, self._audience, team_names=self._team_names
        )
        return recap.model_copy(update={"revision": self._revision})

    # -- the work ----------------------------------------------------------

    async def _compute(self) -> None:
        """Analyse the match, one stretch at a time.

        A stretch that fails is recorded and skipped; the rest of the match
        is still analysed. One unreadable reply used to end the analysis
        there and leave the studio showing a match that stopped at minute
        nineteen -- the feed has to keep running, and a hole in it is a far
        better answer than a frozen match.
        """
        try:
            for index, (start_ms, end_ms, events) in enumerate(self._stretches()):
                try:
                    await self._analyse_stretch(index, start_ms, end_ms, events)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "stretch %d could not be analysed", index
                    )
                    self._record_degradation(index, start_ms, end_ms)
        finally:
            self._advanced.set()

    def _record_degradation(
        self, index: int, start_ms: int, end_ms: int
    ) -> None:
        """Note in the trace that one stretch of the match was lost.

        Args:
            index: Which stretch.
            start_ms: When it starts, in match time.
            end_ms: When it ends, in match time.
        """
        try:
            self._trace.record(
                "analyst",
                "explainer",
                "degradation",
                f"the stretch at {start_ms // 60000:02d}:"
                f"{end_ms // 60000:02d} could not be analysed",
            )
        except Exception:  # pragma: no cover - the trace must never break the feed
            logger.debug("the degradation could not be recorded", exc_info=True)

    async def _analyse_stretch(
        self,
        index: int,
        start_ms: int,
        end_ms: int,
        events: list[MatchEvent],
    ) -> None:
        """Run the graph over one stretch and keep what it produced."""
        trace_before = len(self._trace)
        narrator_before = len(self._narrator.degradations)
        personalizer_before = len(self._personalizer.degradations)
        client_before = self._client_degradation_count()

        async with self._lock:
            audience = self._audience
            workflow = MatchWorkflow(
                store=self._store,
                analyst=self._analyst,
                explainer=self._explainer,
                narrator=self._narrator,
                personalizer=self._personalizer,
                trace=self._trace,
                cursor=self._cursor,
                locale=audience.locale,
                mode=audience.mode.value,
                audience=audience,
            )
            run = await workflow.run(events, match_id=self._match_id)

            degradations = [
                *workflow.local_degradations,
                *self._narrator.degradations[narrator_before:],
                *self._personalizer.degradations[personalizer_before:],
                *self._client_degradations(client_before),
            ]
            overlays = [
                overlay.model_copy(update={"revision": self._revision})
                for overlay in run.overlays
            ]
            self._results.append(
                StretchResult(
                    index=index,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    overlays=overlays,
                    candidates=list(run.candidates),
                    narrations=list(run.narrations),
                    trace=self._trace.as_list()[trace_before:],
                    degradations=degradations,
                    provider=provider_status.current(foundry.is_available()).as_dict(),
                )
            )
            self._overlays.extend(overlays)
            if end_ms >= MATCH_DURATION_MS:
                self._recap = self._build_recap()
                if self._recap is not None:
                    self._overlays.append(self._recap)
        self._computed_ms = end_ms
        self._advanced.set()
        self._advanced.clear()

    def _stretches(self) -> list[tuple[int, int, list[MatchEvent]]]:
        """The match, cut into stretches of match time.

        Each stretch holds the events that happened inside it. The
        generator's log ends a little before the final whistle, so the
        last stretch can be empty; it is kept anyway, so the number of
        stretches is the number of intervals in the match.
        """
        buckets: dict[int, list[MatchEvent]] = {}
        for event in self._events:
            index = event.match_clock_ms // self._stretch_ms
            buckets.setdefault(index, []).append(event)

        stretches: list[tuple[int, int, list[MatchEvent]]] = []
        count = MATCH_DURATION_MS // self._stretch_ms
        for index in range(count):
            start = index * self._stretch_ms
            end = min(start + self._stretch_ms, MATCH_DURATION_MS)
            stretches.append((start, end, buckets.get(index, [])))
        return stretches

    def _client_degradation_count(self) -> int:
        """How many degradations the A2A client has recorded so far."""
        client = getattr(self._narrator, "_explainer_client", None)
        return len(getattr(client, "degradations", []) or [])

    def _client_degradations(self, before: int) -> list[str]:
        """The A2A client's degradations since a mark."""
        client = getattr(self._narrator, "_explainer_client", None)
        return list(getattr(client, "degradations", []) or [])[before:]
