"""The audience projection: the same moment, read by somebody else.

The analyst produces one fact about the match and cites the events it rests
on. The **audience** decides how that fact is presented, and this module is
where that decision is made -- deterministically, before the model is asked
for anything.

The rule is the same in every mode: **the audience reads the same cited
events**. The my-team audience gets that side's numbers over the events the
insight was built from; the player audience gets that player's actions over
those same events; the metric-lens audience gets one metric recomputed from
those same events. Nothing is added to the window and nothing is taken out of
it, so every number on an audience overlay is recomputable from the event ids
the overlay carries, exactly as the analyst's own numbers are.

The projection returns a **copy of the candidate** with the audience's
headline, metrics and side. That matters: the figure guard checks the prose
the model writes against the figures the candidate already carries, so the
guard has to be pointed at the *projected* candidate. Pointed at the original
one, the guard would reject every my-team and every lens number -- they are
not in the original candidate -- and the overlay would silently fall back to
the analyst's copy.

When an audience has nothing to say -- your side was not in this window, or
your player was not in it -- the projection says so instead of showing a
number about somebody else. An empty card that tells the truth is better
than a full card that does not.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence

from touchline.metrics.definitions import METRICS, compute_metric
from touchline.models.audience import Audience, AudienceMode
from touchline.models.events import MATCH_DURATION_MS, MatchEvent, TeamSide
from touchline.models.insights import InsightCandidate, InsightKind, MetricValue
from touchline.models.overlay import Overlay, OverlayType
from touchline.providers import template

logger = logging.getLogger(__name__)

__all__ = [
    "LENS_METRICS",
    "PLAYER_METRICS",
    "RECAP_METRICS",
    "build_recap",
    "project_candidate",
]

#: The metrics the player audience shows. They are the numbers a player would
#: recognise as their own: what they did with the ball, and how far they
#: ran to do it. Every one of them is a registry metric, computed over the
#: player's own events inside the cited window.
PLAYER_METRICS: tuple[str, ...] = (
    "passes",
    "shots",
    "tackles",
    "player_distance_m",
)

#: The metrics the full-time recap shows, for each side.
RECAP_METRICS: tuple[str, ...] = (
    "goals",
    "shots",
    "shots_on_target",
    "xg",
)

#: The metrics a lens may look through. They are the ones that mean
#: something over a single ninety-second window -- a count of events, a
#: share, a distance -- rather than a number that only makes sense over a
#: whole match.
LENS_METRICS: tuple[str, ...] = (
    "pass_accuracy",
    "passes",
    "passes_completed",
    "pass_distance_m",
    "shots",
    "shots_on_target",
    "xg",
    "goals",
    "possession_pct",
    "control_pct",
    "tempo_breaks",
    "momentum_index",
    "progress",
    "pressure",
    "tackles",
    "interceptions",
    "saves",
    "total_distance_m",
    "max_speed_mps",
    "shot_distance_m",
)

#: The tag that marks a projection with nothing to report. The template
#: engine reads it to say why the card is empty instead of saying that no
#: numbers are attached.
_EMPTY_TAG = "audience-empty"

# --- The audience's own words ---------------------------------------------
# The analyst's headline is written in English by the deterministic analyst,
# so in another locale it is quoted rather than mistranslated. The frames
# *around* it are written here, in the audience's language.

_AUDIENCE_HEADLINES: dict[str, dict[str, str]] = {
    "my_team": {
        "en": "For {team}: {headline}",
        "de": "Für {team}: {headline}",
        "bn": "{team}-এর জন্য: {headline}",
    },
    "player": {
        "en": "What {player} saw: {headline}",
        "de": "Was {player} sah: {headline}",
        "bn": "{player} যা দেখেছেন: {headline}",
    },
}

#: What is said when the audience has no part in the moment.
_EMPTY_HEADLINES: dict[str, dict[str, str]] = {
    "my_team": {
        "en": "Your side was nowhere near this moment.",
        "de": "Ihre Mannschaft war an diesem Moment nicht beteiligt.",
        "bn": "এই মুহূর্তে আপনার দলের কোনো ভূমিকা ছিল না।",
    },
    "player": {
        "en": "{player} was not involved in this moment.",
        "de": "{player} war an diesem Moment nicht beteiligt.",
        "bn": "{player} এই মুহূর্তে জড়িত ছিলেন না।",
    },
}

#: The title of the full-time card.
_RECAP_TITLES: dict[str, str] = {
    "en": "Full time",
    "de": "Abpfiff",
    "bn": "ম্যাচ শেষ",
}


def _resolve(locale: str) -> str:
    """The locale, falling back to English."""
    return locale if locale in template.SUPPORTED_LOCALES else "en"


def _metric(
    name: str,
    value: float,
    *,
    side: TeamSide | None,
    subject: str | None = None,
) -> MetricValue:
    """One metric, in the shape the candidate carries them."""
    spec = METRICS[name]
    return MetricValue(
        name=name,
        value=value,
        unit=spec.unit,
        label=spec.label,
        side=side,
        subject=subject,
    )


def _empty_candidate(
    candidate: InsightCandidate,
    audience: Audience,
    headline: str,
) -> InsightCandidate:
    """The candidate as it looks when the audience has no part in it."""
    return candidate.model_copy(
        update={
            "headline": headline,
            "metrics": [],
            "tags": [*candidate.tags, _EMPTY_TAG],
        }
    )


def _framed_headline(
    template_key: str,
    audience: Audience,
    candidate: InsightCandidate,
    **values: str,
) -> str:
    """The audience's frame around the analyst's headline."""
    locale = _resolve(audience.locale)
    frames = _AUDIENCE_HEADLINES.get(template_key, {}).get(locale)
    if frames is None:
        return candidate.headline
    return frames.format(headline=candidate.headline, **values)


def _empty_headline(template_key: str, audience: Audience, **values: str) -> str:
    """What the audience is told when the moment was not theirs."""
    locale = _resolve(audience.locale)
    frames = _EMPTY_HEADLINES.get(template_key, {}).get(locale)
    if frames is None:
        return "This moment is not about you."
    return frames.format(**values)


def project_candidate(
    candidate: InsightCandidate,
    audience: Audience,
    events: Sequence[MatchEvent],
    *,
    team_names: Mapping[TeamSide, str] | None = None,
) -> InsightCandidate:
    """Read one candidate from one audience's point of view.

    Args:
        candidate: The deterministic, fully-cited insight.
        audience: Who the overlay is written for.
        events: The events the candidate cites. The audience reads exactly
            these, so every number it produces is recomputable from the
            overlay's own citations.
        team_names: The two fictional clubs, by side. Used by the my-team
            audience's headline.

    Returns:
        A copy of the candidate with the audience's headline, metrics and
        side. The analyst and fan audiences get the candidate unchanged.
    """
    if audience.mode is AudienceMode.ANALYST or audience.mode is AudienceMode.FAN:
        return candidate

    if audience.mode is AudienceMode.MY_TEAM:
        return _project_my_team(candidate, audience, events, team_names)

    if audience.mode is AudienceMode.PLAYER:
        return _project_player(candidate, audience, events)

    return _project_lens(candidate, audience, events)


def _project_my_team(
    candidate: InsightCandidate,
    audience: Audience,
    events: Sequence[MatchEvent],
    team_names: Mapping[TeamSide, str] | None,
) -> InsightCandidate:
    """The same moment, with one side's numbers over the same events."""
    side = audience.side or candidate.side or TeamSide.HOME
    team = (team_names or {}).get(side, side.value.title())

    involved = [event for event in events if event.side is side]
    if not involved:
        return _empty_candidate(
            candidate,
            audience,
            _empty_headline("my_team", audience),
        )

    metrics = [
        _metric(
            metric.name,
            compute_metric(metric.name, events, side),
            side=side,
        )
        for metric in candidate.metrics
    ]
    return candidate.model_copy(
        update={
            "side": side,
            "headline": _framed_headline("my_team", audience, candidate, team=team),
            "metrics": metrics,
        }
    )


def _project_player(
    candidate: InsightCandidate,
    audience: Audience,
    events: Sequence[MatchEvent],
) -> InsightCandidate:
    """The same moment, with one player's actions inside it."""
    player_id = audience.player_id
    if not player_id:
        return candidate

    name = audience.player_name or player_id
    involved = [event for event in events if event.player_id == player_id]
    if not involved:
        return _empty_candidate(
            candidate,
            audience,
            _empty_headline("player", audience, player=name),
        )

    metrics = [
        _metric(
            metric,
            compute_metric(metric, involved, None),
            side=None,
            subject=name,
        )
        for metric in PLAYER_METRICS
    ]
    return candidate.model_copy(
        update={
            "headline": _framed_headline(
                "player", audience, candidate, player=name
            ),
            "metrics": metrics,
        }
    )


def _project_lens(
    candidate: InsightCandidate,
    audience: Audience,
    events: Sequence[MatchEvent],
) -> InsightCandidate:
    """The same moment, through one metric and nothing else."""
    name = audience.metric
    if not name or name not in METRICS:
        logger.warning(
            "the metric-lens audience asked for %r, which is not a "
            "registered metric; the insight is shown unchanged",
            name,
        )
        return candidate

    metric = _metric(
        name,
        compute_metric(name, events, candidate.side),
        side=candidate.side,
    )
    return candidate.model_copy(update={"metrics": [metric]})


# --- The full-time recap ---------------------------------------------------


def _recap_events(
    events: Sequence[MatchEvent],
    audience: Audience,
) -> list[MatchEvent]:
    """The events the recap's numbers are computed over.

    The recap rests on the shots and the goals -- the moments that changed
    the match -- and, for the my-team and player audiences, only on that
    side's or that player's share of them.
    """
    shots_and_goals = [
        event
        for event in events
        if event.type.value in {"SHOT", "GOAL"}
    ]
    if audience.mode is AudienceMode.MY_TEAM and audience.side is not None:
        return [event for event in shots_and_goals if event.side is audience.side]
    if audience.mode is AudienceMode.PLAYER and audience.player_id:
        return [
            event
            for event in shots_and_goals
            if event.player_id == audience.player_id
        ]
    return shots_and_goals


def _recap_candidate(
    audience: Audience,
    events: Sequence[MatchEvent],
) -> InsightCandidate:
    """The deterministic insight the full-time card is built from."""
    cited = _recap_events(events, audience)

    if audience.mode is AudienceMode.MY_TEAM and audience.side is not None:
        side = audience.side
        metrics = [
            _metric(
                name,
                compute_metric(name, cited, side),
                side=side,
            )
            for name in RECAP_METRICS
        ]
    elif audience.mode is AudienceMode.METRIC_LENS and audience.metric in METRICS:
        name = audience.metric
        metrics = [
            _metric(
                name,
                compute_metric(name, cited, side),
                side=side,
            )
            for side in (TeamSide.HOME, TeamSide.AWAY)
        ]
    elif audience.mode is AudienceMode.PLAYER and audience.player_id:
        involved = [event for event in cited if event.player_id == audience.player_id]
        metrics = [
            _metric(
                name,
                compute_metric(name, involved, None),
                side=None,
                subject=audience.player_name or audience.player_id,
            )
            for name in ("goals", "shots")
        ]
    else:
        metrics = [
            _metric(
                name,
                compute_metric(name, cited, side),
                side=side,
            )
            for side in (TeamSide.HOME, TeamSide.AWAY)
            for name in RECAP_METRICS
        ]

    return InsightCandidate(
        candidate_id="recap",
        kind=InsightKind.MILESTONE,
        match_clock_ms=MATCH_DURATION_MS,
        headline=_RECAP_TITLES[_resolve(audience.locale)],
        metrics=metrics,
        cited_event_ids=[event.event_id for event in cited],
        confidence=1.0,
        tags=["recap"],
    )


def build_recap(
    events: Sequence[MatchEvent],
    audience: Audience,
    *,
    team_names: Mapping[TeamSide, str] | None = None,
) -> Overlay:
    """The full-time card.

    The recap is **deterministic**: it is a summary of the match's numbers,
    and the numbers are the log's. No model is asked to write it, because a
    summary of the log does not need one.

    Args:
        events: The whole event log.
        audience: Who the card is written for. The my-team, player and
            metric-lens audiences get their own reading of the same match.
        team_names: The two fictional clubs, by side.

    Returns:
        The recap overlay.
    """
    locale = _resolve(audience.locale)
    candidate = _recap_candidate(audience, events)

    facts = template.render_facts(candidate, locale=locale)
    closer = template.render_closer(audience.mode.value, locale=locale)
    title = template.render_title(
        candidate,
        locale=locale,
        mode=audience.mode.value,
        audience=audience,
    )

    return template.build_overlay(
        candidate,
        title=title,
        body=f"{facts} {_recap_tail(locale)} {closer}",
        locale=locale,
        mode=audience.mode.value,
        overlay_type=OverlayType.RECAP,
    )


#: The sentence that says what a full-time card is.
_RECAP_TAILS: dict[str, str] = {
    "en": (
        "This is the whole match, counted from the shots and the goals that "
        "are named underneath."
    ),
    "de": (
        "Das ist das gesamte Spiel, gezählt aus den Schüssen und den Toren, "
        "die unten benannt sind."
    ),
    "bn": (
        "এটি পুরো ম্যাচ, নিচে উল্লেখ করা শট ও গোল থেকে গণনা করা।"
    ),
}


def _recap_tail(locale: str) -> str:
    """The recap's interpretive line, in the right language."""
    return _RECAP_TAILS.get(locale, _RECAP_TAILS["en"])
