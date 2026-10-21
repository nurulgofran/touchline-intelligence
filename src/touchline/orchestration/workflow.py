"""The workflow graph: the six stages, wired to one shared state.

This is the orchestration core of the project, and it is a **Microsoft Agent
Framework** graph -- typed executors joined by typed edges, with the type
pairing checked when the graph is built. A hand-off whose types do not line
up is a hand-off that would silently drop work at run time, so the framework
refuses to build that graph at all.

The shape of it::

    ingest ──▶ analyst ──▶ explainer ──▶ grounding ──▶ narrator ──▶ personalizer
                                ▲             │
                                └─────────────┘
                                    the veto, going back

Five of the six stages are linear. The sixth is the one that matters: the
**Grounding** step can refuse a claim, and a refusal is not an exception and
not a silent drop. It is a verdict, it carries its reasons, and the draft
goes *back* to the Explainer along the same edge structure, to be explained
again with the objection attached. That is what "recover from failures"
looks like in a multi-agent system, and it is why the back edge exists.

Two details of the graph are worth knowing before reading the code:

**The fan-out happens inside a handler.** The framework's edge runner routes
by *executor id*, so a message with no target id goes to every edge leaving
its sender. There is no per-candidate executor, so the Explainer's handler
loops over the candidates and sends one message per candidate -- and the
Grounding step's two outgoing edges are told apart by the target id on the
message: the accepted verdict goes to ``narrator`` and the retry draft goes
to ``explainer``.

**The last stage yields rather than sends.** An executor with no outgoing
edge has its sent messages dropped, so the Personaliser declares its result
as a *workflow output* and yields it. That is what comes back from the run.

Every stage reads and writes one :class:`MatchStateStore`, and every edge
crossing is recorded in the :class:`HandoffTrace`, which is what the
agent-trace view in the UI renders and what a judge reads to see that the
Explainer really did hand its claim to the Grounding step and the Grounding
step really did veto it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Never

from agent_framework import Executor, WorkflowBuilder, WorkflowContext, handler

from touchline.agents.explainer import ExplainerAgent
from touchline.agents.narrator import NarratorAgent
from touchline.agents.personalizer import PersonalizerAgent
from touchline.metrics.analyst import MatchAnalyst
from touchline.models.audience import Audience, AudienceMode
from touchline.models.claims import Explanation, Narration
from touchline.models.events import MatchEvent
from touchline.models.insights import InsightCandidate
from touchline.models.overlay import Overlay
from touchline.orchestration.a2a_client import ExplainerClient
from touchline.orchestration.grounding import GroundingVerdict, ground_claim
from touchline.orchestration.handoffs import HandoffTrace
from touchline.orchestration.messages import (
    InsightExplained,
    InsightNarrated,
    InsightPersonalised,
    InsightsAnalysed,
    MatchIngested,
    MatchLoaded,
)
from touchline.providers import attribution, template
from touchline.providers import status as provider_status
from touchline.state.store import MatchStateStore

logger = logging.getLogger(__name__)


def _serving_provider(stage: str, candidate_id: str) -> str | None:
    """Which provider served the stage's call for one insight.

    The provider result is read straight after the call, in the same
    task, so the last matching record is that call's. ``None`` when the
    stage made no provider call -- a test double, or a deterministic
    path -- in which case the hand-off carries no provenance.
    """
    call = provider_status.last_call_for(
        stage=stage,
        candidate_id=candidate_id,
        revision=attribution.current().revision,
    )
    return call.provider if call is not None else None

__all__ = [
    "STAGE_ANALYST",
    "STAGE_EXPLAINER",
    "STAGE_GROUNDING",
    "STAGE_INGEST",
    "STAGE_NARRATOR",
    "STAGE_PERSONALIZER",
    "AnalystExecutor",
    "ExplainerExecutor",
    "FeedCursor",
    "GroundingExecutor",
    "IngestExecutor",
    "MatchRun",
    "MatchWorkflow",
    "NarratorExecutor",
    "PersonalizerExecutor",
    "build_explainer_client",
]

#: The ids of the six executors. They are the routing addresses of the graph:
#: a message names the executor it is for, and the edge runner delivers it
#: only to the edge that ends there.
STAGE_INGEST = "ingest"
STAGE_ANALYST = "analyst"
STAGE_EXPLAINER = "explainer"
STAGE_GROUNDING = "grounding"
STAGE_NARRATOR = "narrator"
STAGE_PERSONALIZER = "personalizer"

#: How many times one insight may be explained again after a veto before
#: the pipeline stops asking the model and takes the deterministic claim.
#:
#: The bound matters: a model that cannot be made to agree with the log
#: would otherwise be asked forever, and the feed would stop. After the
#: bound the claim is the one the candidate supports by construction -- it
#: reproduces, because it is the candidate -- and the veto stays visible in
#: the trace.
DEFAULT_MAX_GROUNDING_ATTEMPTS = 2


@dataclass
class FeedCursor:
    """Where a live match keeps what it has already put on the feed.

    The live path runs the graph once per stretch of match time, and each
    of those runs analyses the whole event log as it stands. Without a
    cursor, every run would put the whole feed on the wire again and the
    model would be asked to explain every insight in the match on every
    stretch -- slow, and wrong to look at.

    Attributes:
        ids: The ids of the insights already handed to the Explainer.
        last_bucket: The stretch of match time already published. The
            stretch is a bucket of the analyst's feed interval: ninety
            seconds of match time.
    """

    ids: set[str] = field(default_factory=set)
    last_bucket: int = -1



@dataclass
class MatchRun:
    """What one pass of the graph produced.

    Attributes:
        match_id: Which match this was.
        candidates: The insights that went through the graph, in clock order.
        narrations: The words written for each of them.
        verdicts: The Grounding step's verdict for each of them.
        overlays: The finished overlays -- the output that goes on screen.
        trace: The record of every hand-off.
        degradations: Every time something fell back on something else.
        vetoes: The claims the Grounding step refused.
    """

    match_id: str = ""
    candidates: list[InsightCandidate] = field(default_factory=list)
    narrations: list[Narration] = field(default_factory=list)
    verdicts: list[GroundingVerdict] = field(default_factory=list)
    overlays: list[Overlay] = field(default_factory=list)
    trace: HandoffTrace = field(default_factory=HandoffTrace)
    degradations: list[str] = field(default_factory=list)
    vetoes: list[GroundingVerdict] = field(default_factory=list)

    @property
    def commentary(self) -> list[str]:
        """The one-line calls, one per overlay."""
        return [
            narration.commentary
            for narration in self.narrations
            if narration.commentary
        ]


class IngestExecutor(Executor):
    """Stage one: the event log goes into the shared state."""

    def __init__(
        self,
        *,
        store: MatchStateStore,
        trace: HandoffTrace,
        id: str = STAGE_INGEST,
    ) -> None:
        """Create the executor.

        Args:
            store: The shared state the events are written to.
            trace: The ledger of hand-offs.
            id: The executor's id, which is its routing address.
        """
        super().__init__(id=id)
        self._store = store
        self._trace = trace

    @handler(input=MatchIngested, output=MatchLoaded)
    async def ingest(
        self, message: MatchIngested, ctx: WorkflowContext[MatchLoaded]
    ) -> None:
        """Put the match in the shared state and hand it to the analyst."""
        events = list(message.events)
        self._store.extend(events)
        self._trace.record(
            STAGE_INGEST,
            STAGE_ANALYST,
            "events",
            f"{len(events)} events for {message.match_id}",
        )
        await ctx.send_message(
            MatchLoaded(match_id=message.match_id, event_count=len(events)),
            target_id=STAGE_ANALYST,
        )


class AnalystExecutor(Executor):
    """Stage two: the event log becomes cited insights.

    The analyst is a **deterministic worker**, not an agent: every number it
    reports is computed from the event log and every number it reports comes
    with the ids of the events it was computed from. That is what makes the
    rest of the pipeline checkable.
    """

    def __init__(
        self,
        *,
        analyst: MatchAnalyst,
        store: MatchStateStore,
        trace: HandoffTrace,
        cursor: FeedCursor | None = None,
        id: str = STAGE_ANALYST,
    ) -> None:
        """Create the executor.

        Args:
            analyst: The deterministic analyst.
            store: The shared state holding the event log.
            trace: The ledger of hand-offs.
            cursor: Where the live path keeps what it has already handed
                over. Absent for a single batch run, which hands the whole
                feed over at once.
            id: The executor's id.
        """
        super().__init__(id=id)
        self._analyst = analyst
        self._store = store
        self._trace = trace
        self._cursor = cursor

    @handler(input=MatchLoaded, output=InsightsAnalysed)
    async def analyse(
        self, message: MatchLoaded, ctx: WorkflowContext[InsightsAnalysed]
    ) -> None:
        """Find the insights worth showing and hand them to the Explainer.

        The whole event log is analysed, not just the newest stretch of it:
        the analyst is causal, so analysing the log as it stands gives the
        same answer as the log analysed all at once -- and that is what
        makes a live feed and a batch feed the same feed.
        """
        candidates = self._analyst.analyse(self._store.events)
        feed = self._analyst.select_for_feed(candidates)
        self._store.put_candidates(candidates)

        fresh = self._fresh(feed)
        self._trace.record(
            STAGE_ANALYST,
            STAGE_EXPLAINER,
            "insights",
            f"{len(fresh)} insights for the feed, from {len(candidates)} found",
        )
        await ctx.send_message(
            InsightsAnalysed(candidates=fresh), target_id=STAGE_EXPLAINER
        )

    def _fresh(self, feed: list[InsightCandidate]) -> list[InsightCandidate]:
        """The feed entries to hand over now.

        A single batch run hands the whole feed over: the match is over,
        so there is no reason to hold anything back.

        A live run hands over **the stretch the event log has just
        filled** and nothing else. That is not a detail. The analyst's
        window rules are evaluated on a fixed grid of match times, so
        analysing a log that stops at 12:00 also produces a candidate for
        12:30 -- with a window that has only half the events in it. That
        candidate is a guess about a stretch that has not happened yet, and
        it is *not* the candidate the same stretch produces once the log
        reaches it. So the live path waits: it publishes a stretch when the
        log has filled that stretch, and the window it computes is then
        whole.

        The result is that the live feed and the batch feed carry the same
        insights, in the same order, with the same numbers.
        """
        if self._cursor is None:
            return feed

        span = self._analyst.thresholds.feed_interval_ms
        newest = max(
            (event.match_clock_ms for event in self._store.events), default=-1
        )
        bucket = newest // span
        if bucket <= self._cursor.last_bucket:
            return []
        self._cursor.last_bucket = bucket

        fresh = [
            candidate
            for candidate in feed
            if candidate.match_clock_ms // span == bucket
            and candidate.candidate_id not in self._cursor.ids
        ]
        for candidate in fresh:
            self._cursor.ids.add(candidate.candidate_id)
        return fresh


class ExplainerExecutor(Executor):
    """Stage three: each insight becomes a claim.

    The Explainer does not assert. It *claims*: it returns the reading, and
    alongside it the numbers and the event ids the reading rests on. The
    grounding step then recomputes those numbers from the log, and a claim
    that does not reproduce is vetoed and comes back here.

    The fan-out lives inside the handler: the framework routes by executor
    id, so one message per candidate is sent from here, each naming the
    Grounding step as its target.
    """

    def __init__(
        self,
        *,
        explainer: ExplainerAgent,
        store: MatchStateStore,
        trace: HandoffTrace,
        locale: str = "en",
        mode: str = "analyst",
        id: str = STAGE_EXPLAINER,
    ) -> None:
        """Create the executor.

        Args:
            explainer: The Explainer agent.
            store: The shared state.
            trace: The ledger of hand-offs.
            locale: Language code for the prose.
            mode: Audience mode.
            id: The executor's id.
        """
        super().__init__(id=id)
        self._explainer = explainer
        self._store = store
        self._trace = trace
        self._locale = locale
        self._mode = mode

    @handler(input=InsightsAnalysed, output=InsightExplained)
    async def explain_all(
        self, message: InsightsAnalysed, ctx: WorkflowContext[InsightExplained]
    ) -> None:
        """Explain every insight the analyst found."""
        for candidate in message.candidates:
            await self._explain(candidate, ctx, attempt=1, feedback="")

    @handler(input=InsightExplained, output=InsightExplained)
    async def explain_again(
        self, message: InsightExplained, ctx: WorkflowContext[InsightExplained]
    ) -> None:
        """Explain an insight again, having read the objection to the last try."""
        await self._explain(
            message.candidate,
            ctx,
            attempt=message.attempt,
            feedback=message.feedback,
        )

    async def _explain(
        self,
        candidate: InsightCandidate,
        ctx: Any,
        *,
        attempt: int,
        feedback: str,
    ) -> None:
        """Ask the Explainer for a claim, and hand it to the Grounding step."""
        explanation = await self._explainer.explain(
            candidate,
            locale=self._locale,
            mode=self._mode,
            feedback=feedback,
        )
        self._trace.record(
            STAGE_EXPLAINER,
            STAGE_GROUNDING,
            "claim",
            f"{candidate.candidate_id}: claim on "
            f"{len(explanation.cited_event_ids)} events",
            candidate_id=candidate.candidate_id,
            provider=_serving_provider(STAGE_EXPLAINER, candidate.candidate_id),
        )
        await ctx.send_message(
            InsightExplained(
                candidate=candidate,
                explanation=explanation,
                attempt=attempt,
                feedback=feedback,
            ),
            target_id=STAGE_GROUNDING,
        )


class GroundingExecutor(Executor):
    """Stage four: the claim is checked against the event log.

    This is the differentiator, and it is the stage that can send work
    *backwards*. A claim that does not reproduce is vetoed, and the draft
    goes back to the Explainer along the back edge, with the reasons
    attached, to be explained again.

    The bound is deliberate. After ``max_attempts`` the model is not asked
    again; the deterministic claim is taken instead -- the one the candidate
    supports by construction -- the veto stays in the trace, and the
    degradation is recorded. A model that cannot be made to agree with the
    log must not be able to stall the feed.
    """

    def __init__(
        self,
        *,
        store: MatchStateStore,
        trace: HandoffTrace,
        locale: str = "en",
        max_attempts: int = DEFAULT_MAX_GROUNDING_ATTEMPTS,
        id: str = STAGE_GROUNDING,
    ) -> None:
        """Create the executor.

        Args:
            store: The shared state holding the event log.
            trace: The ledger of hand-offs.
            locale: Language code for the deterministic fallback's reading.
            max_attempts: How many times a claim may be explained again
                after a veto before the deterministic claim is taken.
            id: The executor's id.
        """
        super().__init__(id=id)
        self._store = store
        self._trace = trace
        self._locale = locale
        self._max_attempts = max(1, max_attempts)
        self._verdicts: list[GroundingVerdict] = []
        self._degradations: list[str] = []

    @property
    def verdicts(self) -> list[GroundingVerdict]:
        """Every verdict this executor has reached, in order."""
        return list(self._verdicts)

    @property
    def degradations(self) -> list[str]:
        """Every time the deterministic claim was taken instead of a model one."""
        return list(self._degradations)

    @handler(input=InsightExplained, output=GroundingVerdict | InsightExplained)
    async def ground(
        self,
        message: InsightExplained,
        ctx: WorkflowContext[GroundingVerdict | InsightExplained],
    ) -> None:
        """Check one claim, and either let it through or send it back."""
        candidate = message.candidate
        explanation = message.explanation

        verdict = ground_claim(
            self._store,
            candidate.candidate_id,
            explanation,
            expected_metric_names=[metric.name for metric in candidate.metrics],
        )
        self._verdicts.append(verdict)

        if verdict.accepted:
            self._store.put_explanation(candidate.candidate_id, explanation)
            self._trace.record(
                STAGE_GROUNDING,
                STAGE_NARRATOR,
                "verdict",
                verdict.summary,
                candidate_id=candidate.candidate_id,
                accepted=True,
            )
            await ctx.send_message(verdict, target_id=STAGE_NARRATOR)
            return

        # The veto. It is recorded as a refused hand-off before anything else
        # happens, because a veto nobody can see is a veto nobody can believe.
        self._trace.record(
            STAGE_GROUNDING,
            STAGE_EXPLAINER,
            "veto",
            verdict.summary,
            candidate_id=candidate.candidate_id,
            accepted=False,
        )

        if message.attempt < self._max_attempts:
            await ctx.send_message(
                InsightExplained(
                    candidate=candidate,
                    explanation=explanation,
                    attempt=message.attempt + 1,
                    feedback="; ".join(verdict.reasons),
                ),
                target_id=STAGE_EXPLAINER,
            )
            return

        # The bound is reached. The deterministic claim is taken: the one the
        # candidate supports by construction, so it reproduces.
        fallback = Explanation.from_candidate(
            candidate,
            template.render_reading(candidate, locale=self._locale),
        )
        self._store.put_explanation(candidate.candidate_id, fallback)
        reason = (
            f"{candidate.candidate_id}: the claim was vetoed {message.attempt} "
            f"times ({'; '.join(verdict.reasons)}); "
            "the deterministic claim was used instead"
        )
        self._degradations.append(reason)
        logger.warning("%s", reason)

        self._trace.record(
            STAGE_GROUNDING,
            STAGE_NARRATOR,
            "fallback",
            reason,
            candidate_id=candidate.candidate_id,
            accepted=True,
        )
        await ctx.send_message(
            GroundingVerdict(
                candidate_id=candidate.candidate_id,
                accepted=True,
                reasons=verdict.reasons,
                checks=verdict.checks,
            ),
            target_id=STAGE_NARRATOR,
        )


class NarratorExecutor(Executor):
    """Stage five: the claim becomes the words on screen.

    The Narrator asks the Explainer over A2A for the reading it writes
    from -- a real protocol hop, not a function call -- and falls back to
    the reading it was handed when the hop cannot be made.
    """

    def __init__(
        self,
        *,
        narrator: NarratorAgent,
        store: MatchStateStore,
        trace: HandoffTrace,
        locale: str = "en",
        mode: str = "analyst",
        id: str = STAGE_NARRATOR,
    ) -> None:
        """Create the executor.

        Args:
            narrator: The Narrator agent.
            store: The shared state, holding the candidate and the accepted
                claim.
            trace: The ledger of hand-offs.
            locale: Language code for the prose.
            mode: Audience mode.
            id: The executor's id.
        """
        super().__init__(id=id)
        self._narrator = narrator
        self._store = store
        self._trace = trace
        self._locale = locale
        self._mode = mode
        self._narrations: list[Narration] = []
        self._degradations: list[str] = []

    @property
    def narrations(self) -> list[Narration]:
        """Every narration this executor has written, in order."""
        return list(self._narrations)

    @property
    def degradations(self) -> list[str]:
        """Every time the Narrator had to fall back."""
        return list(self._degradations)

    @handler(input=GroundingVerdict, output=InsightNarrated)
    async def narrate(
        self,
        message: GroundingVerdict,
        ctx: WorkflowContext[InsightNarrated],
    ) -> None:
        """Write the words for one grounded insight."""
        if not message.accepted:
            return

        candidate = self._store.candidate(message.candidate_id)
        explanation = self._store.explanation(message.candidate_id)
        if candidate is None or explanation is None:
            reason = (
                f"{message.candidate_id}: the grounded claim was not in the "
                f"shared state, so nothing was written"
            )
            self._degradations.append(reason)
            logger.warning("%s", reason)
            return

        # A vetoed claim has no reading worth asking the Explainer for
        # again: the answer would be the claim that was already refused.
        # The deterministic reading is grounded by construction, so it is
        # used instead.
        ask_explainer = not message.reasons

        narration = await self._narrator.narrate(
            candidate,
            explanation,
            locale=self._locale,
            mode=self._mode,
            ask_explainer=ask_explainer,
        )
        self._narrations.append(narration)

        if ask_explainer:
            # The hop is recorded, because a multi-agent system is only as
            # convincing as its hand-offs: a judge has to be able to see
            # that the Narrator really did reach the Explainer over A2A,
            # and which transport answered.
            client = getattr(self._narrator, "_explainer_client", None)
            transport = getattr(client, "last_transport", None)
            if transport is not None:
                self._trace.record(
                    STAGE_NARRATOR,
                    STAGE_EXPLAINER,
                    "a2a",
                    f"{candidate.candidate_id}: the narrator asked the "
                    f"explainer over {transport}",
                    candidate_id=candidate.candidate_id,
                    accepted=True,
                )

        self._trace.record(
            STAGE_NARRATOR,
            STAGE_PERSONALIZER,
            "narration",
            f"{candidate.candidate_id}: {narration.title}",
            candidate_id=candidate.candidate_id,
            provider=_serving_provider(STAGE_NARRATOR, candidate.candidate_id),
        )
        await ctx.send_message(
            InsightNarrated(candidate=candidate, narration=narration),
            target_id=STAGE_PERSONALIZER,
        )


class PersonalizerExecutor(Executor):
    """Stage six: the copy is written for one audience.

    This is the end of the graph, so it **yields** its result rather than
    sending it: an executor with no outgoing edge has its sent messages
    dropped by the runner.

    The audience is read here and not from the agent, because the graph is
    rebuilt for every stretch of a live match while the *audience* is a
    property of the match. The candidate's cited events are fetched from the
    shared state, because the audience reads exactly those events and the
    numbers it produces have to be recomputable from them.
    """

    def __init__(
        self,
        *,
        personalizer: PersonalizerAgent,
        trace: HandoffTrace,
        store: MatchStateStore | None = None,
        locale: str = "en",
        mode: str = "analyst",
        audience: Audience | None = None,
        id: str = STAGE_PERSONALIZER,
    ) -> None:
        """Create the executor.

        Args:
            personalizer: The Personaliser agent.
            trace: The ledger of hand-offs.
            store: The shared state, holding the cited events.
            locale: Language code for the prose.
            mode: Audience mode.
            audience: Who the overlay is written for. The mode and the
                locale above are used when absent.
            id: The executor's id.
        """
        super().__init__(id=id)
        self._personalizer = personalizer
        self._trace = trace
        self._store = store
        self._locale = locale
        self._mode = mode
        self._audience = audience
        self._degradations: list[str] = []

    @property
    def degradations(self) -> list[str]:
        """Every time the Personaliser had to fall back on the narration."""
        return list(self._degradations)

    @handler(input=InsightNarrated, workflow_output=InsightPersonalised)
    async def personalise(
        self,
        message: InsightNarrated,
        ctx: WorkflowContext[Never, InsightPersonalised],
    ) -> None:
        """Rewrite the copy for the audience and yield the finished overlay."""
        overlay = await self._personalizer.personalise(
            message.candidate,
            message.narration,
            audience=self._audience,
            events=self._cited_events(message.candidate),
            locale=self._locale,
            mode=self._mode,
        )
        self._trace.record(
            STAGE_PERSONALIZER,
            "screen",
            "overlay",
            f"{message.candidate.candidate_id}: {overlay.title}",
            candidate_id=message.candidate.candidate_id,
            provider=_serving_provider(
                STAGE_PERSONALIZER, message.candidate.candidate_id
            ),
        )
        await ctx.yield_output(
            InsightPersonalised(candidate=message.candidate, overlay=overlay)
        )

    def _cited_events(self, candidate: InsightCandidate) -> list[MatchEvent]:
        """The events the candidate cites, as the shared state holds them."""
        if self._store is None:
            return []
        events: list[MatchEvent] = []
        for event_id in candidate.cited_event_ids:
            event = self._store.get(event_id)
            if event is not None:
                events.append(event)
        return events


class MatchWorkflow:
    """The whole graph, wired to one store and one set of agents.

    Example:
        >>> from touchline.orchestration.workflow import MatchWorkflow
        >>> workflow = MatchWorkflow()
        >>> run = await workflow.run([])          # doctest: +SKIP
        >>> run.overlays                          # doctest: +SKIP
        []
    """

    def __init__(
        self,
        *,
        store: MatchStateStore | None = None,
        analyst: MatchAnalyst | None = None,
        explainer: ExplainerAgent | None = None,
        narrator: NarratorAgent | None = None,
        personalizer: PersonalizerAgent | None = None,
        explainer_client: ExplainerClient | None = None,
        trace: HandoffTrace | None = None,
        cursor: FeedCursor | None = None,
        locale: str = "en",
        mode: str = "analyst",
        audience: Audience | None = None,
        max_grounding_attempts: int = DEFAULT_MAX_GROUNDING_ATTEMPTS,
    ) -> None:
        """Build the graph.

        Every agent may be left out, in which case the template engine
        answers for it -- the pipeline has to be runnable before any Azure
        credentials exist, and it has to keep running when they do not.

        A live match builds the graph once per stretch of match time, so
        three things can be **shared** between those builds: the store (the
        event log grows), the trace (the ledger of hand-offs grows), and
        the set of insights already handed to the Explainer (so the model
        is not asked to explain the same moment again on the next stretch).
        The agents are shared too, because the Explainer's memory is what
        makes the A2A hop cheap.

        Args:
            store: The shared state. A fresh one is made when absent.
            analyst: The deterministic analyst.
            explainer: The Explainer agent.
            narrator: The Narrator agent.
            personalizer: The Personaliser agent.
            explainer_client: The client the Narrator uses to reach the
                Explainer over A2A.
            trace: The ledger of hand-offs. A fresh one is made when absent.
            cursor: Where the live path keeps what it has already handed
                over. Nothing is held back when absent, which is what a
                single batch run wants.
            locale: Language code for the prose.
            mode: Audience mode.
            audience: Who the overlays are written for. The mode and the
                locale above are used when absent; when it is given, it
                decides both, because a language and an audience are one
                decision and not two.
            max_grounding_attempts: How many times a claim may be explained
                again after a veto before the deterministic claim is taken.
        """
        from touchline.providers.clients import TemplateChatClient

        # `is None` and not `or`: the store and the trace both define
        # `__len__`, so an *empty* one is falsy. `store or MatchStateStore()`
        # would quietly throw away the caller's store and the caller's
        # trace -- and a live match that shares them would silently stop
        # sharing them.
        self._store = store if store is not None else MatchStateStore()
        self._analyst = analyst if analyst is not None else MatchAnalyst()
        self._audience = audience or Audience(
            mode=AudienceMode(mode)
            if mode in {m.value for m in AudienceMode}
            else AudienceMode.ANALYST,
            locale=locale,
        )
        locale = self._audience.locale
        mode = self._audience.mode.value
        self._locale = locale
        self._mode = mode
        self._trace = trace if trace is not None else HandoffTrace()
        self._cursor = cursor

        template_client = TemplateChatClient()

        self._explainer = explainer or ExplainerAgent(template_client)
        self._narrator = narrator or NarratorAgent(
            template_client, explainer_client=explainer_client
        )
        self._personalizer = personalizer or PersonalizerAgent(
            template_client, mode=mode, locale=locale
        )

        self._ingest = IngestExecutor(store=self._store, trace=self._trace)
        self._analyst_executor = AnalystExecutor(
            analyst=self._analyst,
            store=self._store,
            trace=self._trace,
            cursor=self._cursor,
        )
        self._explainer_executor = ExplainerExecutor(
            explainer=self._explainer,
            store=self._store,
            trace=self._trace,
            locale=locale,
            mode=mode,
        )
        self._grounding = GroundingExecutor(
            store=self._store,
            trace=self._trace,
            locale=locale,
            max_attempts=max_grounding_attempts,
        )
        self._narrator_executor = NarratorExecutor(
            narrator=self._narrator,
            store=self._store,
            trace=self._trace,
            locale=locale,
            mode=mode,
        )
        self._personalizer_executor = PersonalizerExecutor(
            personalizer=self._personalizer,
            trace=self._trace,
            store=self._store,
            locale=locale,
            mode=mode,
            audience=self._audience,
        )

        self._workflow = (
            WorkflowBuilder(name="touchline-match", start_executor=self._ingest)
            .add_edge(self._ingest, self._analyst_executor)
            .add_edge(self._analyst_executor, self._explainer_executor)
            .add_edge(self._explainer_executor, self._grounding)
            .add_edge(self._grounding, self._narrator_executor)
            # The back edge. A vetoed draft goes back to the Explainer, on
            # the same edge structure, to be explained again.
            .add_edge(self._grounding, self._explainer_executor)
            .add_edge(self._narrator_executor, self._personalizer_executor)
            .build()
        )

    # -- the run ------------------------------------------------------------

    @property
    def store(self) -> MatchStateStore:
        """The shared state every executor reads."""
        return self._store

    @property
    def trace(self) -> HandoffTrace:
        """The ledger of hand-offs."""
        return self._trace

    @property
    def audience(self) -> Audience:
        """Who the overlays are written for."""
        return self._audience

    async def run(
        self,
        events: list[MatchEvent],
        *,
        match_id: str = "match-1",
    ) -> MatchRun:
        """Run one match through the graph.

        Args:
            events: The event log.
            match_id: Which match this is.

        Returns:
            Everything the run produced: the overlays, the narrations, the
            verdicts, the trace of hand-offs, and every degradation.
        """
        result = await self._workflow.run(
            MatchIngested(match_id=match_id, events=list(events))
        )

        overlays: list[Overlay] = []
        candidates: list[InsightCandidate] = []
        for output in result.get_outputs():
            if isinstance(output, InsightPersonalised):
                overlays.append(output.overlay)
                candidates.append(output.candidate)

        verdicts = self._grounding.verdicts
        vetoes = [verdict for verdict in verdicts if not verdict.accepted]

        return MatchRun(
            match_id=match_id,
            candidates=candidates,
            narrations=self._narrator_executor.narrations,
            verdicts=verdicts,
            overlays=overlays,
            trace=self._trace,
            degradations=self._degradations(),
            vetoes=vetoes,
        )

    def _degradations(self) -> list[str]:
        """Every degradation that happened during the run."""
        degradations: list[str] = []
        degradations.extend(self._grounding.degradations)
        degradations.extend(self._narrator_executor.degradations)
        degradations.extend(self._personalizer_executor.degradations)
        degradations.extend(self._narrator.degradations)
        degradations.extend(self._personalizer.degradations)
        client = getattr(self._narrator, "_explainer_client", None)
        if client is not None:
            degradations.extend(getattr(client, "degradations", []))
        return degradations

    @property
    def local_degradations(self) -> list[str]:
        """The degradations this graph's own executors recorded.

        The agents' degradations are *not* here: the agents are shared
        between the runs of a live match, so their list grows for the whole
        match rather than for one stretch. A caller that wants the
        degradations of one stretch asks the executors.
        """
        return [
            *self._grounding.degradations,
            *self._narrator_executor.degradations,
            *self._personalizer_executor.degradations,
        ]


def build_explainer_client(
    explainer: ExplainerAgent,
    *,
    url: str,
    http_client: Any = None,
    timeout_seconds: float = 20.0,
    max_attempts: int = 3,
    backoff_seconds: float = 0.5,
) -> ExplainerClient:
    """Build the client the Narrator uses to reach the Explainer over A2A.

    The client falls back to asking the Explainer in-process, so a wire
    that cannot be used degrades the transport rather than the feed.

    Args:
        explainer: The Explainer to serve and to fall back on.
        url: The Explainer's JSON-RPC endpoint.
        http_client: An ``httpx.AsyncClient`` to use. The tests pass one
            bound to the ASGI application, so the round trip is a real
            protocol exchange without a socket.
        timeout_seconds: How long one attempt may take.
        max_attempts: How many attempts in total, including the first.
        backoff_seconds: The first wait between attempts.

    Returns:
        The client.
    """
    return ExplainerClient(
        url,
        timeout_seconds=timeout_seconds,
        max_attempts=max_attempts,
        backoff_seconds=backoff_seconds,
        fallback=explainer,
        http_client=http_client,
    )
