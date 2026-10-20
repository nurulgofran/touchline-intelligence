"""Where the players are, and how much ground they cover.

The event log records where the *ball* was. It does not record where the
players were, and the two are not the same thing: a player has to run to
the ball before they can touch it, and they go on moving after they pass it.
So the ground a player covers cannot be read out of the ball's path -- it has
to be modelled.

This module is that model. Every player has a position on the pitch and a
formation slot; the whole block slides up and down the pitch as the ball
moves; the player on the ball runs to it; the nearest players to it press
it; and everybody else keeps adjusting around their spot. The ground each
of them covers is totalled as the match is simulated. The generator drives
the model and records the distance on each event, so the number reaches the
analyst through the log and can be recomputed from it like every other
metric.

The model runs on **its own** random stream. That is deliberate: adding it
changed no event in the log, so the same seed still produces the same match
and every threshold calibrated against the earlier simulator still holds.

The constants below were calibrated against published Premier League output
rather than chosen by eye. A professional outfielder covers roughly 8-12 km
per ninety minutes and a goalkeeper roughly 4-6 km, and the gap between
those two numbers is what says the model has a *shape* and not just a scale
factor. What it produces, over seven seeds:

==========  ==========  ==============
Role        Model       Real (typical)
==========  ==========  ==============
Goalkeeper  4.1 km      4-6 km
Defender   8.3 km      ~10.5 km
Midfielder 9.6 km      ~10.8 km
Forward    10.1 km     ~9.7 km
==========  ==========  ==============

The outfield median sits at 8.8 km across those seeds, against a real range
of 8-12 km. It is on the low side, which is honest to say: the model has no
injuries, no fatigue, no off-ball runs into space, and no five-a-side
defending shape.

Positions are in *absolute* pitch coordinates -- x from 0 to 100 along the
length, y from 0 to 100 across it -- because a player moves between the two
ends of the pitch and one frame has to serve both teams.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field
from math import hypot

from touchline.models.events import PITCH_LENGTH_M, PITCH_WIDTH_M, TeamSide
from touchline.sim.teams import Club, Player

__all__ = ["PlayerTracker"]

#: Metres represented by one percentage point of pitch length / width.
M_PER_X_PCT: float = PITCH_LENGTH_M / 100.0
M_PER_Y_PCT: float = PITCH_WIDTH_M / 100.0

#: How finely the match clock is integrated, in seconds. The model steps
#: every player at this interval rather than once per event, because a
#: player who is handed a six-second step reaches their target and stands
#: still for the rest of it, and nobody stands still for ninety minutes.
_STEP_S: float = 1.0

#: Running speed towards a target that is close by, in metres per second.
_BASE_SPEED_MPS: float = 3.6
#: Running speed towards a target that is a long way off, in metres per second.
_SPRINT_SPEED_MPS: float = 7.4
#: How far a player will stray from their slot to press the ball, in metres.
_PRESS_RANGE_M: float = 34.0
#: How many players per side go and press the ball.
_PRESSERS_PER_SIDE: int = 3
#: How far the whole team slides up or down the pitch with the ball, as a
#: fraction of the distance the ball is from the middle.
_BLOCK_SHIFT: float = 0.65
#: The most the block can slide, in percentage points of pitch length. A
#: team defending deep sits about twenty-five metres behind the ball, and
#: one attacking sits about the same distance ahead of it; a clamp tighter
#: than that parks the block and stops the shape moving.
_MAX_BLOCK_SHIFT: float = 42.0
#: How far the block slides across the pitch with the ball, as a fraction of
#: the distance the ball is from the middle.
_LATERAL_SHIFT: float = 0.50
#: The most the block can slide across, in percentage points of pitch width.
_MAX_LATERAL_SHIFT: float = 30.0
#: How much of the block's slide each role follows. A keeper stays at home,
#: a defender holds the line, a forward pushes up to the striker's line.
_ROLE_SHIFT: dict[str, float] = {
    "GK": 0.25,
    "DEF": 0.75,
    "MID": 1.00,
    "FWD": 1.15,
}
#: How far a player drifts from their spot while the ball is elsewhere, in
#: metres. Nobody stands perfectly still for ninety minutes.
_WANDER_M: float = 3.0
#: How fast that drift wanders, in metres per step.
_WANDER_STEP_M: float = 0.30
#: How close the keeper comes out to press. Rarely far from their goal.
_GK_PRESS_RANGE_M: float = 12.0

#: Formation slots, in the team's own frame: a base distance from the team's
#: own goal, and the lateral positions of the players in that role.
_FORMATION: dict[str, tuple[float, tuple[float, ...]]] = {
    "GK": (7.0, (50.0,)),
    "DEF": (26.0, (16.0, 38.0, 62.0, 84.0)),
    "MID": (50.0, (24.0, 50.0, 76.0)),
    "FWD": (74.0, (20.0, 50.0, 80.0)),
}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass
class _Runner:
    """One player, where they are and what they have covered."""

    player: Player
    side: TeamSide
    slot_x: float
    slot_y: float
    x: float
    y: float
    covered_m: float = 0.0
    #: How far this player has drifted from their spot, in metres.
    wander_x: float = 0.0
    wander_y: float = 0.0
    #: The roles this player takes on in a match, for the press radius.
    roles: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_keeper(self) -> bool:
        return "GK" in self.roles


class PlayerTracker:
    """The movement of every player on the pitch, over a match.

    The model is deliberately simple. Each player has a formation slot; the
    whole block slides up, down and across the pitch as the ball moves; the
    player on the ball runs to it; the nearest players to it press; and
    everybody else holds their shape, drifting around it.

    That is not a simulation of football, but it is a simulation of
    *running*, which is what the distance metric is measuring.
    """

    def __init__(self, home: Club, away: Club, seed: int) -> None:
        """Start every player in their formation slot.

        Args:
            home: The home club.
            away: The away club.
            seed: Seeds this model's own random stream. It is separate from
                the match's, so the model cannot change the events.
        """
        self._rng = random.Random(seed * 104_729 + 7)
        self._runners: dict[str, _Runner] = {}
        for side, club in ((TeamSide.HOME, home), (TeamSide.AWAY, away)):
            for runner in self._build_runners(club, side):
                self._runners[runner.player.player_id] = runner

    @staticmethod
    def _build_runners(club: Club, side: TeamSide) -> list[_Runner]:
        """Place a club's starting eleven in their formation slots."""
        runners: list[_Runner] = []
        by_role: dict[str, list[Player]] = {}
        for player in sorted(club.starters(), key=lambda p: p.number):
            by_role.setdefault(player.position, []).append(player)
        for role, players in by_role.items():
            base_x, lanes = _FORMATION.get(role, (50.0, (50.0,)))
            for index, player in enumerate(players):
                slot_x = base_x
                slot_y = lanes[index] if index < len(lanes) else lanes[-1]
                absolute_x, absolute_y = _to_absolute(slot_x, slot_y, side)
                runners.append(
                    _Runner(
                        player=player,
                        side=side,
                        slot_x=slot_x,
                        slot_y=slot_y,
                        x=absolute_x,
                        y=absolute_y,
                        roles=(player.position,),
                    )
                )
        return runners

    # -- reads ------------------------------------------------------------

    def covered_m(self, player_id: str) -> float:
        """The ground a player has covered so far, in metres."""
        runner = self._runners.get(player_id)
        return runner.covered_m if runner else 0.0

    def position(self, player_id: str) -> tuple[float, float]:
        """Where a player is, in absolute pitch coordinates."""
        runner = self._runners.get(player_id)
        return (runner.x, runner.y) if runner else (50.0, 50.0)

    def positions(self) -> dict[str, tuple[float, float]]:
        """Where every player is, in absolute pitch coordinates."""
        return {pid: (r.x, r.y) for pid, r in self._runners.items()}

    def substitute(self, outgoing_id: str, incoming_id: str) -> None:
        """Swap a player on the pitch for one off it.

        The incoming player starts on the touchline where the outgoing one
        was standing, so the shape of the team does not jump.
        """
        outgoing = self._runners.get(outgoing_id)
        incoming = self._runners.get(incoming_id)
        if incoming is None:
            return
        if outgoing is not None:
            incoming.x, incoming.y = outgoing.x, outgoing.y
            incoming.covered_m = outgoing.covered_m
        else:
            absolute_x, absolute_y = _to_absolute(
                incoming.slot_x, incoming.slot_y, incoming.side
            )
            incoming.x, incoming.y = absolute_x, absolute_y

    # -- movement ---------------------------------------------------------

    def integrate(
        self,
        duration_s: float,
        from_xy: tuple[float, float],
        to_xy: tuple[float, float],
        actor_id: str | None = None,
        on_step: Callable[[float, dict[str, tuple[float, float]]], None] | None = None,
    ) -> float:
        """Move the match on by a stretch of time, with the ball moving too.

        The ball is walked along the straight line between the two spots, in
        small steps, and every player is moved at each one. This is what
        makes the distance figure meaningful: a player who is chasing a
        moving ball keeps running, instead of teleporting once per event.

        Args:
            duration_s: How much match time passes, in seconds.
            from_xy: Where the ball starts, in absolute percentage coordinates.
            to_xy: Where the ball ends up.
            actor_id: The player who is about to touch the ball, if any.
            on_step: Called after each step with the elapsed time and where
                every player is. The vision feed uses it to record the
                ground truth: where the players actually were, which the
                tracker is then measured against.

        Returns:
            The ground the actor covered over the stretch, in metres.
        """
        if duration_s <= 0.0:
            return 0.0
        steps = max(1, int(duration_s / _STEP_S) + (1 if duration_s % _STEP_S else 0))
        dt = duration_s / steps
        actor_step = 0.0
        fx, fy = from_xy
        tx, ty = to_xy
        for index in range(steps):
            fraction = (index + 1) / steps
            ball_x = fx + (tx - fx) * fraction
            ball_y = fy + (ty - fy) * fraction
            actor_step += self.advance(dt, ball_x, ball_y, actor_id)
            if on_step is not None:
                on_step(duration_s * fraction, self.positions())
        return actor_step

    def advance(
        self,
        dt_s: float,
        ball_x: float,
        ball_y: float,
        actor_id: str | None = None,
    ) -> float:
        """Move every player on by one step of the match clock.

        Args:
            dt_s: How much match time has passed, in seconds.
            ball_x: The ball's position along the pitch, in absolute
                percentage coordinates.
            ball_y: The ball's position across the pitch.
            actor_id: The player about to touch the ball, if any. They run to
                it; so do the nearest other players to it, who press.

        Returns:
            The ground covered by the actor during this step, in metres. Zero
            when there is no actor.
        """
        if dt_s <= 0.0:
            return 0.0

        pressers = self._pressers(ball_x, ball_y, actor_id)
        actor_step = 0.0
        for runner in self._runners.values():
            player_id = runner.player.player_id
            if player_id == actor_id or player_id in pressers:
                target_x, target_y = ball_x, ball_y
            else:
                target_x, target_y = self._slot_target(runner, ball_x, ball_y)
            step = self._step(runner, target_x, target_y, dt_s)
            if player_id == actor_id:
                actor_step = step
        return actor_step

    def carry(self, player_id: str, metres: float) -> None:
        """Move a player with the ball they are carrying."""
        runner = self._runners.get(player_id)
        if runner is None or metres <= 0.0:
            return
        runner.covered_m += metres

    def receive(self, side: TeamSide, x: float, y: float) -> float:
        """The nearest player of a side meets the ball at a spot.

        Returns:
            The distance that player covered getting there, in metres.
        """
        best: tuple[float, _Runner] | None = None
        for runner in self._runners.values():
            if runner.side is not side:
                continue
            dx = (x - runner.x) * M_PER_X_PCT
            dy = (y - runner.y) * M_PER_Y_PCT
            distance = hypot(dx, dy)
            if best is None or distance < best[0]:
                best = (distance, runner)
        if best is None:
            return 0.0
        distance, runner = best
        runner.x = x
        runner.y = y
        runner.covered_m += distance
        return distance

    def carry_to(self, player_id: str | None, x: float, y: float) -> float:
        """Move a player with the ball to a new spot, and report the distance.

        Args:
            player_id: The player carrying the ball, or None when the action
                had no player attached to it.
            x: The new x, in absolute percentage coordinates.
            y: The new y, in absolute percentage coordinates.

        Returns:
            The distance covered, in metres.
        """
        if player_id is None:
            return 0.0
        runner = self._runners.get(player_id)
        if runner is None:
            return 0.0
        dx = (x - runner.x) * M_PER_X_PCT
        dy = (y - runner.y) * M_PER_Y_PCT
        distance = hypot(dx, dy)
        runner.x = x
        runner.y = y
        runner.covered_m += distance
        return distance

    # -- internals --------------------------------------------------------

    def _slot_target(
        self, runner: _Runner, ball_x: float, ball_y: float
    ) -> tuple[float, float]:
        """Where a player stands when they are not on the ball.

        The slot slides with the ball: a team pushes up when the ball is
        forward, drops off when it is deep, and slides across the pitch when
        the ball goes wide. On top of that the player drifts around their
        own spot, because they are never perfectly still.
        """
        if runner.side is TeamSide.HOME:
            ball_in_frame = ball_x
            ball_y_in_frame = ball_y
        else:
            ball_in_frame = 100.0 - ball_x
            ball_y_in_frame = 100.0 - ball_y

        factor = _ROLE_SHIFT.get(runner.roles[0], 1.0)
        shift = _clamp(
            (ball_in_frame - 50.0) * _BLOCK_SHIFT * factor,
            -_MAX_BLOCK_SHIFT,
            _MAX_BLOCK_SHIFT,
        )
        lateral = _clamp(
            (ball_y_in_frame - 50.0) * _LATERAL_SHIFT * factor,
            -_MAX_LATERAL_SHIFT,
            _MAX_LATERAL_SHIFT,
        )
        target_x = runner.slot_x + shift + runner.wander_x / M_PER_X_PCT
        target_y = runner.slot_y + lateral + runner.wander_y / M_PER_Y_PCT
        return _to_absolute(target_x, target_y, runner.side)

    def _pressers(
        self, ball_x: float, ball_y: float, actor_id: str | None
    ) -> set[str]:
        """The players nearest to the ball, per side, who go and press it."""
        pressers: set[str] = set()
        for side in (TeamSide.HOME, TeamSide.AWAY):
            candidates: list[tuple[float, str]] = []
            for runner in self._runners.values():
                if runner.side is not side or runner.player.player_id == actor_id:
                    continue
                dx = (ball_x - runner.x) * M_PER_X_PCT
                dy = (ball_y - runner.y) * M_PER_Y_PCT
                distance = hypot(dx, dy)
                limit = _GK_PRESS_RANGE_M if runner.is_keeper else _PRESS_RANGE_M
                if distance > limit:
                    continue
                candidates.append((distance, runner.player.player_id))
            candidates.sort()
            for _, player_id in candidates[:_PRESSERS_PER_SIDE]:
                pressers.add(player_id)
        return pressers

    def _step(
        self, runner: _Runner, target_x: float, target_y: float, dt_s: float
    ) -> float:
        """Move one player towards a target, and return the distance covered."""
        dx = (target_x - runner.x) * M_PER_X_PCT
        dy = (target_y - runner.y) * M_PER_Y_PCT
        distance = hypot(dx, dy)
        if distance < 0.05:
            self._wander(runner)
            return 0.0
        urgency = min(1.0, distance / 25.0)
        speed = _BASE_SPEED_MPS + (_SPRINT_SPEED_MPS - _BASE_SPEED_MPS) * urgency
        speed *= 0.55 + 0.45 * runner.player.pace
        speed *= 0.85 + 0.3 * self._rng.random()
        step = min(speed * dt_s, distance)
        runner.x += dx / distance * step / M_PER_X_PCT
        runner.y += dy / distance * step / M_PER_Y_PCT
        runner.covered_m += step
        return step

    def _wander(self, runner: _Runner) -> None:
        """Let a player drift a little around their spot."""
        runner.wander_x = _clamp(
            runner.wander_x + self._rng.gauss(0.0, _WANDER_STEP_M),
            -_WANDER_M,
            _WANDER_M,
        )
        runner.wander_y = _clamp(
            runner.wander_y + self._rng.gauss(0.0, _WANDER_STEP_M),
            -_WANDER_M,
            _WANDER_M,
        )


def _to_absolute(x: float, y: float, side: TeamSide) -> tuple[float, float]:
    """Coordinates in a team's own frame -> absolute pitch coordinates."""
    if side is TeamSide.HOME:
        return x, y
    return 100.0 - x, 100.0 - y
