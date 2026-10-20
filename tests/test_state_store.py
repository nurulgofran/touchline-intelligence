"""Tests for the shared match state store and its grounding primitive."""

from __future__ import annotations

import threading

import pytest

from touchline.models.events import CardType, EventType, MatchEvent, TeamSide
from touchline.state.store import MatchStateStore


def make_event(
    event_id: str,
    event_type: EventType,
    clock: int,
    *,
    side: TeamSide | None = TeamSide.HOME,
    outcome: str | None = None,
    distance_m: float | None = None,
    speed_mps: float | None = None,
    score_home: int = 0,
    score_away: int = 0,
) -> MatchEvent:
    """Build a hand-made event for the store tests."""
    return MatchEvent(
        event_id=event_id,
        type=event_type,
        match_clock_ms=clock,
        period=1,
        side=side,
        x=50.0,
        y=50.0,
        outcome=outcome,
        distance_m=distance_m,
        speed_mps=speed_mps,
        card=CardType.YELLOW if event_type is EventType.CARD else None,
        score_home=score_home,
        score_away=score_away,
    )


@pytest.fixture()
def events() -> list[MatchEvent]:
    """A small hand-built match."""
    return [
        make_event("ev-000001", EventType.KICK_OFF, 0, side=None),
        make_event(
            "ev-000002", EventType.PASS, 1_000, outcome="completed", distance_m=12.0
        ),
        make_event(
            "ev-000003",
            EventType.PASS,
            2_000,
            side=TeamSide.AWAY,
            outcome="completed",
            distance_m=20.0,
        ),
        make_event(
            "ev-000004", EventType.PASS, 3_000, outcome="intercepted", distance_m=8.0
        ),
        make_event(
            "ev-000005", EventType.SHOT, 4_000, outcome="saved", speed_mps=28.0
        ),
        make_event("ev-000006", EventType.TACKLE, 5_000, side=TeamSide.AWAY),
        make_event("ev-000007", EventType.GOAL, 6_000, score_home=1),
        make_event(
            "ev-000008", EventType.SAVE, 7_000, side=TeamSide.AWAY, speed_mps=30.0
        ),
        make_event(
            "ev-000009",
            EventType.PASS,
            8_000,
            side=TeamSide.AWAY,
            outcome="completed",
            distance_m=10.0,
            speed_mps=15.0,
        ),
    ]


@pytest.fixture()
def store(events: list[MatchEvent]) -> MatchStateStore:
    """A store pre-loaded with the hand-built match."""
    state = MatchStateStore()
    state.extend(events)
    return state


def test_append_and_get(store: MatchStateStore, events: list[MatchEvent]) -> None:
    assert len(store.events) == len(events)
    assert store.get("ev-000003") == events[2]
    assert store.get("ev-does-not-exist") is None


def test_append_replaces_an_existing_id(store: MatchStateStore) -> None:
    replacement = make_event("ev-000002", EventType.PASS, 1_500, outcome="blocked")
    store.append(replacement)
    assert len(store.events) == 9
    assert store.get("ev-000002") == replacement


def test_by_type(store: MatchStateStore) -> None:
    passes = store.by_type(EventType.PASS)
    assert len(passes) == 4
    assert all(event.type is EventType.PASS for event in passes)
    assert store.by_type(EventType.OFFSIDE) == []


def test_window_is_half_open(store: MatchStateStore) -> None:
    window = store.window(2_000, 5_000)
    assert [event.event_id for event in window] == [
        "ev-000003",
        "ev-000004",
        "ev-000005",
    ]


def test_score(store: MatchStateStore) -> None:
    assert store.score() == (1, 0)
    assert MatchStateStore().score() == (0, 0)


def test_recompute_counts(store: MatchStateStore) -> None:
    passes = ["ev-000002", "ev-000003", "ev-000004", "ev-000009"]
    assert store.recompute("passes_completed", passes) == 3.0
    assert store.recompute("shots", ["ev-000005"]) == 1.0
    assert store.recompute("goals", ["ev-000007"]) == 1.0
    assert store.recompute("tackles", ["ev-000006"]) == 1.0
    assert store.recompute("saves", ["ev-000008"]) == 1.0


def test_recompute_pass_accuracy_matches_the_hand_computed_fraction(
    store: MatchStateStore,
) -> None:
    passes = ["ev-000002", "ev-000003", "ev-000004", "ev-000009"]
    # Three of the four cited passes were completed.
    assert store.recompute("pass_accuracy", passes) == pytest.approx(3 / 4)


def test_recompute_pass_accuracy_with_no_passes(store: MatchStateStore) -> None:
    assert store.recompute("pass_accuracy", ["ev-000006"]) == 0.0


def test_recompute_possession_percentage(store: MatchStateStore) -> None:
    cited = ["ev-000002", "ev-000003", "ev-000004", "ev-000009"]
    # Two of the four possession events belong to the home side.
    assert store.recompute("possession_pct", cited) == pytest.approx(50.0)


def test_recompute_max_speed_and_distance(store: MatchStateStore) -> None:
    cited = ["ev-000002", "ev-000005", "ev-000008", "ev-000009"]
    assert store.recompute("max_speed_mps", cited) == 30.0
    assert store.recompute("total_distance_m", cited) == pytest.approx(22.0)


def test_recompute_returns_none_for_an_unknown_event_id(
    store: MatchStateStore,
) -> None:
    assert store.recompute("passes_completed", ["ev-000002", "ev-nope"]) is None


def test_recompute_rejects_an_unknown_metric(store: MatchStateStore) -> None:
    with pytest.raises(ValueError):
        store.recompute("passes_made_up", ["ev-000002"])


def test_state_snapshot_is_serialisable(store: MatchStateStore) -> None:
    snapshot = store.state_snapshot()
    assert snapshot["event_count"] == 9
    assert snapshot["score_home"] == 1
    assert snapshot["score_away"] == 0
    assert snapshot["counts"]["PASS"] == 4
    assert snapshot["counts"]["GOAL"] == 1
    assert "momentum_index" in snapshot
    assert 0.0 <= float(snapshot["possession_pct"]) <= 100.0
    import json

    json.dumps(snapshot)


def test_clear(store: MatchStateStore) -> None:
    store.clear()
    assert store.events == []
    assert store.get("ev-000002") is None


def test_concurrent_appends_are_safe() -> None:
    state = MatchStateStore()
    barrier = threading.Barrier(4)

    def worker(index: int) -> None:
        barrier.wait()
        for step in range(50):
            state.append(
                make_event(f"ev-{index}-{step:04d}", EventType.PASS, step)
            )

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(state.events) == 200
    assert len({event.event_id for event in state.events}) == 200
