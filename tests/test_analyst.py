"""Tests for the deterministic analyst.

The analyst's contract is narrow and strict: every insight it produces must
carry the ids of the events it rests on, and every number it reports must be
recomputable from those ids alone. These tests hold it to that contract on
real simulated matches rather than on hand-made fixtures, because the whole
point of the analyst is that it reads a match and says something true about
it.

The seeds are fixed. A change in the simulator that moves a number is a
change the tests should notice, not something to absorb.
"""

from __future__ import annotations

import pytest

from touchline.metrics.analyst import MatchAnalyst
from touchline.metrics.definitions import METRICS
from touchline.metrics.thresholds import Thresholds
from touchline.models.events import EventType, MatchEvent, TeamSide
from touchline.models.insights import InsightKind
from touchline.sim.generator import simulate, team_names
from touchline.state.store import MatchStateStore

SEEDS = (42, 7, 99)


@pytest.fixture(scope="module")
def matches() -> dict[int, list[MatchEvent]]:
    """A simulated match per seed."""
    return {seed: simulate(seed=seed) for seed in SEEDS}


@pytest.fixture(scope="module")
def stores() -> dict[int, MatchStateStore]:
    """A store holding each simulated match."""
    stores: dict[int, MatchStateStore] = {}
    for seed, events in ((s, simulate(seed=s)) for s in SEEDS):
        store = MatchStateStore()
        store.extend(events)
        stores[seed] = store
    return stores


def test_the_analyst_says_nothing_about_an_empty_log() -> None:
    assert MatchAnalyst().analyse([]) == []


def test_every_candidate_cites_the_events_it_rests_on(
    matches: dict[int, list[MatchEvent]],
) -> None:
    for seed, events in matches.items():
        candidates = MatchAnalyst(team_names=team_names(seed)).analyse(events)
        assert candidates, f"seed {seed} produced no candidates at all"
        for candidate in candidates:
            assert candidate.cited_event_ids, (
                f"{candidate.candidate_id} has no citations"
            )


def test_every_reported_number_recomputes_from_its_citations(
    matches: dict[int, list[MatchEvent]],
    stores: dict[int, MatchStateStore],
) -> None:
    """The load-bearing test.

    Every metric on every candidate must be reproducible from the candidate's
    own citations and that metric's own side. This is what the grounding
    step checks at run time; if it fails here it would fail there, on a live
    feed, in front of a judge.
    """
    for seed, events in matches.items():
        store = stores[seed]
        candidates = MatchAnalyst(team_names=team_names(seed)).analyse(events)
        for candidate in candidates:
            for metric in candidate.metrics:
                recomputed = store.recompute(
                    metric.name, candidate.cited_event_ids, metric.side
                )
                assert recomputed is not None, (
                    f"{candidate.candidate_id}: cited ids unknown for {metric.name}"
                )
                assert recomputed == pytest.approx(metric.value, abs=1e-6), (
                    f"{candidate.candidate_id}: {metric.name} reported "
                    f"{metric.value} but recomputes to {recomputed}"
                )


def test_every_baseline_recomputes_from_its_own_citations(
    matches: dict[int, list[MatchEvent]],
    stores: dict[int, MatchStateStore],
) -> None:
    for seed, events in matches.items():
        store = stores[seed]
        candidates = MatchAnalyst(team_names=team_names(seed)).analyse(events)
        for candidate in candidates:
            baseline = candidate.baseline
            if baseline is None:
                continue
            recomputed = store.recompute(
                baseline.metric_name, baseline.cited_event_ids, baseline.side
            )
            assert recomputed is not None
            assert recomputed == pytest.approx(baseline.value, abs=1e-6)


def test_a_metric_does_not_depend_on_the_order_of_its_citations(
    matches: dict[int, list[MatchEvent]],
    stores: dict[int, MatchStateStore],
) -> None:
    """The grounding step rebuilds the cited set in the claim's own order.

    So a metric has to be a pure function of the *set* of cited events, or
    the same claim would recompute to two different numbers depending on how
    the ids were written down.
    """
    for seed, events in matches.items():
        store = stores[seed]
        candidates = MatchAnalyst(team_names=team_names(seed)).analyse(events)
        for candidate in candidates:
            shuffled = list(reversed(candidate.cited_event_ids))
            for metric in candidate.metrics:
                recomputed = store.recompute(metric.name, shuffled, metric.side)
                assert recomputed == pytest.approx(metric.value, abs=1e-6)


def test_the_analyst_only_uses_registered_metrics(
    matches: dict[int, list[MatchEvent]],
) -> None:
    for seed, events in matches.items():
        candidates = MatchAnalyst(team_names=team_names(seed)).analyse(events)
        for candidate in candidates:
            for metric in candidate.metrics:
                assert metric.name in METRICS


def test_the_same_seed_gives_the_same_insights(
    matches: dict[int, list[MatchEvent]],
) -> None:
    """Determinism, which is what makes the whole feed reproducible."""
    for seed, events in matches.items():
        first = MatchAnalyst(team_names=team_names(seed)).analyse(events)
        second = MatchAnalyst(team_names=team_names(seed)).analyse(events)
        assert [c.candidate_id for c in first] == [c.candidate_id for c in second]
        assert [c.headline for c in first] == [c.headline for c in second]
        assert [c.cited_event_ids for c in first] == [c.cited_event_ids for c in second]


def test_candidates_come_out_in_clock_order(
    matches: dict[int, list[MatchEvent]],
) -> None:
    for seed, events in matches.items():
        candidates = MatchAnalyst(team_names=team_names(seed)).analyse(events)
        clocks = [candidate.match_clock_ms for candidate in candidates]
        assert clocks == sorted(clocks)


def test_a_set_piece_insight_is_caused_by_the_set_piece(
    matches: dict[int, list[MatchEvent]],
) -> None:
    """A set piece claim must cite the restart *and* the shot it led to."""
    for seed, events in matches.items():
        by_id = {event.event_id: event for event in events}
        candidates = MatchAnalyst(team_names=team_names(seed)).analyse(events)
        set_pieces = [
            candidate
            for candidate in candidates
            if candidate.kind.value == "SET_PIECE"
            and candidate.metrics
            and candidate.metrics[0].name == "shots"
        ]
        assert set_pieces, f"seed {seed} produced no set-piece insight"
        for candidate in set_pieces:
            cited = [by_id[event_id] for event_id in candidate.cited_event_ids]
            types = [event.type for event in cited]
            assert types[0] in {EventType.CORNER, EventType.FREE_KICK}
            assert types[-1] is EventType.SHOT
            # The restart is the cause and the shot is the effect.
            assert cited[0].match_clock_ms <= cited[-1].match_clock_ms
            assert cited[0].side is cited[-1].side


def test_a_dangerous_free_kick_is_won_close_to_goal(
    matches: dict[int, list[MatchEvent]],
) -> None:
    for seed, events in matches.items():
        candidates = MatchAnalyst(team_names=team_names(seed)).analyse(events)
        dangerous = [
            candidate
            for candidate in candidates
            if "dangerous" in candidate.tags
        ]
        assert dangerous, f"seed {seed} produced no dangerous set piece"
        for candidate in dangerous:
            metric = next(
                metric
                for metric in candidate.metrics
                if metric.name == "distance_to_goal_m"
            )
            assert metric.value <= Thresholds().set_piece_range_m


def test_a_milestone_reports_the_ground_the_player_has_covered(
    matches: dict[int, list[MatchEvent]],
) -> None:
    """The milestone must be a real running total, not a sum of actions."""
    for seed, events in matches.items():
        candidates = MatchAnalyst(team_names=team_names(seed)).analyse(events)
        milestones = [
            candidate
            for candidate in candidates
            if candidate.kind.value == "MILESTONE"
            and candidate.metrics
            and candidate.metrics[0].name == "player_total_distance_m"
        ]
        assert milestones, f"seed {seed} produced no distance milestone"
        for candidate in milestones:
            metric = candidate.metrics[0]
            assert metric.value >= min(Thresholds().milestone_distances_m)


def test_the_milestone_distance_is_never_less_than_the_milestone_it_fired_on(
    matches: dict[int, list[MatchEvent]],
) -> None:
    """A player who has covered 3,122 m has covered at least 3,000 m.

    The running total only goes up, so the metric a milestone reports can
    never be below the threshold that made it fire.
    """
    for seed, events in matches.items():
        candidates = MatchAnalyst(team_names=team_names(seed)).analyse(events)
        for candidate in candidates:
            metric = next(
                (
                    metric
                    for metric in candidate.metrics
                    if metric.name == "player_total_distance_m"
                ),
                None,
            )
            if metric is None:
                continue
            assert metric.value >= min(Thresholds().milestone_distances_m)


def test_a_fabricated_event_id_does_not_recompute(
    stores: dict[int, MatchStateStore],
) -> None:
    """The grounding step's reason for existing, tested from the other side."""
    store = stores[42]
    assert store.recompute("passes", ["ev-does-not-exist"]) is None


def test_a_tighter_threshold_never_produces_more_insights(
    matches: dict[int, list[MatchEvent]],
) -> None:
    """Raising a threshold can only remove insights, never add them.

    This is the property that makes the thresholds safe to tune later: a
    threshold is a filter, not a source of new claims.
    """
    seed = 42
    events = matches[seed]
    loose = MatchAnalyst(team_names=team_names(seed)).analyse(events)
    strict = MatchAnalyst(
        thresholds=Thresholds(pass_accuracy_shift=40.0, momentum_shift=0.6),
        team_names=team_names(seed),
    ).analyse(events)
    assert len(strict) <= len(loose)


def test_the_momentum_curve_covers_the_whole_match(
    matches: dict[int, list[MatchEvent]],
) -> None:
    points = MatchAnalyst().momentum_curve(matches[42])
    assert points
    clocks = [point.match_clock_ms for point in points]
    assert clocks == sorted(clocks)
    assert clocks[0] > 0
    assert clocks[-1] <= 5_400_000


def test_an_insight_is_about_a_side_that_is_in_the_match(
    matches: dict[int, list[MatchEvent]],
) -> None:
    for seed, events in matches.items():
        candidates = MatchAnalyst(team_names=team_names(seed)).analyse(events)
        for candidate in candidates:
            assert candidate.side in (TeamSide.HOME, TeamSide.AWAY)


def test_the_feed_holds_one_insight_per_stretch_of_the_match(
    matches: dict[int, list[MatchEvent]],
) -> None:
    """The on-screen stream is one thing at a time."""
    for seed, events in matches.items():
        analyst = MatchAnalyst(team_names=team_names(seed))
        candidates = analyst.analyse(events)
        feed = analyst.select_for_feed(candidates)
        span = analyst.thresholds.feed_interval_ms
        buckets = [candidate.match_clock_ms // span for candidate in feed]
        assert len(buckets) == len(set(buckets))
        assert len(feed) <= len(candidates)


def test_the_feed_keeps_the_moments_a_viewer_would_have_missed(
    matches: dict[int, list[MatchEvent]],
) -> None:
    """A chance created beats a passing drift in the same stretch of feed.

    This is the policy that makes the stream read like a broadcast rather
    than a leaderboard: when two insights compete for the same airtime, the
    one about the thing happening on the pitch wins.
    """
    for seed, events in matches.items():
        analyst = MatchAnalyst(team_names=team_names(seed))
        candidates = analyst.analyse(events)
        feed = analyst.select_for_feed(candidates)
        span = analyst.thresholds.feed_interval_ms
        for candidate in candidates:
            bucket = candidate.match_clock_ms // span
            rivals = [
                other
                for other in feed
                if other.match_clock_ms // span == bucket
            ]
            if not rivals:
                continue
            kept = rivals[0]
            if candidate.kind is InsightKind.CHANCE_CREATED:
                assert kept.kind is InsightKind.CHANCE_CREATED


def test_the_feed_is_in_clock_order(
    matches: dict[int, list[MatchEvent]],
) -> None:
    for seed, events in matches.items():
        analyst = MatchAnalyst(team_names=team_names(seed))
        feed = analyst.select_for_feed(analyst.analyse(events))
        clocks = [candidate.match_clock_ms for candidate in feed]
        assert clocks == sorted(clocks)


def _content(candidate: object) -> tuple:
    """What an insight *is*, without the name it was given.

    The id is assigned by position, so it moves when the number of insights
    found before it changes. What has to stay the same is the insight.
    """
    return (
        candidate.kind.value,
        candidate.match_clock_ms,
        candidate.side.value if candidate.side else None,
        candidate.headline,
        tuple(
            (
                metric.name,
                round(metric.value, 6),
                metric.side.value if metric.side else None,
            )
            for metric in candidate.metrics
        ),
        tuple(candidate.cited_event_ids),
    )


@pytest.mark.parametrize("seed", SEEDS)
def test_analysing_a_prefix_gives_the_same_answer_as_the_whole_match(
    seed: int,
    matches: dict[int, list[MatchEvent]],
) -> None:
    """The live feed analyses the match as it is played, so this must hold.

    A live feed does not hand the analyst the finished match. It hands it
    the part that has happened so far, once per stretch, and the insight
    appears when it happens. So the analyst's answer for a stretch of the
    match has to be the answer it would have given with the whole log in
    hand -- otherwise the feed a judge watches in the browser is not the
    feed the project claims to produce.

    This is the property the cooldown order protects: the cooldown is
    first-wins, so applying it to the drafts in the order the rules happen
    to produce them makes the answer depend on how much of the match had
    been seen when the analysis ran.
    """
    events = matches[seed]
    analyst = MatchAnalyst(team_names=team_names(seed))
    span = analyst.thresholds.feed_interval_ms

    # The whole match, analysed in one go. The feed holds one insight per
    # stretch of match time, so the answer is a list of stretches, and the
    # first insight in a stretch is the one the live path would publish.
    whole = analyst.select_for_feed(analyst.analyse(events))
    expected: dict[int, list[tuple]] = {}
    for candidate in whole:
        expected.setdefault(candidate.match_clock_ms // span, []).append(
            _content(candidate)
        )

    # The same match, analysed stretch by stretch as it is played. Each
    # stretch is read at its **closing** boundary: the prefix ends at the
    # moment the stretch is over, which is the only moment the live feed
    # could have read it at.
    prefix: list[MatchEvent] = []
    index = 0
    bucket = 0
    produced: dict[int, list[tuple]] = {}
    while index < len(events):
        boundary = bucket * span
        while index < len(events) and events[index].match_clock_ms < boundary + span:
            prefix.append(events[index])
            index += 1
        candidates = analyst.analyse(prefix)
        picked = [
            candidate
            for candidate in analyst.select_for_feed(candidates)
            if candidate.match_clock_ms // span == bucket
        ]
        if picked:
            produced[bucket] = [_content(candidate) for candidate in picked]
        bucket += 1

    assert produced == expected, (
        f"seed {seed}: the live feed and the whole-match feed disagree"
    )
