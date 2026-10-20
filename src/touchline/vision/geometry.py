"""The camera: how the pitch is seen.

The tracker works in metres, not in pixels, and this module is the only
place that knows how the two relate. Everything downstream -- the truth
feed, the renderer, the validation -- goes through it, so a change to the
resolution moves the whole pipeline together and cannot leave the report
describing a different camera from the one the video was shot on.

The pitch is 105 m by 68 m and it is seen from directly above, so a pixel
is a square of ground and a distance in the image is a distance on the
pitch. The margin around the touchlines is there so that a player running
off the edge of the frame is still drawn, rather than disappearing.
"""

from __future__ import annotations

from dataclasses import dataclass

from touchline.models.events import PITCH_LENGTH_M, PITCH_WIDTH_M

__all__ = ["DEFAULT_CAMERA", "Camera"]


@dataclass(frozen=True)
class Camera:
    """A top-down camera over the pitch.

    Attributes:
        pixels_per_metre: How many pixels one metre of ground is.
        margin_m: The ground left around the pitch, in metres.
    """

    pixels_per_metre: float = 8.0
    margin_m: float = 3.0

    @property
    def width(self) -> int:
        """The width of the image, in pixels."""
        return round((PITCH_LENGTH_M + 2 * self.margin_m) * self.pixels_per_metre)

    @property
    def height(self) -> int:
        """The height of the image, in pixels."""
        return round((PITCH_WIDTH_M + 2 * self.margin_m) * self.pixels_per_metre)

    @property
    def metres_per_pixel(self) -> float:
        """How much ground one pixel covers."""
        return 1.0 / self.pixels_per_metre

    def to_pixels(self, x_m: float, y_m: float) -> tuple[float, float]:
        """A spot on the pitch, in pixels.

        Args:
            x_m: Along the pitch, in metres from the left-hand goal.
            y_m: Across the pitch, in metres from the bottom touchline.

        Returns:
            The pixel coordinates, with the origin at the top left.
        """
        px = (self.margin_m + x_m) * self.pixels_per_metre
        py = (self.height - 1) - (self.margin_m + y_m) * self.pixels_per_metre
        return px, py

    def to_metres(self, px: float, py: float) -> tuple[float, float]:
        """A pixel, as a spot on the pitch."""
        x_m = px / self.pixels_per_metre - self.margin_m
        y_m = (self.height - 1 - py) / self.pixels_per_metre - self.margin_m
        return x_m, y_m

    def distance_m(self, a: tuple[float, float], b: tuple[float, float]) -> float:
        """How far apart two spots on the pitch are, in metres."""
        dx = a[0] - b[0]
        dy = a[1] - b[1]
        return (dx * dx + dy * dy) ** 0.5


#: The camera the feed is shot on.
DEFAULT_CAMERA = Camera()
