"""The no-LLM narrative provider.

This provider is load-bearing on two paths: pre-credential local development,
and the judging-resilience path when inference is unavailable. It renders a
complete, grammatical insight from the candidate's kind and metrics, in
English, German or Bengali, and it carries the candidate's ``cited_event_ids``
through untouched -- so a template-rendered overlay is grounded exactly like a
model-rendered one.

Rendering is deterministic: the same candidate always produces a byte
identical overlay.
"""

from __future__ import annotations

from dataclasses import dataclass

from touchline.models.insights import InsightCandidate, InsightKind, MetricValue
from touchline.models.overlay import Overlay, OverlayType
from touchline.providers.base import ProviderName

#: The locales this provider can render.
SUPPORTED_LOCALES: tuple[str, ...] = ("en", "de", "bn")

#: The audience modes the closing line can be written for.
SUPPORTED_MODES: tuple[str, ...] = ("analyst", "fan", "player")

#: Overlay type produced for each insight kind.
_OVERLAY_TYPES: dict[InsightKind, OverlayType] = {
    InsightKind.TACTICAL: OverlayType.TACTICAL,
    InsightKind.MOMENTUM_SHIFT: OverlayType.NARRATIVE,
    InsightKind.PRESS_BREAK: OverlayType.TACTICAL,
    InsightKind.THREAT: OverlayType.ALERT,
    InsightKind.MILESTONE: OverlayType.STAT,
    InsightKind.CHANCE_CREATED: OverlayType.TACTICAL,
    InsightKind.SET_PIECE: OverlayType.TACTICAL,
    InsightKind.DISCIPLINE: OverlayType.ALERT,
}

#: How long each overlay type stays on screen.
_DURATION_MS: dict[OverlayType, int] = {
    OverlayType.TACTICAL: 8_000,
    OverlayType.STAT: 7_000,
    OverlayType.NARRATIVE: 9_000,
    OverlayType.ALERT: 6_000,
    OverlayType.RECAP: 12_000,
}

#: The word that joins the last two items of a list.
_LIST_JOIN: dict[str, str] = {"en": " and ", "de": " und ", "bn": " এবং "}

#: The sentence terminator used by each locale.
_TERMINATOR: dict[str, str] = {"en": ".", "de": ".", "bn": "।"}


@dataclass(frozen=True)
class _Frame:
    """The title and body templates for one insight kind in one locale."""

    title: str
    body: str


# --- Locale frames --------------------------------------------------------
# The title templates use a placeholder clock phrase that already carries the
# locale's own time expression, so no preposition is repeated.

_FRAMES: dict[InsightKind, dict[str, _Frame]] = {
    InsightKind.TACTICAL: {
        "en": _Frame(
            title="Tactical shift at {clock}",
            body=(
                "{headline} {metrics} The shape of the move points to a "
                "deliberate change of approach rather than a one-off moment."
            ),
        ),
        "de": _Frame(
            title="Taktische Umstellung bei {clock}",
            body=(
                "{headline} {metrics} Die Struktur des Zuges spricht für eine "
                "bewusste Veränderung des Spielplans und nicht für einen "
                "einmaligen Moment."
            ),
        ),
        "bn": _Frame(
            title="{clock} কৌশলগত পরিবর্তন",
            body=(
                "{headline} {metrics} আক্রমণের ধরনটি দেখায় যে এটি একটি "
                "মুহূর্তের ঘটনা নয়, বরং পরিকল্পনার ইচ্ছাকৃত পরিবর্তন।"
            ),
        ),
    },
    InsightKind.MOMENTUM_SHIFT: {
        "en": _Frame(
            title="Momentum swings at {clock}",
            body=(
                "{headline} {metrics} The balance of the match has visibly "
                "turned, and the next phase will show who has taken control."
            ),
        ),
        "de": _Frame(
            title="Momentum kippt bei {clock}",
            body=(
                "{headline} {metrics} Die Balance des Spiels hat sich sichtbar "
                "verschoben, und die nächste Phase zeigt, wer die Kontrolle "
                "übernommen hat."
            ),
        ),
        "bn": _Frame(
            title="{clock} ভারসাম্যের দিক পাল্টেছে",
            body=(
                "{headline} {metrics} ম্যাচের ভারসাম্য স্পষ্টভাবে বদলে গেছে, "
                "এবং পরবর্তী পর্বে দেখা যাবে কোন দল নিয়ন্ত্রণ নিয়েছে।"
            ),
        ),
    },
    InsightKind.PRESS_BREAK: {
        "en": _Frame(
            title="Press broken at {clock}",
            body=(
                "{headline} {metrics} The press was beaten in the build-up, and "
                "the defending side now has a chance to reset."
            ),
        ),
        "de": _Frame(
            title="Presse gebrochen bei {clock}",
            body=(
                "{headline} {metrics} Das Pressing wurde im Aufbauspiel "
                "überwunden, und die verteidigende Seite kann jetzt neu "
                "aufbauen."
            ),
        ),
        "bn": _Frame(
            title="{clock} প্রেস ভাঙা",
            body=(
                "{headline} {metrics} বিল্ড-আপে প্রেস ভেঙে দেওয়া হয়েছে, এখন "
                "প্রতিরক্ষাকারী দল নতুন করে সাজানোর সুযোগ পেয়েছে।"
            ),
        ),
    },
    InsightKind.THREAT: {
        "en": _Frame(
            title="Threat developing at {clock}",
            body=(
                "{headline} {metrics} The move is still alive, and the "
                "defending side needs to close the space now."
            ),
        ),
        "de": _Frame(
            title="Gefahr entsteht bei {clock}",
            body=(
                "{headline} {metrics} Der Zug ist noch nicht zu Ende, und die "
                "verteidigende Seite muss den Raum jetzt schließen."
            ),
        ),
        "bn": _Frame(
            title="{clock} বিপদ তৈরি হচ্ছে",
            body=(
                "{headline} {metrics} আক্রমণটি এখনও চলমান, প্রতিরক্ষাকারী দলকে "
                "এখনই জায়গা বন্ধ করতে হবে।"
            ),
        ),
    },
    InsightKind.MILESTONE: {
        "en": _Frame(
            title="Milestone at {clock}",
            body=(
                "{headline} {metrics} A moment worth marking in the match "
                "record."
            ),
        ),
        "de": _Frame(
            title="Meilenstein bei {clock}",
            body=(
                "{headline} {metrics} Ein Moment, den man sich im "
                "Spielprotokoll merken sollte."
            ),
        ),
        "bn": _Frame(
            title="{clock} একটি মাইলফলক",
            body=(
                "{headline} {metrics} ম্যাচের নথিতে ধরে রাখার মতো একটি মুহূর্ত।"
            ),
        ),
    },
    InsightKind.CHANCE_CREATED: {
        "en": _Frame(
            title="Chance created at {clock}",
            body=(
                "{headline} {metrics} A clear opening, and the value of the "
                "chance is the number worth holding on to."
            ),
        ),
        "de": _Frame(
            title="Torchance bei {clock}",
            body=(
                "{headline} {metrics} Eine klare Gelegenheit, und der Torwert "
                "dieser Chance ist die Zahl, auf die es ankommt."
            ),
        ),
        "bn": _Frame(
            title="{clock} সুযোগ তৈরি হয়েছে",
            body=(
                "{headline} {metrics} একটি স্পষ্ট সুযোগ, এবং এই সুযোগের মানটিই "
                "হলো গুরুত্বপূর্ণ সংখ্যা।"
            ),
        ),
    },
    InsightKind.SET_PIECE: {
        "en": _Frame(
            title="Set piece at {clock}",
            body=(
                "{headline} {metrics} Set pieces are where narrow margins are "
                "won, and this delivery is another option for the attacking side."
            ),
        ),
        "de": _Frame(
            title="Standardsituation bei {clock}",
            body=(
                "{headline} {metrics} Bei Standardsituationen entscheiden sich "
                "enge Spiele, und diese Hereingabe ist eine weitere Option für "
                "die angreifende Seite."
            ),
        ),
        "bn": _Frame(
            title="{clock} সেট পিস",
            body=(
                "{headline} {metrics} সেট পিসেই কাছাকাছি ম্যাচের ফল নির্ধারিত হয়, "
                "এবং এই বলটি আক্রমণকারী দলের জন্য আরেকটি বিকল্প।"
            ),
        ),
    },
    InsightKind.DISCIPLINE: {
        "en": _Frame(
            title="Discipline under pressure at {clock}",
            body=(
                "{headline} {metrics} Repeated indiscipline costs a side its "
                "shape and its momentum."
            ),
        ),
        "de": _Frame(
            title="Disziplin unter Druck bei {clock}",
            body=(
                "{headline} {metrics} Wiederholte undisziplinierte Aktionen "
                "kosten einer Mannschaft ihre Ordnung und ihr Momentum."
            ),
        ),
        "bn": _Frame(
            title="{clock} চাপের মধ্যে শৃঙ্খলা",
            body=(
                "{headline} {metrics} বারবার শৃঙ্খলা ভাঙা একটি দলের সাজানো "
                "কাঠামো ও গতিশীলতা দুটোই কাড়ে।"
            ),
        ),
    },
}

# --- Locale details -------------------------------------------------------

#: A short sentence per mode, closing the body in the right register.
_MODE_CLOSERS: dict[str, dict[str, str]] = {
    "analyst": {
        "en": "Every number above comes straight from the event log.",
        "de": "Jede Zahl oben stammt direkt aus dem Ereignisprotokoll.",
        "bn": "উপরের প্রতিটি সংখ্যা সরাসরি ইভেন্ট লগ থেকে এসেছে।",
    },
    "fan": {
        "en": "It is the kind of moment the crowd talks about afterwards.",
        "de": "Ein Moment, über den das Publikum später noch spricht.",
        "bn": "এমন একটি মুহূর্ত যা নিয়ে পরে দর্শকরা কথা বলেন।",
    },
    "player": {
        "en": "It is the kind of detail the players on the pitch can feel.",
        "de": "Ein Detail, das die Spieler auf dem Platz spüren.",
        "bn": "মাঠের খেলোয়াড়রা যা বুঝতে পারেন, এমন একটি বিবরণ।",
    },
}

#: Used when the candidate carries no metrics at all.
_NO_METRICS: dict[str, str] = {
    "en": "No further numbers are attached to this observation.",
    "de": "Zu dieser Beobachtung sind keine weiteren Zahlen hinterlegt.",
    "bn": "এই পর্যবেক্ষণের সাথে আর কোনো সংখ্যা যুক্ত নেই।",
}

#: Translations of the metric labels the analyst produces.
_METRIC_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "pass_accuracy": "pass accuracy",
        "passes_completed": "completed passes",
        "passes": "passes",
        "pass_difficulty": "pass difficulty",
        "pass_distance_m": "average pass distance",
        "shots": "shots",
        "shots_on_target": "shots on target",
        "goals": "goals",
        "xg": "expected goals",
        "xg_conceded": "expected goals conceded",
        "possession_pct": "possession",
        "possession_runs": "spells of possession",
        "longest_run": "longest spell of possession",
        "tempo_breaks": "turnovers won",
        "control_pct": "control",
        "tackles": "tackles",
        "interceptions": "interceptions",
        "saves": "saves",
        "corners": "corners",
        "free_kicks": "free kicks",
        "fouls": "fouls",
        "cards": "cards",
        "total_distance_m": "ball distance",
        "max_speed_mps": "top speed",
        "player_distance_m": "player ground covered",
        "player_total_distance_m": "player ground covered in total",
        "distance_to_goal_m": "distance to goal",
        "shot_distance_m": "shot distance",
        "momentum_index": "momentum index",
        "pressure": "pressure",
        "progress": "field progression",
    },
    "de": {
        "pass_accuracy": "Passquote",
        "passes_completed": "vollendete Pässe",
        "passes": "Pässe",
        "pass_difficulty": "Passschwierigkeit",
        "pass_distance_m": "durchschnittliche Passdistanz",
        "shots": "Schüsse",
        "shots_on_target": "Schüsse aufs Tor",
        "goals": "Tore",
        "xg": "erwartete Tore",
        "xg_conceded": "erwartete Gegentore",
        "possession_pct": "Ballbesitz",
        "possession_runs": "Ballbesitzphasen",
        "longest_run": "längste Ballbesitzphase",
        "tempo_breaks": "eroberte Bälle",
        "control_pct": "Ballkontrolle",
        "tackles": "Zweikämpfe gewonnen",
        "interceptions": "Ballabfang",
        "saves": "Paraden",
        "corners": "Ecken",
        "free_kicks": "Freistöße",
        "fouls": "Fouls",
        "cards": "Karten",
        "total_distance_m": "Ballstrecke",
        "max_speed_mps": "Höchstgeschwindigkeit",
        "player_distance_m": "gelaufene Strecke",
        "player_total_distance_m": "gelaufene Strecke insgesamt",
        "distance_to_goal_m": "Distanz zum Tor",
        "shot_distance_m": "Schussdistanz",
        "momentum_index": "Momentum-Index",
        "pressure": "Pressing",
        "progress": "Feldvorrat",
    },
    "bn": {
        "pass_accuracy": "পাসের নির্ভুলতা",
        "passes_completed": "সম্পন্ন পাস",
        "passes": "পাস",
        "pass_difficulty": "পাসের কঠিনতা",
        "pass_distance_m": "গড় পাসের দূরত্ব",
        "shots": "শট",
        "shots_on_target": "লক্ষ্যমাত্রে শট",
        "goals": "গোল",
        "xg": "প্রত্যাশিত গোল",
        "xg_conceded": "প্রত্যাশিত বিপক্ষের গোল",
        "possession_pct": "বল নিয়ন্ত্রণ",
        "possession_runs": "বল নিয়ন্ত্রণের ধারা",
        "longest_run": "দীর্ঘতম বল নিয়ন্ত্রণ",
        "tempo_breaks": "কাড়ানো বল",
        "control_pct": "বল নিয়ন্ত্রণের হার",
        "tackles": "জিতে নেওয়া ট্যাকল",
        "interceptions": "বাধা",
        "saves": "সেভ",
        "corners": "কর্নার",
        "free_kicks": "ফ্রি কিক",
        "fouls": "ফাউল",
        "cards": "কার্ড",
        "total_distance_m": "বলের দূরত্ব",
        "max_speed_mps": "সর্বোচ্চ গতি",
        "player_distance_m": "খেলোয়াড়ের অতিক্রান্ত দূরত্ব",
        "player_total_distance_m": "মোট অতিক্রান্ত দূরত্ব",
        "distance_to_goal_m": "গোলের দূরত্ব",
        "shot_distance_m": "শটের দূরত্ব",
        "momentum_index": "মোমেন্টাম সূচক",
        "pressure": "চাপ",
        "progress": "মাঠে অগ্রগতি",
    },
}

_ORDINAL_SUFFIX: dict[int, str] = {1: "st", 2: "nd", 3: "rd"}


def _ordinal(value: int) -> str:
    """English ordinal, e.g. ``1st``, ``2nd``, ``3rd``, ``11th``, ``22nd``."""
    suffix = "th" if 10 <= value % 100 <= 20 else _ORDINAL_SUFFIX.get(value % 10, "th")
    return f"{value}{suffix}"


def _clock_phrase(match_clock_ms: int, locale: str) -> str:
    """A locale-appropriate phrase for the match clock."""
    minute = match_clock_ms // 60_000
    if locale == "de":
        return f"der {minute}. Minute"
    if locale == "bn":
        return f"{minute} মিনিটে"
    return f"the {_ordinal(minute)} minute"


def _format_value(value: float, unit: str) -> str:
    """Render one metric value with its unit."""
    if unit == "%":
        return f"{value:.0f}%"
    if unit == "":
        return f"{value:.1f}".rstrip("0").rstrip(".")
    return f"{value:.1f} {unit}"


def _localise_metric(metric: MetricValue, locale: str) -> MetricValue:
    """Return the metric with a label in the target locale."""
    table = _METRIC_LABELS.get(locale, {})
    label = table.get(metric.name, metric.label)
    return metric.model_copy(update={"label": label})


def _metrics_sentence(metrics: list[MetricValue], locale: str) -> str:
    """Join the candidate's metrics into one locale-appropriate sentence.

    The returned string is a complete sentence, terminator included.
    """
    if not metrics:
        return _NO_METRICS[locale]
    parts = [
        f"{metric.label} {_format_value(metric.value, metric.unit)}"
        for metric in metrics
    ]
    joiner = _LIST_JOIN[locale]
    text = ", ".join(parts[:-1]) + joiner + parts[-1] if len(parts) > 1 else parts[0]
    return text + _TERMINATOR[locale]


def _headline_clause(headline: str, locale: str) -> str:
    """Render the analyst's headline as a complete clause.

    The headline is authored by the deterministic analyst and arrives in
    English, so in another locale it is quoted as a term rather than
    mistranslated.
    """
    if locale == "de":
        return f"„{headline}“."
    if locale == "bn":
        return f"“{headline}”।"
    return f"{headline}."


class TemplateProvider:
    """Renders overlays deterministically, without calling a model.

    Example:
        >>> import asyncio
        >>> provider = TemplateProvider()
        >>> provider.name
        'template'
    """

    name: str = ProviderName.TEMPLATE.value

    def __init__(self, mode: str = "analyst") -> None:
        self.default_mode = mode if mode in SUPPORTED_MODES else "analyst"

    async def narrate(
        self,
        candidate: InsightCandidate,
        *,
        locale: str = "en",
        mode: str = "analyst",
    ) -> Overlay:
        """Render one candidate into an overlay.

        The same candidate always produces the same overlay, byte for byte.
        """
        resolved_locale = locale if locale in SUPPORTED_LOCALES else "en"
        resolved_mode = mode if mode in SUPPORTED_MODES else self.default_mode

        frames = _FRAMES[candidate.kind]
        frame = frames.get(resolved_locale, frames["en"])
        metrics = [
            _localise_metric(metric, resolved_locale) for metric in candidate.metrics
        ]

        headline = candidate.headline.strip().rstrip(".")
        clock = _clock_phrase(candidate.match_clock_ms, resolved_locale)
        headline_clause = _headline_clause(headline, resolved_locale)
        metrics_sentence = _metrics_sentence(metrics, resolved_locale)
        closer = _MODE_CLOSERS[resolved_mode].get(
            resolved_locale, _MODE_CLOSERS[resolved_mode]["en"]
        )

        title = frame.title.format(clock=clock, headline=headline)
        body = frame.body.format(
            headline=headline_clause,
            metrics=metrics_sentence,
            clock=clock,
        )
        body = f"{body} {closer}"

        overlay_type = _OVERLAY_TYPES[candidate.kind]
        return Overlay(
            overlay_id=f"ovl-{candidate.candidate_id}",
            type=overlay_type,
            start_ms=candidate.match_clock_ms,
            end_ms=candidate.match_clock_ms + _DURATION_MS[overlay_type],
            match_clock_ms=candidate.match_clock_ms,
            title=title,
            body=body,
            metrics=metrics,
            cited_event_ids=list(candidate.cited_event_ids),
            locale=resolved_locale,
            confidence=candidate.confidence,
        )
