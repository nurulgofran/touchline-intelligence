"""The demo recorder: deadlines, validation, and cleanup ordering.

The recorder drives a real browser against the shared deployment, so a
take is only promoted when it can be shown to be clean: every beat
landed, the language switch arrived, no provider fallbacks or prose
substitutions happened during the capture, and the encoded video is
under two minutes. The video is always finalized before the shared
reader is reset, and the reset is verified.

These tests drive the recorder's units with a fake CDP connection --
never a real browser, and never the live deployment.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

RECORD_DEMO = Path(__file__).resolve().parents[1] / "scripts" / "record-demo.py"


def load_recorder() -> Any:
    """Load scripts/record-demo.py as a module, without running it."""
    name = "record_demo_under_test"
    spec = importlib.util.spec_from_file_location(name, RECORD_DEMO)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def rec() -> Any:
    return load_recorder()


class FakeCDP:
    """A CDP connection that answers from a script."""

    def __init__(self, script: Any = None) -> None:
        self.script = script or (lambda expression: None)
        self.sent: list[str] = []
        self.evaluated: list[str] = []

    async def send(self, method: str, params: Any = None) -> dict:
        self.sent.append(method)
        return {}

    async def evaluate(self, expression: str, await_promise: bool = True) -> Any:
        self.evaluated.append(expression)
        result = self.script(expression)
        if asyncio.iscoroutine(result):
            result = await result
        return result


def counters_script(
    before: dict[str, int], after: dict[str, int], substituted: list[str] | None = None
) -> Any:
    """Answer the recorder's REST reads off the page: counters, then insights."""
    calls = {"n": 0}

    def script(expression: str) -> Any:
        if "insights" in expression:
            return list(substituted or [])
        if "api/match" in expression:
            calls["n"] += 1
            return dict(before if calls["n"] == 1 else after)
        return None

    return script


def clean_counters(**overrides: int) -> dict[str, int]:
    counters = {"call_count": 10, "fallback_count": 0, "substitution_count": 0}
    counters.update(overrides)
    return counters


# -- the temporary take -------------------------------------------------------


def test_the_take_is_written_temporarily(rec: Any) -> None:
    out = Path("/tmp/x/touchline-demo.mp4")
    temp = rec.take_path(out)
    assert temp != out
    assert temp.parent == out.parent
    assert temp.suffix == ".mp4"
    assert ".take" in temp.name


# -- validation ---------------------------------------------------------------


def _validate(
    rec: Any,
    monkeypatch: pytest.MonkeyPatch,
    *,
    beats: list | None = None,
    before: dict[str, int] | None = None,
    after: dict[str, int] | None = None,
    substituted: list[str] | None = None,
    duration: float = 100.0,
) -> Any:
    cdp = FakeCDP(
        counters_script(
            before or clean_counters(),
            after or clean_counters(call_count=25),
            substituted,
        )
    )
    monkeypatch.setattr(rec, "probe_duration", lambda path: duration)
    return asyncio.run(
        rec.validate_take(
            cdp,
            seed=42,
            beats=beats if beats is not None else [rec.BeatResult("a", True)],
            counters_before=before or clean_counters(),
            counters_after=after or clean_counters(call_count=25),
            video_path=Path("/tmp/x/take.mp4"),
        )
    )


def test_a_clean_take_validates(rec: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    validation = _validate(rec, monkeypatch)
    assert validation.ok, validation.reasons


def test_a_missing_beat_rejects_the_take(
    rec: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    beats = [
        rec.BeatResult("a", True),
        rec.BeatResult("b", False, "a step found nothing"),
    ]
    validation = _validate(rec, monkeypatch, beats=beats)
    assert not validation.ok
    assert any("'b'" in reason for reason in validation.reasons)


def test_a_fallback_during_the_take_rejects_it(
    rec: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    validation = _validate(
        rec,
        monkeypatch,
        after=clean_counters(call_count=25, fallback_count=1),
    )
    assert not validation.ok
    assert any("fallback" in reason for reason in validation.reasons)


def test_a_substitution_during_the_take_rejects_it(
    rec: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    validation = _validate(
        rec,
        monkeypatch,
        after=clean_counters(call_count=25, substitution_count=1),
    )
    assert not validation.ok
    assert any("substitution" in reason for reason in validation.reasons)


def test_substituted_cards_reject_the_take(
    rec: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    validation = _validate(rec, monkeypatch, substituted=["ovl-c-1"])
    assert not validation.ok
    assert any("ovl-c-1" in reason for reason in validation.reasons)


def test_no_calls_during_the_take_rejects_it(
    rec: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = clean_counters()
    validation = _validate(rec, monkeypatch, before=before, after=dict(before))
    assert not validation.ok
    assert any("no provider calls" in reason for reason in validation.reasons)


def test_an_overlong_encoding_rejects_the_take(
    rec: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert not _validate(rec, monkeypatch, duration=120.0).ok
    assert not _validate(rec, monkeypatch, duration=180.5).ok
    assert _validate(rec, monkeypatch, duration=119.9).ok


def test_an_unverifiable_duration_rejects_the_take(
    rec: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rec, "probe_duration", lambda path: None)
    cdp = FakeCDP(counters_script(clean_counters(), clean_counters(call_count=25)))
    validation = asyncio.run(
        rec.validate_take(
            cdp,
            seed=42,
            beats=[rec.BeatResult("a", True)],
            counters_before=clean_counters(),
            counters_after=clean_counters(call_count=25),
            video_path=Path("/tmp/x/take.mp4"),
        )
    )
    assert not validation.ok


# -- the beats -----------------------------------------------------------------


def test_a_beat_whose_step_finds_nothing_is_missing(rec: Any) -> None:
    cdp = FakeCDP(lambda expression: False)
    shot = rec.Shot(name="x", action="return false", hold=0)
    ok, detail = asyncio.run(rec._play_shot(cdp, shot, 0))
    assert not ok
    assert "found nothing" in detail


def test_a_beat_whose_check_does_not_land_is_missing(rec: Any) -> None:
    """The language switch must actually arrive, or the take is rejected."""
    async def check_script(expression: str) -> bool:
        return expression != "false"

    cdp = FakeCDP(check_script)
    shot = rec.Shot(
        name="the same moment in Deutsch", action=None, hold=0, check="false"
    )
    ok, detail = asyncio.run(rec._play_shot(cdp, shot, 0))
    assert not ok
    assert "did not land" in detail


def test_wait_for_idle_is_a_settle_aid_not_a_beat(
    rec: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A page that never goes quiet is slow, not missing."""

    async def never_idle(cdp: FakeCDP) -> bool:
        return False

    monkeypatch.setattr(rec, "wait_for_idle", never_idle)
    cdp = FakeCDP(lambda expression: True)
    shot = rec.Shot(name="x", action=[never_idle], hold=0)
    ok, _ = asyncio.run(rec._play_shot(cdp, shot, 0))
    assert ok


def test_the_timeline_enforces_the_capture_deadline(rec: Any) -> None:
    """Beats past the deadline are missing, not skipped silently."""

    async def quick(expression: str) -> Any:
        await asyncio.sleep(0.05)
        return ""

    cdp = FakeCDP(quick)
    timeline = rec.Timeline()
    for name in ("one", "two", "three"):
        timeline.add(rec.Shot(name=name, hold=0.05, settle=0.05))
    beats = asyncio.run(rec._run_timeline(cdp, timeline, total_seconds=0.35))
    assert len(beats) == 3
    assert beats[0].ok, "the first beat should fit in the budget"
    assert not beats[-1].ok
    assert "deadline" in beats[-1].detail


def test_the_deutsch_beat_requires_the_switch_to_land(rec: Any) -> None:
    """The timeline's language beat carries the landed check."""
    timeline = rec.build_timeline()
    deutsch = next(s for s in timeline.shots if "Deutsch" in s.name)
    assert deutsch.check is not None


# -- cleanup ordering ------------------------------------------------------------


def test_capture_stops_before_the_video_is_finalized(rec: Any) -> None:
    """The screencast stops before the writer is closed."""

    class Writer:
        def __init__(self) -> None:
            self.events: list[str] = []

        def pump(self) -> None:
            self.events.append("pump")

        def close(self) -> None:
            self.events.append("close")

    async def run() -> tuple[list[str], list[str]]:
        cdp = FakeCDP()
        writer = Writer()
        pump_task = asyncio.create_task(asyncio.sleep(60))
        await rec._stop_capture(cdp, writer, pump_task)
        return cdp.sent, writer.events

    sent, events = asyncio.run(run())
    assert sent[0] == "Page.stopScreencast"
    assert events == ["pump", "close"]


def test_verify_reader_checks_the_picker_state(rec: Any) -> None:
    ok = asyncio.run(rec.verify_reader(FakeCDP(lambda expression: True)))
    assert ok is True
    bad = asyncio.run(rec.verify_reader(FakeCDP(lambda expression: False)))
    assert bad is False


def test_verify_reader_survives_a_dead_page(rec: Any) -> None:
    async def dead(expression: str) -> Any:
        raise RuntimeError("gone")

    assert asyncio.run(rec.verify_reader(FakeCDP(dead))) is False
