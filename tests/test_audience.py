"""The audience: one moment, five genuinely different readings.

The claim this file has to defend is that the personaliser's modes are
**different experiences** and not five ways of saying the same sentence.
So the tests are about difference: different numbers, different words,
different closures -- and, above all, numbers that still recompute from
the event log after the audience has had its way with them.

The rule the whole design rests on is that the audience reads *the same
cited events*. A my-team number, a lens number and a player number are all
recomputable from the ids the overlay carries, because they are computed
by the same registry over the same events.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from touchline.agents.audience import build_recap, project_candidate
from touchline.agents.figures import check_figures
from touchline.agents.personalizer import PersonalizerAgent
from touchline.metrics.definitions import compute_metric
from touchline.models.audience import Audience, AudienceMode
from touchline.models.events import TeamSide
from touchline.models.insights import InsightCandidate
from touchline.providers.clients import TemplateChatClient
from touchline.providers.payload import decode_payload, encode_payload
from touchline.sim.generator import simulate, team_names

SEED = 42


@pytest.fixture(scope="module")
def match() -> tuple[list, dict, InsightCandidate, list]:
    """A real match, its clubs, one of its insights, and the cited events."""
    from touchline.metrics.analyst import MatchAnalyst

    events = simulate(seed=SEED)
    names = team_names(SEED)
    analyst = MatchAnalyst(team_names=names)
    feed = analyst.select_for_feed(analyst.analyse(events))
    candidate = feed[3]
    by_id = {event.event_id: event for event in events}
    cited = [by_id[event_id] for event_id in candidate.cited_event_ids]
    return events, names, candidate, cited


def _audiences() -> list[Audience]:
    """The five audiences, each with what it needs."""
    return [
        Audience(mode=AudienceMode.ANALYST, locale="en"),
        Audience(mode=AudienceMode.FAN, locale="en"),
        Audience(mode=AudienceMode.MY_TEAM, locale="en", side=TeamSide.AWAY),
        Audience(mode=AudienceMode.METRIC_LENS, locale="en", metric="xg"),
        Audience(mode=AudienceMode.PLAYER, locale="en", player_id="p-1"),
    ]


# -- the projection --------------------------------------------------------


def test_the_analyst_and_the_fan_read_the_same_numbers(
    match: tuple[list, dict, InsightCandidate, list],
) -> None:
    """The fan sees the analyst's numbers. The difference is the voice."""
    _, names, candidate, cited = match
    analyst = project_candidate(
        candidate, Audience(mode=AudienceMode.ANALYST), cited, team_names=names
    )
    fan = project_candidate(
        candidate, Audience(mode=AudienceMode.FAN), cited, team_names=names
    )
    assert analyst.metrics == candidate.metrics
    assert fan.metrics == candidate.metrics
    assert analyst.headline == fan.headline


def test_my_team_reads_the_other_sides_numbers(
    match: tuple[list, dict, InsightCandidate, list],
) -> None:
    """Your side's numbers, over the same events, recomputed by the registry."""
    _, names, candidate, cited = match
    audience = Audience(mode=AudienceMode.MY_TEAM, side=TeamSide.AWAY)
    projected = project_candidate(candidate, audience, cited, team_names=names)

    assert projected.metrics, "the my-team audience produced no numbers"
    for metric in projected.metrics:
        expected = compute_metric(metric.name, cited, TeamSide.AWAY)
        assert metric.value == pytest.approx(expected, abs=1e-9)
        assert metric.side is TeamSide.AWAY
    assert projected.headline.startswith("For ")


def test_my_team_says_so_when_your_side_was_not_there(
    match: tuple[list, dict, InsightCandidate, list],
) -> None:
    """An empty card that tells the truth beats a full card that does not."""
    _, names, candidate, cited = match
    other = TeamSide.AWAY if candidate.side is TeamSide.HOME else TeamSide.HOME
    audience = Audience(mode=AudienceMode.MY_TEAM, side=other)
    projected = project_candidate(candidate, audience, cited, team_names=names)

    if any(event.side is other for event in cited):
        return
    assert projected.metrics == []
    assert "nowhere near" in projected.headline


def test_the_lens_shows_one_metric_and_only_one(
    match: tuple[list, dict, InsightCandidate, list],
) -> None:
    """The lens never empties the feed, and never shows two numbers."""
    _, names, candidate, cited = match
    audience = Audience(mode=AudienceMode.METRIC_LENS, metric="pass_accuracy")
    projected = project_candidate(candidate, audience, cited, team_names=names)

    assert len(projected.metrics) == 1
    metric = projected.metrics[0]
    assert metric.name == "pass_accuracy"
    assert metric.value == pytest.approx(
        compute_metric("pass_accuracy", cited, candidate.side), abs=1e-9
    )


def test_the_lens_refuses_a_metric_that_is_not_registered(
    match: tuple[list, dict, InsightCandidate, list],
) -> None:
    """An unknown metric leaves the insight alone rather than breaking it."""
    _, names, candidate, cited = match
    audience = Audience(mode=AudienceMode.METRIC_LENS, metric="vibes")
    projected = project_candidate(candidate, audience, cited, team_names=names)
    assert projected.metrics == candidate.metrics


def test_the_player_audience_counts_that_players_actions(
    match: tuple[list, dict, InsightCandidate, list],
) -> None:
    """One player's actions inside the window the insight was built from."""
    _, names, candidate, cited = match
    counts: dict[str, int] = {}
    for event in cited:
        if event.player_id:
            counts[event.player_id] = counts.get(event.player_id, 0) + 1
    busiest = max(counts, key=lambda player: counts[player])

    audience = Audience(
        mode=AudienceMode.PLAYER, player_id=busiest, player_name="A Player"
    )
    projected = project_candidate(candidate, audience, cited, team_names=names)

    if counts[busiest] == 0:  # pragma: no cover - the busiest player has one
        return
    assert projected.metrics, "the player audience produced no numbers"
    assert all(metric.subject == "A Player" for metric in projected.metrics)
    assert "A Player" in projected.headline


def test_the_player_audience_is_honest_about_an_absent_player(
    match: tuple[list, dict, InsightCandidate, list],
) -> None:
    """A player who was not in the window gets a card that says so."""
    _, names, candidate, cited = match
    audience = Audience(
        mode=AudienceMode.PLAYER, player_id="nobody-at-all", player_name="Nobody"
    )
    projected = project_candidate(candidate, audience, cited, team_names=names)
    assert projected.metrics == []
    assert "not involved" in projected.headline


# -- the whole overlay -----------------------------------------------------


def _personalise(candidate, narration, audience, cited, names):
    """Run the Personaliser over the template engine, without a network."""
    agent = PersonalizerAgent(
        TemplateChatClient(), team_names=names
    )
    return asyncio.run(
        agent.personalise(
            candidate, narration, audience=audience, events=cited
        )
    )


def _narration(candidate: InsightCandidate):
    """A narration for a candidate, written by the template engine."""
    from touchline.providers import template

    rendered = template.render_narration(candidate, locale="en")
    from touchline.models.claims import Narration

    return Narration(
        candidate_id=candidate.candidate_id,
        title=rendered["title"],
        body=rendered["body"],
        commentary=rendered["commentary"],
    )


def test_the_five_audiences_are_five_different_cards(
    match: tuple[list, dict, InsightCandidate, list],
) -> None:
    """The claim: at least three genuinely different experiences.

    This asserts difference rather than equality: different bodies, and
    different numbers for the three audiences that change the numbers.
    """
    _, names, candidate, cited = match
    narration = _narration(candidate)

    cards = {}
    for audience in _audiences():
        cards[audience.mode.value] = _personalise(
            candidate, narration, audience, cited, names
        )

    bodies = {mode: card.body for mode, card in cards.items()}
    assert len(set(bodies.values())) == len(bodies), (
        f"two audiences wrote the same card: {bodies}"
    )

    metrics = {
        mode: tuple(metric.name for metric in card.metrics)
        for mode, card in cards.items()
    }
    assert metrics["metric_lens"] == ("xg",)

    # The my-team card carries the *other* side's numbers, so the values
    # differ even where the names do not.
    analyst_values = [metric.value for metric in cards["analyst"].metrics]
    team_values = [metric.value for metric in cards["my_team"].metrics]
    assert analyst_values != team_values
    assert cards["my_team"].metrics[0].side is TeamSide.AWAY


def test_every_audience_number_recomputes_from_the_citations(
    match: tuple[list, dict, InsightCandidate, list],
) -> None:
    """The overlay's numbers are the log's, whichever audience is reading."""
    _, names, candidate, cited = match
    narration = _narration(candidate)

    for audience in _audiences():
        card = _personalise(candidate, narration, audience, cited, names)
        assert card.cited_event_ids == candidate.cited_event_ids

        for metric in card.metrics:
            if audience.mode is AudienceMode.PLAYER:
                # The player's numbers are computed over that player's own
                # events inside the cited window.
                involved = [
                    event for event in cited if event.player_id == audience.player_id
                ]
                expected = compute_metric(metric.name, involved, None)
            elif audience.mode is AudienceMode.MY_TEAM:
                expected = compute_metric(
                    metric.name, cited, audience.side or candidate.side
                )
            elif audience.mode is AudienceMode.METRIC_LENS:
                expected = compute_metric(
                    metric.name, cited, candidate.side
                )
            else:
                expected = compute_metric(
                    metric.name, cited, candidate.side
                )
            assert metric.value == pytest.approx(expected, abs=1e-6), (
                f"{audience.mode.value}: {metric.name} did not recompute"
            )


def test_the_figure_guard_accepts_the_audiences_own_numbers(
    match: tuple[list, dict, InsightCandidate, list],
) -> None:
    """The guard is pointed at the *projected* candidate.

    If it were pointed at the original one, every my-team and every lens
    number would be rejected and the card would fall back to the
    analyst's copy without saying why.
    """
    _, names, candidate, cited = match
    for audience in _audiences():
        projected = project_candidate(
            candidate, audience, cited, team_names=names
        )
        for metric in projected.metrics:
            assert check_figures(f"{metric.value:.0f}", projected) == []


def test_every_locale_reads_differently(
    match: tuple[list, dict, InsightCandidate, list],
) -> None:
    """Three languages, three readings, for the same moment."""
    _, names, candidate, cited = match
    narration = _narration(candidate)

    for mode in AudienceMode:
        bodies = set()
        for locale in ("en", "de", "bn"):
            audience = Audience(mode=mode, locale=locale, metric="xg")
            card = _personalise(candidate, narration, audience, cited, names)
            bodies.add(card.body)
            assert card.locale == locale
        assert len(bodies) == 3, f"{mode.value} reads the same in every language"


# -- the full-time recap ---------------------------------------------------


def test_the_recap_counts_the_match_from_the_log(
    match: tuple[list, dict, InsightCandidate, list],
) -> None:
    """The full-time card is a summary of the shots and the goals."""
    events, names, _, _ = match
    recap = build_recap(events, Audience(mode=AudienceMode.ANALYST), team_names=names)

    assert recap.overlay_id == "ovl-recap"
    assert recap.type.value == "RECAP"
    assert recap.cited_event_ids

    by_id = {event.event_id: event for event in events}
    cited = [by_id[event_id] for event_id in recap.cited_event_ids]
    for metric in recap.metrics:
        expected = compute_metric(metric.name, cited, metric.side)
        assert metric.value == pytest.approx(expected, abs=1e-9)


def test_the_recap_reads_differently_for_each_audience(
    match: tuple[list, dict, InsightCandidate, list],
) -> None:
    """The recap has the same five readings as the rest of the feed."""
    events, names, _, _ = match
    bodies = set()
    for audience in _audiences():
        recap = build_recap(events, audience, team_names=names)
        bodies.add(recap.body)
    assert len(bodies) == len(_audiences())


# -- the payload -----------------------------------------------------------


def test_the_payload_carries_the_audience(
    match: tuple[list, dict, InsightCandidate, list],
) -> None:
    """The audience reaches the model in the payload, not in a side channel."""
    _, names, candidate, cited = match
    audience = Audience(
        mode=AudienceMode.MY_TEAM, locale="de", side=TeamSide.HOME, metric="xg"
    )
    projected = project_candidate(candidate, audience, cited, team_names=names)
    prompt = encode_payload(
        "personalise",
        projected,
        locale=audience.locale,
        mode=audience.mode.value,
        reading="a reading",
        extra={
            "AUDIENCE_MODE": audience.mode.value,
            "AUDIENCE_SIDE": audience.side.value if audience.side else "-",
            "AUDIENCE_METRIC": audience.metric or "-",
        },
    )
    decoded = decode_payload(prompt)
    assert decoded.mode == "my_team"
    assert decoded.locale == "de"
    assert decoded.extra["audience_mode"] == "my_team"
    assert decoded.extra["audience_side"] == "HOME"
    assert decoded.extra["audience_metric"] == "xg"
    assert decoded.candidate.metrics == projected.metrics


def test_a_german_decimal_comma_is_a_decimal_comma() -> None:
    """The grounding check reads a number the way its language writes it.

    German writes twenty point six seven as "20,67". Read the way English
    writes it -- comma as a thousands separator -- that is two thousand and
    sixty-seven, and the grounding check would throw away a sentence that
    the event log perfectly well supports. The copy is written in German
    and then checked in German, and the German copy has to survive.
    """
    from touchline.agents.figures import _figures_in, unsupported_figures

    assert _figures_in("weite 20,67 m", "de") == [pytest.approx(20.67)]
    assert _figures_in("weite 20,67 m", "en") == [pytest.approx(2067.0)]
    # The same number, checked against itself, is supported.
    assert unsupported_figures("weite 20,67 m", [20.67], "de") == []
    assert unsupported_figures("weite 20,67 m", [20.67], "en") != []


class _RecordingClient:
    """The template engine, with every prompt it is given kept."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def get_response(self, messages: Any, **kwargs: Any) -> Any:
        from touchline.providers.clients import _assistant_response, _last_user_text

        self.prompts.append(_last_user_text(messages))
        return _assistant_response('{"title": "T", "body": "B"}')


def test_the_personaliser_names_the_language_it_wants(
    match: tuple[list, dict, InsightCandidate, list],
) -> None:
    """The prompt names the language, not only its code.

    A payload that says ``LOCALE: de`` and nothing else does not reliably
    get German out of a model that is looking at an English reading and
    English instructions. The name has to be in there, or the language
    switch quietly does nothing.
    """
    _, names, candidate, cited = match
    client = _RecordingClient()
    agent = PersonalizerAgent(client, team_names=names)
    narration = _narration(candidate)
    asyncio.run(
        agent.personalise(
            candidate,
            narration,
            audience=Audience(mode=AudienceMode.ANALYST, locale="de"),
            events=cited,
        )
    )
    assert client.prompts, "the Personaliser sent no prompt"
    prompt = client.prompts[0]
    assert "LANGUAGE: German" in prompt
    assert "LOCALE: de" in prompt


def test_the_audience_key_is_stable() -> None:
    """The key is what the engine caches on."""
    first = Audience(mode=AudienceMode.MY_TEAM, locale="en", side=TeamSide.HOME)
    second = Audience(mode=AudienceMode.MY_TEAM, locale="en", side=TeamSide.HOME)
    assert first.key == second.key
    assert Audience(mode=AudienceMode.FAN).key != first.key


# -- the engine ------------------------------------------------------------


def test_the_engine_rewrites_its_overlays_for_a_new_audience() -> None:
    """Changing the audience re-runs the Personaliser and nothing else."""

    async def run() -> tuple[list, list]:
        from touchline.live.engine import LiveMatchEngine

        engine = LiveMatchEngine(seed=SEED, client=TemplateChatClient())
        assert await engine.wait_for(engine.duration_ms, timeout=180)
        before = list(engine.overlays)
        await engine.set_audience(Audience(mode=AudienceMode.FAN, locale="de"))
        # The rewrite runs in the background: the change is recorded at
        # once, and the new copy arrives when the model has written it.
        assert await engine.wait_for_rewrite(timeout=60)
        after = list(engine.overlays)
        await engine.close()
        return before, after

    before, after = asyncio.run(run())
    assert before and after
    assert len(before) == len(after)
    assert [overlay.overlay_id for overlay in before] == [
        overlay.overlay_id for overlay in after
    ]
    assert [overlay.body for overlay in before] != [
        overlay.body for overlay in after
    ]
    assert all(overlay.locale == "de" for overlay in after)
    assert all(overlay.revision == 1 for overlay in after)
