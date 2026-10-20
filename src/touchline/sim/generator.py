"""A deterministic, fully synthetic 90-minute football match.

The simulator walks a match clock forward in **possession sequences** rather
than uniform noise: a team in possession strings passes and dribbles, a shot
or a turnover ends the move, and possession switches. The whole thing is a
pure function of the seed, so the same seed always produces a byte-identical
event log.

Geometry
--------
The pitch is 105 m long and 68 m wide. Events carry ``x``/``y`` as
percentages of those dimensions: ``x`` runs from 0 (own goal) to 100
(opponent goal) *from the perspective of the team the event belongs to*, and
``y`` from 0 to 100 between the two touchlines (also mirrored per side, so
that both axes are always in the acting team's frame).

Scoring
-------
The score is registered on the ``GOAL`` event, never on the shot that led to
it, so the invariant is simple to check: the score of any event equals the
number of ``GOAL`` events at or before it.
"""

from __future__ import annotations

import argparse
import math
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from touchline.models.events import (
    HALF_TIME_MS,
    MATCH_DURATION_MS,
    PITCH_LENGTH_M,
    PITCH_WIDTH_M,
    CardType,
    EventType,
    MatchEvent,
    TeamSide,
    meta_int,
)
from touchline.sim.players import PlayerTracker
from touchline.sim.teams import Club, Player, build_teams

#: Metres represented by one percentage point of pitch length / width.
M_PER_X_PCT: float = PITCH_LENGTH_M / 100.0
M_PER_Y_PCT: float = PITCH_WIDTH_M / 100.0

#: Relative duration of each event type, used to space events on the clock.
_DURATIONS: dict[EventType, float] = {
    EventType.PASS: 1.0,
    EventType.DRIBBLE: 1.15,
    EventType.RECOVERY: 0.5,
    EventType.SHOT: 0.6,
    EventType.GOAL: 9.0,
    EventType.SAVE: 1.6,
    EventType.TACKLE: 0.9,
    EventType.INTERCEPTION: 0.7,
    EventType.CLEARANCE: 1.2,
    EventType.FOUL: 3.0,
    EventType.CARD: 3.5,
    EventType.BALL_OUT: 1.8,
    EventType.CORNER: 2.6,
    EventType.FREE_KICK: 2.6,
    EventType.OFFSIDE: 2.0,
    EventType.SUBSTITUTION: 6.0,
}

#: Dead time added between possessions (restarts, players resetting).
_POSSESSION_OVERHEAD: float = 1.1


@dataclass
class _Action:
    """An event that has not been given a clock yet."""

    type: EventType
    side: TeamSide | None
    player: Player | None = None
    target_player: Player | None = None
    x: float = 50.0
    y: float = 50.0
    end_x: float | None = None
    end_y: float | None = None
    outcome: str | None = None
    value: float | None = None
    distance_m: float | None = None
    difficulty: float | None = None
    speed_mps: float | None = None
    card: CardType | None = None
    metadata: dict[str, float | int | str | bool] = field(default_factory=dict)
    score_home: int = 0
    score_away: int = 0

    @property
    def weight(self) -> float:
        """Relative time cost of this event."""
        return _DURATIONS.get(self.type, 1.0)


@dataclass
class _Possession:
    """A run of actions by one team, plus the dead time that follows it."""

    actions: list[_Action]


@dataclass(frozen=True)
class PlayerSample:
    """Where every player was, at one point in the match.

    The event log records where the *ball* was; it does not record where
    the players were. The vision feed needs both -- the video is rendered
    from the players' positions and the tracker is measured against them --
    so the movement model can be asked to report where it put everybody.

    Attributes:
        clock_ms: The match clock the sample belongs to.
        positions: Where every player was, by player id, in absolute
            percentage coordinates.
    """

    clock_ms: int
    positions: dict[str, tuple[float, float]]


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    """Clamp a percentage into the pitch."""
    return max(low, min(high, value))


class _MatchSimulator:
    """Builds one synthetic match from a seed."""

    def __init__(self, seed: int, *, record: bool = False) -> None:
        self.seed = seed
        self.record = record
        self.rng = random.Random(seed)
        self.home, self.away = build_teams(seed)
        self.score_home = 0
        self.score_away = 0
        self.ball_x = 50.0
        self.ball_y = 50.0
        self.on_pitch: dict[TeamSide, list[Player]] = {
            TeamSide.HOME: list(self.home.starters()),
            TeamSide.AWAY: list(self.away.starters()),
        }
        self.bench: dict[TeamSide, list[Player]] = {
            TeamSide.HOME: list(self.home.substitutes()),
            TeamSide.AWAY: list(self.away.substitutes()),
        }
        self.possession_index = 0
        #: The restart a possession has just been given: who takes it, and
        #: where, in that side's own frame.
        self._restart: tuple[TeamSide, float, float] | None = None
        self.cards_issued = 0
        self.corners_issued = 0
        self.free_kicks_issued = 0
        self.fouls_issued = 0
        self.action_counter = 0
        self.goals_scored = 0
        self.actions_since_goal = 99
        # The player model runs on its own random stream, so adding it did
        # not change a single event in the log.
        self.tracker = PlayerTracker(self.home, self.away, seed)
        #: Where every player was, sampled as the match was played. Only
        #: filled in when the vision feed asks for it, because recording
        #: every position of every player is a lot of memory for a match
        #: nobody is going to watch.
        self.samples: list[PlayerSample] = []

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _other(side: TeamSide) -> TeamSide:
        return TeamSide.AWAY if side is TeamSide.HOME else TeamSide.HOME

    @staticmethod
    def _frame(x_abs: float, y_abs: float, side: TeamSide) -> tuple[float, float]:
        """Absolute pitch coordinates -> coordinates in ``side``'s frame."""
        if side is TeamSide.HOME:
            return x_abs, y_abs
        return 100.0 - x_abs, 100.0 - y_abs

    @staticmethod
    def _absolute(x: float, y: float, side: TeamSide) -> tuple[float, float]:
        """Coordinates in ``side``'s frame -> absolute pitch coordinates."""
        if side is TeamSide.HOME:
            return x, y
        return 100.0 - x, 100.0 - y

    def _club(self, side: TeamSide) -> Club:
        return self.home if side is TeamSide.HOME else self.away

    def _pick(self, side: TeamSide, position: str | None = None) -> Player:
        """Pick a player on the pitch for ``side``."""
        pool = self.on_pitch[side]
        if position is not None:
            narrowed = [p for p in pool if p.position == position]
            if narrowed:
                pool = narrowed
        return self.rng.choice(pool)

    def _snapshot(self) -> tuple[int, int]:
        """The current score, used to stamp events as they are created."""
        return self.score_home, self.score_away

    def _new(
        self,
        event_type: EventType,
        side: TeamSide | None,
        **kwargs: object,
    ) -> _Action:
        """Create an action stamped with the current score."""
        score_home, score_away = self._snapshot()
        action = _Action(type=event_type, side=side, **kwargs)  # type: ignore[arg-type]
        action.score_home = score_home
        action.score_away = score_away
        self.action_counter += 1
        self.actions_since_goal += 1
        return action

    @staticmethod
    def _distance(x1: float, y1: float, x2: float, y2: float) -> float:
        """Metres between two percentage coordinates."""
        dx = (x2 - x1) * M_PER_X_PCT
        dy = (y2 - y1) * M_PER_Y_PCT
        return math.hypot(dx, dy)

    @staticmethod
    def _corner_spot(fy: float) -> tuple[float, float]:
        """The corner flag a corner is taken from, in the attacker's frame.

        A corner is taken from the flag nearest where the ball left play, so
        the corner's own position is the flag -- not the spot the ball went
        out at, which is recorded separately on the ball-out event. ``fy`` is
        the lateral position of the ball-out in the attacking team's frame, so
        it picks which of the two flags at the attacking end is used.
        """
        return (100.0, 0.0 if fy < 50.0 else 100.0)

    def _corner_cross(self, fy: float) -> tuple[float, float]:
        """Where a corner is crossed to, in the attacker's frame.

        A corner is taken from the flag and delivered into the box, not
        played along the goal line. The cross lands somewhere between the
        penalty spot and the six-yard box, which is where the next action
        starts -- so a corner produces a chance from a plausible range
        instead of a shot from the byline.
        """
        rng = self.rng
        cross_x = rng.uniform(84.0, 93.0)
        # The cross leans towards the middle of the pitch.
        cross_y = _clamp(
            50.0 + (fy - 50.0) * rng.uniform(0.2, 0.6) + rng.gauss(0, 5.0),
            30.0,
            70.0,
        )
        return (cross_x, cross_y)

    # -- offensive actions ------------------------------------------------

    def _pass_action(
        self, side: TeamSide, fx: float, fy: float, pressure: float
    ) -> tuple[_Action, bool]:
        """A pass: mostly forward, sometimes sideways or backwards."""
        rng = self.rng
        advance = rng.gauss(9.0, 7.0)
        if rng.random() < 0.18:
            advance = -abs(advance) * 0.6
        advance = max(-25.0, min(38.0, advance))
        lateral = rng.gauss(0.0, 9.0)
        dest_x = _clamp(fx + advance, 1.0, 99.0)
        dest_y = _clamp(fy + lateral, 1.0, 99.0)

        distance = self._distance(fx, fy, dest_x, dest_y)
        difficulty = _clamp(
            0.04
            + 0.30 * min(distance / 45.0, 1.0)
            + 0.22 * (dest_x / 100.0)
            + 0.30 * pressure,
            0.0,
            1.0,
        )
        speed = 8.0 + 17.0 * min(distance / 40.0, 1.0) + rng.uniform(-1.5, 1.5)
        speed = max(8.0, min(25.0, speed))

        roll = rng.random()
        if roll < 0.80:
            outcome = "completed"
        elif roll < 0.89:
            outcome = "intercepted"
        elif roll < 0.95:
            outcome = "blocked"
        else:
            outcome = "out"

        player = self._pick(side)
        action = self._new(
            EventType.PASS,
            side,
            player=player,
            x=round(fx, 2),
            y=round(fy, 2),
            end_x=round(dest_x, 2),
            end_y=round(dest_y, 2),
            outcome=outcome,
            distance_m=round(distance, 2),
            difficulty=round(difficulty, 3),
            speed_mps=round(speed, 2),
            value=round(1.0 - difficulty, 3),
            metadata={"pressure": round(pressure, 3)},
        )
        return action, outcome == "completed"

    def _dribble_action(
        self, side: TeamSide, fx: float, fy: float, pressure: float
    ) -> tuple[_Action, bool]:
        """A carry: short distance, usually forward, occasionally lost."""
        rng = self.rng
        carry = rng.uniform(3.0, 13.0)
        direction = rng.gauss(12.0, 14.0) * (carry / 8.0)
        dest_x = _clamp(fx + direction, 1.0, 99.0)
        dest_y = _clamp(fy + rng.gauss(0.0, 7.0), 1.0, 99.0)
        distance = self._distance(fx, fy, dest_x, dest_y)
        difficulty = _clamp(
            0.05 + 0.25 * pressure + 0.02 * min(distance / 20.0, 1.0), 0.0, 1.0
        )
        lost = rng.random() < 0.12
        player = self._pick(side)
        action = self._new(
            EventType.DRIBBLE,
            side,
            player=player,
            x=round(fx, 2),
            y=round(fy, 2),
            end_x=round(dest_x, 2),
            end_y=round(dest_y, 2),
            outcome="lost" if lost else "completed",
            distance_m=round(distance, 2),
            difficulty=round(difficulty, 3),
            speed_mps=round(rng.uniform(5.0, 9.0), 2),
            metadata={"pressure": round(pressure, 3)},
        )
        return action, not lost

    def _recovery_action(
        self, side: TeamSide, fx: float, fy: float
    ) -> tuple[_Action, bool]:
        """A loose ball won back by the same team."""
        player = self._pick(side)
        action = self._new(
            EventType.RECOVERY,
            side,
            player=player,
            x=round(fx, 2),
            y=round(fy, 2),
            outcome="won",
            metadata={"loose_ball": True},
        )
        return action, True

    def _shot_action(self, side: TeamSide, fx: float, fy: float) -> _Action:
        """A shot at goal, with an xG-style value derived from range and angle."""
        rng = self.rng
        to_goal_m = max(1.0, (100.0 - fx) * M_PER_X_PCT)
        lateral_m = abs(fy - 50.0) * M_PER_Y_PCT
        angle_factor = max(0.12, 1.0 - lateral_m / 24.0)
        xg = _clamp(
            0.02 + 0.62 * math.exp(-to_goal_m / 8.5) * angle_factor, 0.01, 0.90
        )
        # A just-scored side is settled for a moment: the next couple of
        # possessions are worth markedly less.
        if self.actions_since_goal < 12:
            xg *= 0.4
        if self.goals_scored >= 5:
            # Hard ceiling: a match in this format never runs past five goals.
            xg = 0.0
        speed = rng.uniform(20.0, 33.0)
        roll = rng.random()
        if roll < xg:
            outcome = "goal"
        elif roll < xg + (1.0 - xg) * 0.36:
            outcome = "saved"
        elif roll < xg + (1.0 - xg) * 0.68:
            outcome = "off_target"
        else:
            outcome = "blocked"

        player = self._pick(side, "FWD" if rng.random() < 0.6 else "MID")
        return self._new(
            EventType.SHOT,
            side,
            player=player,
            x=round(fx, 2),
            y=round(fy, 2),
            outcome=outcome,
            value=round(xg, 3),
            difficulty=round(_clamp(0.35 + 0.5 * (to_goal_m / 40.0), 0.0, 1.0), 3),
            speed_mps=round(speed, 2),
            distance_m=round(to_goal_m, 2),
            metadata={
                "xg": round(xg, 3),
                "range_m": round(to_goal_m, 2),
                "angle_factor": round(angle_factor, 3),
            },
        )

    # -- defensive and dead-ball actions ---------------------------------

    def _turnover(
        self, actions: list[_Action], side: TeamSide, fx: float, fy: float
    ) -> None:
        """The defending side wins the ball: a tackle, an interception or a foul."""
        rng = self.rng
        defender = self._other(side)
        abs_x, abs_y = self._absolute(fx, fy, side)
        dx, dy = self._frame(abs_x, abs_y, defender)
        tackler = self._pick(defender, "DEF" if rng.random() < 0.6 else "MID")
        last_player = actions[-1].player if actions else None
        victim = last_player if last_player else self._pick(side)

        if rng.random() < 0.18:
            # A foul: the defender concedes a free kick, and sometimes a card.
            foul = self._new(
                EventType.FOUL,
                defender,
                player=tackler,
                target_player=victim,
                x=round(dx, 2),
                y=round(dy, 2),
                outcome="foul",
                metadata={"severity": round(rng.uniform(0.2, 1.0), 2)},
            )
            actions.append(foul)
            self.fouls_issued += 1
            # The free kick is taken by ``side``, so it is positioned in
            # ``side``'s own frame -- the spot the ball is at, measured from
            # the team that won it. The defender's frame is used for the foul
            # itself, because the foul is the defender's event.
            free_kick = self._new(
                EventType.FREE_KICK,
                side,
                player=victim,
                x=round(fx, 2),
                y=round(fy, 2),
                metadata={"distance_m": round(self._distance(fx, fy, 100.0, 50.0), 2)},
            )
            actions.append(free_kick)
            self.free_kicks_issued += 1
            # The free kick is taken by the team that was fouled, so they
            # keep the ball: the next possession is theirs, from this spot.
            self._restart = (side, fx, fy)
            if self.cards_issued == 0 or rng.random() < 0.18:
                card_type = CardType.RED if rng.random() < 0.06 else CardType.YELLOW
                actions.append(
                    self._new(
                        EventType.CARD,
                        defender,
                        player=tackler,
                        target_player=victim,
                        x=round(dx, 2),
                        y=round(dy, 2),
                        card=card_type,
                        metadata={"reason": "persistent fouling"},
                    )
                )
                self.cards_issued += 1
            return

        if rng.random() < 0.42:
            actions.append(
                self._new(
                    EventType.INTERCEPTION,
                    defender,
                    player=tackler,
                    target_player=victim,
                    x=round(dx, 2),
                    y=round(dy, 2),
                    outcome="won",
                    metadata={"pressure": round(rng.uniform(0.2, 0.9), 3)},
                )
            )
        else:
            actions.append(
                self._new(
                    EventType.TACKLE,
                    defender,
                    player=tackler,
                    target_player=victim,
                    x=round(dx, 2),
                    y=round(dy, 2),
                    outcome="won" if rng.random() < 0.78 else "lost",
                    metadata={"pressure": round(rng.uniform(0.2, 0.9), 3)},
                )
            )

    def _after_shot(
        self,
        actions: list[_Action],
        side: TeamSide,
        shot: _Action,
        fx: float,
        fy: float,
    ) -> None:
        """Resolve a shot: goal, save, or the ball out of play."""
        rng = self.rng
        defender = self._other(side)
        abs_x, abs_y = self._absolute(fx, fy, side)
        dx, dy = self._frame(abs_x, abs_y, defender)

        if shot.outcome == "goal":
            if side is TeamSide.HOME:
                self.score_home += 1
            else:
                self.score_away += 1
            self.goals_scored += 1
            self.actions_since_goal = 0
            goal_y = _clamp(50.0 + rng.gauss(0.0, 11.0), 12.0, 88.0)
            actions.append(
                self._new(
                    EventType.GOAL,
                    side,
                    player=shot.player,
                    x=99.0,
                    y=round(goal_y, 2),
                    value=shot.value,
                    metadata={"assist": "open play", "xg": shot.value or 0.0},
                )
            )
            self.ball_x, self.ball_y = 50.0, 50.0
            return

        if shot.outcome == "saved":
            keeper = self._pick(defender, "GK")
            actions.append(
                self._new(
                    EventType.SAVE,
                    defender,
                    player=keeper,
                    target_player=shot.player,
                    x=round(dx, 2),
                    y=round(dy, 2),
                    outcome="saved",
                    value=shot.value,
                    metadata={"xg_conceded": shot.value or 0.0},
                )
            )
            self.ball_x, self.ball_y = abs_x, abs_y
            return

        # off_target or blocked: the ball leaves play
        actions.append(
            self._new(
                EventType.BALL_OUT,
                side,
                x=round(fx, 2),
                y=round(fy, 2),
                outcome="out",
                metadata={"reason": shot.outcome or "out"},
            )
        )
        if fx > 60.0 and rng.random() < 0.7:
            corner_x, corner_y = self._corner_spot(fy)
            cross_x, cross_y = self._corner_cross(fy)
            actions.append(
                self._new(
                    EventType.CORNER,
                    side,
                    x=corner_x,
                    y=corner_y,
                    end_x=round(cross_x, 2),
                    end_y=round(cross_y, 2),
                    metadata={
                        "delivery_side": "left" if fy < 50 else "right",
                        "cross_distance_m": round(
                            self._distance(corner_x, corner_y, cross_x, cross_y), 2
                        ),
                    },
                )
            )
            self.corners_issued += 1
            # The corner is taken by the side that won it, and the ball is
            # crossed into the box: the next possession starts where the
            # cross lands.
            self._restart = (side, cross_x, cross_y)

    def _ball_out(
        self, actions: list[_Action], side: TeamSide, fx: float, fy: float
    ) -> None:
        """The ball goes out of play; possibly for a corner."""
        rng = self.rng
        actions.append(
            self._new(
                EventType.BALL_OUT,
                side,
                x=round(fx, 2),
                y=round(fy, 2),
                outcome="out",
                metadata={"reason": "deflected"},
            )
        )
        if fx > 62.0 and rng.random() < 0.55:
            corner_x, corner_y = self._corner_spot(fy)
            cross_x, cross_y = self._corner_cross(fy)
            actions.append(
                self._new(
                    EventType.CORNER,
                    side,
                    x=corner_x,
                    y=corner_y,
                    end_x=round(cross_x, 2),
                    end_y=round(cross_y, 2),
                    metadata={
                        "delivery_side": "left" if fy < 50 else "right",
                        "cross_distance_m": round(
                            self._distance(corner_x, corner_y, cross_x, cross_y), 2
                        ),
                    },
                )
            )
            self.corners_issued += 1
            self._restart = (side, cross_x, cross_y)
        else:
            # The throw-in is taken by the other side, so the spot it is taken
            # from is in that side's frame, not this one's.
            thrower = self._other(side)
            abs_x, abs_y = self._absolute(fx, fy, side)
            tx, ty = self._frame(abs_x, abs_y, thrower)
            actions.append(
                self._new(
                    EventType.RECOVERY,
                    thrower,
                    x=round(tx, 2),
                    y=round(ty, 2),
                    outcome="won",
                    metadata={"restart": "throw-in"},
                )
            )
            self._restart = (thrower, tx, ty)

    # -- possessions ------------------------------------------------------

    def _build_possession(self, side: TeamSide, max_actions: int) -> _Possession:
        """Build one possession: a run of actions ending in a shot or a turnover."""
        rng = self.rng
        actions: list[_Action] = []
        pressure = rng.uniform(0.05, 0.85)

        restart = self._restart
        # The restart has been taken; whatever ends this possession decides
        # who has the ball next.
        self._restart = None
        if restart is not None and restart[0] is side:
            # A restart is taken where the ball was put down, so the
            # possession starts there rather than somewhere new.
            fx, fy = restart[1], restart[2]
        else:
            fx, fy = self._frame(self.ball_x, self.ball_y, side)
            if rng.random() < 0.55:
                fx = rng.uniform(6.0, 40.0)
                fy = rng.uniform(5.0, 95.0)

        steps = 0
        while steps < max_actions:
            steps += 1
            roll = rng.random()
            if roll < 0.74:
                action, keep = self._pass_action(side, fx, fy, pressure)
            elif roll < 0.90:
                action, keep = self._dribble_action(side, fx, fy, pressure)
            else:
                action, keep = self._recovery_action(side, fx, fy)
            actions.append(action)
            if action.end_x is not None and action.end_y is not None:
                fx, fy = action.end_x, action.end_y
            if not keep:
                self._turnover(actions, side, fx, fy)
                break
            if fx > 64.0 and rng.random() < 0.32:
                shot = self._shot_action(side, fx, fy)
                actions.append(shot)
                self._after_shot(actions, side, shot, fx, fy)
                break
            if rng.random() < 0.14:
                self._ball_out(actions, side, fx, fy)
                break
        else:
            self._turnover(actions, side, fx, fy)

        self.possession_index += 1
        self.ball_x, self.ball_y = self._absolute(fx, fy, side)
        for action in actions:
            action.metadata.setdefault("possession", self.possession_index)
        return _Possession(actions=actions)

    # -- substitutions ----------------------------------------------------

    def _plan_substitutions(self, total_actions: int) -> list[tuple[int, TeamSide]]:
        """Pick the action indices at which substitutions happen."""
        rng = self.rng
        count = rng.randint(3, 5)
        fractions = [0.22, 0.45, 0.68, 0.86][:count]
        sides = [TeamSide.HOME, TeamSide.AWAY]
        plans: list[tuple[int, TeamSide]] = []
        for index, fraction in enumerate(fractions):
            jitter = rng.uniform(-0.04, 0.04)
            at = int((fraction + jitter) * total_actions)
            plans.append((max(0, at), sides[index % 2]))
        return sorted(plans)

    def _apply_substitution(self, side: TeamSide) -> _Action:
        """Take a substitute off the bench and put a starter to the bench."""
        rng = self.rng
        pitch = self.on_pitch[side]
        bench = self.bench[side]
        if not bench:
            raise RuntimeError("no substitutes left for this side")

        position = bench[0].position
        for candidate in bench:
            if candidate.position == position:
                position = candidate.position
                break
        incoming = next(p for p in bench if p.position == position)
        bench.remove(incoming)

        starters = [p for p in pitch if p.number <= 11]
        outgoing = rng.choice(starters) if starters else rng.choice(pitch)
        pitch.remove(outgoing)
        pitch.append(incoming)
        bench.append(outgoing)

        return self._new(
            EventType.SUBSTITUTION,
            side,
            player=incoming,
            target_player=outgoing,
            x=50.0,
            y=50.0,
            metadata={"position": position},
        )

    # -- half building ----------------------------------------------------

    def _build_half(
        self, target_actions: int, subs: list[tuple[int, TeamSide]]
    ) -> list[_Possession]:
        """Build one half's worth of possessions."""
        rng = self.rng
        possessions: list[_Possession] = []
        count = 0
        side = TeamSide.HOME
        pending_subs = list(subs)

        while count < target_actions:
            remaining = target_actions - count
            cap = max(2, min(rng.randint(3, 8), remaining))
            possession = self._build_possession(side, cap)
            actions = possession.actions

            # Any substitution scheduled inside this possession is inserted now.
            while pending_subs and pending_subs[0][0] <= self.action_counter:
                _, sub_side = pending_subs.pop(0)
                actions.append(self._apply_substitution(sub_side))

            possessions.append(possession)
            count += len(actions)
            # Possession normally changes hands. It does not when the last
            # possession ended in a restart -- a free kick, a corner, a
            # throw-in -- because the side awarded the restart keeps the ball.
            if self._restart is not None:
                side = self._restart[0]
                self._restart = None
            else:
                side = self._other(side)

        return possessions

    # -- clock ------------------------------------------------------------

    @staticmethod
    def _scale(
        possessions: list[_Possession], start_ms: int, end_ms: int
    ) -> float:
        """Milliseconds of match clock per unit of event weight."""
        total_weight = sum(
            sum(a.weight for a in p.actions) + _POSSESSION_OVERHEAD for p in possessions
        )
        if total_weight <= 0:
            return 0.0
        return (end_ms - start_ms) / total_weight

    @staticmethod
    def _timed(
        possessions: list[_Possession], start_ms: int, end_ms: int
    ) -> list[tuple[int, _Action]]:
        """Place every action on the clock, in possession-sized clusters."""
        scale = _MatchSimulator._scale(possessions, start_ms, end_ms)
        if scale <= 0:
            return []
        clock = float(start_ms)
        timed: list[tuple[int, _Action]] = []
        for possession in possessions:
            for action in possession.actions:
                timed.append((int(clock), action))
                clock += action.weight * scale
            clock += _POSSESSION_OVERHEAD * scale
        return timed

    # -- player movement ---------------------------------------------------

    def _track_players(
        self,
        possessions: list[_Possession],
        timed: list[tuple[int, _Action]],
        scale_ms: float,
    ) -> None:
        """Run the player model over a half, and record the ground covered.

        The distance each player covers during an action is written onto the
        event as ``player_distance_m``, so the analyst reads it out of the log
        instead of inferring it from the path of the ball. The log records
        where the ball was; it does not record where the players were, and
        the two are not the same thing.

        The model is stepped over *time*, not over events: between one
        action and the next the clock runs for several seconds, and during
        those seconds everybody keeps moving.

        The ``timed`` list is the half's actions with the clock they were
        given, and it is what the samples are stamped with: the movement
        model's output and the event log have to sit on the same timeline,
        or the ground truth would be a second or two away from the match it
        describes.
        """
        tracker = self.tracker
        previous_xy: tuple[float, float] | None = None
        previous_possession: int | None = None
        index = 0
        for possession in possessions:
            for action in possession.actions:
                clock = timed[index][0] if index < len(timed) else 0
                next_clock = timed[index + 1][0] if index + 1 < len(timed) else None
                index += 1
                if action.side is None:
                    continue
                if action.type is EventType.SUBSTITUTION:
                    if action.player and action.target_player:
                        tracker.substitute(
                            action.target_player.player_id, action.player.player_id
                        )
                    continue
                ball_x, ball_y = self._absolute(action.x, action.y, action.side)
                actor = action.player.player_id if action.player else None
                possession_index = meta_int(action.metadata, "possession")
                # A restart puts the ball down somewhere new, and there is no
                # path to walk: it was placed there, and the players close in.
                if previous_xy is None or possession_index != previous_possession:
                    from_xy = (ball_x, ball_y)
                else:
                    from_xy = previous_xy
                duration_s = action.weight * scale_ms / 1000.0
                recorder = self._recorder(clock, next_clock, duration_s)
                covered = tracker.integrate(
                    duration_s,
                    from_xy,
                    (ball_x, ball_y),
                    actor,
                    recorder,
                )
                previous_xy = (ball_x, ball_y)
                previous_possession = possession_index
                if action.end_x is not None and action.end_y is not None:
                    end_x, end_y = self._absolute(
                        action.end_x, action.end_y, action.side
                    )
                    if action.type is EventType.DRIBBLE:
                        covered += tracker.carry_to(actor, end_x, end_y)
                    elif action.type is EventType.PASS:
                        # A completed pass is met by a team-mate; a pass that
                        # was not completed is won by the other side.
                        receiver = (
                            action.side
                            if action.outcome == "completed"
                            else self._other(action.side)
                        )
                        tracker.receive(receiver, end_x, end_y)
                    previous_xy = (end_x, end_y)
                if actor is not None:
                    action.metadata["player_distance_m"] = round(covered, 2)
                    # The player's running total, so the log can say how far
                    # a player has run in total and not just on this action.
                    action.metadata["player_total_distance_m"] = round(
                        tracker.covered_m(actor), 2
                    )

    def _recorder(
        self, clock: int, next_clock: int | None, duration_s: float
    ) -> Callable[[float, dict[str, tuple[float, float]]], None] | None:
        """The callback that records where the players are.

        The samples are spread across the *event* clock rather than the
        model's own clock, so that the ground truth and the event log are
        on one timeline. The model's own time still decides how far the
        players moved, so the distance figures do not change.
        """
        if not self.record:
            return None

        span = float(next_clock - clock) if next_clock is not None else 0.0
        if span <= 0.0:
            span = duration_s * 1000.0
        samples = self.samples

        def record(
            elapsed_s: float, positions: dict[str, tuple[float, float]]
        ) -> None:
            fraction = min(1.0, elapsed_s / duration_s) if duration_s > 0 else 1.0
            samples.append(
                PlayerSample(
                    clock_ms=clock + round(span * fraction),
                    positions=dict(positions),
                )
            )

        return record

    # -- assembly ---------------------------------------------------------

    def run(self) -> list[MatchEvent]:
        """Build the whole match and return the ordered event list."""
        rng = self.rng
        target_total = rng.randint(720, 1090)
        half_one_target = target_total // 2
        half_two_target = target_total - half_one_target

        subs = self._plan_substitutions(target_total)

        # The first half starts with the home team in possession.
        half_one = self._build_half(half_one_target, subs)
        half_two = self._build_half(half_two_target, subs)

        possessions = half_one + half_two

        if self.cards_issued == 0:
            possessions = self._force_card(possessions)
        if self.corners_issued == 0:
            possessions = self._force_corner(possessions)
        if self.free_kicks_issued == 0:
            possessions = self._force_free_kick(possessions)

        half_time_score = self._score_after(half_one)
        final_score = self._score_after(half_two)

        # The player model runs over the same actions the clock does, so the
        # ground a player covers is measured in the same match time the
        # events are timestamped in. The clock is settled first, because the
        # samples the model gives up are stamped with it.
        timed_one = self._timed(half_one, 1, HALF_TIME_MS - 30_000)
        timed_two = self._timed(
            half_two, HALF_TIME_MS + 5_000, MATCH_DURATION_MS - 30_000
        )
        timed = _settle_clocks(timed_one + timed_two)
        self._track_players(
            half_one,
            timed[: len(timed_one)],
            self._scale(half_one, 1, HALF_TIME_MS - 30_000),
        )
        self._track_players(
            half_two,
            timed[len(timed_one) :],
            self._scale(half_two, HALF_TIME_MS + 5_000, MATCH_DURATION_MS - 30_000),
        )

        events: list[MatchEvent] = []
        events.append(
            MatchEvent(
                event_id="",
                type=EventType.KICK_OFF,
                match_clock_ms=0,
                period=1,
                side=TeamSide.HOME,
                x=50.0,
                y=50.0,
                score_home=0,
                score_away=0,
                metadata={
                    "stadium": "synthetic ground",
                    "attendance": rng.randint(4_000, 41_000),
                },
            )
        )
        events.append(
            MatchEvent(
                event_id="",
                type=EventType.HALF_TIME,
                match_clock_ms=HALF_TIME_MS,
                period=1,
                x=50.0,
                y=50.0,
                score_home=half_time_score[0],
                score_away=half_time_score[1],
                metadata={
                    "score_at_break": f"{half_time_score[0]}-{half_time_score[1]}"
                },
            )
        )
        events.append(
            MatchEvent(
                event_id="",
                type=EventType.FULL_TIME,
                match_clock_ms=MATCH_DURATION_MS,
                period=2,
                x=50.0,
                y=50.0,
                score_home=final_score[0],
                score_away=final_score[1],
                metadata={"result": f"{final_score[0]}-{final_score[1]}"},
            )
        )

        for clock_ms, action in timed:
            period = 1 if clock_ms < HALF_TIME_MS else 2
            events.append(
                MatchEvent(
                    event_id="",
                    type=action.type,
                    match_clock_ms=min(clock_ms, MATCH_DURATION_MS),
                    period=period,
                    side=action.side,
                    player_id=action.player.player_id if action.player else None,
                    player_name=action.player.name if action.player else None,
                    target_player_id=(
                        action.target_player.player_id if action.target_player else None
                    ),
                    target_player_name=(
                        action.target_player.name if action.target_player else None
                    ),
                    x=action.x,
                    y=action.y,
                    end_x=action.end_x,
                    end_y=action.end_y,
                    outcome=action.outcome,
                    value=action.value,
                    distance_m=action.distance_m,
                    difficulty=action.difficulty,
                    speed_mps=action.speed_mps,
                    card=action.card,
                    score_home=action.score_home,
                    score_away=action.score_away,
                    metadata=action.metadata,
                )
            )

        events.sort(key=lambda e: e.match_clock_ms)
        return [
            event.model_copy(update={"event_id": f"ev-{index:06d}"})
            for index, event in enumerate(events, start=1)
        ]

    # -- guarantees -------------------------------------------------------

    @staticmethod
    def _score_after(possessions: list[_Possession]) -> tuple[int, int]:
        """The score after the last action of a run of possessions."""
        for possession in reversed(possessions):
            if possession.actions:
                last = possession.actions[-1]
                return last.score_home, last.score_away
        return (0, 0)

    @staticmethod
    def _restamp(action: _Action, anchor: _Action) -> _Action:
        """Copy the score of ``anchor`` onto ``action`` (for inserted events)."""
        action.score_home = anchor.score_home
        action.score_away = anchor.score_away
        # Forced events are inserted after the possession has been stamped, so
        # they have to inherit the possession index from their anchor.
        possession = meta_int(anchor.metadata, "possession")
        if possession is not None:
            action.metadata.setdefault("possession", possession)
        return action

    def _force_card(self, possessions: list[_Possession]) -> list[_Possession]:
        """Guarantee at least one card in the match."""
        for possession in possessions:
            for action in possession.actions:
                if action.type is EventType.FOUL:
                    card = self._new(
                        EventType.CARD,
                        action.side,
                        player=action.player,
                        target_player=action.target_player,
                        x=action.x,
                        y=action.y,
                        card=CardType.YELLOW,
                        metadata={"reason": "late challenge"},
                    )
                    self._restamp(card, action)
                    possession.actions.insert(
                        possession.actions.index(action) + 1, card
                    )
                    self.cards_issued += 1
                    return possessions
        return possessions

    def _force_corner(self, possessions: list[_Possession]) -> list[_Possession]:
        """Guarantee at least one corner in the match."""
        for possession in possessions:
            for action in possession.actions:
                if action.type is EventType.SHOT:
                    corner_x, corner_y = self._corner_spot(action.y)
                    corner = self._new(
                        EventType.CORNER,
                        action.side,
                        x=corner_x,
                        y=corner_y,
                        metadata={
                            "delivery_side": "left" if action.y < 50 else "right"
                        },
                    )
                    self._restamp(corner, action)
                    possession.actions.insert(
                        possession.actions.index(action) + 1, corner
                    )
                    self.corners_issued += 1
                    return possessions
        return possessions

    def _force_free_kick(self, possessions: list[_Possession]) -> list[_Possession]:
        """Guarantee at least one free kick in the match."""
        for possession in possessions:
            for action in possession.actions:
                if action.type is EventType.TACKLE:
                    fouled = action.side
                    if fouled is None:
                        taker = None
                        kx, ky = action.x, action.y
                    else:
                        taker = self._other(fouled)
                        # The tackle is positioned in the tackler's frame; the
                        # free kick belongs to the team that was fouled, so its
                        # position is converted into that team's frame.
                        abs_x, abs_y = self._absolute(action.x, action.y, fouled)
                        kx, ky = self._frame(abs_x, abs_y, taker)
                    free_kick = self._new(
                        EventType.FREE_KICK,
                        taker,
                        x=round(kx, 2),
                        y=round(ky, 2),
                        metadata={
                            "restart": "foul",
                            "distance_m": round(self._distance(kx, ky, 100.0, 50.0), 2),
                        },
                    )
                    self._restamp(free_kick, action)
                    possession.actions.insert(
                        possession.actions.index(action) + 1, free_kick
                    )
                    self.free_kicks_issued += 1
                    return possessions
        return possessions


def simulate(seed: int = 42) -> list[MatchEvent]:
    """Simulate a complete 90-minute match.

    Args:
        seed: Any integer. The result is fully deterministic for a given seed.

    Returns:
        The ordered event list, from kick-off to full time.
    """
    return _MatchSimulator(seed).run()


def _settle_clocks(
    timed: list[tuple[int, _Action]],
) -> list[tuple[int, _Action]]:
    """Make sure every action sits at its own moment on the clock.

    Two actions can be scaled onto the same millisecond, and the log needs
    them to be in an order rather than to share a moment. The clock is
    pushed on by a millisecond when it would otherwise collide.
    """
    settled: list[tuple[int, _Action]] = []
    last = 0
    for clock_ms, action in timed:
        clock_ms = max(clock_ms, last + 1)
        last = clock_ms
        settled.append((clock_ms, action))
    return settled


def simulate_with_feed(
    seed: int = 42,
) -> tuple[list[MatchEvent], list[PlayerSample]]:
    """Simulate a match and hand back where the players were, as well.

    The event log says where the *ball* was. The vision feed needs to know
    where the *players* were, because the video is rendered from them and
    the tracker is measured against them -- so the movement model is asked
    to report its output as it goes.

    Args:
        seed: Any integer. The result is fully deterministic for a given seed.

    Returns:
        The event list, and the samples of where every player was.
    """
    simulator = _MatchSimulator(seed, record=True)
    events = simulator.run()
    return events, simulator.samples


def simulate_with_tracker(
    seed: int = 42,
) -> tuple[list[MatchEvent], PlayerTracker]:
    """Simulate a match and hand back the player model that ran with it.

    The tracker holds the ground each player covered and where they ended
    up, which is the ground truth the vision tracker is checked against.

    Args:
        seed: Any integer. The result is fully deterministic for a given seed.

    Returns:
        The event list, and the player tracker that produced it.
    """
    simulator = _MatchSimulator(seed)
    return simulator.run(), simulator.tracker


def team_names(seed: int = 42) -> dict[TeamSide, str]:
    """The two clubs that play under a seed, by side.

    The event log records which side did what but not what the clubs are
    called, so this is how the analyst and the narrator are given the names
    they put in a headline.

    Args:
        seed: Any integer. The same seed always yields the same two clubs.

    Returns:
        A mapping from home and away to the club's name.
    """
    home, away = build_teams(seed)
    return {TeamSide.HOME: home.name, TeamSide.AWAY: away.name}


def write_jsonl(events: list[MatchEvent], path: Path) -> None:
    """Write events to a JSON Lines file, one event per line."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(event.to_json_line())
            handle.write("\n")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Usage: ``python -m touchline.sim.generator --seed 42 --out f.jsonl``.
    """
    parser = argparse.ArgumentParser(
        prog="touchline.sim.generator",
        description="Generate a deterministic synthetic football match as JSON Lines.",
    )
    parser.add_argument(
        "--seed", type=int, default=42, help="Random seed (default: 42)."
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("data/match.jsonl"),
        help="Output path for the JSON Lines event log (default: data/match.jsonl).",
    )
    args = parser.parse_args(argv)

    events = simulate(seed=args.seed)
    write_jsonl(events, args.out)
    print(
        f"seed={args.seed} events={len(events)} "
        f"score={events[-1].score_home}-{events[-1].score_away} -> {args.out}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via the CLI
    raise SystemExit(main())
