"""Tests for the seeded synthetic match simulator."""

from __future__ import annotations

import itertools
from collections import Counter

import pytest

from touchline.models.events import (
    HALF_TIME_MS,
    MATCH_DURATION_MS,
    EventType,
    MatchEvent,
    TeamSide,
)
from touchline.sim.generator import simulate, write_jsonl
from touchline.sim.teams import FICTIONAL_CLUB_NAMES, build_teams

MIN_EVENTS = 700
MAX_EVENTS = 1200


@pytest.fixture(scope="module")
def match() -> list[MatchEvent]:
    """A single simulated match, reused across the tests in this module."""
    return simulate(42)


def test_match_length_is_in_range(match: list[MatchEvent]) -> None:
    assert MIN_EVENTS <= len(match) <= MAX_EVENTS


def test_event_ids_are_unique_and_well_formed(match: list[MatchEvent]) -> None:
    ids = [event.event_id for event in match]
    assert len(set(ids)) == len(ids)
    assert ids[0] == "ev-000001"
    assert all(event_id.startswith("ev-") for event_id in ids)
    assert all(len(event_id) == len("ev-000000") for event_id in ids)


def test_clock_is_strictly_increasing_and_bounded(match: list[MatchEvent]) -> None:
    clocks = [event.match_clock_ms for event in match]
    assert all(0 <= clock <= MATCH_DURATION_MS for clock in clocks)
    assert all(later > earlier for earlier, later in itertools.pairwise(clocks))


def test_periods_follow_the_clock(match: list[MatchEvent]) -> None:
    for event in match:
        # The half-time marker itself belongs to the first half.
        expected = 1 if event.match_clock_ms <= HALF_TIME_MS else 2
        assert event.period == expected


def test_score_is_consistent_with_goal_events(match: list[MatchEvent]) -> None:
    goals = Counter(
        event.side for event in match if event.type is EventType.GOAL
    )
    assert goals[TeamSide.HOME] == match[-1].score_home
    assert goals[TeamSide.AWAY] == match[-1].score_away

    home_goals = 0
    away_goals = 0
    for event in match:
        if event.type is EventType.GOAL:
            if event.side is TeamSide.HOME:
                home_goals += 1
            else:
                away_goals += 1
        assert event.score_home == home_goals
        assert event.score_away == away_goals


def test_score_never_decreases(match: list[MatchEvent]) -> None:
    for earlier, later in itertools.pairwise(match):
        assert later.score_home >= earlier.score_home
        assert later.score_away >= earlier.score_away


def test_goals_are_between_zero_and_five(match: list[MatchEvent]) -> None:
    goals = sum(1 for event in match if event.type is EventType.GOAL)
    assert 0 <= goals <= 5


def test_marker_events_are_present(match: list[MatchEvent]) -> None:
    types = {event.type for event in match}
    assert EventType.KICK_OFF in types
    assert EventType.HALF_TIME in types
    assert EventType.FULL_TIME in types

    kick_off = next(e for e in match if e.type is EventType.KICK_OFF)
    half_time = next(e for e in match if e.type is EventType.HALF_TIME)
    full_time = next(e for e in match if e.type is EventType.FULL_TIME)
    assert kick_off.match_clock_ms == 0
    assert half_time.match_clock_ms == HALF_TIME_MS
    assert full_time.match_clock_ms == MATCH_DURATION_MS


def test_every_goal_follows_a_shot(match: list[MatchEvent]) -> None:
    for index, event in enumerate(match):
        if event.type is not EventType.GOAL:
            continue
        previous = match[index - 1]
        assert previous.type is EventType.SHOT
        assert previous.outcome == "goal"
        assert previous.side is event.side


def test_varied_event_types_are_present(match: list[MatchEvent]) -> None:
    counts = Counter(event.type for event in match)
    for required in (
        EventType.PASS,
        EventType.SHOT,
        EventType.TACKLE,
        EventType.INTERCEPTION,
        EventType.CARD,
        EventType.SUBSTITUTION,
        EventType.CORNER,
        EventType.FREE_KICK,
    ):
        assert counts[required] > 0, f"no {required} events were generated"


def test_same_seed_is_identical() -> None:
    first = simulate(42)
    second = simulate(42)
    assert [e.model_dump() for e in first] == [e.model_dump() for e in second]
    assert first[0].to_json_line() == second[0].to_json_line()


def test_different_seed_is_different() -> None:
    first = simulate(42)
    second = simulate(43)
    assert [e.model_dump() for e in first] != [e.model_dump() for e in second]


def test_club_names_are_fictional() -> None:
    home, away = build_teams(42)
    assert home.name in FICTIONAL_CLUB_NAMES
    assert away.name in FICTIONAL_CLUB_NAMES
    assert home.name != away.name


def test_every_seed_uses_fictional_clubs() -> None:
    for seed in range(20):
        home, away = build_teams(seed)
        assert home.name in FICTIONAL_CLUB_NAMES
        assert away.name in FICTIONAL_CLUB_NAMES


def test_squads_are_the_right_size() -> None:
    home, away = build_teams(7)
    for club in (home, away):
        assert len(club.squad) == 16
        assert len(club.starters()) == 11
        assert len(club.substitutes()) == 5
        assert len(club.squad) == len({p.player_id for p in club.squad})
        assert len(club.squad) == len({p.name for p in club.squad})
        assert len(club.short_name) == 3
        assert len(club.primary_colour) == 3
        assert all(0 <= channel <= 255 for channel in club.primary_colour)
        assert len(club.secondary_colour) == 3


def test_jsonl_round_trip(match: list[MatchEvent], tmp_path) -> None:
    path = tmp_path / "match.jsonl"
    write_jsonl(match, path)
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == len(match)
    parsed = [MatchEvent.from_json_line(line) for line in lines]
    assert [e.model_dump() for e in parsed] == [e.model_dump() for e in match]


def test_pass_speed_and_distance_are_plausible(match: list[MatchEvent]) -> None:
    passes = [e for e in match if e.type is EventType.PASS and e.distance_m]
    assert passes
    for event in passes:
        assert 8.0 <= (event.speed_mps or 0.0) <= 25.0
        assert 0.0 <= (event.difficulty or 0.0) <= 1.0
        assert event.distance_m is not None
        assert 0.0 < event.distance_m < 80.0


def test_shot_speed_is_plausible(match: list[MatchEvent]) -> None:
    shots = [e for e in match if e.type is EventType.SHOT and e.speed_mps]
    assert shots
    for event in shots:
        assert 20.0 <= (event.speed_mps or 0.0) <= 33.0
