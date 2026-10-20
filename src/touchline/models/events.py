"""The event contract every other layer depends on.

A :class:`MatchEvent` is the atomic unit of the match log. It is immutable
(``frozen=True``) so that it can be shared freely between the simulator, the
state store, the metrics layer and the narrative providers without any risk of
one stage mutating another stage's view of the past.

Pitch coordinates
-----------------
``x`` and ``y`` are expressed in **percentages** of the pitch, not metres:

* ``x`` runs from ``0`` to ``100``, where ``0`` is the team's **own** goal and
  ``100`` is the **opponent's** goal. The mapping is from the perspective of
  the team that owns the event: the same physical spot therefore has a
  complementary ``x`` for the two sides (``x`` and ``100 - x``).
* ``y`` runs from ``0`` to ``100``, where ``0`` is one touchline and ``100`` is
  the opposite touchline.

The pitch itself is 105 m long and 68 m wide, so one percentage of ``x`` is
1.05 m and one percentage of ``y`` is 0.68 m. Distances in metres are always
derived from these constants rather than hard-coded, so the two views can never
drift apart.
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field

# --- Pitch geometry -------------------------------------------------------

PITCH_LENGTH_M: float = 105.0
PITCH_WIDTH_M: float = 68.0
MATCH_DURATION_MS: int = 90 * 60 * 1000  # 5_400_000
HALF_TIME_MS: int = MATCH_DURATION_MS // 2


class EventType(str, Enum):
    """Every kind of thing that can happen on the pitch."""

    KICK_OFF = "KICK_OFF"
    HALF_TIME = "HALF_TIME"
    FULL_TIME = "FULL_TIME"
    PASS = "PASS"
    SHOT = "SHOT"
    GOAL = "GOAL"
    SAVE = "SAVE"
    TACKLE = "TACKLE"
    INTERCEPTION = "INTERCEPTION"
    FOUL = "FOUL"
    CARD = "CARD"
    SUBSTITUTION = "SUBSTITUTION"
    CORNER = "CORNER"
    FREE_KICK = "FREE_KICK"
    OFFSIDE = "OFFSIDE"
    DRIBBLE = "DRIBBLE"
    CLEARANCE = "CLEARANCE"
    RECOVERY = "RECOVERY"
    BALL_OUT = "BALL_OUT"


class TeamSide(str, Enum):
    """Which of the two teams an event belongs to."""

    HOME = "HOME"
    AWAY = "AWAY"


class CardType(str, Enum):
    """Kind of disciplinary card."""

    YELLOW = "YELLOW"
    RED = "RED"


class MatchEvent(BaseModel):
    """One immutable entry in the match log."""

    model_config = ConfigDict(frozen=True)

    event_id: str = Field(
        description="Stable identifier, e.g. 'ev-000123'. Unique within a match."
    )
    type: EventType
    match_clock_ms: int = Field(
        ge=0, le=MATCH_DURATION_MS, description="Milliseconds since kick-off."
    )
    period: int = Field(ge=1, le=2, description="1 = first half, 2 = second half.")
    side: TeamSide | None = None
    player_id: str | None = None
    player_name: str | None = None
    target_player_id: str | None = None
    target_player_name: str | None = None
    x: float = Field(
        ge=0.0,
        le=100.0,
        description="Ball/actor x in percent: 0 = own goal, 100 = opponent goal.",
    )
    y: float = Field(
        ge=0.0,
        le=100.0,
        description="Ball/actor y in percent: 0 = one touchline, 100 = the other.",
    )
    end_x: float | None = Field(
        default=None, ge=0.0, le=100.0, description="Destination x for passes/carry."
    )
    end_y: float | None = Field(
        default=None, ge=0.0, le=100.0, description="Destination y for passes/carry."
    )
    outcome: str | None = Field(
        default=None,
        description=(
            "One of 'completed', 'intercepted', 'blocked', 'out', 'saved', "
            "'off_target', 'goal', 'won', 'lost' or 'foul'."
        ),
    )
    value: float | None = Field(
        default=None, description="Generic magnitude, e.g. shot xG or pass quality."
    )
    distance_m: float | None = Field(
        default=None, description="Pass or carry distance in metres."
    )
    difficulty: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Normalised difficulty, 0 (easy) to 1.",
    )
    speed_mps: float | None = Field(
        default=None, description="Ball or shot speed in metres per second."
    )
    card: CardType | None = None
    score_home: int = Field(ge=0, description="Home score after this event.")
    score_away: int = Field(ge=0, description="Away score after this event.")
    metadata: dict[str, float | int | str | bool] = Field(
        default_factory=dict, description="Free-form, JSON-serialisable extras."
    )

    def to_json_line(self) -> str:
        """Serialise to a single JSON line (no trailing newline)."""
        payload = self.model_dump(mode="json")
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json_line(cls, line: str) -> Self:
        """Parse one JSON line back into a :class:`MatchEvent`."""
        return cls.model_validate(json.loads(line))
