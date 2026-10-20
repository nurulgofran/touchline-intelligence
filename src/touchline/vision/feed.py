"""The ground truth: what the camera saw, frame by frame.

The tracker is only worth anything if it is measured against something. So
the feed is built **backwards**: the truth comes first, the video is
rendered from it, and the tracker is then run over the video and scored
against the truth it was rendered from.

The truth has two halves, and they come from two different places.

**The players** come from the movement model that ran alongside the match.
The event log records where the *ball* was; the model knows where the
players were, and the simulator can be asked to report it. The model
samples once a second, and the truth between two samples is the straight
line between them.

**The ball** comes from the event log itself. Every event says where the
ball was, and the ones that moved it -- a pass, a carry, a shot -- say
where it went and how fast it was travelling. So the ball's truth is the
log's own geometry, read out as a path: a flight at the log's speed, then
the ball resting until the next event.

Both halves are on the **event clock**, so the truth and the log are one
timeline and a reader can put a truth frame next to the events it sits
between.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from math import hypot

from touchline.models.events import (
    PITCH_LENGTH_M,
    PITCH_WIDTH_M,
    EventType,
    MatchEvent,
    TeamSide,
)
from touchline.sim.generator import PlayerSample, simulate_with_feed

logger = logging.getLogger(__name__)

__all__ = [
    "BallFlight",
    "TruthClip",
    "TruthFrame",
    "build_truth_clip",
    "pick_window",
]

#: How long a clip is, in match time.
DEFAULT_DURATION_MS = 20_000

#: How many frames a second the clip is shot at.
DEFAULT_FPS = 25

#: The speed a ball is assumed to travel at when the log does not say.
_DEFAULT_SPEED_MPS = 10.0

#: The event types that carry the ball from one spot to another.
_FLIGHT_EVENTS = frozenset(
    {EventType.PASS, EventType.DRIBBLE, EventType.SHOT, EventType.CORNER}
)


@dataclass(frozen=True)
class BallFlight:
    """One stretch of the ball's journey, from the log.

    Attributes:
        start_ms: When the ball left.
        end_ms: When it arrived.
        from_xy: Where it left, in metres.
        to_xy: Where it arrived, in metres.
        speed_mps: How fast it was travelling.
        distance_m: How far it went.
        kind: What it was -- a pass, a carry, a shot or a cross.
    """

    start_ms: int
    end_ms: int
    from_xy: tuple[float, float]
    to_xy: tuple[float, float]
    speed_mps: float
    distance_m: float
    kind: str


@dataclass(frozen=True)
class TruthFrame:
    """One frame of the truth.

    Attributes:
        index: Which frame this is, from zero.
        clock_ms: The match clock it belongs to.
        ball: Where the ball was, in metres.
        ball_speed_mps: How fast the ball was travelling.
        players: Where every player was, by player id, in metres.
    """

    index: int
    clock_ms: int
    ball: tuple[float, float]
    ball_speed_mps: float
    players: dict[str, tuple[float, float]]


@dataclass(frozen=True)
class TruthClip:
    """The whole truth: the frames, and what the log says happened in them.

    Attributes:
        frames: The frames, in order.
        fps: How many frames a second.
        start_ms: The match clock the clip starts at.
        end_ms: The match clock the clip ends at.
        flights: The ball's flights inside the clip, from the log.
        home_players: The home players' ids, in the order they appear.
        away_players: The away players' ids.
    """

    frames: list[TruthFrame] = field(default_factory=list)
    fps: int = DEFAULT_FPS
    start_ms: int = 0
    end_ms: int = 0
    flights: list[BallFlight] = field(default_factory=list)
    home_players: list[str] = field(default_factory=list)
    away_players: list[str] = field(default_factory=list)

    @property
    def duration_ms(self) -> int:
        """How much match time the clip covers."""
        return self.end_ms - self.start_ms


def _absolute(event: MatchEvent) -> tuple[float, float]:
    """An event's position, in metres on the pitch, whoever made it."""
    assert event.side is not None
    x_pct, y_pct = event.x, event.y
    if event.side is TeamSide.AWAY:
        x_pct, y_pct = 100.0 - x_pct, 100.0 - y_pct
    return (x_pct / 100.0 * PITCH_LENGTH_M, y_pct / 100.0 * PITCH_WIDTH_M)


def _absolute_end(event: MatchEvent) -> tuple[float, float] | None:
    """An event's destination, in metres on the pitch."""
    if event.end_x is None or event.end_y is None or event.side is None:
        return None
    x_pct, y_pct = event.end_x, event.end_y
    if event.side is TeamSide.AWAY:
        x_pct, y_pct = 100.0 - x_pct, 100.0 - y_pct
    return (x_pct / 100.0 * PITCH_LENGTH_M, y_pct / 100.0 * PITCH_WIDTH_M)


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    """How far apart two spots on the pitch are, in metres."""
    return hypot(a[0] - b[0], a[1] - b[1])


def _flight_target(event: MatchEvent) -> tuple[tuple[float, float], str] | None:
    """Where an event sent the ball, and what kind of journey it was.

    A shot is aimed at the goal the shooting side was attacking; the log
    does not say where on the goal line it was going, so the middle of the
    goal is used, which is where the goal event puts it.
    """
    if event.type is EventType.SHOT:
        assert event.side is not None
        x_m = PITCH_LENGTH_M if event.side is TeamSide.HOME else 0.0
        return (x_m, PITCH_WIDTH_M / 2.0), "shot"
    end = _absolute_end(event)
    if end is None:
        return None
    if event.type is EventType.PASS:
        return end, "pass"
    if event.type is EventType.DRIBBLE:
        return end, "carry"
    if event.type is EventType.CORNER:
        return end, "cross"
    return None


def _build_flights(
    events: list[MatchEvent], start_ms: int, end_ms: int
) -> tuple[list[BallFlight], list[BallFlight]]:
    """The ball's flights, read out of the log.

    The flight is timed by the log's own speed: a pass struck at twenty
    metres a second covers its distance in that time, and the ball rests
    where it stopped until the next event.

    Two lists come back. The **movements** are every flight, including the
    ones before the clip, and they are what the ball's path is drawn from:
    the ball has to be where the last flight left it, even if that flight
    happened before the camera started rolling. The **flights** are the
    ones inside the clip, and they are what the tracker's measurements are
    compared against.

    Returns:
        The flights inside the clip, and every movement.
    """
    movements: list[BallFlight] = []
    position: tuple[float, float] | None = None

    for index, event in enumerate(events):
        if event.side is None:
            continue
        here = _absolute(event)
        if position is None:
            position = here

        target = _flight_target(event)
        if target is None:
            position = here
            continue
        destination, kind = target

        speed = event.speed_mps or _DEFAULT_SPEED_MPS
        distance = _distance(position, destination)
        if distance < 0.05:
            position = destination
            continue
        flight_ms = round(distance / max(speed, 0.1) * 1000.0)

        # The flight has to fit before the next event, or the ball would
        # still be travelling when the log says something else happened.
        next_clock = (
            events[index + 1].match_clock_ms if index + 1 < len(events) else end_ms
        )
        available = max(0, next_clock - event.match_clock_ms)
        if available <= 0:
            position = destination
            continue
        flight_ms = min(flight_ms, available)

        movements.append(
            BallFlight(
                start_ms=event.match_clock_ms,
                end_ms=event.match_clock_ms + flight_ms,
                from_xy=position,
                to_xy=destination,
                speed_mps=speed,
                distance_m=distance,
                kind=kind,
            )
        )
        position = destination

    flights = [
        flight
        for flight in movements
        if flight.end_ms >= start_ms and flight.start_ms <= end_ms
    ]
    return flights, movements


def _ball_at(
    movements: list[BallFlight], events: list[MatchEvent], clock_ms: int
) -> tuple[tuple[float, float], float]:
    """Where the ball was at one moment, and how fast it was going.

    The movements are the ball's journeys; between them the ball is
    sitting where the last one left it. The events fill in where it was
    sitting before the first movement of the clip.
    """
    for flight in movements:
        if flight.start_ms <= clock_ms <= flight.end_ms:
            span = max(1, flight.end_ms - flight.start_ms)
            fraction = (clock_ms - flight.start_ms) / span
            x = flight.from_xy[0] + (flight.to_xy[0] - flight.from_xy[0]) * fraction
            y = flight.from_xy[1] + (flight.to_xy[1] - flight.from_xy[1]) * fraction
            return (x, y), flight.speed_mps

    # Not in a flight: the ball is resting where the last flight left it,
    # or where the log last put it.
    resting: tuple[float, float] | None = None
    for flight in movements:
        if flight.end_ms <= clock_ms:
            resting = flight.to_xy
    if resting is None:
        for event in events:
            if event.match_clock_ms <= clock_ms and event.side is not None:
                resting = _absolute_end(event) or _absolute(event)
    if resting is None:
        resting = (PITCH_LENGTH_M / 2.0, PITCH_WIDTH_M / 2.0)
    return resting, 0.0


def _player_at(
    samples: list[PlayerSample], clock_ms: int
) -> dict[str, tuple[float, float]]:
    """Where every player was at one moment.

    The model samples about once a second, and the truth between two
    samples is the straight line between them.
    """
    if not samples:
        return {}

    before = None
    after = None
    for sample in samples:
        if sample.clock_ms <= clock_ms:
            before = sample
        else:
            after = sample
            break

    if before is None:
        first = samples[0]
        return {
            player_id: (x / 100.0 * PITCH_LENGTH_M, y / 100.0 * PITCH_WIDTH_M)
            for player_id, (x, y) in first.positions.items()
        }
    if after is None or after.clock_ms == before.clock_ms:
        return {
            player_id: (x / 100.0 * PITCH_LENGTH_M, y / 100.0 * PITCH_WIDTH_M)
            for player_id, (x, y) in before.positions.items()
        }

    span = after.clock_ms - before.clock_ms
    fraction = min(1.0, max(0.0, (clock_ms - before.clock_ms) / span))
    positions: dict[str, tuple[float, float]] = {}
    for player_id, (x, y) in before.positions.items():
        later = after.positions.get(player_id)
        if later is None:
            positions[player_id] = (
                x / 100.0 * PITCH_LENGTH_M,
                y / 100.0 * PITCH_WIDTH_M,
            )
            continue
        positions[player_id] = (
            (x + (later[0] - x) * fraction) / 100.0 * PITCH_LENGTH_M,
            (y + (later[1] - y) * fraction) / 100.0 * PITCH_WIDTH_M,
        )
    return positions


def pick_window(
    events: list[MatchEvent],
    *,
    duration_ms: int = DEFAULT_DURATION_MS,
    after_ms: int = 20 * 60 * 1000,
) -> int:
    """Pick the passage of the match the camera watches.

    A clip wants a shot in it: the ball moves fast for a moment, and that
    is the moment the tracker has to get right. So the window is built
    around the first shot after the twentieth minute.
    """
    for event in events:
        if event.type is not EventType.SHOT and event.type is not EventType.GOAL:
            continue
        if event.match_clock_ms < after_ms:
            continue
        start = max(0, event.match_clock_ms - int(duration_ms * 0.6))
        return start
    return max(0, after_ms)


def build_truth_clip(
    seed: int = 42,
    *,
    start_ms: int | None = None,
    duration_ms: int = DEFAULT_DURATION_MS,
    fps: int = DEFAULT_FPS,
) -> TruthClip:
    """Build the ground truth for a clip of the match.

    Args:
        seed: Which match to shoot. The same seed is the same clip.
        start_ms: Where the clip starts. A passage with a shot in it is
            chosen when absent.
        duration_ms: How much match time the clip covers.
        fps: How many frames a second the clip is shot at.

    Returns:
        The truth: every frame, and the ball's flights inside it.
    """
    events, samples = simulate_with_feed(seed)
    if start_ms is None:
        start_ms = pick_window(events, duration_ms=duration_ms)
    final_clock = events[-1].match_clock_ms if events else 0
    end_ms = max(start_ms + 1000, min(final_clock, start_ms + duration_ms))

    window = [
        event
        for event in events
        if start_ms - 30_000 <= event.match_clock_ms <= end_ms + 5_000
    ]
    flights, movements = _build_flights(window, start_ms, end_ms)

    frames: list[TruthFrame] = []
    step_ms = 1000 / fps
    count = int((end_ms - start_ms) / step_ms)
    for index in range(count):
        clock = int(start_ms + index * step_ms)
        position, speed = _ball_at(movements, window, clock)
        frames.append(
            TruthFrame(
                index=index,
                clock_ms=clock,
                ball=position,
                ball_speed_mps=speed,
                players=_player_at(samples, clock),
            )
        )

    home, away = _squads(seed)
    players = frames[0].players if frames else {}
    home_players = sorted(p for p in players if p.startswith(home))
    away_players = sorted(p for p in players if p.startswith(away))

    return TruthClip(
        frames=frames,
        fps=fps,
        start_ms=start_ms,
        end_ms=end_ms,
        flights=flights,
        home_players=home_players,
        away_players=away_players,
    )


def _squads(seed: int) -> tuple[str, str]:
    """The two clubs' id prefixes, which their players' ids start with."""
    from touchline.sim.teams import build_teams

    home, away = build_teams(seed)
    return (
        home.club_id.removeprefix("clb-") + "-",
        away.club_id.removeprefix("clb-") + "-",
    )
