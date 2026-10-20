"""The vision tracker, measured against the truth it was shown.

The tracker's report is the point of the whole exercise, so the tests are
the report: they assert that the tracker finds the ball, that it finds the
players, that it does not invent things, that it puts them in the right
place, and that the pass it measures is the pass the log recorded.

The thresholds are not decoration. They are the numbers the demo claims,
and they are set where a real tracker on a real feed would sit: the ball
is a clean target, the players sometimes overlap, and the numbers come out
of a noisy image rather than out of the truth.
"""

from __future__ import annotations

import pytest

from touchline.vision.feed import build_truth_clip
from touchline.vision.geometry import Camera
from touchline.vision.render import render_clip
from touchline.vision.tracker import VisionTracker
from touchline.vision.validate import TrackingReport, build_report, validate

SEED = 42


@pytest.fixture(scope="module")
def clip():
    """The ground truth for a clip of the match."""
    return build_truth_clip(SEED)


@pytest.fixture(scope="module")
def tracked(clip):
    """The tracker run over the rendered feed."""
    from touchline.vision.geometry import DEFAULT_CAMERA

    images = render_clip(
        clip.frames, DEFAULT_CAMERA, set(clip.home_players), seed=SEED
    )
    tracker = VisionTracker(DEFAULT_CAMERA)
    return tracker.run(images, [frame.clock_ms for frame in clip.frames])


@pytest.fixture(scope="module")
def report(clip, tracked) -> TrackingReport:
    """The report the studio shows."""
    return validate(clip, tracked)


# -- the camera ------------------------------------------------------------


def test_the_camera_round_trips_a_spot_on_the_pitch() -> None:
    """Metres in, pixels, metres again: the same spot."""
    camera = Camera()
    for x_m, y_m in ((0.0, 0.0), (52.5, 34.0), (105.0, 68.0)):
        px, py = camera.to_pixels(x_m, y_m)
        back = camera.to_metres(px, py)
        assert back[0] == pytest.approx(x_m, abs=0.15)
        assert back[1] == pytest.approx(y_m, abs=0.15)


def test_the_camera_measures_in_metres() -> None:
    """A pixel is a piece of ground, and the camera knows how big it is."""
    camera = Camera()
    assert camera.metres_per_pixel == pytest.approx(1.0 / camera.pixels_per_metre)
    assert camera.distance_m((0.0, 0.0), (3.0, 4.0)) == pytest.approx(5.0)


# -- the truth -------------------------------------------------------------


def test_the_clip_is_a_clip_of_the_match(clip) -> None:
    """Twenty seconds of a real match, with both sides on the pitch."""
    assert clip.frames
    assert clip.fps == 25
    assert clip.duration_ms == 20_000
    assert len(clip.home_players) == 11
    assert len(clip.away_players) == 11
    assert clip.flights, "the clip has no ball movement in it"


def test_the_truth_ball_never_teleports(clip) -> None:
    """The ball moves at the speed the log says, and no faster.

    A jump would mean the truth had a hole in it, and the tracker would
    then be measured against something that never happened.
    """
    fastest = max(flight.speed_mps for flight in clip.flights)
    previous = clip.frames[0]
    for frame in clip.frames[1:]:
        seconds = (frame.clock_ms - previous.clock_ms) / 1000.0
        distance = (
            (frame.ball[0] - previous.ball[0]) ** 2
            + (frame.ball[1] - previous.ball[1]) ** 2
        ) ** 0.5
        assert distance / seconds <= fastest * 1.2
        previous = frame


def test_the_truth_players_are_inside_the_pitch(clip) -> None:
    """Every player is on the pitch, in metres."""
    for frame in clip.frames:
        for x_m, y_m in frame.players.values():
            assert -5.0 <= x_m <= 110.0
            assert -5.0 <= y_m <= 73.0


# -- the tracker -----------------------------------------------------------


def test_the_tracker_finds_the_ball(report: TrackingReport) -> None:
    """The ball is a clean target, and the tracker does not lose it."""
    assert report.ball.precision >= 0.95
    assert report.ball.recall >= 0.95
    assert report.ball.mean_error_m <= 0.5


def test_the_tracker_finds_the_players(report: TrackingReport) -> None:
    """The players are found, and the ones it misses are the ones that
    were standing on top of each other."""
    assert report.home.precision >= 0.95
    assert report.away.precision >= 0.95
    assert report.home.recall >= 0.70
    assert report.away.recall >= 0.70
    assert report.overall.mean_error_m <= 0.5


def test_the_tracker_measures_the_pass_the_log_recorded(
    report: TrackingReport,
) -> None:
    """A pass of thirty metres has to come out as thirty metres."""
    assert report.flight_count_truth >= 2
    assert report.flight_matches >= 1
    assert report.flight_distance_mae_m <= 1.5


def test_the_tracker_measures_the_shot_the_log_recorded(
    report: TrackingReport,
) -> None:
    """A shot struck at twenty-five metres a second comes out at about
    twenty-five."""
    assert report.shot_speed_mae_mps <= 2.0
    assert report.ball_top_speed_mps >= 20.0


def test_the_tracker_keeps_its_tags_mostly_straight(
    report: TrackingReport,
) -> None:
    """The tags are worth having, so they should not be thrashing."""
    assert report.id_switches <= 25
    assert report.tracks_home >= 6
    assert report.tracks_away >= 6


def test_the_report_is_serialisable(report: TrackingReport) -> None:
    """The studio reads it as JSON."""
    payload = report.model_dump(mode="json")
    assert payload["frames"] == report.frames
    assert 0.0 <= payload["overall"]["precision"] <= 1.0
    assert 0.0 <= payload["overall"]["recall"] <= 1.0
    assert "errors_m" not in payload["overall"]


def test_the_whole_pipeline_runs_end_to_end() -> None:
    """The truth, the feed, the tracker and the score, from one call."""
    report = build_report(SEED)
    assert report.frames == 500
    assert report.overall.f1 >= 0.80
