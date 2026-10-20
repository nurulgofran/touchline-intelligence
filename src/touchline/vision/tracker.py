"""The tracker: OpenCV, on a synthetic feed, in real time.

This is a real computer-vision pipeline, not a simulation of one. It is
the three things a broadcast tracking system is made of:

**Thresholding.** The frame is converted to HSV and each class -- the ball,
the home team, the away team -- is a range in that space. The ball is
bright and unsaturated; the teams are saturated and in their own hue band.
The markings are painted dim on purpose, so they cannot leak into the
ball's range.

**Prediction.** Every track carries a ``cv2.KalmanFilter``: a constant
velocity model in pixel space, so that a detection which misses a frame
does not throw the track away, and a detection which is a little wrong does
not throw it either.

**Association.** Detections are matched to tracks greedily, nearest first,
inside a gate. A detection that matches nothing starts a new track; a
track that matches nothing for a while is dropped.

What comes out is the ball's speed, the distance of every pass the ball
made, and a stable tag for every player on the pitch -- which is what the
cross-validation then measures against the truth.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from math import hypot

import numpy as np

from touchline.vision.geometry import Camera

logger = logging.getLogger(__name__)

__all__ = [
    "CLASS_AWAY",
    "CLASS_BALL",
    "CLASS_HOME",
    "DetectedFlight",
    "Detection",
    "FrameResult",
    "Track",
    "TrackingResult",
    "VisionTracker",
]

#: The three things the tracker looks for.
CLASS_BALL = "ball"
CLASS_HOME = "home"
CLASS_AWAY = "away"

#: The smallest blob that counts as a detection, in pixels. Below this it
#: is noise, not a player.
MIN_AREA_PX = 6.0

#: The biggest blob that counts as a detection, in pixels. A player drawn
#: as a disc of 0.9 m at this resolution is about 45 square pixels; the
#: limit is well above that and well below the pitch.
MAX_AREA_PX = 4_000.0

#: How far a detection may be from a track's prediction and still be
#: matched to it, in metres.
GATE_M = 3.5

#: How many frames a track may go unmatched before it is dropped.
MAX_MISSED_FRAMES = 8

#: The ball's speed above which it is travelling rather than being carried,
#: in metres per second.
FLIGHT_SPEED_MPS = 6.0

#: How many frames a flight has to last to count as one.
MIN_FLIGHT_FRAMES = 4


@dataclass(frozen=True)
class Detection:
    """Something the tracker found in one frame.

    Attributes:
        x_m: Where it was, along the pitch, in metres.
        y_m: Where it was, across the pitch, in metres.
        area_px: How big it was, in square pixels.
        class_name: Which of the three things it is.
    """

    x_m: float
    y_m: float
    area_px: float
    class_name: str


@dataclass
class Track:
    """One thing the tracker has been following.

    Attributes:
        track_id: The tracker's own tag for it. The tag is what the
            cross-validation counts the switches of.
        class_name: Which of the three things it is.
        positions: Where it was, frame by frame, in metres.
        times_ms: The clock each position belongs to.
        speeds_mps: How fast it was moving, frame by frame.
        missed: How many frames in a row it has not been seen.
    """

    track_id: int
    class_name: str
    positions: list[tuple[float, float]] = field(default_factory=list)
    times_ms: list[int] = field(default_factory=list)
    speeds_mps: list[float] = field(default_factory=list)
    missed: int = 0

    @property
    def frames(self) -> int:
        """How many frames it was seen in."""
        return len(self.positions)

    @property
    def total_distance_m(self) -> float:
        """How far it travelled, in metres."""
        total = 0.0
        for previous, current in zip(self.positions, self.positions[1:], strict=False):
            total += hypot(current[0] - previous[0], current[1] - previous[1])
        return total

    @property
    def max_speed_mps(self) -> float:
        """How fast it ever went, in metres per second."""
        return max(self.speeds_mps, default=0.0)

    @property
    def mean_speed_mps(self) -> float:
        """How fast it went on average, in metres per second."""
        if not self.speeds_mps:
            return 0.0
        return sum(self.speeds_mps) / len(self.speeds_mps)


@dataclass(frozen=True)
class DetectedFlight:
    """One stretch of the ball's travel, as the tracker saw it.

    Attributes:
        start_ms: When the ball set off.
        end_ms: When it stopped.
        distance_m: How far it went.
        peak_speed_mps: How fast it was going at its quickest.
        mean_speed_mps: How fast it went on average.
    """

    start_ms: int
    end_ms: int
    distance_m: float
    peak_speed_mps: float
    mean_speed_mps: float


@dataclass(frozen=True)
class FrameResult:
    """What the tracker made of one frame.

    Attributes:
        clock_ms: The clock the frame belongs to.
        detections: What it found.
        ball_position: Where it thought the ball was.
        ball_speed_mps: How fast it thought the ball was going.
    """

    clock_ms: int
    detections: list[Detection]
    ball_position: tuple[float, float] | None
    ball_speed_mps: float


@dataclass
class TrackingResult:
    """Everything the tracker made of a clip.

    Attributes:
        frames: What it made of each frame.
        tracks: Every track it kept.
        flights: The ball's flights.
    """

    frames: list[FrameResult] = field(default_factory=list)
    tracks: list[Track] = field(default_factory=list)
    flights: list[DetectedFlight] = field(default_factory=list)


class _KalmanTrack:
    """A track, and the filter that predicts it."""

    def __init__(self, track_id: int, class_name: str, px: float, py: float) -> None:
        import cv2

        self.track = Track(track_id=track_id, class_name=class_name)
        self._filter = cv2.KalmanFilter(4, 2)
        self._filter.statePost = np.array(
            [px, py, 0.0, 0.0], dtype=np.float32
        ).reshape(4, 1)
        self._filter.measurementMatrix = np.array(
            [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]], dtype=np.float32
        )
        self._filter.processNoiseCov = np.eye(4, dtype=np.float32) * 1.0
        self._filter.measurementNoiseCov = np.eye(2, dtype=np.float32) * 2.0
        self._filter.errorCovPost = np.eye(4, dtype=np.float32)
        self.predicted_px = px
        self.predicted_py = py
        self._last_position: tuple[float, float] | None = None
        self._last_ms: int | None = None

    def predict(self) -> None:
        """Move the prediction on to where the track should be."""
        predicted = self._filter.predict()
        self.predicted_px = float(predicted[0, 0])
        self.predicted_py = float(predicted[1, 0])

    def correct(self, px: float, py: float, clock_ms: int, camera: Camera) -> None:
        """Take in a detection and record where the track now is."""
        measurement = np.array([[px], [py]], dtype=np.float32)
        corrected = self._filter.correct(measurement)
        self.predicted_px = float(corrected[0, 0])
        self.predicted_py = float(corrected[1, 0])

        x_m, y_m = camera.to_metres(self.predicted_px, self.predicted_py)
        self.track.positions.append((x_m, y_m))
        self.track.times_ms.append(clock_ms)
        if self._last_ms is not None and clock_ms > self._last_ms:
            seconds = (clock_ms - self._last_ms) / 1000.0
            last = self._last_position or (x_m, y_m)
            speed = hypot(x_m - last[0], y_m - last[1]) / seconds
            self.track.speeds_mps.append(speed)
        self._last_position = (x_m, y_m)
        self._last_ms = clock_ms
        self.track.missed = 0

    def hold(self) -> None:
        """Record that the track was not seen in this frame."""
        self.track.missed += 1

    @property
    def last_position(self) -> tuple[float, float] | None:
        """Where the track was last seen, in metres."""
        return self.track.positions[-1] if self.track.positions else None


class VisionTracker:
    """Tracks the ball and the two teams through a clip.

    Example:
        >>> tracker = VisionTracker(Camera())          # doctest: +SKIP
        >>> result = tracker.run(images, clock_ms)     # doctest: +SKIP
    """

    def __init__(
        self,
        camera: Camera,
        *,
        gate_m: float = GATE_M,
        max_missed: int = MAX_MISSED_FRAMES,
    ) -> None:
        """Create the tracker.

        Args:
            camera: The camera the feed is shot on.
            gate_m: How far a detection may be from a track's prediction
                and still be matched to it.
            max_missed: How many frames a track may go unseen before it is
                dropped.
        """
        self._camera = camera
        self._gate_px = gate_m * camera.pixels_per_metre
        self._max_missed = max_missed
        self._tracks: list[_KalmanTrack] = []
        self._next_id = 1
        self._last_ms: int | None = None

    # -- the detection ----------------------------------------------------

    def detect(self, image: np.ndarray) -> list[Detection]:
        """Find the ball and the two teams in one frame.

        Args:
            image: The frame, as an RGB array.

        Returns:
            Every detection, in pixels converted to metres.
        """
        import cv2

        hsv = cv2.cvtColor(image, cv2.COLOR_RGB2HSV)
        detections: list[Detection] = []
        # OpenCV's hue runs 0..179, so it is half the angle: the blue band
        # is 100..130 and the orange band 5..25.
        for class_name, lower, upper in (
            (CLASS_BALL, (0, 0, 170), (179, 60, 255)),
            (CLASS_HOME, (100, 60, 60), (130, 255, 255)),
            (CLASS_AWAY, (5, 60, 60), (25, 255, 255)),
        ):
            mask = cv2.inRange(
                hsv, np.array(lower, np.uint8), np.array(upper, np.uint8)
            )
            contours, _ = cv2.findContours(
                mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            for contour in contours:
                area = float(cv2.contourArea(contour))
                if area < MIN_AREA_PX or area > MAX_AREA_PX:
                    continue
                moments = cv2.moments(contour)
                if moments["m00"] == 0:
                    continue
                px = moments["m10"] / moments["m00"]
                py = moments["m01"] / moments["m00"]
                x_m, y_m = self._camera.to_metres(px, py)
                detections.append(
                    Detection(
                        x_m=x_m, y_m=y_m, area_px=area, class_name=class_name
                    )
                )
        return detections

    # -- the tracking -----------------------------------------------------

    def step(self, detections: list[Detection], clock_ms: int) -> FrameResult:
        """Match one frame's detections to the tracks, and update them.

        Args:
            detections: What was found in the frame.
            clock_ms: The clock the frame belongs to.

        Returns:
            What the tracker made of the frame.
        """
        pixels = [
            (
                detection,
                *self._camera.to_pixels(detection.x_m, detection.y_m),
            )
            for detection in detections
        ]

        for track in self._tracks:
            track.predict()

        pairs: list[tuple[float, int, int]] = []
        for det_index, (detection, px, py) in enumerate(pixels):
            for track_index, track in enumerate(self._tracks):
                if track.track.class_name != detection.class_name:
                    continue
                distance = hypot(
                    px - track.predicted_px, py - track.predicted_py
                )
                if distance <= self._gate_px:
                    pairs.append((distance, det_index, track_index))
        pairs.sort()

        matched_detections: set[int] = set()
        matched_tracks: set[int] = set()
        for _, det_index, track_index in pairs:
            if det_index in matched_detections or track_index in matched_tracks:
                continue
            matched_detections.add(det_index)
            matched_tracks.add(track_index)
            detection, px, py = pixels[det_index]
            self._tracks[track_index].correct(
                px, py, clock_ms, self._camera
            )

        for index, track in enumerate(self._tracks):
            if index not in matched_tracks:
                track.hold()

        for index, (detection, px, py) in enumerate(pixels):
            if index in matched_detections:
                continue
            self._tracks.append(
                _KalmanTrack(self._next_id, detection.class_name, px, py)
            )
            self._next_id += 1

        alive = [
            track for track in self._tracks if track.track.missed <= self._max_missed
        ]
        self._tracks = alive

        self._last_ms = clock_ms
        return self._frame_result(clock_ms, detections)

    def _frame_result(
        self, clock_ms: int, detections: list[Detection]
    ) -> FrameResult:
        """The frame's result: what was found, and where the ball was."""
        ball_tracks = [
            track for track in self._tracks if track.track.class_name == CLASS_BALL
        ]
        ball_position: tuple[float, float] | None = None
        ball_speed = 0.0
        if ball_tracks:
            best = max(ball_tracks, key=lambda track: track.track.frames)
            position = best.last_position
            if position is not None:
                ball_position = position
            if best.track.speeds_mps:
                ball_speed = best.track.speeds_mps[-1]
        return FrameResult(
            clock_ms=clock_ms,
            detections=list(detections),
            ball_position=ball_position,
            ball_speed_mps=ball_speed,
        )

    # -- the run ----------------------------------------------------------

    def run(
        self, images: Iterable[np.ndarray], clock_ms: Iterable[int]
    ) -> TrackingResult:
        """Track a whole clip.

        The frames are taken one at a time, so a clip can be watched as it
        is shot without the whole video being held in memory.

        Args:
            images: The frames, in order.
            clock_ms: The clock each frame belongs to.

        Returns:
            Everything the tracker made of the clip.
        """
        result = TrackingResult()
        for image, clock in zip(images, clock_ms, strict=False):
            detections = self.detect(image)
            result.frames.append(self.step(detections, clock))
        result.tracks = [track.track for track in self._tracks]
        result.flights = self._flights(result.frames)
        return result

    def _flights(self, frames: list[FrameResult]) -> list[DetectedFlight]:
        """The stretches the ball spent travelling.

        A flight is a run of frames in which the ball is going faster than
        a carried ball goes. The distance is measured from where the ball
        was sitting when it set off -- which is the frame before the run,
        because the first frame of the run already has the ball a frame's
        worth of travel away from where it was -- so that a pass of thirty
        metres is not measured as twenty-nine.

        The speed is smoothed over three frames first, because the
        difference between two noisy positions is noisy, and a flight has
        to last long enough to be more than noise.
        """
        flights: list[DetectedFlight] = []
        run: list[FrameResult] = []

        def close(run_frames: list[FrameResult], before: FrameResult | None) -> None:
            if len(run_frames) < MIN_FLIGHT_FRAMES:
                return
            distance = 0.0
            peak = 0.0
            speeds: list[float] = []
            previous: tuple[float, float] | None = None
            if before is not None:
                previous = before.ball_position
            for frame in run_frames:
                if frame.ball_position is None:
                    continue
                if previous is not None:
                    distance += hypot(
                        frame.ball_position[0] - previous[0],
                        frame.ball_position[1] - previous[1],
                    )
                previous = frame.ball_position
                peak = max(peak, frame.ball_speed_mps)
                speeds.append(frame.ball_speed_mps)
            if previous is None or not speeds:
                return
            flights.append(
                DetectedFlight(
                    start_ms=(
                        before.clock_ms
                        if before is not None
                        else run_frames[0].clock_ms
                    ),
                    end_ms=run_frames[-1].clock_ms,
                    distance_m=distance,
                    peak_speed_mps=peak,
                    mean_speed_mps=sum(speeds) / len(speeds),
                )
            )

        previous_frame: FrameResult | None = None
        start_from: FrameResult | None = None
        for frame in frames:
            if frame.ball_speed_mps >= FLIGHT_SPEED_MPS:
                if not run:
                    start_from = previous_frame
                run.append(frame)
            else:
                close(run, start_from)
                run = []
                start_from = None
            previous_frame = frame
        close(run, start_from)
        return flights
