"""The live session: one viewer's watch of one match.

The **engine** computes; the **session** plays. The engine analyses the
match stretch by stretch as fast as the model allows and keeps the
answers. The session has its own clock, its own speed and its own place
in the feed, and reveals each answer when its clock reaches it.

That split is what makes the demo possible and what makes it honest:

* the *analysis* is real, incremental and causal -- the insight for 12:00
  was computed from the log as it stood at 12:00, not from the whole match
  and not from a script;
* the *pacing* is a presentation choice, and it is the only thing that is.
  A judge watching for three minutes sees a whole match, and the studio
  can be paused, sped up or jumped forward without the analysis changing,
  because the analysis for a stretch does not depend on when it was
  computed.

Two viewers of the same match share one engine, so the model is asked
once, not twice.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from touchline.live.engine import LiveMatchEngine
from touchline.metrics.definitions import METRICS
from touchline.models.audience import Audience, AudienceMode
from touchline.models.events import TeamSide
from touchline.providers import foundry
from touchline.providers import status as provider_status
from touchline.providers.template import SUPPORTED_LOCALES

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_SPEED",
    "SPEED_CHOICES",
    "LiveSession",
    "get_engine",
    "list_seeds",
    "overlay_candidate_id",
    "parse_audience",
    "set_engine_hook",
]

#: How much match time one second of wall clock is worth. Thirty means
#: thirty match-seconds a second, so a ninety-minute match takes three
#: minutes to watch.
DEFAULT_SPEED = 30.0

#: The speeds the studio offers. The slowest is long enough to read the
#: trace; the fastest is a highlight reel.
SPEED_CHOICES = (10.0, 20.0, 30.0, 45.0, 60.0)

#: The engines, one per match, shared by every viewer.
_engines: dict[int, LiveMatchEngine] = {}

#: Called when a new engine is made, before it starts. The API layer uses
#: it to publish the engine's Explainer as an A2A agent and to hand the
#: Narrator the client that reaches it.
_engine_hook: Callable[[LiveMatchEngine], None] | None = None


def set_engine_hook(hook: Callable[[LiveMatchEngine], None] | None) -> None:
    """Set the hook called when a new engine is made.

    Args:
        hook: The hook, or ``None`` to remove it.
    """
    global _engine_hook
    _engine_hook = hook


def get_engine(seed: int = 42, *, start: bool = True) -> LiveMatchEngine:
    """The engine for a match, made once and shared.

    Args:
        seed: Which match. The same seed is the same match.
        start: Whether to start analysing straight away. The API layer
            passes ``False`` while it is still wiring the engine up, and
            starts it once the wiring is done.

    Returns:
        The engine. It is already running unless ``start`` was ``False``.
    """
    engine = _engines.get(seed)
    if engine is None:
        engine = LiveMatchEngine(seed=seed)
        _engines[seed] = engine
        if _engine_hook is not None:
            _engine_hook(engine)
        if start:
            engine.start()
            # The vision tracker is a secondary path: it is shot and scored
            # in the background, and the demo never waits for it.
            engine.start_tracking()
        logger.info("started the live analysis of seed %s", seed)
    elif start:
        engine.start()
        engine.start_tracking()
    return engine


def list_seeds() -> list[int]:
    """The matches that can be watched."""
    return sorted(_engines) or [42, 7, 99]


def provider_payload() -> dict[str, object]:
    """The provider state, in the shape the badge in the studio reads.

    The badge shows the provider that actually served the last call, with
    a live or fallback chip. The *configured* provider is shown
    separately, as static text, so that the two facts never contradict
    each other.
    """
    return provider_status.current(foundry.is_available()).as_dict()


def parse_audience(value: object, engine: LiveMatchEngine) -> Audience | None:
    """Read an audience out of a control message.

    The message is a small object the studio sends: the mode, the locale,
    and whichever of the side, the player and the metric the mode needs.
    Anything unreadable is refused rather than guessed at, because a wrong
    audience would put the wrong numbers on screen.

    A language the service does not write is the one thing that is kept
    rather than refused: the reader stays where they are, and the studio
    is told what was actually applied, so the picker shows the truth
    instead of a language nobody is reading in.

    Args:
        value: The control message's value.
        engine: The engine whose roster the player is looked up in.

    Returns:
        The audience, or ``None`` when the message cannot be read.
    """
    if not isinstance(value, dict):
        return None

    raw_mode = str(value.get("mode", "")).lower()
    if raw_mode not in {mode.value for mode in AudienceMode}:
        return None
    mode = AudienceMode(raw_mode)

    locale = str(value.get("locale", "")).lower()
    if locale not in SUPPORTED_LOCALES:
        locale = engine.audience.locale

    side: TeamSide | None = None
    raw_side = value.get("side")
    if isinstance(raw_side, str) and raw_side.upper() in {s.value for s in TeamSide}:
        side = TeamSide(raw_side.upper())

    player_id: str | None = None
    player_name: str | None = None
    raw_player = value.get("player_id")
    if isinstance(raw_player, str) and raw_player:
        for player in engine.roster:
            if player["player_id"] == raw_player:
                player_id = raw_player
                player_name = str(player["name"])
                break

    metric: str | None = None
    raw_metric = value.get("metric")
    if isinstance(raw_metric, str) and raw_metric in METRICS:
        metric = raw_metric

    return Audience(
        mode=mode,
        locale=locale,
        side=side,
        player_id=player_id,
        player_name=player_name,
        metric=metric,
    )


@dataclass
class LiveSession:
    """One viewer's watch of one match.

    Attributes:
        engine: The engine doing the work.
        speed: How much match time one wall-clock second is worth.
        playing: Whether the clock is running.
        clock_ms: Where the clock is, in match time.
    """

    engine: LiveMatchEngine
    speed: float = DEFAULT_SPEED
    playing: bool = True
    clock_ms: float = 0.0

    _last_tick: float = field(default_factory=time.monotonic)
    _event_cursor: int = 0
    _overlay_cursor: int = 0
    _momentum_cursor: int = 0
    _trace_cursor: int = 0
    _feed_version: int = 0
    _audience_key: str = ""
    _rewriting: bool = False
    _events: list[Any] = field(default_factory=list)
    _momentum: list[Any] = field(default_factory=list)
    _seen_overlays: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        """Take a copy of what the session walks through."""
        self._events = list(self.engine.events)
        self._momentum = list(self.engine.momentum)
        # The audience the engine is already writing for. The studio is
        # told about it in the greeting frame, so the first frame is not
        # a repeat of it -- and, more to the point, the first frame is
        # never a stale one that would talk the studio out of a reader
        # change it has just asked for.
        self._audience_key = self.engine.audience.key
        self._rewriting = self.engine.rewriting

    # -- the control surface ----------------------------------------------

    def play(self) -> None:
        """Start the clock."""
        self.playing = True
        self._last_tick = time.monotonic()

    def pause(self) -> None:
        """Stop the clock."""
        self.playing = False

    def replay(self) -> None:
        """Start the match again from the beginning."""
        self.clock_ms = 0.0
        self._event_cursor = 0
        self._overlay_cursor = 0
        self._momentum_cursor = 0
        self._trace_cursor = 0
        self._seen_overlays.clear()
        self.playing = True
        self._last_tick = time.monotonic()

    def set_speed(self, speed: float) -> None:
        """Change how fast the match plays.

        Args:
            speed: Match-seconds per wall-clock second. Positive numbers
                only; anything else is ignored.
        """
        if speed > 0:
            self.speed = float(speed)

    def seek(self, clock_ms: int) -> None:
        """Move the clock.

        The analysis does not change: an insight for 12:00 is the same
        insight whether the clock reaches it now or in a minute, because
        the analyst is causal. So seeking is safe, and it is what makes
        "jump to the next key moment" work.

        Args:
            clock_ms: Where to move the clock to.
        """
        target = max(0, min(float(clock_ms), float(self.engine.duration_ms)))
        self.clock_ms = target
        self._last_tick = time.monotonic()

    def next_moment(self) -> int | None:
        """The match clock of the next insight after the current one.

        Returns:
            The clock to jump to, or ``None`` when there is no further
            insight computed yet.
        """
        clocks = self.engine.overlay_clocks()
        for clock in clocks:
            if clock > int(self.clock_ms):
                return clock
        return None

    async def set_audience(self, audience: Audience) -> None:
        """Change who the overlays are written for.

        The engine changes its state at once and rewrites the feed in the
        background, so this returns as soon as the change is recorded. The
        session walks the feed again when the rewritten cards arrive --
        see :meth:`_overlay_frames` -- because a rewritten card keeps its
        id and the viewer has to be shown the new copy of it.

        The session's clock goes with it: the rewrite starts at the
        moments nearest where this viewer is looking, because those are
        the ones on the screen. A rewrite of the whole feed takes minutes
        at the deployment's rate, and the reader should not spend them
        looking at a card that is still in the old language.

        Args:
            audience: Who the overlays are written for.
        """
        await self.engine.set_audience(audience, focus_ms=int(self.clock_ms))

    # -- the frames --------------------------------------------------------

    def hello(self) -> dict[str, object]:
        """The first frame: what the studio needs to draw the match."""
        engine = self.engine
        return {
            "type": "hello",
            "match_id": engine.match_id,
            "seed": engine.seed,
            "teams": engine.teams,
            "team_names": {
                "home": engine.team_names[TeamSide.HOME],
                "away": engine.team_names[TeamSide.AWAY],
            },
            "duration_ms": engine.duration_ms,
            "stretch_ms": engine.stretch_ms,
            "speed": self.speed,
            "clock_ms": int(self.clock_ms),
            "provider": provider_payload(),
            "event_count": len(self._events),
            "audience": self.engine.audience.describe(),
            "rewriting": self.engine.rewriting,
        }

    def tick(self, now: float | None = None) -> list[dict[str, object]]:
        """Advance the clock and return the frames that fall due.

        Args:
            now: The current time. The clock is advanced from the last
                tick to this one.

        Returns:
            The frames to send, in the order they should be sent.
        """
        moment = time.monotonic() if now is None else now
        if self.playing:
            self.clock_ms += max(0.0, moment - self._last_tick) * self.speed * 1000.0
        self._last_tick = moment

        clock = int(self.clock_ms)
        if clock >= self.engine.duration_ms:
            clock = self.engine.duration_ms
            self.playing = False

        frames: list[dict[str, object]] = []
        frames.extend(self._event_frames(clock))
        frames.extend(self._momentum_frames(clock))
        frames.extend(self._overlay_frames(clock))
        frames.extend(self._trace_frames())
        frames.extend(self._audience_frames())
        frames.append(self._clock_frame(clock))
        return frames

    def _audience_frames(self) -> list[dict[str, object]]:
        """Who the overlays are written for, and whether it is still changing.

        The reader is service state, so the studio is told what the
        service is doing rather than what it asked for: a change made
        here, or by somebody else watching the same match, reaches every
        viewer. The *rewriting* flag is on the frame too, because a
        rewrite takes minutes at the deployment's rate and a feed that is
        still being rewritten is not a finished one.
        """
        audience = self.engine.audience
        rewriting = self.engine.rewriting
        if audience.key == self._audience_key and rewriting == self._rewriting:
            return []
        self._audience_key = audience.key
        self._rewriting = rewriting
        return [
            {
                "type": "audience",
                "audience": audience.describe(),
                "rewriting": rewriting,
            }
        ]

    def _event_frames(self, clock: int) -> list[dict[str, object]]:
        """The events the clock has just passed."""
        batch: list[dict[str, object]] = []
        while self._event_cursor < len(self._events):
            event = self._events[self._event_cursor]
            if event.match_clock_ms > clock:
                break
            batch.append(_event_payload(event))
            self._event_cursor += 1
        if not batch:
            return []
        return [{"type": "events", "events": batch}]

    def _momentum_frames(self, clock: int) -> list[dict[str, object]]:
        """The points the chart has just reached."""
        batch: list[dict[str, object]] = []
        while self._momentum_cursor < len(self._momentum):
            point = self._momentum[self._momentum_cursor]
            if point.match_clock_ms > clock:
                break
            batch.append(
                {
                    "match_clock_ms": point.match_clock_ms,
                    "home": point.home,
                    "away": point.away,
                }
            )
            self._momentum_cursor += 1
        if not batch:
            return []
        return [{"type": "momentum", "points": batch}]

    def _overlay_frames(self, clock: int) -> list[dict[str, object]]:
        """The insights the clock has just reached.

        An overlay that arrives after the clock has passed its moment is
        still shown: the clock does not wait for the model, the model
        catches up with the clock. That is the difference between a live
        feed that stalls and one that does not.

        When the reader changes, the feed is rewritten and every card
        keeps its id, so the walk starts again: the viewer is shown the
        same moments, written for somebody else.
        """
        frames: list[dict[str, object]] = []
        if self.engine.feed_version != self._feed_version:
            self._feed_version = self.engine.feed_version
            self._overlay_cursor = 0
            self._seen_overlays.clear()
        overlays = self.engine.overlays
        while self._overlay_cursor < len(overlays):
            overlay = overlays[self._overlay_cursor]
            self._overlay_cursor += 1
            if overlay.overlay_id in self._seen_overlays:
                continue
            if overlay.match_clock_ms > clock:
                # Not yet. Put it back for the next tick.
                self._overlay_cursor -= 1
                break
            self._seen_overlays.add(overlay.overlay_id)
            frames.append(
                {
                    "type": "overlay",
                    "clock_ms": overlay.match_clock_ms,
                    "candidate_id": overlay_candidate_id(overlay.overlay_id),
                    "overlay": overlay.model_dump(mode="json"),
                }
            )
        return frames

    def _trace_frames(self) -> list[dict[str, object]]:
        """The hand-offs the analysis has made since the last frame."""
        entries = self.engine.trace.as_list()
        if len(entries) <= self._trace_cursor:
            return []
        batch = entries[self._trace_cursor :]
        self._trace_cursor = len(entries)
        return [
            {
                "type": "trace",
                "entries": [entry.model_dump(mode="json") for entry in batch],
            }
        ]

    def _clock_frame(self, clock: int) -> dict[str, object]:
        """Where the clock is, and how far the analysis has got."""
        score_home, score_away = _score_at(self._events, clock)
        return {
            "type": "clock",
            "clock_ms": clock,
            "score_home": score_home,
            "score_away": score_away,
            "period": 1 if clock < 2_700_000 else 2,
            "computed_ms": self.engine.computed_ms,
            "complete": clock >= self.engine.duration_ms,
        }


def overlay_candidate_id(overlay_id: str) -> str:
    """The insight id behind an overlay id.

    The overlay is named after the insight it came from, so the studio can
    link an overlay back to the trace entries about the same moment. Both
    the socket frame and the REST payload go through here, so the two
    endpoints cannot disagree about which insight an overlay is about.
    """
    if overlay_id.startswith("ovl-"):
        return overlay_id[4:]
    return overlay_id


def _event_payload(event: Any) -> dict[str, object]:
    """One event, in the shape the studio's pitch draws."""
    return {
        "event_id": event.event_id,
        "type": event.type.value,
        "match_clock_ms": event.match_clock_ms,
        "period": event.period,
        "side": event.side.value if event.side else None,
        "player_name": event.player_name,
        "target_player_name": event.target_player_name,
        "x": event.x,
        "y": event.y,
        "end_x": event.end_x,
        "end_y": event.end_y,
        "outcome": event.outcome,
        "value": event.value,
        "distance_m": event.distance_m,
        "difficulty": event.difficulty,
        "speed_mps": event.speed_mps,
        "card": event.card.value if event.card else None,
        "score_home": event.score_home,
        "score_away": event.score_away,
    }


def _score_at(events: list[Any], clock: int) -> tuple[int, int]:
    """The score at a point in the match."""
    home = 0
    away = 0
    for event in events:
        if event.match_clock_ms > clock:
            break
        home = event.score_home
        away = event.score_away
    return home, away
