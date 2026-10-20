"""The metric registry -- one definition per number the system shows.

Every number that reaches a judge is defined exactly once, here, together
with the function that computes it. The deterministic analyst reads this
registry to produce its numbers; the grounding step reads the *same* registry
to recompute them from the cited event ids. Because both sides call the same
function over the same events, a claim that fails to reproduce is a real
disagreement rather than a difference of formula.

Two rules keep the grounding honest.

**A metric is a pure function of the event set it is given.** No metric reads
global state, the match clock, or anything outside the events handed to it.
The analyst therefore cites exactly the events it computed on, and the
grounding step recomputes over exactly the cited set.

**A metric is either side-filtered or side-attributed.** A *filtered* metric
restricts the set to one side before computing -- the count of a side's
completed passes, for instance. An *attributed* metric computes over the
whole set and then reports the part belonging to the requested side -- the
share of possession, the momentum balance. Both are exact, and both name the
side they describe, so a claim about the away side is verifiable with the
same code path as a claim about the home side.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from itertools import pairwise
from math import hypot

from touchline.models.events import (
    PITCH_LENGTH_M,
    PITCH_WIDTH_M,
    EventType,
    MatchEvent,
    TeamSide,
    meta_float,
    meta_int,
    meta_number,
)

__all__ = [
    "METRICS",
    "POSSESSION_EVENT_TYPES",
    "MetricScope",
    "MetricSpec",
    "compute_metric",
    "is_chaotic",
    "is_controlled",
    "metric_spec",
    "other_side",
]

#: Event types that mean "this side had the ball". The possession share and
#: the possession runs are derived from these.
POSSESSION_EVENT_TYPES: frozenset[EventType] = frozenset(
    {
        EventType.PASS,
        EventType.DRIBBLE,
        EventType.SHOT,
        EventType.GOAL,
        EventType.CORNER,
        EventType.FREE_KICK,
        EventType.RECOVERY,
    }
)


class MetricScope(str, Enum):
    """How a metric relates to the side it is attributed to."""

    FILTER = "FILTER"
    """Restrict the event set to the side first, then compute.

    With no side given, the whole set is used.
    """

    ATTRIBUTE = "ATTRIBUTE"
    """Compute over the whole set; the side selects which part of the result
    the metric reports."""


@dataclass(frozen=True)
class MetricSpec:
    """One metric: what it is called, what it measures, and how it is derived."""

    name: str
    unit: str
    label: str
    scope: MetricScope
    compute: Callable[[Sequence[MatchEvent], TeamSide | None], float]


def other_side(side: TeamSide) -> TeamSide:
    """The opposite side."""
    return TeamSide.AWAY if side is TeamSide.HOME else TeamSide.HOME


# --- helpers ---------------------------------------------------------------


def _count(
    events: Sequence[MatchEvent], predicate: Callable[[MatchEvent], bool]
) -> float:
    return float(sum(1 for event in events if predicate(event)))


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _shot_xg(event: MatchEvent) -> float:
    """The expected-goal value carried by a shot."""
    xg = meta_number(event.metadata, "xg")
    return xg if xg is not None else float(event.value or 0.0)


def _possession_groups(
    events: Sequence[MatchEvent],
) -> list[tuple[TeamSide, list[MatchEvent]]]:
    """Split an event set into possessions.

    The simulator tags every action with the possession it belongs to, so a
    possession is read straight off the log rather than inferred from the
    ball's position. Within a possession the *owner* is the side that had the
    ball: the side with the most possession events, or the side of the first
    possession event when the counts tie. The events a side contributed to a
    possession it did not own -- the defender's tackle, the keeper's save --
    are not part of that side's run.

    The possessions are ordered by their possession number rather than by
    the order the events arrive in, so the result does not depend on the
    order the caller listed the events in. The grounding step rebuilds a
    claim's citations in the claim's own order, and a metric that changed
    with the order would veto a claim for the wrong reason.

    Returns:
        One ``(owner, own_events)`` pair per possession, in log order.
    """
    grouped: dict[int, list[MatchEvent]] = {}
    for event in events:
        index = meta_int(event.metadata, "possession")
        if index is None:
            continue
        grouped.setdefault(index, []).append(event)

    groups: list[tuple[TeamSide, list[MatchEvent]]] = []
    for index in sorted(grouped):
        members = sorted(grouped[index], key=lambda e: (e.match_clock_ms, e.event_id))
        counts: Counter[TeamSide] = Counter()
        for member in members:
            if member.type in POSSESSION_EVENT_TYPES and member.side is not None:
                counts[member.side] += 1
        if not counts:
            continue
        # On a tie the side that appears first in the log owns it.
        best = max(counts.values())
        owner = min(
            (side for side, count in counts.items() if count == best),
            key=lambda side: next(
                (member.match_clock_ms, member.event_id)
                for member in members
                if member.side is side and member.type in POSSESSION_EVENT_TYPES
            ),
        )
        own = [
            member
            for member in members
            if member.side is owner and member.type in POSSESSION_EVENT_TYPES
        ]
        groups.append((owner, own))
    return groups


# --- metric implementations ------------------------------------------------


def _passes(events: Sequence[MatchEvent], side: TeamSide | None) -> float:
    return _count(events, lambda e: e.type is EventType.PASS)


def _passes_completed(events: Sequence[MatchEvent], side: TeamSide | None) -> float:
    return _count(
        events,
        lambda e: e.type is EventType.PASS and e.outcome == "completed",
    )


def _pass_accuracy(events: Sequence[MatchEvent], side: TeamSide | None) -> float:
    """Completed passes as a percentage of all passes attempted."""
    attempts = [e for e in events if e.type is EventType.PASS]
    if not attempts:
        return 0.0
    completed = sum(1 for e in attempts if e.outcome == "completed")
    return 100.0 * completed / len(attempts)


def _pass_difficulty(events: Sequence[MatchEvent], side: TeamSide | None) -> float:
    """Mean difficulty of the passes attempted, 0 (easy) to 1 (hard)."""
    values = [
        float(e.difficulty)
        for e in events
        if e.type is EventType.PASS and e.difficulty is not None
    ]
    return _mean(values)


def _pass_distance_m(events: Sequence[MatchEvent], side: TeamSide | None) -> float:
    """Mean length of the completed passes, in metres."""
    values = [
        float(e.distance_m)
        for e in events
        if e.type is EventType.PASS
        and e.outcome == "completed"
        and e.distance_m is not None
    ]
    return _mean(values)


def _shots(events: Sequence[MatchEvent], side: TeamSide | None) -> float:
    return _count(events, lambda e: e.type is EventType.SHOT)


def _shots_on_target(events: Sequence[MatchEvent], side: TeamSide | None) -> float:
    return _count(
        events,
        lambda e: e.type is EventType.SHOT
        and e.outcome in {"goal", "saved", "on_target"},
    )


def _xg(events: Sequence[MatchEvent], side: TeamSide | None) -> float:
    """Total expected goals from the shots in the set."""
    return sum(_shot_xg(e) for e in events if e.type is EventType.SHOT)


def _xg_conceded(events: Sequence[MatchEvent], side: TeamSide | None) -> float:
    """Expected goals the side's keeper faced."""
    total = 0.0
    for event in events:
        if event.type is not EventType.SAVE:
            continue
        conceded = meta_number(event.metadata, "xg_conceded")
        if conceded is not None:
            total += conceded
        else:
            total += float(event.value or 0.0)
    return total


def _goals(events: Sequence[MatchEvent], side: TeamSide | None) -> float:
    return _count(events, lambda e: e.type is EventType.GOAL)


def _possession_pct(events: Sequence[MatchEvent], side: TeamSide | None) -> float:
    """Share of the possession events in the set that belong to the side."""
    target = side or TeamSide.HOME
    relevant = [e for e in events if e.type in POSSESSION_EVENT_TYPES]
    if not relevant:
        return 0.0
    owned = sum(1 for e in relevant if e.side is target)
    return 100.0 * owned / len(relevant)


def _possession_runs(events: Sequence[MatchEvent], side: TeamSide | None) -> float:
    """How many separate spells of possession the side had in the set."""
    target = side or TeamSide.HOME
    return float(sum(1 for owner, _ in _possession_groups(events) if owner is target))


def _longest_run(events: Sequence[MatchEvent], side: TeamSide | None) -> float:
    """Length of the side's longest uninterrupted spell, in possession events."""
    target = side or TeamSide.HOME
    lengths = [len(own) for owner, own in _possession_groups(events) if owner is target]
    return float(max(lengths)) if lengths else 0.0


def _tempo_breaks(events: Sequence[MatchEvent], side: TeamSide | None) -> float:
    """How many times the side won the ball back inside the set.

    A break is a possession the side took from the opponent, so the first
    possession in the window does not count -- there is nothing to win it
    from.
    """
    target = side or TeamSide.HOME
    groups = _possession_groups(events)
    breaks = 0
    for previous, current in pairwise(groups):
        if current[0] is target and previous[0] is not target:
            breaks += 1
    return float(breaks)


def is_controlled(event: MatchEvent) -> bool:
    """Did this side keep the ball under control in the action?

    The control share is a ratio over the actions that can be classified at
    all, so this predicate defines the numerator. It is public because the
    analyst needs the same classification to decide whether a window holds
    enough evidence to compare.
    """
    if event.type is EventType.PASS:
        return event.outcome == "completed"
    if event.type is EventType.DRIBBLE:
        return event.outcome == "completed"
    if event.type in {
        EventType.RECOVERY,
        EventType.SAVE,
        EventType.CLEARANCE,
    }:
        return True
    if event.type in {EventType.TACKLE, EventType.INTERCEPTION}:
        return event.outcome == "won"
    return False


def is_chaotic(event: MatchEvent) -> bool:
    """Did the ball get away from the side in this action?"""
    if event.type is EventType.PASS:
        return event.outcome in {"intercepted", "blocked", "out"}
    if event.type is EventType.DRIBBLE:
        return event.outcome == "lost"
    return event.type in {
        EventType.FOUL,
        EventType.OFFSIDE,
        EventType.BALL_OUT,
        EventType.CARD,
    }


def _control_pct(events: Sequence[MatchEvent], side: TeamSide | None) -> float:
    """Share of the side's actions that were controlled rather than chaotic."""
    controlled = sum(1 for e in events if is_controlled(e))
    chaotic = sum(1 for e in events if is_chaotic(e))
    total = controlled + chaotic
    if total == 0:
        return 0.0
    return 100.0 * controlled / total


def _pressure(events: Sequence[MatchEvent], side: TeamSide | None) -> float:
    """Mean pressure the side was under when it passed, 0 to 1."""
    values: list[float] = []
    for event in events:
        if event.type is not EventType.PASS:
            continue
        pressure = meta_number(event.metadata, "pressure")
        if pressure is not None:
            values.append(pressure)
    return _mean(values)


def _progress(events: Sequence[MatchEvent], side: TeamSide | None) -> float:
    """Mean forward progress of the side's passes and carries, in metres.

    Negative when the side spent the window going backwards.
    """
    values: list[float] = []
    for event in events:
        if event.type not in {EventType.PASS, EventType.DRIBBLE}:
            continue
        if event.end_x is None:
            continue
        values.append((event.end_x - event.x) * PITCH_LENGTH_M / 100.0)
    return _mean(values)


def _attack_weight(events: Sequence[MatchEvent], side: TeamSide) -> float:
    """How much the side was creating."""
    total = 0.0
    for event in events:
        if event.side is not side:
            continue
        if event.type is EventType.SHOT:
            total += 10.0 * _shot_xg(event)
        elif event.type is EventType.GOAL:
            total += 12.0
        elif event.type is EventType.CORNER:
            total += 2.0
        elif event.type is EventType.FREE_KICK:
            total += 1.0 if event.x >= 60.0 else 0.3
        elif event.type in {EventType.PASS, EventType.DRIBBLE}:
            advance = (event.end_x - event.x) if event.end_x is not None else 0.0
            total += 0.03 * max(0.0, advance)
    return total


def _defence_weight(events: Sequence[MatchEvent], side: TeamSide) -> float:
    """How much the side won back or kept out."""
    total = 0.0
    for event in events:
        if event.side is not side:
            continue
        if event.type is EventType.SAVE:
            total += 3.0
        elif event.type is EventType.INTERCEPTION:
            total += 1.5
        elif event.type is EventType.TACKLE and event.outcome == "won":
            total += 1.0
        elif event.type is EventType.RECOVERY:
            total += 1.5
        elif event.type is EventType.CLEARANCE:
            total += 1.0
    return total


def _momentum_index(events: Sequence[MatchEvent], side: TeamSide | None) -> float:
    """The balance of the match, from -1 (all against the side) to +1.

    Built from what each side created and what each side won back: shots and
    goals and forward progress on one side of the ledger, saves, interceptions
    and tackles on the other.
    """
    target = side or TeamSide.HOME
    opponent = other_side(target)
    mine = _attack_weight(events, target) + _defence_weight(events, target)
    theirs = _attack_weight(events, opponent) + _defence_weight(events, opponent)
    total = mine + theirs
    if total <= 0.0:
        return 0.0
    return (mine - theirs) / total


def _tackles(events: Sequence[MatchEvent], side: TeamSide | None) -> float:
    return _count(events, lambda e: e.type is EventType.TACKLE)


def _interceptions(events: Sequence[MatchEvent], side: TeamSide | None) -> float:
    return _count(events, lambda e: e.type is EventType.INTERCEPTION)


def _saves(events: Sequence[MatchEvent], side: TeamSide | None) -> float:
    return _count(events, lambda e: e.type is EventType.SAVE)


def _corners(events: Sequence[MatchEvent], side: TeamSide | None) -> float:
    return _count(events, lambda e: e.type is EventType.CORNER)


def _free_kicks(events: Sequence[MatchEvent], side: TeamSide | None) -> float:
    return _count(events, lambda e: e.type is EventType.FREE_KICK)


def _fouls(events: Sequence[MatchEvent], side: TeamSide | None) -> float:
    return _count(events, lambda e: e.type is EventType.FOUL)


def _cards(events: Sequence[MatchEvent], side: TeamSide | None) -> float:
    return _count(events, lambda e: e.type is EventType.CARD)


def _total_distance_m(events: Sequence[MatchEvent], side: TeamSide | None) -> float:
    """Total distance the ball travelled in the set, in metres."""
    return float(sum(e.distance_m or 0.0 for e in events))


def _max_speed_mps(events: Sequence[MatchEvent], side: TeamSide | None) -> float:
    """The fastest ball in the set, in metres per second."""
    speeds = [e.speed_mps for e in events if e.speed_mps is not None]
    return max(speeds) if speeds else 0.0


def _player_distance_m(events: Sequence[MatchEvent], side: TeamSide | None) -> float:
    """Ground covered by the players in the set, in metres.

    The simulator runs a player model alongside the match and records on
    each event how far the player who performed it moved to get there. This
    metric reads that number straight off the log and sums it.

    It is a sum over *the acting players*, so it is the ground the side
    covered while it was on the ball -- not the total for the eleven on the
    pitch, which the log has no way of describing.
    """
    return float(
        sum(meta_float(event.metadata, "player_distance_m") for event in events)
    )


def _player_total_distance_m(
    events: Sequence[MatchEvent], side: TeamSide | None
) -> float:
    """How far the furthest player in the set has run in total, in metres.

    Each event carries the running total of the player who made it, so the
    furthest of those totals is how far the leading runner in the set has
    covered. It is the number a broadcast uses for "has now covered eight
    kilometres".
    """
    totals = [
        meta_float(event.metadata, "player_total_distance_m") for event in events
    ]
    return max(totals) if totals else 0.0


def _distance_to_goal_m(
    events: Sequence[MatchEvent], side: TeamSide | None
) -> float:
    """How far the ball was from the goal being attacked, in metres.

    Each event's position is in the frame of the team that owns it, so the
    goal it is measured against is that team's attacking goal. The metric
    averages the distance over the set.
    """
    total = 0.0
    count = 0
    for event in events:
        if event.side is None:
            continue
        total += _to_attacking_goal_m(event)
        count += 1
    return total / count if count else 0.0


def _shot_distance_m(events: Sequence[MatchEvent], side: TeamSide | None) -> float:
    """How far the shots in the set were from goal, in metres, on average."""
    distances = [
        event.distance_m
        for event in events
        if event.type is EventType.SHOT and event.distance_m is not None
    ]
    return float(sum(distances) / len(distances)) if distances else 0.0


def _to_attacking_goal_m(event: MatchEvent) -> float:
    """The distance from one event to the goal its side is attacking."""
    goal_x = 100.0 if event.side is TeamSide.HOME else 0.0
    dx = (event.x - goal_x) * PITCH_LENGTH_M / 100.0
    dy = (event.y - 50.0) * PITCH_WIDTH_M / 100.0
    return hypot(dx, dy)


# --- the registry ----------------------------------------------------------


def _spec(
    name: str,
    unit: str,
    label: str,
    scope: MetricScope,
    compute: Callable[[Sequence[MatchEvent], TeamSide | None], float],
) -> MetricSpec:
    return MetricSpec(
        name=name, unit=unit, label=label, scope=scope, compute=compute
    )


#: Every metric the system knows, keyed by its machine name.
METRICS: dict[str, MetricSpec] = {
    spec.name: spec
    for spec in (
        _spec("passes", "", "passes", MetricScope.FILTER, _passes),
        _spec(
            "passes_completed",
            "",
            "completed passes",
            MetricScope.FILTER,
            _passes_completed,
        ),
        _spec(
            "pass_accuracy", "%", "pass accuracy", MetricScope.FILTER, _pass_accuracy
        ),
        _spec(
            "pass_difficulty",
            "",
            "pass difficulty",
            MetricScope.FILTER,
            _pass_difficulty,
        ),
        _spec(
            "pass_distance_m",
            "m",
            "average pass distance",
            MetricScope.FILTER,
            _pass_distance_m,
        ),
        _spec("shots", "", "shots", MetricScope.FILTER, _shots),
        _spec(
            "shots_on_target",
            "",
            "shots on target",
            MetricScope.FILTER,
            _shots_on_target,
        ),
        _spec("xg", "", "expected goals", MetricScope.FILTER, _xg),
        _spec(
            "xg_conceded",
            "",
            "expected goals conceded",
            MetricScope.FILTER,
            _xg_conceded,
        ),
        _spec("goals", "", "goals", MetricScope.FILTER, _goals),
        _spec(
            "possession_pct", "%", "possession", MetricScope.ATTRIBUTE, _possession_pct
        ),
        _spec(
            "possession_runs",
            "",
            "spells of possession",
            MetricScope.ATTRIBUTE,
            _possession_runs,
        ),
        _spec(
            "longest_run",
            "",
            "longest spell of possession",
            MetricScope.ATTRIBUTE,
            _longest_run,
        ),
        _spec(
            "tempo_breaks", "", "turnovers won", MetricScope.ATTRIBUTE, _tempo_breaks
        ),
        _spec("control_pct", "%", "control", MetricScope.FILTER, _control_pct),
        _spec("pressure", "", "pressure", MetricScope.FILTER, _pressure),
        _spec("progress", "m", "field progression", MetricScope.FILTER, _progress),
        _spec(
            "momentum_index",
            "",
            "momentum index",
            MetricScope.ATTRIBUTE,
            _momentum_index,
        ),
        _spec("tackles", "", "tackles", MetricScope.FILTER, _tackles),
        _spec("interceptions", "", "interceptions", MetricScope.FILTER, _interceptions),
        _spec("saves", "", "saves", MetricScope.FILTER, _saves),
        _spec("corners", "", "corners", MetricScope.FILTER, _corners),
        _spec("free_kicks", "", "free kicks", MetricScope.FILTER, _free_kicks),
        _spec("fouls", "", "fouls", MetricScope.FILTER, _fouls),
        _spec("cards", "", "cards", MetricScope.FILTER, _cards),
        _spec(
            "total_distance_m",
            "m",
            "ball distance",
            MetricScope.FILTER,
            _total_distance_m,
        ),
        _spec("max_speed_mps", "m/s", "top speed", MetricScope.FILTER, _max_speed_mps),
        _spec(
            "player_distance_m",
            "m",
            "player ground covered",
            MetricScope.FILTER,
            _player_distance_m,
        ),
        _spec(
            "player_total_distance_m",
            "m",
            "player ground covered in total",
            MetricScope.FILTER,
            _player_total_distance_m,
        ),
        _spec(
            "distance_to_goal_m",
            "m",
            "distance to goal",
            MetricScope.FILTER,
            _distance_to_goal_m,
        ),
        _spec(
            "shot_distance_m",
            "m",
            "shot distance",
            MetricScope.FILTER,
            _shot_distance_m,
        ),
    )
}


def metric_spec(name: str) -> MetricSpec:
    """Look one metric up by name.

    Raises:
        ValueError: If the name is not a registered metric.
    """
    spec = METRICS.get(name)
    if spec is None:
        raise ValueError(
            f"unsupported metric {name!r}; expected one of {sorted(METRICS)}"
        )
    return spec


def compute_metric(
    name: str,
    events: Sequence[MatchEvent],
    side: TeamSide | None = None,
) -> float:
    """Compute one metric over an event set.

    Args:
        name: A registered metric name.
        events: The events the metric is computed over, in log order.
        side: The side the metric is about. A filtered metric restricts the
            set to that side; an attributed metric reports that side's share
            of the whole set. ``None`` means "the whole set" for a filtered
            metric and "the home side" for an attributed one.

    Returns:
        The metric value.

    Raises:
        ValueError: If the name is not a registered metric.
    """
    spec = metric_spec(name)
    if spec.scope is MetricScope.FILTER and side is not None:
        events = [event for event in events if event.side is side]
    return spec.compute(events, side)
