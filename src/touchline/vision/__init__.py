"""The vision tracker: a synthetic feed, a real tracker, and the score.

The feed is synthetic and the tracker is not. A top-down camera watches a
synthetic match, the frame is thresholded in HSV, every blob is tracked
with a Kalman filter and a nearest-neighbour association, and the result is
measured against the truth the video was rendered from.

Four modules, in the order the pipeline runs:

:mod:`touchline.vision.feed`
    The ground truth: where the ball and the players were, frame by frame.
:mod:`touchline.vision.render`
    The camera: the truth, drawn.
:mod:`touchline.vision.tracker`
    The tracker: OpenCV, on the video.
:mod:`touchline.vision.validate`
    The score: the tracker against the truth.
"""

from __future__ import annotations

from typing import Any

__all__ = ["build_report"]


def build_report(*args: Any, **kwargs: Any) -> Any:
    """Build the truth, shoot the feed, track it, and measure the result.

    A thin wrapper over :func:`touchline.vision.validate.build_report`, so
    that the package can be used without knowing which module does what.
    """
    from touchline.vision.validate import build_report as _build_report

    return _build_report(*args, **kwargs)
