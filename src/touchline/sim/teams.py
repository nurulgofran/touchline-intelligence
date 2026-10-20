"""Fictional clubs and deterministically generated squads.

Everything in this module is invented. The club names come from a fixed
catalogue of fictional clubs, and the players are generated from invented
syllable pools so that no real footballer can appear in the output. Nothing
here reads from the network or from any external data source.
"""

from __future__ import annotations

import random
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

#: The complete catalogue of fictional clubs. The simulator picks two of them
#: per seed; no other club name may ever appear in the project.
FICTIONAL_CLUB_NAMES: Final[tuple[str, ...]] = (
    "Northgate FC",
    "Riverside United",
    "Kingsmoor Rovers",
    "Ashfield Town",
    "Dovercrest Athletic",
    "Westmere City",
)

#: Muted, generic RGB triples (0..1 per channel). Plain colours only -- no
#: crests, no badges, and no attempt to reproduce anyone's brand palette.
CLUB_COLOURS: Final[dict[str, tuple[tuple[int, int, int], tuple[int, int, int]]]] = {
    "Northgate FC": ((28, 62, 118), (222, 226, 232)),
    "Riverside United": ((17, 92, 74), (236, 238, 232)),
    "Kingsmoor Rovers": ((112, 34, 58), (238, 232, 226)),
    "Ashfield Town": ((58, 58, 64), (240, 240, 240)),
    "Dovercrest Athletic": ((36, 84, 132), (244, 238, 214)),
    "Westmere City": ((126, 74, 26), (240, 236, 228)),
}

# --- Invented name pools ---------------------------------------------------
# Heads and tails are combined into names that do not correspond to any real
# person. The pools are deliberately common-sounding but non-specific.

_FIRST_HEADS: Final[tuple[str, ...]] = (
    "Al",
    "Bel",
    "Bram",
    "Cas",
    "Cor",
    "Dun",
    "El",
    "Fen",
    "Gar",
    "Hal",
    "Ivo",
    "Jor",
    "Kes",
    "Lor",
    "Mar",
    "Nev",
    "Os",
    "Pel",
    "Quen",
    "Ryn",
    "Sor",
    "Tal",
    "Ulf",
    "Vor",
    "Wen",
    "Xan",
    "Yor",
    "Zev",
)

_FIRST_TAILS: Final[tuple[str, ...]] = (
    "",
    "an",
    "ar",
    "el",
    "en",
    "ick",
    "in",
    "is",
    "ley",
    "or",
    "us",
)

_SURNAME_HEADS: Final[tuple[str, ...]] = (
    "Alder",
    "Brack",
    "Corvin",
    "Duns",
    "Ellory",
    "Fenmar",
    "Garrow",
    "Halv",
    "Ivers",
    "Jarrow",
    "Kester",
    "Lowmar",
    "Mabry",
    "Norre",
    "Orbin",
    "Pell",
    "Quorn",
    "Ravell",
    "Strand",
    "Tull",
    "Umber",
    "Vexley",
    "Wend",
    "Yarrow",
    "Zorel",
)

_SURNAME_TAILS: Final[tuple[str, ...]] = (
    "brook",
    "burn",
    "by",
    "cliff",
    "combe",
    "den",
    "dene",
    "field",
    "gate",
    "holt",
    "hurst",
    "ley",
    "marsh",
    "mer",
    "ner",
    "port",
    "ridge",
    "shaw",
    "ston",
    "thorpe",
    "vale",
    "wick",
    "wood",
    "worth",
)

_POSITIONS: Final[tuple[str, ...]] = ("GK", "DEF", "MID", "FWD")


class Player(BaseModel):
    """One fictional player."""

    model_config = ConfigDict(frozen=True)

    player_id: str
    name: str
    number: int = Field(ge=1, le=99)
    position: str = Field(description="One of 'GK', 'DEF', 'MID' or 'FWD'.")
    pace: float = Field(ge=0.0, le=1.0)
    stamina: float = Field(ge=0.0, le=1.0)
    technical: float = Field(ge=0.0, le=1.0)
    home_club: bool


class Club(BaseModel):
    """One fictional club and its matchday squad."""

    model_config = ConfigDict(frozen=True)

    club_id: str
    name: str
    short_name: str = Field(description="Three-letter code used in the UI.")
    primary_colour: tuple[int, int, int] = Field(
        description="Plain RGB triple, 0..255."
    )
    secondary_colour: tuple[int, int, int] = Field(
        description="Plain RGB triple, 0..255."
    )
    squad: list[Player]

    def starters(self) -> list[Player]:
        """The eleven players who start the match."""
        return [p for p in self.squad if p.number <= 11]

    def substitutes(self) -> list[Player]:
        """The five players on the bench."""
        return [p for p in self.squad if p.number > 11]

    def by_position(self, position: str) -> list[Player]:
        """Every player in the given position."""
        return [p for p in self.squad if p.position == position]

    def player(self, player_id: str) -> Player | None:
        """Look a player up by id."""
        for p in self.squad:
            if p.player_id == player_id:
                return p
        return None


def _slug(name: str) -> str:
    """Lowercase, dash-separated slug of a club name."""
    return name.lower().replace(" ", "-")


def _make_name(rng: random.Random, used: set[str]) -> str:
    """Draw an invented full name that has not been used yet."""
    for _ in range(200):
        first = rng.choice(_FIRST_HEADS) + rng.choice(_FIRST_TAILS)
        last = rng.choice(_SURNAME_HEADS) + rng.choice(_SURNAME_TAILS)
        name = f"{first} {last}"
        if name not in used:
            used.add(name)
            return name
    # Deterministic fallback: never reached in practice, but keeps the function
    # total rather than raising mid-simulation.
    fallback = f"Player {len(used)}"
    used.add(fallback)
    return fallback


def _attribute(
    rng: random.Random, position: str, attribute: str
) -> float:
    """Draw a 0..1 attribute, nudged by the position's usual demands."""
    base = rng.uniform(0.42, 0.94)
    if position == "GK":
        base += {"pace": -0.12, "stamina": 0.0, "technical": 0.04}[attribute]
    elif position == "DEF":
        base += {"pace": -0.02, "stamina": 0.06, "technical": -0.02}[attribute]
    elif position == "MID":
        base += {"pace": 0.0, "stamina": 0.04, "technical": 0.05}[attribute]
    else:  # FWD
        base += {"pace": 0.08, "stamina": -0.04, "technical": 0.0}[attribute]
    return round(min(1.0, max(0.05, base)), 3)


def _build_squad(
    rng: random.Random, club: str, home_club: bool
) -> list[Player]:
    """Build the 11 starters and 5 substitutes of one club."""
    used_names: set[str] = set()
    slug = _slug(club)
    squad: list[Player] = []

    # number -> position for the starting eleven.
    starting_positions: list[str] = [
        "GK",
        "DEF",
        "DEF",
        "DEF",
        "DEF",
        "MID",
        "MID",
        "MID",
        "FWD",
        "FWD",
        "FWD",
    ]
    # number -> position for the bench.
    bench_positions: list[str] = ["GK", "DEF", "MID", "MID", "FWD"]

    for number, position in enumerate(starting_positions, start=1):
        name = _make_name(rng, used_names)
        squad.append(
            Player(
                player_id=f"{slug}-{number:02d}",
                name=name,
                number=number,
                position=position,
                pace=_attribute(rng, position, "pace"),
                stamina=_attribute(rng, position, "stamina"),
                technical=_attribute(rng, position, "technical"),
                home_club=home_club,
            )
        )

    for offset, position in enumerate(bench_positions, start=12):
        name = _make_name(rng, used_names)
        squad.append(
            Player(
                player_id=f"{slug}-{offset:02d}",
                name=name,
                number=offset,
                position=position,
                pace=_attribute(rng, position, "pace"),
                stamina=_attribute(rng, position, "stamina"),
                technical=_attribute(rng, position, "technical"),
                home_club=home_club,
            )
        )

    return squad


def build_teams(seed: int) -> tuple[Club, Club]:
    """Build the two fictional clubs that play the synthetic match.

    Args:
        seed: Any integer. The same seed always yields the same two clubs
            with the same squads.

    Returns:
        A ``(home, away)`` tuple of :class:`Club`.
    """
    rng = random.Random(seed * 7919 + 13)
    home_name, away_name = rng.sample(FICTIONAL_CLUB_NAMES, 2)

    home = Club(
        club_id=f"clb-{_slug(home_name)}",
        name=home_name,
        short_name=_short_name(home_name),
        primary_colour=CLUB_COLOURS[home_name][0],
        secondary_colour=CLUB_COLOURS[home_name][1],
        squad=_build_squad(rng, home_name, home_club=True),
    )
    away = Club(
        club_id=f"clb-{_slug(away_name)}",
        name=away_name,
        short_name=_short_name(away_name),
        primary_colour=CLUB_COLOURS[away_name][0],
        secondary_colour=CLUB_COLOURS[away_name][1],
        squad=_build_squad(rng, away_name, home_club=False),
    )
    return home, away


def _short_name(club_name: str) -> str:
    """Derive a three-letter code from a club name."""
    words = [w for w in club_name.replace(".", " ").split() if w]
    letters = "".join(word[0] for word in words)
    if len(letters) < 3:
        # Top up from the first word so every code is exactly three letters.
        letters += words[0][1 : 1 + (3 - len(letters))]
    return letters[:3].upper()
