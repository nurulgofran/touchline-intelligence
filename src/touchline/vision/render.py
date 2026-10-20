"""The synthetic camera feed: the truth, drawn.

This is a **top-down camera** on a synthetic match, rendered frame by frame
from the ground truth. It is not footage of anything: it is a picture of
the truth, and the tracker is measured against the same truth it was drawn
from. That is the whole point -- the tracker's error is a *detection*
error, measured against what was actually on the pitch, not a difference
between two guesses.

The feed is deliberately a little hard. There is sensor noise, there is a
slight blur, and the players are not identical discs. None of that is
there to make the numbers look bad; it is there so that the numbers mean
something. A tracker that reads a clean synthetic image perfectly has not
been tested.

**The palette is the feed's own.** The two teams are drawn in a blue and an
orange rather than in their club colours, because a feed that has to be
segmented by colour needs the two teams to be separable, and the club
palette is not guaranteed to be -- two of the six fictional clubs are blue.
The lines are painted dim on purpose, so that the ball's threshold cannot
pick them up.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np

from touchline.models.events import PITCH_LENGTH_M, PITCH_WIDTH_M
from touchline.vision.feed import TruthFrame
from touchline.vision.geometry import Camera

__all__ = [
    "AWAY_COLOUR",
    "BALL_COLOUR",
    "HOME_COLOUR",
    "PITCH_COLOUR",
    "PitchPalette",
    "render_clip",
    "render_frame",
]

#: The grass.
PITCH_COLOUR: tuple[int, int, int] = (28, 72, 40)

#: The markings. Deliberately dim, so the ball's threshold cannot see them.
LINE_COLOUR: tuple[int, int, int] = (120, 132, 120)

#: The home team.
HOME_COLOUR: tuple[int, int, int] = (30, 90, 220)

#: The away team.
AWAY_COLOUR: tuple[int, int, int] = (240, 130, 30)

#: The ball. Bright and unsaturated, which is what finds it.
BALL_COLOUR: tuple[int, int, int] = (245, 245, 245)

#: How wide a player is drawn, in metres. A player is about 0.4 m across
#: the shoulders; a disc is drawn wider than that so that it is a shape the
#: contour finder can see.
PLAYER_RADIUS_M = 0.9

#: How wide the ball is drawn, in metres. The real ball is 0.11 m across,
#: which at this resolution is less than a pixel. It is drawn larger so
#: that it can be found at all; the distances the tracker measures are
#: between the ball's *positions*, so the size does not enter into them.
BALL_RADIUS_M = 0.45

#: How far the sensor noise moves a pixel's value, either way.
NOISE_LEVEL = 8


@dataclass(frozen=True)
class PitchPalette:
    """The colours the feed is drawn in.

    Attributes:
        pitch: The grass.
        line: The markings.
        home: The home team.
        away: The away team.
        ball: The ball.
    """

    pitch: tuple[int, int, int] = PITCH_COLOUR
    line: tuple[int, int, int] = LINE_COLOUR
    home: tuple[int, int, int] = HOME_COLOUR
    away: tuple[int, int, int] = AWAY_COLOUR
    ball: tuple[int, int, int] = BALL_COLOUR


def _draw_pitch(camera: Camera, palette: PitchPalette) -> np.ndarray:
    """The empty pitch, drawn once and copied for every frame."""
    import cv2

    frame = np.zeros((camera.height, camera.width, 3), dtype=np.uint8)
    frame[:, :] = palette.pitch

    def spot(x_m: float, y_m: float) -> tuple[int, int]:
        px, py = camera.to_pixels(x_m, y_m)
        return round(px), round(py)

    def line(x1: float, y1: float, x2: float, y2: float) -> None:
        cv2.line(frame, spot(x1, y1), spot(x2, y2), palette.line, 1)

    def circle(x_m: float, y_m: float, radius_m: float) -> None:
        cx, cy = spot(x_m, y_m)
        cv2.circle(
            frame,
            (cx, cy),
            round(radius_m * camera.pixels_per_metre),
            palette.line,
            1,
        )

    # The touchlines and the goal lines.
    line(0.0, 0.0, PITCH_LENGTH_M, 0.0)
    line(0.0, PITCH_WIDTH_M, PITCH_LENGTH_M, PITCH_WIDTH_M)
    line(0.0, 0.0, 0.0, PITCH_WIDTH_M)
    line(PITCH_LENGTH_M, 0.0, PITCH_LENGTH_M, PITCH_WIDTH_M)

    # The halfway line and the centre circle.
    line(PITCH_LENGTH_M / 2.0, 0.0, PITCH_LENGTH_M / 2.0, PITCH_WIDTH_M)
    circle(PITCH_LENGTH_M / 2.0, PITCH_WIDTH_M / 2.0, 9.15)

    # The penalty areas and the six-yard boxes, at both ends.
    for goal_x in (0.0, PITCH_LENGTH_M):
        sign = 1.0 if goal_x == 0.0 else -1.0
        line(goal_x, 16.5, goal_x + sign * 16.5, 16.5)
        line(goal_x, PITCH_WIDTH_M - 16.5, goal_x + sign * 16.5, PITCH_WIDTH_M - 16.5)
        line(goal_x + sign * 16.5, 16.5, goal_x + sign * 16.5, PITCH_WIDTH_M - 16.5)
        line(goal_x, 5.5, goal_x + sign * 5.5, 5.5)
        line(goal_x, PITCH_WIDTH_M - 5.5, goal_x + sign * 5.5, PITCH_WIDTH_M - 5.5)
        line(goal_x + sign * 5.5, 5.5, goal_x + sign * 5.5, PITCH_WIDTH_M - 5.5)

    return frame


def render_frame(
    frame: TruthFrame,
    camera: Camera,
    palette: PitchPalette,
    background: np.ndarray,
    home_players: set[str],
    rng: np.random.Generator,
) -> np.ndarray:
    """Draw one frame of the feed.

    Args:
        frame: The truth for this frame.
        camera: The camera the feed is shot on.
        palette: The feed's colours.
        background: The empty pitch, drawn once.
        home_players: Which players are on the home side.
        rng: The noise source.

    Returns:
        The image, as an RGB array.
    """
    import cv2

    image = background.copy()

    for player_id, (x_m, y_m) in frame.players.items():
        colour = palette.home if player_id in home_players else palette.away
        # No two players are quite the same shade, so the threshold has to
        # work rather than match a single exact colour.
        shade = int(rng.integers(-10, 11))
        shaded = tuple(max(0, min(255, channel + shade)) for channel in colour)
        px, py = camera.to_pixels(x_m, y_m)
        cv2.circle(
            image,
            (round(px), round(py)),
            round(PLAYER_RADIUS_M * camera.pixels_per_metre),
            shaded,
            -1,
        )

    px, py = camera.to_pixels(*frame.ball)
    cv2.circle(
        image,
        (round(px), round(py)),
        round(BALL_RADIUS_M * camera.pixels_per_metre),
        palette.ball,
        -1,
    )

    # The sensor, then the lens. The noise is uniform rather than
    # Gaussian, which is what a quantising sensor actually produces, and
    # it is also a good deal quicker to draw.
    noise = rng.integers(
        -NOISE_LEVEL, NOISE_LEVEL + 1, image.shape, dtype=np.int16
    )
    image = np.clip(image.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    return cv2.GaussianBlur(image, (3, 3), 0)


def render_clip(
    frames: list[TruthFrame],
    camera: Camera,
    home_players: set[str],
    *,
    palette: PitchPalette | None = None,
    seed: int = 7,
) -> Iterator[np.ndarray]:
    """Draw a whole clip.

    The frames are yielded one at a time rather than returned as a list.
    A twenty-second clip at this resolution is three quarters of a
    gigabyte of pixels, and the tracker does not need to see them all at
    once -- so the feed is shot and watched in the same breath.

    Args:
        frames: The truth, in order.
        camera: The camera the feed is shot on.
        home_players: Which players are on the home side.
        palette: The feed's colours.
        seed: The noise seed, so the same clip is always the same clip.

    Yields:
        The images, as RGB arrays.
    """
    resolved = palette or PitchPalette()
    background = _draw_pitch(camera, resolved)
    rng = np.random.default_rng(seed)
    for frame in frames:
        yield render_frame(frame, camera, resolved, background, home_players, rng)
