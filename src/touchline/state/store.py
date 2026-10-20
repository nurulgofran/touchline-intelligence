"""The shared match state every agent in the pipeline reads.

The store is **in-process and single-instance**: the event log lives in one
Python object, in one process, behind one lock. That is precisely why the
deployment runs exactly one replica -- there is no external coordination
layer, so a second replica would hold a second, divergent copy of the match.
Durability is a later concern (the service can replay the event stream from
the log on start-up); the live path must never block on a database.

The lock is held only for the duration of a mutation or a snapshot copy, so
readers on the WebSocket thread never see a half-written state.
"""

from __future__ import annotations

import threading
from collections import Counter
from collections.abc import Iterable

from touchline.metrics.definitions import (
    METRICS,
    POSSESSION_EVENT_TYPES,
    compute_metric,
)
from touchline.models.claims import Explanation
from touchline.models.events import EventType, MatchEvent, TeamSide
from touchline.models.insights import InsightCandidate

#: The metrics that :meth:`MatchStateStore.recompute` knows how to reproduce.
#: The registry is the single source of truth: the analyst computes its
#: numbers from it and the grounding step recomputes them from it, so a
#: mismatch is a real disagreement rather than a difference of formula.
SUPPORTED_METRICS: frozenset[str] = frozenset(METRICS)


class MatchStateStore:
    """An append-only event log with derived-state helpers."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: list[MatchEvent] = []
        self._by_id: dict[str, MatchEvent] = {}
        self._candidates: dict[str, InsightCandidate] = {}
        self._explanations: dict[str, Explanation] = {}

    # -- mutation ---------------------------------------------------------

    def append(self, event: MatchEvent) -> None:
        """Append one event to the log.

        Re-appending an event id replaces the previous entry, which keeps the
        store consistent if a source is replayed.
        """
        with self._lock:
            previous = self._by_id.get(event.event_id)
            if previous is not None:
                index = self._events.index(previous)
                self._events[index] = event
            else:
                self._events.append(event)
            self._by_id[event.event_id] = event

    def extend(self, events: list[MatchEvent]) -> None:
        """Append many events in one locked batch."""
        with self._lock:
            for event in events:
                previous = self._by_id.get(event.event_id)
                if previous is not None:
                    self._events[self._events.index(previous)] = event
                else:
                    self._events.append(event)
                self._by_id[event.event_id] = event

    def clear(self) -> None:
        """Drop every event. Used when a new match is loaded."""
        with self._lock:
            self._events.clear()
            self._by_id.clear()
            self._candidates.clear()
            self._explanations.clear()

    # -- candidates --------------------------------------------------------

    def put_candidates(self, candidates: Iterable[InsightCandidate]) -> None:
        """Record the insights the analyst found.

        The candidates are kept alongside the event log because a later stage
        sometimes holds only an id -- the grounding verdict names one, and
        the veto it carries has to be sent back to the candidate it is about.
        Without the registry that hand-off would have to carry the whole
        candidate in its payload, and the payload would grow with it.

        Args:
            candidates: The candidates to record. One with an id already
                recorded replaces the earlier one.
        """
        with self._lock:
            for candidate in candidates:
                self._candidates[candidate.candidate_id] = candidate

    def candidate(self, candidate_id: str) -> InsightCandidate | None:
        """Look one candidate up by id."""
        with self._lock:
            return self._candidates.get(candidate_id)

    @property
    def candidates(self) -> list[InsightCandidate]:
        """The recorded candidates, in the order they were recorded."""
        with self._lock:
            return list(self._candidates.values())

    # -- explanations ------------------------------------------------------

    def put_explanation(self, candidate_id: str, explanation: Explanation) -> None:
        """Record the claim the grounding step accepted.

        The verdict carries only an id, so the claim itself has to live
        somewhere the next stage can reach. It lives here, beside the
        candidate it is about, because the Narrator needs both: the
        candidate to write about and the accepted claim to write from.

        Args:
            candidate_id: The candidate the claim is about.
            explanation: The claim that was accepted.
        """
        with self._lock:
            self._explanations[candidate_id] = explanation

    def explanation(self, candidate_id: str) -> Explanation | None:
        """The claim that was accepted for one candidate, if there is one."""
        with self._lock:
            return self._explanations.get(candidate_id)

    # -- reads ------------------------------------------------------------

    @property
    def events(self) -> list[MatchEvent]:
        """A copy of the event list, in the order it was appended."""
        with self._lock:
            return list(self._events)

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)

    def get(self, event_id: str) -> MatchEvent | None:
        """Look one event up by id."""
        with self._lock:
            return self._by_id.get(event_id)

    def by_type(self, type: EventType) -> list[MatchEvent]:
        """Every event of one type."""
        with self._lock:
            return [event for event in self._events if event.type is type]

    def window(self, start_ms: int, end_ms: int) -> list[MatchEvent]:
        """Every event whose clock falls in the half-open range ``[start, end)``."""
        with self._lock:
            return [
                event
                for event in self._events
                if start_ms <= event.match_clock_ms < end_ms
            ]

    def score(self) -> tuple[int, int]:
        """The current ``(home, away)`` score.

        The score is read as the highest score carried by any event, so a
        partially replayed log still reports the right number.
        """
        with self._lock:
            if not self._events:
                return (0, 0)
            return (
                max(event.score_home for event in self._events),
                max(event.score_away for event in self._events),
            )

    # -- grounding --------------------------------------------------------

    def recompute(
        self,
        metric_name: str,
        cited_event_ids: list[str],
        side: TeamSide | None = None,
    ) -> float | None:
        """Recompute a named metric using **only** the cited events.

        This is the grounding primitive: a narrative claim states a metric and
        the ids it rests on, and the grounding step re-derives the number from
        the log. If the claim is wrong, the recomputed value will not match
        and the claim is vetoed.

        Args:
            metric_name: One of :data:`SUPPORTED_METRICS`.
            cited_event_ids: The events the claim rests on.
            side: The side the metric is about. A filtered metric restricts
                the cited events to that side; an attributed metric reports
                that side's share of them.

        Returns:
            The recomputed value, or ``None`` when any cited id is unknown to
            the log.

        Raises:
            ValueError: If ``metric_name`` is not a supported metric.
        """
        if metric_name not in SUPPORTED_METRICS:
            raise ValueError(
                f"unsupported metric {metric_name!r}; "
                f"expected one of {sorted(SUPPORTED_METRICS)}"
            )

        with self._lock:
            cited: list[MatchEvent] = []
            for event_id in cited_event_ids:
                event = self._by_id.get(event_id)
                if event is None:
                    return None
                cited.append(event)

        return compute_metric(metric_name, cited, side)

    # -- snapshots --------------------------------------------------------

    def state_snapshot(self) -> dict[str, object]:
        """A serialisable view of the derived state.

        The snapshot is what the UI polls and what the agent trace snapshots,
        so it contains only plain Python types.
        """
        with self._lock:
            events = list(self._events)
        counts = Counter(event.type.value for event in events)
        score_home, score_away = (
            (
                max(event.score_home for event in events),
                max(event.score_away for event in events),
            )
            if events
            else (0, 0)
        )
        relevant = [e for e in events if e.type in POSSESSION_EVENT_TYPES]
        possession_home = (
            100.0 * sum(1 for e in relevant if e.side is TeamSide.HOME) / len(relevant)
            if relevant
            else 0.0
        )
        speeds = [e.speed_mps for e in events if e.speed_mps is not None]
        return {
            "event_count": len(events),
            "score_home": score_home,
            "score_away": score_away,
            "counts": dict(sorted(counts.items())),
            "possession_pct": round(possession_home, 2),
            "momentum_index": 0.0,
            "momentum_index_note": (
                "Placeholder: a rolling momentum curve is computed by the "
                "deterministic analyst, not by the shared store."
            ),
            "last_clock_ms": events[-1].match_clock_ms if events else 0,
            "max_speed_mps": round(max(speeds), 2) if speeds else 0.0,
        }
