"""The deterministic analyst -- stage (b) of the pipeline.

The analyst is not an agent and is not a model. It is ordinary Python that
reads the event log and decides what is worth saying. Microsoft's own Agent
Framework guidance puts it plainly: if a function can handle the task, write
the function. Framing it this way is a strength in the pitch, not a weakness.

What makes the analyst worth reading is what it refuses to do. It never
restates a statistic: every insight is a **deviation** -- a pass accuracy that
has moved against the fifteen minutes before it, a side that has suddenly kept
the ball, a player who has crossed two and a half kilometres. And every
number it reports is computed by the shared metric registry
(:mod:`touchline.metrics.definitions`) over a set of event ids that it cites,
so the grounding step can recompute each one from the log and veto any claim
that does not reproduce.

The rolling baseline is what makes the deviation claim honest. A five-minute
window is compared against the fifteen minutes before it; the two windows
never overlap, and the baseline carries its own citations, so both halves of
every comparison are independently verifiable.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from touchline.metrics.definitions import (
    compute_metric,
    is_chaotic,
    is_controlled,
    metric_spec,
)
from touchline.metrics.thresholds import Thresholds
from touchline.models.events import (
    MATCH_DURATION_MS,
    EventType,
    MatchEvent,
    TeamSide,
    meta_float,
)
from touchline.models.insights import (
    BaselineComparison,
    InsightCandidate,
    InsightKind,
    MetricValue,
)

__all__ = ["MatchAnalyst", "MomentumPoint"]

#: How interesting each kind of insight is, when two of them land in the same
#: stretch of the feed. A broadcast can say one thing at a time, and a chance
#: is worth more airtime than a drift in the passing.
_KIND_PRIORITY: dict[InsightKind, int] = {
    InsightKind.CHANCE_CREATED: 6,
    InsightKind.SET_PIECE: 5,
    InsightKind.MILESTONE: 4,
    InsightKind.THREAT: 3,
    InsightKind.PRESS_BREAK: 2,
    InsightKind.MOMENTUM_SHIFT: 1,
    InsightKind.TACTICAL: 0,
    InsightKind.DISCIPLINE: 0,
}


@dataclass(frozen=True)
class MomentumPoint:
    """One point on the momentum curve, for the chart in the studio view."""

    match_clock_ms: int
    home: float
    away: float


@dataclass(frozen=True)
class _Draft:
    """An insight before it is given an id and becomes a candidate."""

    rule: str
    kind: InsightKind
    clock: int
    side: TeamSide
    headline: str
    metrics: tuple[tuple[str, float, TeamSide | None], ...]
    evidence: tuple[MatchEvent, ...]
    baseline_metric: str | None = None
    baseline_value: float | None = None
    baseline_side: TeamSide | None = None
    baseline_delta: float | None = None
    baseline_events: tuple[MatchEvent, ...] = ()
    confidence: float = 0.5
    tags: tuple[str, ...] = ()


@dataclass
class _PlayerTimeline:
    """One player's actions, and the ground they have covered so far."""

    player_id: str
    name: str
    side: TeamSide | None
    events: list[MatchEvent] = field(default_factory=list)
    distance_m: float = 0.0
    goals: int = 0


class MatchAnalyst:
    """Turns an event log into cited, recomputable insight candidates.

    Example:
        >>> from touchline.sim.generator import simulate
        >>> analyst = MatchAnalyst()
        >>> candidates = analyst.analyse(simulate(seed=42))
        >>> all(candidate.cited_event_ids for candidate in candidates)
        True
    """

    def __init__(
        self,
        thresholds: Thresholds | None = None,
        team_names: Mapping[TeamSide, str] | None = None,
    ) -> None:
        self.thresholds = thresholds or Thresholds()
        self.team_names: dict[TeamSide, str] = dict(team_names or {})

    # -- naming -----------------------------------------------------------

    def team(self, side: TeamSide) -> str:
        """The display name of a side."""
        named = self.team_names.get(side)
        if named:
            return named
        return "the home side" if side is TeamSide.HOME else "the away side"

    # -- public API -------------------------------------------------------

    def analyse(self, events: Sequence[MatchEvent]) -> list[InsightCandidate]:
        """Analyse a whole match and return its insights, in clock order.

        The result is deterministic: the same log always produces the same
        candidates, with the same ids, in the same order.
        """
        ordered = sorted(events, key=lambda e: (e.match_clock_ms, e.event_id))
        if not ordered:
            return []

        drafts: list[_Draft] = []
        last_fired: dict[tuple[str, TeamSide], int] = {}

        for draft in self._window_drafts(ordered):
            if self._allowed(draft, last_fired):
                drafts.append(draft)

        for draft in self._set_piece_drafts(ordered):
            if self._allowed(draft, last_fired):
                drafts.append(draft)

        for draft in self._dangerous_set_piece_drafts(ordered):
            if self._allowed(draft, last_fired):
                drafts.append(draft)

        for draft in self._milestone_drafts(ordered):
            if self._allowed(draft, last_fired):
                drafts.append(draft)

        drafts.sort(key=lambda d: (d.clock, d.rule, d.side.value))
        return [
            self._candidate(index, draft) for index, draft in enumerate(drafts, start=1)
        ]

    def select_for_feed(
        self,
        candidates: Sequence[InsightCandidate],
        interval_ms: int | None = None,
    ) -> list[InsightCandidate]:
        """Choose the insights worth putting on screen, one stretch at a time.

        The analyst finds more moments than a broadcast can say -- a match
        holds dozens of genuine deviations, and several of them can land in
        the same two minutes. This picks the one that matters most from each
        stretch of the feed, so the on-screen stream stays readable while the
        full list stays available behind it.

        The choice is by kind first and confidence second: a chance created
        outranks a passing drift even when the drift is the more confident
        of the two, because the chance is the thing a viewer would have
        missed.

        Args:
            candidates: The candidates to choose from.
            interval_ms: How much match time one insight occupies. Defaults
                to the analyst's ``feed_interval_ms``.

        Returns:
            The chosen candidates, in clock order.
        """
        span = interval_ms
        if span is None:
            span = self.thresholds.feed_interval_ms
        if span <= 0:
            return list(candidates)

        chosen: dict[int, InsightCandidate] = {}
        for candidate in candidates:
            bucket = candidate.match_clock_ms // span
            incumbent = chosen.get(bucket)
            if incumbent is None or self._outranks(candidate, incumbent):
                chosen[bucket] = candidate
        return sorted(chosen.values(), key=lambda c: (c.match_clock_ms, c.candidate_id))

    @staticmethod
    def _outranks(
        candidate: InsightCandidate, incumbent: InsightCandidate
    ) -> bool:
        """Whether one candidate is worth more airtime than another."""
        candidate_rank = (
            _KIND_PRIORITY.get(candidate.kind, 0),
            candidate.confidence,
        )
        incumbent_rank = (
            _KIND_PRIORITY.get(incumbent.kind, 0),
            incumbent.confidence,
        )
        return candidate_rank > incumbent_rank

    def momentum_curve(
        self,
        events: Sequence[MatchEvent],
        interval_ms: int = 60_000,
    ) -> list[MomentumPoint]:
        """The rolling momentum balance, for the studio view's chart.

        The index is the same one the momentum rule fires on, sampled on a
        fixed grid so the chart has one point per interval rather than one
        per event.
        """
        ordered = sorted(events, key=lambda e: (e.match_clock_ms, e.event_id))
        if not ordered:
            return []
        clocks = [event.match_clock_ms for event in ordered]
        points: list[MomentumPoint] = []
        clock = 0
        while clock <= MATCH_DURATION_MS:
            lo = bisect_left(clocks, clock - self.thresholds.window_ms)
            hi = bisect_right(clocks, clock)
            window = ordered[lo:hi]
            if len(window) >= self.thresholds.min_window_events:
                points.append(
                    MomentumPoint(
                        match_clock_ms=clock,
                        home=compute_metric("momentum_index", window, TeamSide.HOME),
                        away=compute_metric("momentum_index", window, TeamSide.AWAY),
                    )
                )
            clock += interval_ms
        return points

    # -- window rules -----------------------------------------------------

    def _window_drafts(self, ordered: list[MatchEvent]) -> list[_Draft]:
        """Every rule that looks at a rolling window."""
        thresholds = self.thresholds
        clocks = [event.match_clock_ms for event in ordered]
        drafts: list[_Draft] = []

        clock = thresholds.window_ms + thresholds.baseline_ms
        while clock <= MATCH_DURATION_MS:
            window_start = clock - thresholds.window_ms
            recent = ordered[
                bisect_left(clocks, window_start) : bisect_right(clocks, clock)
            ]
            if len(recent) >= thresholds.min_window_events:
                baseline = ordered[
                    bisect_left(
                        clocks, window_start - thresholds.baseline_ms
                    ) : bisect_left(clocks, window_start)
                ]
                for side in (TeamSide.HOME, TeamSide.AWAY):
                    drafts.extend(
                        draft
                        for draft in (
                            self._rule_momentum(clock, recent, baseline, side),
                            self._rule_passing(clock, recent, baseline, side),
                            self._rule_control(clock, recent, baseline, side),
                            self._rule_tempo(clock, recent, side),
                            self._rule_press(clock, recent, side),
                            self._rule_chance(clock, recent, side),
                            self._rule_threat(clock, recent, side),
                            self._rule_discipline(clock, recent, side),
                        )
                        if draft is not None
                    )
            clock += thresholds.eval_interval_ms
        return drafts

    def _rule_momentum(
        self,
        clock: int,
        recent: list[MatchEvent],
        baseline: list[MatchEvent],
        side: TeamSide,
    ) -> _Draft | None:
        """The balance of the match has moved to one side."""
        thresholds = self.thresholds
        now = compute_metric("momentum_index", recent, side)
        before = compute_metric("momentum_index", baseline, side)
        delta = now - before
        # Only the side that gained momentum reports it, so one swing is one
        # insight rather than two sides describing the same thing.
        if delta < thresholds.momentum_shift:
            return None
        return _Draft(
            rule="momentum",
            kind=InsightKind.MOMENTUM_SHIFT,
            clock=clock,
            side=side,
            headline=f"Momentum swings toward {self.team(side)}",
            metrics=(("momentum_index", now, side),),
            evidence=tuple(recent),
            baseline_metric="momentum_index",
            baseline_value=before,
            baseline_side=side,
            baseline_delta=delta,
            baseline_events=tuple(baseline),
            confidence=self._confidence(
                delta, thresholds.momentum_shift, len(recent), 40
            ),
            tags=("momentum",),
        )

    def _rule_passing(
        self,
        clock: int,
        recent: list[MatchEvent],
        baseline: list[MatchEvent],
        side: TeamSide,
    ) -> _Draft | None:
        """The side's passing has changed shape."""
        thresholds = self.thresholds
        attempts = compute_metric("passes", recent, side)
        if attempts < thresholds.pass_accuracy_min_passes:
            return None
        now = compute_metric("pass_accuracy", recent, side)
        before = compute_metric("pass_accuracy", baseline, side)
        delta = now - before
        if abs(delta) < thresholds.pass_accuracy_shift:
            return None
        completed = compute_metric("passes_completed", recent, side)
        headline = (
            f"{self.team(side)} put the ball away cleanly"
            if delta > 0
            else f"{self.team(side)} give the ball away too often"
        )
        return _Draft(
            rule="passing",
            kind=InsightKind.TACTICAL,
            clock=clock,
            side=side,
            headline=headline,
            metrics=(
                ("pass_accuracy", now, side),
                ("passes_completed", completed, side),
            ),
            evidence=tuple(recent),
            baseline_metric="pass_accuracy",
            baseline_value=before,
            baseline_side=side,
            baseline_delta=delta,
            baseline_events=tuple(baseline),
            confidence=self._confidence(
                delta, thresholds.pass_accuracy_shift, int(attempts), 10
            ),
            tags=("passing", "tactical"),
        )

    def _rule_control(
        self,
        clock: int,
        recent: list[MatchEvent],
        baseline: list[MatchEvent],
        side: TeamSide,
    ) -> _Draft | None:
        """The side has swapped chaos for control, or the other way round."""
        thresholds = self.thresholds
        # The same classification the control metric uses, so the sample
        # counted here is the sample the metric was computed over.
        classified = sum(
            1
            for e in recent
            if e.side is side and (is_controlled(e) or is_chaotic(e))
        )
        if classified < thresholds.control_min_events:
            return None
        now = compute_metric("control_pct", recent, side)
        before = compute_metric("control_pct", baseline, side)
        delta = now - before
        if abs(delta) < thresholds.control_shift:
            return None
        headline = (
            f"{self.team(side)} take control of the ball"
            if delta > 0
            else f"{self.team(side)} lose control of the ball"
        )
        return _Draft(
            rule="control",
            kind=InsightKind.TACTICAL,
            clock=clock,
            side=side,
            headline=headline,
            metrics=(
                ("control_pct", now, side),
                (
                    "passes_completed",
                    compute_metric("passes_completed", recent, side),
                    side,
                ),
            ),
            evidence=tuple(recent),
            baseline_metric=thresholds.baseline_metrics["control"],
            baseline_value=before,
            baseline_side=side,
            baseline_delta=delta,
            baseline_events=tuple(baseline),
            confidence=self._confidence(
                delta, thresholds.control_shift, classified, 12
            ),
            tags=("control", "tactical"),
        )

    def _rule_tempo(
        self,
        clock: int,
        recent: list[MatchEvent],
        side: TeamSide,
    ) -> _Draft | None:
        """The side keeps winning the ball back."""
        thresholds = self.thresholds
        breaks = compute_metric("tempo_breaks", recent, side)
        if breaks < thresholds.tempo_min_breaks:
            return None
        return _Draft(
            rule="tempo",
            kind=InsightKind.TACTICAL,
            clock=clock,
            side=side,
            headline=f"{self.team(side)} keep winning the ball back",
            metrics=(
                ("tempo_breaks", breaks, side),
                (
                    "possession_pct",
                    compute_metric("possession_pct", recent, side),
                    side,
                ),
            ),
            evidence=tuple(recent),
            confidence=self._flat_confidence(len(recent), 40),
            tags=("tempo", "tactical", "press"),
        )

    def _rule_press(
        self,
        clock: int,
        recent: list[MatchEvent],
        side: TeamSide,
    ) -> _Draft | None:
        """The side plays through a press rather than around it."""
        thresholds = self.thresholds
        run = compute_metric("longest_run", recent, side)
        if run < thresholds.press_min_run:
            return None
        completed = compute_metric("passes_completed", recent, side)
        if completed < thresholds.press_min_passes:
            return None
        control = compute_metric("control_pct", recent, side)
        if control < thresholds.press_min_control:
            return None
        return _Draft(
            rule="press",
            kind=InsightKind.PRESS_BREAK,
            clock=clock,
            side=side,
            headline=f"{self.team(side)} play through the press",
            metrics=(
                ("longest_run", run, side),
                ("passes_completed", completed, side),
                ("control_pct", control, side),
            ),
            evidence=tuple(recent),
            confidence=self._flat_confidence(int(run), thresholds.press_min_run),
            tags=("press", "break"),
        )

    def _rule_chance(
        self,
        clock: int,
        recent: list[MatchEvent],
        side: TeamSide,
    ) -> _Draft | None:
        """The side has created a chance worth the name."""
        thresholds = self.thresholds
        shots = compute_metric("shots", recent, side)
        if shots < thresholds.chance_min_shots:
            return None
        xg = compute_metric("xg", recent, side)
        if xg < thresholds.chance_min_xg:
            return None
        return _Draft(
            rule="chance",
            kind=InsightKind.CHANCE_CREATED,
            clock=clock,
            side=side,
            headline=f"{self.team(side)} create a clear opening",
            metrics=(("shots", shots, side), ("xg", xg, side)),
            evidence=tuple(recent),
            confidence=self._flat_confidence(int(shots * 10 + xg * 100), 40),
            tags=("chance", "attack"),
        )

    def _rule_threat(
        self,
        clock: int,
        recent: list[MatchEvent],
        side: TeamSide,
    ) -> _Draft | None:
        """The side has struck the ball at serious speed."""
        thresholds = self.thresholds
        speed = compute_metric("max_speed_mps", recent, side)
        if speed < thresholds.threat_speed_mps:
            return None
        return _Draft(
            rule="threat",
            kind=InsightKind.THREAT,
            clock=clock,
            side=side,
            headline=f"{self.team(side)} strike at serious speed",
            metrics=(
                ("max_speed_mps", speed, side),
                ("shots", compute_metric("shots", recent, side), side),
            ),
            evidence=tuple(recent),
            confidence=self._flat_confidence(len(recent), 30),
            tags=("threat", "speed"),
        )

    def _rule_discipline(
        self,
        clock: int,
        recent: list[MatchEvent],
        side: TeamSide,
    ) -> _Draft | None:
        """The side's discipline has slipped."""
        thresholds = self.thresholds
        cards = compute_metric("cards", recent, side)
        fouls = compute_metric("fouls", recent, side)
        if cards >= 1:
            headline = f"{self.team(side)} pick up a card"
            tags = ("discipline", "card")
        elif fouls >= thresholds.discipline_min_fouls:
            headline = f"{self.team(side)} are over-committing in the challenge"
            tags = ("discipline", "fouls")
        else:
            return None
        return _Draft(
            rule="discipline",
            kind=InsightKind.DISCIPLINE,
            clock=clock,
            side=side,
            headline=headline,
            metrics=(("cards", cards, side), ("fouls", fouls, side)),
            evidence=tuple(recent),
            confidence=self._flat_confidence(len(recent), 20),
            tags=tags,
        )

    # -- event-driven rules ------------------------------------------------

    def _set_piece_drafts(self, ordered: list[MatchEvent]) -> list[_Draft]:
        """A set piece that is worked into a shot."""
        thresholds = self.thresholds
        drafts: list[_Draft] = []
        for index, event in enumerate(ordered):
            if event.type not in {EventType.CORNER, EventType.FREE_KICK}:
                continue
            if event.side is None:
                continue
            limit = event.match_clock_ms + thresholds.set_piece_window_ms
            for offset in range(index + 1, len(ordered)):
                follower = ordered[offset]
                if follower.match_clock_ms > limit:
                    break
                if (
                    follower.type is not EventType.SHOT
                    or follower.side is not event.side
                ):
                    continue
                evidence = tuple(ordered[index : offset + 1])
                shot_distance = compute_metric("shot_distance_m", evidence, event.side)
                drafts.append(
                    _Draft(
                        rule="set_piece",
                        kind=InsightKind.SET_PIECE,
                        clock=follower.match_clock_ms,
                        side=event.side,
                        headline=(
                            f"{self.team(event.side)} work the "
                            f"{self._set_piece_name(event)} into a shot "
                            f"from {shot_distance:.0f} metres"
                        ),
                        metrics=(
                            (
                                "shots",
                                compute_metric("shots", evidence, event.side),
                                event.side,
                            ),
                            (
                                "xg",
                                compute_metric("xg", evidence, event.side),
                                event.side,
                            ),
                            (
                                "shot_distance_m",
                                shot_distance,
                                event.side,
                            ),
                            (
                                "passes_completed",
                                compute_metric(
                                    "passes_completed", evidence, event.side
                                ),
                                event.side,
                            ),
                        ),
                        evidence=evidence,
                        confidence=self._flat_confidence(len(evidence), 6),
                        tags=("set_piece",),
                    )
                )
                break
        return drafts

    def _dangerous_set_piece_drafts(self, ordered: list[MatchEvent]) -> list[_Draft]:
        """A free kick won inside shooting range of the goal."""
        thresholds = self.thresholds
        drafts: list[_Draft] = []
        for index, event in enumerate(ordered):
            if event.type is not EventType.FREE_KICK or event.side is None:
                continue
            # The foul is the event that won it, and the free kick is taken
            # from the same spot, so the two together describe the position.
            cause = next(
                (
                    earlier
                    for earlier in reversed(ordered[:index])
                    if earlier.type is EventType.FOUL
                    and earlier.side is not event.side
                ),
                None,
            )
            if cause is None:
                continue
            evidence = (cause, event)
            distance = compute_metric("distance_to_goal_m", evidence, event.side)
            if distance > thresholds.set_piece_range_m:
                continue
            drafts.append(
                _Draft(
                    rule="dangerous_set_piece",
                    kind=InsightKind.SET_PIECE,
                    clock=event.match_clock_ms,
                    side=event.side,
                    headline=(
                        f"{self.team(event.side)} win a free kick "
                        f"{distance:.0f} metres from goal"
                    ),
                    metrics=(
                        ("distance_to_goal_m", distance, event.side),
                        (
                            "free_kicks",
                            compute_metric("free_kicks", evidence, event.side),
                            event.side,
                        ),
                        # The foul belongs to the team that conceded it, so
                        # the metric is reported against that side.
                        (
                            "fouls",
                            compute_metric("fouls", evidence, cause.side),
                            cause.side,
                        ),
                    ),
                    evidence=evidence,
                    confidence=self._flat_confidence(len(evidence), 12),
                    tags=("set_piece", "dangerous"),
                )
            )
        return drafts

    @staticmethod
    def _set_piece_name(event: MatchEvent) -> str:
        """What a set piece is called, for the headline."""
        return "corner" if event.type is EventType.CORNER else "free kick"

    def _milestone_drafts(self, ordered: list[MatchEvent]) -> list[_Draft]:
        """A player crosses a distance milestone, or scores a landmark goal."""
        thresholds = self.thresholds
        drafts: list[_Draft] = []

        for timeline in self._player_timelines(ordered).values():
            if timeline.side is None:
                continue
            crossed: set[float] = set()
            for index, event in enumerate(timeline.events):
                # The log carries the player's running total, so the
                # milestone is a real distance covered and not a sum of the
                # actions they happened to be involved in. The guard keeps
                # this equal to what the metric recomputes from the same
                # events, which filters by side.
                if event.side is timeline.side:
                    timeline.distance_m = max(
                        timeline.distance_m,
                        meta_float(event.metadata, "player_total_distance_m"),
                    )
                timeline.goals += 1 if event.type is EventType.GOAL else 0

                for threshold in thresholds.milestone_distances_m:
                    if threshold in crossed:
                        continue
                    if timeline.distance_m < threshold:
                        continue
                    crossed.add(threshold)
                    prefix = tuple(timeline.events[: index + 1])
                    drafts.append(
                        _Draft(
                            rule="milestone",
                            kind=InsightKind.MILESTONE,
                            clock=event.match_clock_ms,
                            side=timeline.side,
                            headline=(
                                f"{timeline.name} has covered "
                                f"{timeline.distance_m:,.0f} metres of ground"
                            ),
                            metrics=(
                                (
                                    "player_total_distance_m",
                                    compute_metric(
                                        "player_total_distance_m", prefix, timeline.side
                                    ),
                                    timeline.side,
                                ),
                            ),
                            evidence=prefix,
                            confidence=self._flat_confidence(len(prefix), 30),
                            tags=("milestone", "distance", timeline.player_id),
                        )
                    )

                if timeline.goals == thresholds.milestone_goals:
                    prefix = tuple(timeline.events[: index + 1])
                    drafts.append(
                        _Draft(
                            rule="milestone",
                            kind=InsightKind.MILESTONE,
                            clock=event.match_clock_ms,
                            side=timeline.side,
                            headline=f"{timeline.name} scores a landmark fifth goal",
                            metrics=(
                                (
                                    "goals",
                                    compute_metric("goals", prefix, timeline.side),
                                    timeline.side,
                                ),
                            ),
                            evidence=prefix,
                            confidence=self._flat_confidence(len(prefix), 30),
                            tags=("milestone", "goal", timeline.player_id),
                        )
                    )
        return drafts

    def _player_timelines(
        self, ordered: list[MatchEvent]
    ) -> dict[str, _PlayerTimeline]:
        """Group the log by player, keeping the order the events happened in."""
        timelines: dict[str, _PlayerTimeline] = {}
        for event in ordered:
            player_id = event.player_id
            if player_id is None:
                continue
            timeline = timelines.get(player_id)
            if timeline is None:
                timeline = _PlayerTimeline(
                    player_id=player_id,
                    name=event.player_name or player_id,
                    side=event.side,
                )
                timelines[player_id] = timeline
            timeline.events.append(event)
        return timelines

    # -- output ------------------------------------------------------------

    def _allowed(
        self,
        draft: _Draft,
        last_fired: dict[tuple[str, TeamSide], int],
    ) -> bool:
        """Apply the cooldown, so one busy spell cannot flood the feed."""
        key = (draft.rule, draft.side)
        previous = last_fired.get(key)
        if (
            previous is not None
            and draft.clock - previous < self.thresholds.cooldown_ms
        ):
            return False
        last_fired[key] = draft.clock
        return True

    def _candidate(self, index: int, draft: _Draft) -> InsightCandidate:
        """Turn a draft into a candidate with its citations attached."""
        metrics = [
            MetricValue(
                name=name,
                value=value,
                unit=metric_spec(name).unit,
                label=metric_spec(name).label,
                side=side,
            )
            for name, value, side in draft.metrics
        ]

        baseline: BaselineComparison | None = None
        if (
            draft.baseline_metric is not None
            and draft.baseline_value is not None
            and draft.baseline_delta is not None
        ):
            baseline = BaselineComparison(
                metric_name=draft.baseline_metric,
                value=draft.baseline_value,
                unit=metric_spec(draft.baseline_metric).unit,
                side=draft.baseline_side,
                delta=draft.baseline_delta,
                window_ms=self.thresholds.baseline_ms,
                cited_event_ids=[event.event_id for event in draft.baseline_events],
            )

        return InsightCandidate(
            candidate_id=f"cand-{index:04d}",
            kind=draft.kind,
            match_clock_ms=draft.clock,
            side=draft.side,
            headline=draft.headline,
            metrics=metrics,
            baseline=baseline,
            cited_event_ids=[event.event_id for event in draft.evidence],
            confidence=draft.confidence,
            tags=list(draft.tags),
        )

    # -- confidence --------------------------------------------------------

    def _confidence(
        self,
        deviation: float,
        threshold: float,
        sample: float,
        target: float,
    ) -> float:
        """Confidence from the size of the deviation and the size of the sample.

        A deviation just over the threshold on a thin sample is worth less
        than the same deviation on a rich one, and the difference has to be
        visible in the number the UI shows.
        """
        ratio = abs(deviation) / threshold if threshold else 1.0
        excess = min(1.0, max(0.0, ratio - 1.0))
        sample_factor = min(1.0, sample / target) if target else 1.0
        score = (0.55 + 0.35 * excess) * (0.7 + 0.3 * sample_factor)
        return round(min(0.97, score), 3)

    def _flat_confidence(self, sample: float, target: float) -> float:
        """Confidence for a rule that fires on an event rather than a size."""
        sample_factor = min(1.0, sample / target) if target else 1.0
        return round(min(0.9, 0.6 + 0.25 * sample_factor), 3)
