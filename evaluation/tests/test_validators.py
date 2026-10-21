"""Tests for the evaluation validators (deterministic, local, no model)."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO))

from evaluation import validators  # noqa: E402
from touchline.metrics.analyst import MatchAnalyst  # noqa: E402
from touchline.sim.generator import simulate, team_names  # noqa: E402


@pytest.fixture(scope="module")
def seed42():
    events = simulate(42)
    analyst = MatchAnalyst(team_names=team_names(42))
    feed = analyst.select_for_feed(analyst.analyse(events))
    return events, feed


def test_metric_close_uses_grounding_tolerance():
    assert validators.metric_close(66.0, 66.2, "%")
    assert validators.metric_close(20.6, 20.67, "m")
    assert not validators.metric_close(66.0, 67.0, "%")
    assert validators.metric_close(3.0, 3.0, "")
    assert not validators.metric_close(3.0, 3.5, "")


def test_citation_check_flags_unknown_ids():
    assert validators.check_citations(["a", "b"], {"a", "b"}) == []
    assert validators.check_citations(["a", "zzz"], {"a", "b"}) == ["zzz"]


def test_metrics_recompute_from_the_log(seed42):
    events, feed = seed42
    by_id = {event.event_id: event for event in events}
    candidate = feed[2]
    cited = [by_id[event_id] for event_id in candidate.cited_event_ids]
    assert validators.check_metrics(candidate.metrics, cited) == []


def test_metrics_flag_a_wrong_value(seed42):
    events, feed = seed42
    by_id = {event.event_id: event for event in events}
    candidate = feed[2]
    cited = [by_id[event_id] for event_id in candidate.cited_event_ids]
    wrong = [
        SimpleNamespace(
            name=candidate.metrics[0].name,
            value=66.0,
            unit=candidate.metrics[0].unit,
            side=candidate.metrics[0].side,
        )
    ]
    failures = validators.check_metrics(wrong, cited)
    assert failures and "claimed 66" in failures[0]


def test_numeric_prose_skips_clock_and_scorelines():
    metrics = [
        SimpleNamespace(name="pass_accuracy", value=82.0, unit="%", side=None)
    ]
    issues = validators.check_numeric_prose(
        ["In the 65th minute the score was 2-1 in the second half."],
        metrics,
    )
    assert issues == []


def test_numeric_prose_flags_a_contradicted_figure():
    metrics = [SimpleNamespace(name="xg", value=0.4, unit="", side=None)]
    issues = validators.check_numeric_prose(
        ["His expected goals collapsed to 9.7."],
        metrics,
    )
    assert len(issues) == 1
    assert issues[0].text == "9.7"


def test_numeric_prose_accepts_a_matching_figure():
    metrics = [SimpleNamespace(name="xg", value=0.42, unit="", side=None)]
    issues = validators.check_numeric_prose(
        ["Expected goals of 0.42 tell the story."],
        metrics,
    )
    assert issues == []


def test_numeric_prose_skips_german_ordinal_minute():
    metrics = [SimpleNamespace(name="x", value=1.0, unit="", side=None)]
    issues = validators.check_numeric_prose(
        ["Ein Tor bei der 28. Minute."],
        metrics,
        locale="de",
    )
    assert issues == []


def test_numeric_prose_skips_bengali_minute():
    metrics = [SimpleNamespace(name="x", value=1.0, unit="", side=None)]
    issues = validators.check_numeric_prose(
        ["আপনার দল: 41 মিনিটে গোল।"],
        metrics,
        locale="bn",
    )
    assert issues == []


def test_numeric_prose_parses_german_thousands_separator():
    metrics = [SimpleNamespace(name="dist", value=3020.0, unit="m", side=None)]
    issues = validators.check_numeric_prose(
        ["3.020 Meter gelaufen."],
        metrics,
        locale="de",
    )
    assert issues == []


def test_numeric_prose_accepts_fair_rounding():
    metrics = [SimpleNamespace(name="xg", value=0.42, unit="", side=None)]
    issues = validators.check_numeric_prose(
        ["Expected goals of 0.4 tell the story."],
        metrics,
    )
    assert issues == []


def test_numeric_prose_still_flags_real_contradictions():
    metrics = [SimpleNamespace(name="xg", value=0.42, unit="", side=None)]
    issues = validators.check_numeric_prose(
        ["Expected goals of 0.9 tell the story."],
        metrics,
    )
    assert len(issues) == 1
    assert issues[0].text == "0.9"


def test_validate_overlay_accepts_the_template_feed(seed42):
    from touchline.providers import template

    events, feed = seed42
    by_id = {event.event_id: event for event in events}
    candidate = feed[2]
    narration = template.render_narration(candidate, locale="en", mode="analyst")
    overlay = template.build_overlay(
        candidate,
        title=narration["title"],
        body=narration["body"],
        locale="en",
        mode="analyst",
    )
    result = validators.validate_overlay(overlay, by_id)
    assert result.ok, result.summary()
