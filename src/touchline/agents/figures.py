"""The figure guard: no number on screen that the event log does not carry.

The grounding step checks the *claim* -- the structured metrics, the sides
they describe and the events they were computed from. What it cannot check is
the prose around them. A model can be handed a correct claim and still write
"his pass accuracy collapsed to 66 per cent" when the claim says 84, and
that sentence would reach the screen with a perfectly good citation
attached to it, because the citation belongs to the claim and not to the
sentence.

So the prose gets its own check, and it is a narrow one: **every figure in
the prose has to be a figure the candidate already carries.** The allowed
figures are read out of the deterministic rendering of the candidate -- the
facts sentence and the title -- so the allowed set is exactly "the numbers
that are on screen anyway". A figure that is not in that set did not come
from the event log, and it does not go on the screen.

The tolerance is the same half-a-unit the grounding step uses. A model that
writes ``84`` for ``83.7142857`` has not invented anything; a model that
writes ``66`` has.

This is deliberately not a spell-checker and not a fact-checker. It does not
know whether "the press was broken" is true. It only knows that the number
in the sentence is one the analyst computed.
"""

from __future__ import annotations

import math
import re

from touchline.models.insights import InsightCandidate
from touchline.providers import template

__all__ = [
    "allowed_figures",
    "check_figures",
    "unsupported_figures",
]

#: A figure in running text: ``12``, ``12.3``, ``1,200``.
_FIGURE = re.compile(r"\d+(?:[.,]\d+)?")

#: How far a figure may sit from one the candidate carries and still be
#: "the same number said more roughly". The same half-unit the grounding
#: step allows for a rounded percentage or a rounded distance.
_TOLERANCE = 0.5


def _figures_in(text: str) -> list[float]:
    """Every figure in a piece of text."""
    found: list[float] = []
    for match in _FIGURE.finditer(text or ""):
        raw = match.group(0).replace(",", "")
        try:
            found.append(float(raw))
        except ValueError:
            # A digit in a script this parser cannot read as a number --
            # Bengali numerals, say -- is not a figure it can judge.
            continue
    return found


def allowed_figures(candidate: InsightCandidate, *, locale: str = "en") -> list[float]:
    """The figures the candidate itself puts on screen.

    They are read out of the deterministic rendering rather than out of the
    candidate's raw metric values, because the rendering is what rounds: the
    screen shows ``84%``, not ``83.71428571428571``, and the model is
    allowed to write the number that is actually on the screen.

    Args:
        candidate: The deterministic, fully-cited insight.
        locale: The language the overlay is written in.

    Returns:
        The figures a piece of prose may contain.
    """
    allowed: list[float] = []
    for text in (
        template.render_facts(candidate, locale=locale),
        template.render_title(candidate, locale=locale),
    ):
        allowed.extend(_figures_in(text))
    return allowed


def _is_supported(figure: float, allowed: list[float]) -> bool:
    """Whether one figure is one the candidate carries."""
    return any(
        math.isclose(figure, other, rel_tol=1e-3, abs_tol=_TOLERANCE)
        for other in allowed
    )


def unsupported_figures(text: str, allowed: list[float]) -> list[float]:
    """The figures in a piece of text that the candidate does not carry.

    Args:
        text: The text to check.
        allowed: The figures the candidate carries.

    Returns:
        Every figure that is not supported, in the order it appears.
    """
    return [
        figure for figure in _figures_in(text) if not _is_supported(figure, allowed)
    ]


def check_figures(
    text: str, candidate: InsightCandidate, *, locale: str = "en"
) -> list[float]:
    """The figures in a piece of text that the candidate does not carry.

    Args:
        text: The text to check.
        candidate: The deterministic, fully-cited insight.
        locale: The language the overlay is written in.

    Returns:
        Every figure that is not supported, in the order it appears.
    """
    return unsupported_figures(text, allowed_figures(candidate, locale=locale))
