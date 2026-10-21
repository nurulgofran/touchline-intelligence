"""Deterministic validators for the comparative evaluation.

These checks run locally, need no model, and are the SAME checks applied
to both approaches (the multi-agent pipeline and the single-agent baseline).

1. Citation validity: every cited event id exists in the event log.
2. Metric recomputation: every claimed metric recomputes from the cited
   events via the project's own metric registry, with the same tolerance
   the Grounding step uses (percentages and metres tolerate rounding;
   everything else compares near-exactly).
3. Numeric-prose consistency: numbers written in the prose (reading, title,
   body) are matched against the claimed metric values. This one is a
   heuristic -- see the notes in ``check_numeric_prose`` -- and every
   unmatched number is reported with its context so a human can review it.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Sequence

__all__ = [
    "ProseIssue",
    "ValidationResult",
    "check_citations",
    "check_metrics",
    "check_numeric_prose",
    "metric_close",
    "validate_overlay",
]


# ---------------------------------------------------------------------------
# Tolerance: identical to touchline.orchestration.grounding._tolerance/_close.
# ---------------------------------------------------------------------------


def _tolerance(unit: str, value: float) -> float:
    """How far a claimed value may sit from the recomputed one."""
    if unit in ("%", "m"):
        return 0.5
    return 0.01


def metric_close(claimed: float, recomputed: float, unit: str) -> bool:
    """Whether a claimed value is the recomputed one, near enough."""
    return math.isclose(
        claimed, recomputed, rel_tol=1e-3, abs_tol=_tolerance(unit, recomputed)
    )


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass
class ProseIssue:
    """A number in the prose that matched no claimed metric."""

    text: str
    context: str


@dataclass
class ValidationResult:
    """The outcome of validating one published output."""

    unknown_citations: list[str] = field(default_factory=list)
    metric_failures: list[str] = field(default_factory=list)
    prose_issues: list[ProseIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Whether the published output passed the decisive checks.

        Numeric-prose consistency is advisory and does not decide ``ok``:
        see :func:`validate_overlay`.
        """
        return not self.unknown_citations and not self.metric_failures

    def summary(self) -> str:
        parts = []
        if self.unknown_citations:
            parts.append(f"unknown citations: {self.unknown_citations}")
        if self.metric_failures:
            parts.append(f"metric failures: {self.metric_failures}")
        if self.prose_issues:
            parts.append(
                "prose advisories: "
                + ", ".join(repr(issue.text) for issue in self.prose_issues)
            )
        return "; ".join(parts) if parts else "all checks passed"


# ---------------------------------------------------------------------------
# 1. Citation validity
# ---------------------------------------------------------------------------


def check_citations(
    cited_event_ids: Sequence[str],
    known_event_ids: set[str],
) -> list[str]:
    """Return the cited ids that are not in the event log."""
    return [event_id for event_id in cited_event_ids if event_id not in known_event_ids]


# ---------------------------------------------------------------------------
# 2. Metric recomputation
# ---------------------------------------------------------------------------


def check_metrics(
    metrics: Sequence[Any],
    cited_events: Sequence[Any],
) -> list[str]:
    """Recompute every metric from the cited events and compare.

    Args:
        metrics: Objects with ``name``, ``value``, ``unit`` and ``side``
            attributes (``MetricValue`` or ``ClaimedMetric``).
        cited_events: The events the citations resolve to. A citation that
            does not resolve is the citation check's failure, not this
            one's -- but a metric that cannot be recomputed at all is still
            reported here.

    Returns:
        A list of human-readable failure strings, empty when everything
        reproduces.
    """
    from touchline.metrics.definitions import compute_metric

    failures: list[str] = []
    for metric in metrics:
        recomputed = compute_metric(metric.name, cited_events, metric.side)
        if recomputed is None:
            failures.append(
                f"{metric.name}: does not recompute from the cited events"
            )
            continue
        if not metric_close(metric.value, recomputed, metric.unit or ""):
            failures.append(
                f"{metric.name}: claimed {metric.value:g}, "
                f"recomputed {recomputed:g}"
            )
    return failures


# ---------------------------------------------------------------------------
# 3. Numeric-prose consistency
# ---------------------------------------------------------------------------

_NUMBER = re.compile(r"(?<!\w)(\d{1,4}(?:[.,]\d{1,3})?)(?!\w)")

#: Suffixes that mark an ordinal or a clock reading rather than a metric.
_SKIP_SUFFIX = re.compile(
    r"^(st|nd|rd|th|'|′|’|min|mins|minute|minutes|minuten?|"
    r"\.\s*(?:minute|minuten?|min)|মিনিটে?)\b",
    re.I,
)


def _parse_number(raw: str, locale: str) -> float:
    """Read a prose number, honouring the locale's separators.

    In German prose "3.020" is three thousand and twenty and "32,6" is
    thirty-two point six; in English it is the other way round. Bengali
    prose in this project uses the English conventions.
    """
    if locale == "de":
        if re.fullmatch(r"\d{1,3}\.\d{3}", raw):
            return float(raw.replace(".", ""))
        return float(raw.replace(",", "."))
    return float(raw.replace(",", ""))


def _decimals(raw: str, locale: str) -> int:
    """How many decimals the prose figure was written with."""
    if locale == "de":
        if re.fullmatch(r"\d{1,3}\.\d{3}", raw):
            return 0
        tail = raw.split(",")[-1] if "," in raw else ""
    else:
        tail = raw.split(".")[-1] if "." in raw else ""
    return len(tail) if tail.isdigit() else 0


def check_numeric_prose(
    prose_parts: Sequence[str],
    metrics: Sequence[Any],
    *,
    locale: str = "en",
) -> list[ProseIssue]:
    """Match the numbers in the prose against the claimed metric values.

    The rule is deliberately narrow, and its limits are stated rather than
    hidden:

    - Years (19xx, 20xx), ordinals (1st, 2nd, 3rd, German "28."), minute
      marks (65', 65th minute, Bengali "41 মিনিটে"), scorelines (2-1) and
      "first/second half" phrasing are skipped: they are not metric claims.
    - Numbers are parsed with the prose locale's separators (German
      "3.020" is 3020, "32,6" is 32.6).
    - A bare number matches when it equals any claimed metric value under
      the metric tolerance for that metric's unit, OR when it is a fair
      rounding of a claimed value (within half of its last written digit).
      A number followed by a percent sign or a metres marker is only
      compared against metrics with a compatible unit.
    - Anything else is reported as an issue with its context, for a human
      to review. The check is advisory: it can raise false positives on
      prose that states counts ("0 Tore") or derived figures ("32.6 m/s")
      the metric list does not carry, but it does not silently bless a
      prose figure the log contradicts.
    """
    issues: list[ProseIssue] = []
    metric_values = [
        (float(metric.value), metric.unit or "", metric.name) for metric in metrics
    ]
    for part in prose_parts:
        for match in _NUMBER.finditer(part):
            raw = match.group(1)
            after = part[match.end() : match.end() + 12]
            before = part[max(0, match.start() - 8) : match.start()]
            if _SKIP_SUFFIX.match(after.strip()):
                continue
            if re.match(r"^\d{4}$", raw) and raw[:2] in ("19", "20"):
                continue
            if "-" in before[-2:] or after.startswith("-"):
                # Scoreline-like "2-1": a hyphen-adjacent pair is a score,
                # not a metric claim.
                continue
            value = _parse_number(raw, locale)
            decimals = _decimals(raw, locale)
            unit_hint = after.strip().lower()
            percent = (
                unit_hint.startswith("%")
                or "per cent" in unit_hint
                or "percent" in unit_hint
                or "prozent" in unit_hint
            )
            metres = (
                re.match(r"^m\b", unit_hint) and not unit_hint.startswith("min")
            ) or "metre" in unit_hint or "meter" in unit_hint
            matched = False
            for claimed, unit, name in metric_values:
                if percent and unit != "%":
                    continue
                if metres and unit != "m":
                    continue
                if metric_close(value, claimed, unit):
                    matched = True
                    break
                # A fair rounding: the prose may write "0.4" for 0.42.
                if abs(value - claimed) <= 0.5 * 10**-decimals + 1e-9:
                    matched = True
                    break
            if not matched:
                context = f"...{before.strip()[-24:]} [{raw}] {after.strip()[:24]}..."
                issues.append(ProseIssue(text=raw, context=context))
    return issues


# ---------------------------------------------------------------------------
# One published overlay, all three checks
# ---------------------------------------------------------------------------


def validate_overlay(
    overlay: Any,
    events_by_id: dict[str, Any],
) -> ValidationResult:
    """Run all three independent checks on one published overlay."""
def validate_overlay(
    overlay: Any,
    events_by_id: dict[str, Any],
    *,
    locale: str = "en",
) -> ValidationResult:
    """Run all three independent checks on one published overlay.

    Citation validity and metric recomputation are the decisive checks:
    they decide ``result.ok``. Numeric-prose consistency is advisory -- it
    is reported with full context for human review, because prose legitimately
    rounds figures and states counts the metric list does not carry.
    """
    result = ValidationResult()
    result.unknown_citations = check_citations(
        list(overlay.cited_event_ids), set(events_by_id)
    )
    cited_events = [
        events_by_id[event_id]
        for event_id in overlay.cited_event_ids
        if event_id in events_by_id
    ]
    result.metric_failures = check_metrics(list(overlay.metrics), cited_events)
    prose = [overlay.title, overlay.body]
    reading = getattr(overlay, "reading", "")
    if reading:
        prose.append(reading)
    result.prose_issues = check_numeric_prose(
        prose, list(overlay.metrics), locale=locale
    )
    return result
