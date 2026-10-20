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

from touchline.agents.explainer import ExplainerAgent
from touchline.agents.narrator import NarratorAgent
from touchline.agents.personalizer import PersonalizerAgent
from touchline.metrics.analyst import MatchAnalyst
from touchline.models.events import MATCH_DURATION_MS, MatchEvent, TeamSide
from touchline.models.insights import InsightCandidate
from touchline.models.overlay import Overlay
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
    trace: list[Handoff] = field(default_factory=list)
    degradations: list[str] = field(default_factory=list)
    provider: dict[str, object] = field(default_factory=dict)

    @property
    def overlay_clocks(self) -> list[int]:
        """The match clock of each overlay, in order."""
        return [overlay.match_clock_ms for overlay in self.overlays]


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
        stretch_ms: int | None = None,
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
            stretch_ms: How much match time one stretch covers. The
                analyst's own feed interval is used when absent, so the
                stretches line up with the feed exactly.
        """
        from touchline.providers.clients import build_client

        self._seed = seed
        self._match_id = match_id or f"seed-{seed}"
        self._locale = locale
        self._mode = mode

        self._events: list[MatchEvent] = sorted(
            simulate(seed=seed), key=lambda e: (e.match_clock_ms, e.event_id)
        )
        self._team_names = team_names(seed)

        self._analyst = MatchAnalyst(team_names=self._team_names)
        self._stretch_ms = stretch_ms or self._analyst.thresholds.feed_interval_ms

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
            chat_client, mode=mode, locale=locale
        )

        self._results: list[StretchResult] = []
        self._computed_ms = 0
        self._task: asyncio.Task[None] | None = None
        self._advanced = asyncio.Event()

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
    def overlays(self) -> list[Overlay]:
        """Every overlay computed so far, in clock order."""
        return [overlay for result in self._results for overlay in result.overlays]

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

    # -- the work ----------------------------------------------------------

    async def _compute(self) -> None:
        """Analyse the match, one stretch at a time."""
        try:
            for index, (start_ms, end_ms, events) in enumerate(self._stretches()):
                await self._analyse_stretch(index, start_ms, end_ms, events)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("the live analysis stopped")
        finally:
            self._advanced.set()

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

        workflow = MatchWorkflow(
            store=self._store,
            analyst=self._analyst,
            explainer=self._explainer,
            narrator=self._narrator,
            personalizer=self._personalizer,
            trace=self._trace,
            cursor=self._cursor,
            locale=self._locale,
            mode=self._mode,
        )
        run = await workflow.run(events, match_id=self._match_id)

        degradations = [
            *workflow.local_degradations,
            *self._narrator.degradations[narrator_before:],
            *self._personalizer.degradations[personalizer_before:],
            *self._client_degradations(client_before),
        ]
        self._results.append(
            StretchResult(
                index=index,
                start_ms=start_ms,
                end_ms=end_ms,
                overlays=list(run.overlays),
                candidates=list(run.candidates),
                trace=self._trace.as_list()[trace_before:],
                degradations=degradations,
                provider=provider_status.current(foundry.is_available()).as_dict(),
            )
        )
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
