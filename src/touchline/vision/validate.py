"""The cross-validation: what the tracker got right, and what it got wrong.

A tracker that says "22 players tracked" has said nothing. The number that
matters is how close the tracked positions are to where the players
actually were -- and that can only be measured against a truth, which is
why the feed is built backwards.

So this module compares two things, frame by frame:

* the **detections** the tracker made in the rendered video, and
* the **truth** the video was rendered from.

and reports, per class and overall:

* **precision** -- of the things the tracker said were there, how many were.
* **recall** -- of the things that were there, how many the tracker found.
* the **localisation error**, in metres, for the ones it did find.
* the **id switches** -- how often a track changed its mind about which
  player it was following.

On top of that it checks the three numbers a broadcast would use: the
**ball's speed**, the **distance of every pass**, and the **speed of every
shot** -- against the event log, which is where those numbers come from in
the first place. That is the part of the check that cannot be faked by
drawing a clean picture: the tracker has to recover a pass of 29.7 m from
a noisy disc in a video, and the log says the pass was 29.7 m.
"""

from __future__ import annotations

import logging
from math import hypot
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field

from touchline.vision.feed import BallFlight, TruthClip
from touchline.vision.tracker import (
    CLASS_AWAY,
    CLASS_BALL,
    CLASS_HOME,
    DetectedFlight,
    TrackingResult,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ClassScores",
    "TrackingReport",
    "build_report",
    "validate",
]

#: How close a detection has to be to a truth to count as the same thing.
MATCH_RADIUS_M = 2.0


class ClassScores(BaseModel):
    """How the tracker did on one class of thing.

    Attributes:
        true_positives: The things it found that were there.
        false_positives: The things it said were there that were not.
        false_negatives: The things that were there that it missed.
    """

    model_config = ConfigDict(validate_assignment=True)

    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    errors_m: list[float] = Field(default_factory=list, exclude=True)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def precision(self) -> float:
        """Of the things it claimed, how many were really there."""
        denominator = self.true_positives + self.false_positives
        if denominator == 0:
            return 0.0
        return self.true_positives / denominator

    @computed_field  # type: ignore[prop-decorator]
    @property
    def recall(self) -> float:
        """Of the things that were there, how many it found."""
        denominator = self.true_positives + self.false_negatives
        if denominator == 0:
            return 0.0
        return self.true_positives / denominator

    @computed_field  # type: ignore[prop-decorator]
    @property
    def f1(self) -> float:
        """The two of them, together."""
        precision, recall = self.precision, self.recall
        if precision + recall == 0:
            return 0.0
        return 2 * precision * recall / (precision + recall)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def mean_error_m(self) -> float:
        """How far off it was, on average, in metres."""
        if not self.errors_m:
            return 0.0
        return sum(self.errors_m) / len(self.errors_m)


class TrackingReport(BaseModel):
    """What the tracker managed, measured against the truth.

    Attributes:
        frames: How many frames were tracked.
        fps: How many frames a second.
        duration_ms: How much match time the clip covers.
        ball: How the tracker did on the ball.
        home: How the tracker did on the home team.
        away: How the tracker did on the away team.
        overall: How it did on everything together.
        ball_speed_mae_mps: How far off the ball's speed was, on average.
        flight_distance_mae_m: How far off the passes were, on average.
        flight_count_truth: How many flights the log says there were.
        flight_count_detected: How many the tracker found.
        flight_matches: How many of the log's flights were matched.
        shot_speed_mae_mps: How far off the shots' speeds were.
        id_switches: How often a track changed its mind about which player
            it was following.
        tracks_home: How many home players were tracked.
        tracks_away: How many away players were tracked.
        ball_top_speed_mps: The fastest the tracker saw the ball go.
    """

    frames: int = 0
    fps: int = 0
    duration_ms: int = 0
    ball: ClassScores = Field(default_factory=ClassScores)
    home: ClassScores = Field(default_factory=ClassScores)
    away: ClassScores = Field(default_factory=ClassScores)
    overall: ClassScores = Field(default_factory=ClassScores)
    ball_speed_mae_mps: float = 0.0
    flight_distance_mae_m: float = 0.0
    flight_count_truth: int = 0
    flight_count_detected: int = 0
    flight_matches: int = 0
    shot_speed_mae_mps: float = 0.0
    id_switches: int = 0
    tracks_home: int = 0
    tracks_away: int = 0
    ball_top_speed_mps: float = 0.0


def _match(
    truth: list[tuple[float, float]],
    detections: list[tuple[float, float]],
) -> tuple[int, int, int, list[float]]:
    """Match detections to truths, nearest first.

    Returns:
        The true positives, the false positives, the false negatives, and
        how far off the matched ones were, in metres.
    """
    pairs: list[tuple[float, int, int]] = []
    for det_index, (dx, dy) in enumerate(detections):
        for truth_index, (tx, ty) in enumerate(truth):
            distance = hypot(dx - tx, dy - ty)
            if distance <= MATCH_RADIUS_M:
                pairs.append((distance, det_index, truth_index))
    pairs.sort()

    used_detections: set[int] = set()
    used_truths: set[int] = set()
    errors: list[float] = []
    for distance, det_index, truth_index in pairs:
        if det_index in used_detections or truth_index in used_truths:
            continue
        used_detections.add(det_index)
        used_truths.add(truth_index)
        errors.append(distance)

    return (
        len(used_truths),
        len(detections) - len(used_detections),
        len(truth) - len(used_truths),
        errors,
    )


def _class_of(player_id: str, home_players: set[str]) -> str:
    """Which team a player is on."""
    return CLASS_HOME if player_id in home_players else CLASS_AWAY


def validate(
    clip: TruthClip,
    result: TrackingResult,
) -> TrackingReport:
    """Measure the tracker against the truth.

    Args:
        clip: The truth the video was rendered from.
        result: What the tracker made of the video.

    Returns:
        The report.
    """
    home_players = set(clip.home_players)
    scores: dict[str, ClassScores] = {
        CLASS_BALL: ClassScores(),
        CLASS_HOME: ClassScores(),
        CLASS_AWAY: ClassScores(),
    }

    for truth_frame, tracked in zip(clip.frames, result.frames, strict=False):
        truth_by_class: dict[str, list[tuple[float, float]]] = {
            CLASS_BALL: [truth_frame.ball],
            CLASS_HOME: [],
            CLASS_AWAY: [],
        }
        for player_id, position in truth_frame.players.items():
            truth_by_class[_class_of(player_id, home_players)].append(position)

        detected_by_class: dict[str, list[tuple[float, float]]] = {
            CLASS_BALL: [],
            CLASS_HOME: [],
            CLASS_AWAY: [],
        }
        for detection in tracked.detections:
            detected_by_class[detection.class_name].append(
                (detection.x_m, detection.y_m)
            )

        for class_name, score in scores.items():
            true_positives, false_positives, false_negatives, errors = _match(
                truth_by_class[class_name], detected_by_class[class_name]
            )
            score.true_positives += true_positives
            score.false_positives += false_positives
            score.false_negatives += false_negatives
            score.errors_m.extend(errors)

    overall = ClassScores()
    for score in scores.values():
        overall.true_positives += score.true_positives
        overall.false_positives += score.false_positives
        overall.false_negatives += score.false_negatives
        overall.errors_m.extend(score.errors_m)

    report = TrackingReport(
        frames=len(clip.frames),
        fps=clip.fps,
        duration_ms=clip.duration_ms,
        ball=scores[CLASS_BALL],
        home=scores[CLASS_HOME],
        away=scores[CLASS_AWAY],
        overall=overall,
        tracks_home=sum(
            1 for track in result.tracks if track.class_name == CLASS_HOME
        ),
        tracks_away=sum(
            1 for track in result.tracks if track.class_name == CLASS_AWAY
        ),
    )

    report.ball_speed_mae_mps = _ball_speed_error(clip, result)
    report.flight_distance_mae_m, report.flight_matches = _flight_error(
        clip.flights, result.flights
    )
    report.flight_count_truth = len(clip.flights)
    report.flight_count_detected = len(result.flights)
    report.shot_speed_mae_mps = _shot_speed_error(clip.flights, result.flights)
    report.id_switches = _id_switches(clip, result, home_players)
    report.ball_top_speed_mps = max(
        (flight.peak_speed_mps for flight in result.flights), default=0.0
    )
    return report


def _ball_speed_error(clip: TruthClip, result: TrackingResult) -> float:
    """How far off the ball's speed was, frame by frame.

    The truth's speed is the ball's own movement between two frames, which
    is exactly what the tracker measures, so the two are comparable.
    """
    errors: list[float] = []
    for index, truth_frame in enumerate(clip.frames):
        if index >= len(result.frames):
            break
        tracked = result.frames[index]
        if tracked.ball_position is None:
            continue
        previous = clip.frames[index - 1] if index > 0 else None
        if previous is None:
            continue
        seconds = (truth_frame.clock_ms - previous.clock_ms) / 1000.0
        if seconds <= 0:
            continue
        truth_speed = hypot(
            truth_frame.ball[0] - previous.ball[0],
            truth_frame.ball[1] - previous.ball[1],
        ) / seconds
        errors.append(abs(truth_speed - tracked.ball_speed_mps))
    if not errors:
        return 0.0
    return sum(errors) / len(errors)


def _overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> int:
    """How many milliseconds two stretches of time share."""
    return max(0, min(a_end, b_end) - max(a_start, b_start))


def _match_flights(
    truth: list[BallFlight], detected: list[DetectedFlight]
) -> list[tuple[BallFlight, DetectedFlight]]:
    """Pair the log's flights with the ones the tracker found.

    A pair is the truth flight and the detected flight that share the most
    time with it. Each flight is used once.
    """
    pairs: list[tuple[int, int, int]] = []
    for truth_index, flight in enumerate(truth):
        for detected_index, found in enumerate(detected):
            overlap = _overlap(
                flight.start_ms, flight.end_ms, found.start_ms, found.end_ms
            )
            if overlap > 0:
                pairs.append((overlap, truth_index, detected_index))
    pairs.sort(reverse=True)

    used_truth: set[int] = set()
    used_detected: set[int] = set()
    matched: list[tuple[BallFlight, DetectedFlight]] = []
    for _, truth_index, detected_index in pairs:
        if truth_index in used_truth or detected_index in used_detected:
            continue
        used_truth.add(truth_index)
        used_detected.add(detected_index)
        matched.append((truth[truth_index], detected[detected_index]))
    return matched


def _flight_error(
    truth: list[BallFlight], detected: list[DetectedFlight]
) -> tuple[float, int]:
    """How far off the pass distances were, and how many were matched."""
    matched = _match_flights(truth, detected)
    if not matched:
        return 0.0, 0
    errors = [
        abs(found.distance_m - flight.distance_m) for flight, found in matched
    ]
    return sum(errors) / len(errors), len(matched)


def _shot_speed_error(
    truth: list[BallFlight], detected: list[DetectedFlight]
) -> float:
    """How far off the shots' speeds were, in metres per second."""
    matched = _match_flights(
        [flight for flight in truth if flight.kind == "shot"], detected
    )
    if not matched:
        return 0.0
    errors = [
        abs(found.peak_speed_mps - flight.speed_mps) for flight, found in matched
    ]
    return sum(errors) / len(errors)


def _id_switches(
    clip: TruthClip, result: TrackingResult, home_players: set[str]
) -> int:
    """How often a track changed its mind about which player it was on.

    A tracker that keeps its tags straight is worth much more than one
    that merely finds the right number of blobs, so the switches are
    counted: for every track, the sequence of players it was matched to,
    and every change in that sequence is a switch.
    """
    truth_positions: list[dict[str, tuple[float, float]]] = [
        frame.players for frame in clip.frames
    ]

    switches = 0
    for track in result.tracks:
        if track.class_name == CLASS_BALL:
            continue
        previous: str | None = None
        for index, position in enumerate(track.positions):
            if index >= len(truth_positions):
                break
            players = truth_positions[index]
            best: str | None = None
            best_distance = MATCH_RADIUS_M
            for player_id, truth_position in players.items():
                if _class_of(player_id, home_players) != track.class_name:
                    continue
                distance = hypot(
                    position[0] - truth_position[0], position[1] - truth_position[1]
                )
                if distance < best_distance:
                    best_distance = distance
                    best = player_id
            if best is None:
                continue
            if previous is not None and best != previous:
                switches += 1
            previous = best
    return switches


def build_report(
    seed: int = 42,
    *,
    duration_ms: int = 20_000,
    fps: int = 25,
) -> TrackingReport:
    """Build the truth, shoot the feed, track it, and measure the result.

    Args:
        seed: Which match to shoot.
        duration_ms: How much match time the clip covers.
        fps: How many frames a second.

    Returns:
        The report.
    """
    from touchline.vision.feed import build_truth_clip
    from touchline.vision.geometry import DEFAULT_CAMERA
    from touchline.vision.render import render_clip
    from touchline.vision.tracker import VisionTracker

    clip = build_truth_clip(seed, duration_ms=duration_ms, fps=fps)
    images = render_clip(
        clip.frames,
        DEFAULT_CAMERA,
        set(clip.home_players),
        seed=seed,
    )
    tracker = VisionTracker(DEFAULT_CAMERA)
    result = tracker.run(images, [frame.clock_ms for frame in clip.frames])
    return validate(clip, result)


def report_to_dict(report: TrackingReport) -> dict[str, Any]:
    """The report, in the shape the API sends it."""
    return report.model_dump(mode="json")
