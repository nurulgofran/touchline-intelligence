"""Who the match is being explained to.

An insight is a fact about the match. The **audience** decides how that fact
is put on screen: which numbers are shown, whose numbers they are, and what
register the words around them are written in. Keeping the two apart is the
point -- the analyst's claim is checked once, against the event log, and the
audience only changes the *presentation* of it.

Five audiences are supported, and they are genuinely different experiences
rather than five ways of saying the same sentence:

``analyst``
    The deviation and the numbers behind it. The default register.
``fan``
    What just happened, in the register of somebody watching.
``player``
    One player: the overlay counts *that player's* actions inside the
    window the insight was computed over.
``my_team``
    One side: the overlay carries *that side's* numbers over exactly the
    same cited events, and says so when the side was not involved.
``metric_lens``
    One metric: the overlay shows *only* that metric, recomputed from the
    same cited events, so the feed never empties.

The audience is a property of the **analysis**, not of the viewer: one
engine, one match, one audience at a time. Changing it re-runs the
Personaliser alone -- the Explainer and the Narrator are not asked again --
so a mode change costs one pass, not three.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from touchline.models.events import TeamSide

__all__ = ["Audience", "AudienceMode"]


class AudienceMode(str, Enum):
    """The audiences the Personaliser can write for."""

    ANALYST = "analyst"
    """The deviation and the numbers behind it."""

    FAN = "fan"
    """What just happened, for somebody watching."""

    PLAYER = "player"
    """One player: their own actions inside the window."""

    MY_TEAM = "my_team"
    """One side: that side's numbers over the same cited events."""

    METRIC_LENS = "metric_lens"
    """One metric: only that metric, recomputed from the same events."""


class Audience(BaseModel):
    """The audience the overlays are written for.

    Attributes:
        mode: Which of the five audiences this is.
        locale: The language the prose is written in.
        side: Whose side, for the ``my_team`` audience. Defaults to the side
            the insight is about.
        player_id: Which player, for the ``player`` audience.
        player_name: That player's name, for the overlay's headline. The
            name is carried with the audience so the headline can be
            written without another lookup.
        metric: Which metric, for the ``metric_lens`` audience.
    """

    model_config = ConfigDict(frozen=True)

    mode: AudienceMode = Field(default=AudienceMode.ANALYST)
    locale: str = Field(default="en")
    side: TeamSide | None = Field(default=None)
    player_id: str | None = Field(default=None)
    player_name: str | None = Field(default=None)
    metric: str | None = Field(default=None)

    @property
    def key(self) -> str:
        """A stable identity for this audience.

        Two audiences with the same key produce the same overlays, so the
        key is what the engine caches on and what the studio sends back.
        """
        return ":".join(
            [
                self.mode.value,
                self.locale,
                self.side.value if self.side else "-",
                self.player_id or "-",
                self.metric or "-",
            ]
        )

    def describe(self) -> dict[str, str | None]:
        """The audience in the shape the studio's pickers read."""
        return {
            "mode": self.mode.value,
            "locale": self.locale,
            "side": self.side.value if self.side else None,
            "player_id": self.player_id,
            "player_name": self.player_name,
            "metric": self.metric,
        }
