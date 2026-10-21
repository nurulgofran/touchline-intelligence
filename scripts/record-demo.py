#!/usr/bin/env python3
"""Record the demo video of the live studio.

The video is captured from the real deployment over the public URL, in a real
browser, with the real model behind it -- there is no mock and no cut. The
capture is driven through the Chrome DevTools Protocol rather than an OS
screen recorder, for two reasons: the frames are the page's own rendering, so
the recording is unaffected by whatever else is on the screen, and the
timeline is a script, so the take is reproducible.

The output is silent by design. The hackathon rules allow silence and forbid
copyrighted music, and a silent video with on-screen captions is the safe
form of that: the narration is text, it is in English, and it is inside the
frame.

Usage:
    python scripts/record-demo.py --out build/touchline-demo.mp4
    python scripts/record-demo.py --out build/touchline-demo.mp4 --headed

The URL defaults to the deployed Container App. Override it to record
against a local server instead:

    python scripts/record-demo.py --url http://localhost:8000 --out build/demo.mp4
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import websockets
except ImportError:  # pragma: no cover - the venv has it
    print("This script needs the 'websockets' package.", file=sys.stderr)
    raise

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_URL = (
    "https://touchline-app.icywave-bfcbf183.germanywestcentral.azurecontainerapps.io"
)

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
]

WIDTH = 1600
HEIGHT = 900
FPS = 30


# --- Chrome DevTools Protocol ------------------------------------------------


class CDP:
    """A single CDP connection to one page target."""

    def __init__(self, ws: Any) -> None:
        self._ws = ws
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._listeners: dict[str, list[Any]] = {}
        self._task: asyncio.Task[None] | None = None

    @classmethod
    async def connect(cls, url: str) -> CDP:
        ws = await websockets.connect(url, max_size=None, ping_interval=None)
        cdp = cls(ws)
        cdp._task = asyncio.create_task(cdp._read())
        return cdp

    async def _read(self) -> None:
        async for raw in self._ws:
            message = json.loads(raw)
            if "id" in message:
                future = self._pending.pop(message["id"], None)
                if future and not future.done():
                    future.set_result(message)
            else:
                for handler in self._listeners.get(message.get("method", ""), []):
                    handler(message.get("params", {}))

    def on(self, method: str, handler: Any) -> None:
        self._listeners.setdefault(method, []).append(handler)

    async def send(
        self, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self._next_id += 1
        message_id = self._next_id
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[message_id] = future
        await self._ws.send(
            json.dumps(
                {"id": message_id, "method": method, "params": params or {}}
            )
        )
        response = await asyncio.wait_for(future, timeout=240)
        if "error" in response:
            raise RuntimeError(f"{method} failed: {response['error']}")
        return response.get("result", {})

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
        await self._ws.close()

    # -- convenience ---------------------------------------------------------

    async def evaluate(self, expression: str, await_promise: bool = True) -> Any:
        result = await self.send(
            "Runtime.evaluate",
            {
                "expression": expression,
                "awaitPromise": await_promise,
                "returnByValue": True,
                "allowUnsafeEvalBlockedByCSP": True,
            },
        )
        if result.get("exceptionDetails"):
            detail = result["exceptionDetails"]
            description = detail.get("exception", {}).get("description", "")
            raise RuntimeError(
                f"evaluate failed: {detail.get('text')} {description}"
            )
        return result.get("result", {}).get("value")

    async def wait_for(
        self, expression: str, timeout: float = 30.0, interval: float = 0.25
    ) -> bool:
        """Poll a JS expression until it is truthy."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if await self.evaluate(expression):
                    return True
            except RuntimeError:
                pass
            await asyncio.sleep(interval)
        return False


# --- the frame writer --------------------------------------------------------


class FrameWriter:
    """Feeds screencast frames into ffmpeg at a fixed frame rate.

    A screencast only produces a frame when the page changes, so the frames
    are padded out to a constant rate with the last frame that arrived. The
    result is a video that runs in real time: a ten-second pause is ten
    seconds of footage, not a jump cut.
    """

    def __init__(self, ffmpeg: subprocess.Popen[bytes], fps: int = FPS) -> None:
        self._ffmpeg = ffmpeg
        self._fps = fps
        self._current: bytes | None = None
        self._written_at: float | None = None

    def push(self, jpeg: bytes) -> None:
        self._current = jpeg

    def pump(self) -> None:
        if self._current is None:
            return
        now = time.time()
        if self._written_at is None:
            self._written_at = now
        step = 1.0 / self._fps
        written = 0
        while now - self._written_at >= step:
            with contextlib.suppress(BrokenPipeError, ValueError):
                self._ffmpeg.stdin.write(self._current)
            self._written_at += step
            written += 1
        if written:
            with contextlib.suppress(BrokenPipeError, ValueError):
                self._ffmpeg.stdin.flush()

    def close(self) -> None:
        with contextlib.suppress(BrokenPipeError, ValueError):
            self._ffmpeg.stdin.close()
        self._ffmpeg.wait()


# --- the caption layer -------------------------------------------------------
#
# The captions are a DOM element injected into the page rather than drawn by
# ffmpeg, so the text is rendered by the same engine as the rest of the
# frame: crisp, correctly hinted, and in the same type as the studio.

CAPTION_HTML = """
(() => {
  const style = document.createElement('style');
  style.textContent = `
    #__touchline_captions__ {
      position: fixed; left: 0; right: 0; bottom: 52px; z-index: 2147483647;
      padding: 16px 40px 18px; pointer-events: none; max-width: 1440px;
      font: 500 22px/1.35 system-ui, -apple-system, sans-serif;
      overflow-wrap: anywhere;
      color: #f4f7fb; letter-spacing: 0.01em;
      text-shadow: 0 1px 3px rgba(0,0,0,0.6);
      background: linear-gradient(180deg, rgba(6,10,16,0) 0%,
                                  rgba(6,10,16,0.82) 40%, rgba(6,10,16,0.9) 100%);
    }
    #__touchline_title__ {
      position: fixed; inset: 0; z-index: 2147483646;
      display: flex; flex-direction: column; align-items: center;
      justify-content: center; gap: 18px; pointer-events: none; opacity: 0;
      background: radial-gradient(ellipse at 50% 45%, rgba(18,38,32,0.97) 0%,
                                  rgba(5,9,14,0.99) 72%);
    }
    #tl_title_main {
      font: 600 58px/1.12 system-ui, -apple-system, sans-serif;
      color: #f7fafc; letter-spacing: -0.015em; text-align: center;
      max-width: 1200px;
    }
    #tl_title_sub {
      font: 400 27px/1.4 system-ui, -apple-system, sans-serif;
      color: #9fb3c4; text-align: center; max-width: 1000px;
    }
    #tl_title_foot {
      margin-top: 14px; color: #5f7488; letter-spacing: 0.06em;
      text-align: center; font: 500 15px/1.4 ui-monospace, Menlo, monospace;
    }
  `;
  document.head.appendChild(style);

  const id = '__touchline_captions__';
  let el = document.getElementById(id);
  if (!el) {
    el = document.createElement('div');
    el.id = id;
    document.body.appendChild(el);
  }

  const tid = '__touchline_title__';
  let title = document.getElementById(tid);
  if (!title) {
    title = document.createElement('div');
    title.id = tid;
    title.innerHTML = [
      '<div id="tl_title_main"></div>',
      '<div id="tl_title_sub"></div>',
      '<div id="tl_title_foot"></div>'
    ].join('');
    document.body.appendChild(title);
  }
  return true;
})()
"""


async def set_caption(cdp: CDP, text: str) -> None:
    escaped = json.dumps(text)
    await cdp.evaluate(
        f"""
        (() => {{
          const el = document.getElementById('__touchline_captions__');
          if (!el) return false;
          el.textContent = {escaped};
          el.style.opacity = {1 if text else 0};
          return true;
        }})()
        """
    )


async def set_title(cdp: CDP, main: str, sub: str = "", foot: str = "") -> None:
    """A full-frame title card, for the opening beat."""
    payload = json.dumps([main, sub, foot])
    await cdp.evaluate(
        f"""
        (() => {{
          const el = document.getElementById('__touchline_title__');
          if (!el) return false;
          const [m, s, f] = {payload};
          document.getElementById('tl_title_main').textContent = m;
          document.getElementById('tl_title_sub').textContent = s;
          document.getElementById('tl_title_foot').textContent = f;
          el.style.opacity = m ? 1 : 0;
          return true;
        }})()
        """
    )


# --- the timeline ------------------------------------------------------------


@dataclass
class Shot:
    """One beat of the video."""

    name: str
    caption: str | Any = ""
    action: Any = None
    settle: float = 0.0
    hold: float = 3.0
    title: tuple[str, str, str] | None = None
    caption_after: Any = None
    #: An extra check run after the action: a JS expression string or a
    #: callable taking the CDP connection. Falsy means the beat did not
    #: land (e.g. the language switch never arrived) and the take is
    #: rejected.
    check: Any = None


@dataclass
class BeatResult:
    """Whether one beat of the take actually happened."""

    name: str
    ok: bool
    detail: str = ""


@dataclass
class Timeline:
    shots: list[Shot] = field(default_factory=list)

    def add(self, shot: Shot) -> Timeline:
        self.shots.append(shot)
        return self


# --- the actions the timeline drives ----------------------------------------
#
# Each is a JavaScript expression evaluated in the page. They return true when
# they did something and false when the thing they wanted was not there, so a
# missing control shows up in the recording log rather than as a silent
# no-op.

CLICK = """
(() => {{
  const wanted = {text};
  const button = [...document.querySelectorAll('button')]
    .find((b) => b.textContent.includes(wanted));
  if (!button) return false;
  button.click();
  return true;
}})()
"""


def click(text: str) -> str:
    return CLICK.format(text=json.dumps(text))


# The same, but it waits for the control to appear. A control that is not
# there yet is not a failure -- it is a wait -- and the feed is briefly
# empty while the service re-narrates.
CLICK_WAITING = """
(async () => {{
  const wanted = {text};
  for (let i = 0; i < 100; i += 1) {{
    const button = [...document.querySelectorAll('button')]
      .find((b) => b.textContent.includes(wanted));
    if (button) {{ button.click(); return true; }}
    await new Promise((r) => setTimeout(r, 250));
  }}
  return false;
}})()
"""


def click_when_ready(text: str) -> str:
    return CLICK_WAITING.format(text=json.dumps(text))


# Read the moment for one side, and wait for it to be rewritten.
#
# The body is compared rather than the title, because a title can be the
# same word in two languages, and the comparison is with the text that was
# on screen a moment earlier. It is all one script, so the card cannot
# change underneath the comparison.
SWITCH_SIDE = """
(async () => {{
  const pick = () => {{
    const cards = [...document.querySelectorAll('article')];
    return cards.find((card) => !card.textContent.includes('RECAP'))
      ?? cards[0] ?? null;
  }};
  const find = (label) => [...document.querySelectorAll('button')]
    .find((b) => b.textContent.includes(label));
  const card = pick();
  const body = card ? card.querySelector('p') : null;
  const before = body ? body.textContent.trim() : '';
  const mode = find('My team');
  if (!mode) return false;
  mode.click();
  await new Promise((r) => setTimeout(r, 400));
  const side = find('Riverside United');
  if (!side) return false;
  side.click();
  for (let i = 0; i < 160; i += 1) {{
    await new Promise((r) => setTimeout(r, 250));
    const now_card = pick();
    const now_body = now_card ? now_card.querySelector('p') : null;
    const now = now_body ? now_body.textContent.trim() : '';
    if (now && now !== before) return true;
  }}
  return false;
}})()
"""


# Switch the language and wait for the card to be rewritten. The body is
# compared rather than the title, because a title can be the same word in two
# languages. It is all one script, so the text it compares against is the
# text that was on screen a moment earlier.
SWITCH_LANGUAGE = """
(async () => {{
  // Watch the FIRST article (the recap): the captions track this same card
  // (READ_BODY / READ_OVERLAY_ID use querySelector('article')), and the
  // recap is the card the language rewrite reaches first -- the excluded
  // second card can stay English for minutes while an earlier mode rewrite
  // is still draining through the shared 8/min pacer.
  const pick = () => document.querySelector('article');
  const card = pick();
  const body = card ? card.querySelector('p') : null;
  const before = body ? body.textContent.trim() : '';
  const button = [...document.querySelectorAll('button')]
    .find((b) => b.textContent.includes('Deutsch'));
  if (!button) return false;
  button.click();
  for (let i = 0; i < 160; i += 1) {{
    await new Promise((r) => setTimeout(r, 250));
    const now_card = pick();
    const now_body = now_card ? now_card.querySelector('p') : null;
    const now = now_body ? now_body.textContent.trim() : '';
    if (now && now !== before) return true;
  }}
  return false;
}})()
"""


async def wait_for_idle(cdp: CDP, quiet_ms: int = 200, timeout: float = 30.0) -> bool:
    """Wait for the page's main thread to go quiet.

    A re-narration arrives as a burst of overlay frames, and each one
    re-renders the feed. While that is going on, anything sent to the page
    queues behind it and comes back seconds late -- a click that should take
    a millisecond takes twenty. So the wait measures the round trip rather
    than guessing at it, and it wants three quiet samples in a row, because
    a burst has gaps in it.

    The wait is short on purpose. The page goes quiet within a second or
    two of a burst ending, and a wait that runs long is a take that runs
    long: the video is under two minutes and a beat that waits for
    nothing is a beat that spends the video.
    """
    deadline = time.monotonic() + timeout
    quiet = 0
    while time.monotonic() < deadline:
        started = time.monotonic()
        with contextlib.suppress(RuntimeError):
            await cdp.evaluate("1")
        elapsed = (time.monotonic() - started) * 1000
        quiet = quiet + 1 if elapsed < quiet_ms else 0
        if quiet >= 3:
            return True
        await asyncio.sleep(0.4)
    return False


async def reset_audience(cdp: CDP, *, replay: bool = False) -> None:
    """Put the reader back to the default, and wait for the feed to settle.

    The audience is service state, not page state, so it survives a reload.
    Without this, a take opens on whatever the last visitor left behind.

    The wait is for the *rewrite* to finish, not for the page to go quiet:
    a reader change rewrites the whole feed, one model call per insight, and
    the studio says "rewriting the feed…" while it is going on. A judge who
    arrived in the middle of that would find a half-rewritten feed.

    ``replay`` puts the clock back to kick-off first. The service
    rewrites the feed around the moment the reader is looking at, so a
    reset made at the end of the match would rewrite the last moments
    first and leave the opening ones in the old language for minutes --
    and the opening ones are where a judge starts.
    """
    if replay:
        await cdp.evaluate(CLICK.format(text=json.dumps("Replay")))
        await wait_for_idle(cdp)
    for label in ("Analyst", "English"):
        await cdp.evaluate(CLICK.format(text=json.dumps(label)))
        await wait_for_idle(cdp)
    await wait_for_rewrite(cdp)


# Is the service still rewriting the feed?
REWRITING = """
(() => document.body.innerText.includes('rewriting the feed'))()
"""


async def wait_for_rewrite(cdp: CDP, timeout: float = 420.0) -> bool:
    """Wait for the service to finish rewriting the feed.

    A reader change is minutes of work at the deployment's rate, so the
    wait is minutes too. It is the difference between leaving the
    deployment with a feed that is finished and leaving it with one that
    is still being written.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not await cdp.evaluate(REWRITING):
            return True
        await asyncio.sleep(2.0)
    return False


FOCUS_EVENT = """
(() => {
  const chip = document.querySelector('button[title^="Show ev-"]');
  if (!chip) return false;
  chip.focus();
  chip.scrollIntoView({block: 'center'});
  return true;
})()
"""


# Run the match out to full time with the control the interface offers.
# "Next key moment" walks the clock to the last insight and the recap, and
# it is the button a judge would press.
SPRINT_TO_END = """
(async () => {{
  const find = () => [...document.querySelectorAll('button')]
    .find((x) => x.textContent.includes('Next key moment'));
  for (let i = 0; i < 120; i += 1) {{
    const button = find();
    if (!button || button.disabled) return i;
    button.click();
    await new Promise((r) => setTimeout(r, 120));
  }}
  return -1;
}})()
"""


PICK_PLAYER = """
(() => {
  const select = document.querySelector('select');
  if (!select) return false;
  const option = [...select.options].find((o) => o.value);
  if (!option) return false;
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLSelectElement.prototype, 'value').set;
  setter.call(select, option.value);
  select.dispatchEvent(new Event('change', {bubbles: true}));
  return true;
})()
"""


# The English text of the moment on screen, so the caption can carry the
# English alongside a translation the model wrote in another language.
READ_INSIGHT = """
(() => {
  const cards = [...document.querySelectorAll('article')];
  const card = cards.find((c) => !c.textContent.includes('RECAP')) ?? cards[0];
  if (!card) return '';
  const title = card.querySelector('h3');
  const body = card.querySelector('p');
  const heading = title ? title.textContent.trim() : '';
  const first = body ? (body.textContent.trim().split(/(?<=[.!?])\\s/)[0] || '') : '';
  return (heading + ' — ' + first).trim();
})()
"""


# The caption bar is one or two lines, so the excerpt is cut to fit.
LIMIT_INSIGHT = """
((text) => {
  const limit = __LIMIT__;
  if (text.length <= limit) return text;
  const cut = text.slice(0, limit);
  const stop = cut.lastIndexOf('. ');
  return (stop > limit * 0.5 ? cut.slice(0, stop + 1) : cut).trim() + ' \u2026';
})(__TEXT__)
"""


async def limit_insight(cdp: CDP, text: str, limit: int = 150) -> str:
    script = LIMIT_INSIGHT.replace("__LIMIT__", str(limit)).replace(
        "__TEXT__", json.dumps(text)
    )
    return await cdp.evaluate(script)


# Is the card on screen still the one the English was taken from?
SAME_CARD = """
(() => {{
  const wanted = __ID__;
  if (!wanted) return true;
  const card = document.querySelector('article');
  if (!card) return false;
  return card.textContent.includes(wanted);
}})()
"""


# Did the copy on screen actually change language? German and Bengali
# both write characters outside ASCII, so a card that has one is a card
# that was rewritten in one of them.
LANGUAGE_LANDED = """
(() => {{
  // Same card the captions track: the first article (the recap). It is the
  // first card the language rewrite reaches, so this lands in seconds
  // instead of timing out behind an earlier mode rewrite.
  const card = document.querySelector('article');
  if (!card) return false;
  return /[^\x00-\x7F]/.test(card.textContent);
}})()
"""


READ_OVERLAY_ID = """
(() => {{
  const card = document.querySelector('article');
  if (!card) return '';
  const id = [...card.querySelectorAll('span')]
    .find((s) => s.textContent.startsWith('ovl-'));
  return id ? id.textContent.trim() : '';
}})()
"""


READ_BODY = """
(() => {{
  const card = document.querySelector('article');
  if (!card) return '';
  const body = card.querySelector('p');
  return body ? body.textContent.trim() : '';
}})()
"""


READ_BADGE = """
(() => {{
  const badge = document.querySelector(
    '[title="Which provider served the last call."]');
  if (!badge) return '';
  const chip = [...badge.querySelectorAll('span')].find(
    (s) => s.className.includes('rounded-full'));
  const name = [...badge.querySelectorAll('span')].find(
    (s) => s.className.includes('truncate'));
  return (name ? name.textContent.trim() : '?') + ' | '
       + (chip ? chip.textContent.trim() : 'no chip');
}})()
"""


# --- take validation ---------------------------------------------------------
#
# A take is only promoted when it can be shown to be clean. The checks are
# the ones the demo's trust rests on: every beat happened, the language
# switch landed, and the provider counters moved the way a clean take
# moves them. Badge snapshots alone are insufficient -- the badge only
# shows the last call -- so the counters are compared across the capture
# interval.

#: The longest a take may be, in seconds, before it is rejected. The
#: target is 110; the encoded video must stay under two minutes.
MAX_ENCODED_SECONDS = 120.0


async def read_provider_counters(cdp: CDP, seed: int = 42) -> dict[str, int]:
    """The provider's counters, read off the service.

    ``call_count`` counts the model calls served; ``fallback_count`` the
    ones the template engine served instead; ``substitution_count`` the
    times validated prose was rejected. Compared before and after the
    capture, they say what happened during the take -- which a badge
    snapshot of the last call cannot.
    """
    script = (
        "fetch('/api/match?seed="
        + str(int(seed))
        + "').then((r) => r.json()).then((j) => j.provider)"
    )
    provider = await cdp.evaluate(script) or {}
    return {
        key: int(provider.get(key) or 0)
        for key in ("call_count", "fallback_count", "substitution_count")
    }


async def read_substituted_cards(cdp: CDP, seed: int = 42) -> list[str]:
    """The ids of cards whose prose was substituted, if any."""
    script = (
        "fetch('/api/match/insights?seed="
        + str(int(seed))
        + "').then((r) => r.json()).then((j) => j.insights"
        " .filter((i) => i.prose_substituted).map((i) => i.overlay_id))"
    )
    return list(await cdp.evaluate(script) or [])


def probe_duration(path: Path) -> float | None:
    """The encoded duration of a video file, in seconds.

    Returns ``None`` when ffprobe is missing or the file cannot be read,
    which the validation treats as a rejection: a take whose length
    cannot be verified is not a take that can be published.
    """
    if shutil.which("ffprobe") is None:
        return None
    try:
        completed = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.SubprocessError, ValueError):
        return None
    try:
        return float(completed.stdout.strip())
    except ValueError:
        return None


VERIFY_READER = """
(() => {
  const active = (label) => {
    const b = [...document.querySelectorAll('button')]
      .find((x) => x.textContent.includes(label));
    return !!b && b.dataset.active === 'true';
  };
  return active('Analyst') && active('English')
    && !document.body.innerText.includes('rewriting the feed');
})()
"""


async def verify_reader(cdp: CDP) -> bool:
    """Check the shared reader is back on Analyst / English and settled.

    The cleanup always restores the reader; this verifies it, so a
    half-restored deployment is reported rather than assumed.
    """
    try:
        return bool(await cdp.evaluate(VERIFY_READER))
    except Exception:
        return False


@dataclass
class TakeValidation:
    """Whether a take may be promoted."""

    ok: bool
    reasons: list[str] = field(default_factory=list)


async def validate_take(
    cdp: CDP,
    *,
    seed: int,
    beats: list[BeatResult],
    counters_before: dict[str, int],
    counters_after: dict[str, int],
    video_path: Path,
) -> TakeValidation:
    """Decide whether a take is clean enough to promote.

    A take is rejected when a required beat did not land, when the
    provider fell back or substituted prose during the capture, when no
    provider calls were served at all, or when the encoded video runs to
    two minutes or more.
    """
    reasons: list[str] = []
    for beat in beats:
        if not beat.ok:
            reasons.append(
                f"beat {beat.name!r} did not land"
                + (f": {beat.detail}" if beat.detail else "")
            )

    calls_before = counters_before.get("call_count", 0)
    calls_after = counters_after.get("call_count", 0)
    fallbacks_before = counters_before.get("fallback_count", 0)
    fallbacks_after = counters_after.get("fallback_count", 0)
    subs_before = counters_before.get("substitution_count", 0)
    subs_after = counters_after.get("substitution_count", 0)
    if calls_after <= calls_before:
        reasons.append("no provider calls were served during the take")
    if fallbacks_after != fallbacks_before:
        reasons.append(
            "provider fallbacks during the take "
            f"({fallbacks_before} -> {fallbacks_after})"
        )
    if subs_after != subs_before:
        reasons.append(
            "prose substitutions during the take "
            f"({subs_before} -> {subs_after})"
        )
    substituted = await read_substituted_cards(cdp, seed)
    if substituted:
        shown = ", ".join(substituted[:5])
        reasons.append(f"cards with substituted prose: {shown}")

    duration = probe_duration(video_path)
    if duration is None:
        reasons.append("the encoded duration could not be verified")
    elif duration >= MAX_ENCODED_SECONDS:
        reasons.append(
            f"the encoded duration is {duration:.1f}s "
            f"(>= {MAX_ENCODED_SECONDS:.0f}s)"
        )
    return TakeValidation(ok=not reasons, reasons=reasons)


async def _play_shot(cdp: CDP, shot: Shot, hold: float) -> tuple[bool, str]:
    """Play one beat of the timeline.

    Returns whether the beat landed, with a detail when it did not. A
    step that finds nothing -- a button that is not there -- means the
    beat is missing, except for ``wait_for_idle``, which is a settle aid:
    a page that never goes quiet is slow, not missing.
    """
    caption = shot.caption
    if callable(caption):
        caption = await caption(cdp)
    if shot.title is not None:
        await set_title(cdp, *shot.title)
    else:
        await set_title(cdp, "", "", "")
    await set_caption(cdp, caption or "")

    if shot.action is not None:
        steps = shot.action if isinstance(shot.action, list) else [shot.action]
        for step in steps:
            result = await step(cdp) if callable(step) else await cdp.evaluate(step)
            if result is False and step is not wait_for_idle:
                return False, "a step found nothing"

    if shot.caption_after is not None:
        after = shot.caption_after
        text = await after(cdp) if callable(after) else after
        await set_caption(cdp, text or "")

    if shot.check is not None:
        check = shot.check
        landed = await check(cdp) if callable(check) else await cdp.evaluate(check)
        if not landed:
            return False, "the beat's check did not land"

    await asyncio.sleep(shot.settle)
    await asyncio.sleep(hold)
    return True, ""


async def _stop_capture(
    cdp: CDP | None,
    writer: FrameWriter,
    pump_task: asyncio.Task[None] | None,
) -> None:
    """Stop the screencast and finalize the video.

    This runs **before** the shared reader is reset: the reset's clicks
    are cleanup, and they must not be part of the take.
    """
    if cdp is not None:
        with contextlib.suppress(Exception):
            await cdp.send("Page.stopScreencast")
    if pump_task is not None:
        pump_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pump_task
    writer.pump()
    writer.close()


# --- the recording -----------------------------------------------------------


def find_chrome() -> str:
    for candidate in CHROME_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    found = shutil.which("google-chrome") or shutil.which("chromium")
    if found:
        return found
    raise SystemExit("No Chrome or Chromium found.")


async def wait_for_target(profile: Path, timeout: float = 30.0) -> str:
    """Find the page target once Chrome is up.

    Chrome is started with --remote-debugging-port=0, so it picks a port
    that is free and writes it into the profile's DevToolsActivePort file.
    Guessing a port does not work: the machine's own Chrome may already be
    listening on the usual 9222, and the second instance then binds
    somewhere else entirely.
    """
    import urllib.request

    port_file = profile / "DevToolsActivePort"
    deadline = time.monotonic() + timeout
    port = 0
    while time.monotonic() < deadline:
        if port_file.exists():
            try:
                port = int(port_file.read_text().splitlines()[0])
            except (ValueError, IndexError):
                port = 0
        if port:
            break
        await asyncio.sleep(0.2)
    if not port:
        raise SystemExit("Chrome did not report a debugging port in time.")

    while time.monotonic() < deadline:
        try:
            url = f"http://127.0.0.1:{port}/json/list"
            with urllib.request.urlopen(url, timeout=2) as response:
                targets = json.load(response)
            for target in targets:
                if target.get("type") == "page" and target.get("webSocketDebuggerUrl"):
                    return target["webSocketDebuggerUrl"]
        except Exception:
            pass
        await asyncio.sleep(0.4)
    raise SystemExit("Chrome did not expose a page target in time.")


def take_path(out_path: Path) -> Path:
    """Where the take is written before validation promotes it."""
    return out_path.with_name(out_path.stem + ".take" + out_path.suffix)


async def record(
    url: str,
    out_path: Path,
    timeline: Timeline,
    headed: bool,
    total_seconds: float,
    seed: int = 42,
) -> bool:
    """Record one take, and promote it only if it validates.

    Returns whether the take was accepted. A rejected take is kept at
    the temporary path for inspection, and the shared reader is
    restored to Analyst / English either way.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = take_path(out_path)

    chrome = find_chrome()
    profile = Path(tempfile.mkdtemp(prefix="touchline-record-"))
    args = [
        chrome,
        # 0 means "pick a free port and tell me which one".
        "--remote-debugging-port=0",
        f"--user-data-dir={profile}",
        f"--window-size={WIDTH},{HEIGHT}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-infobars",
        "--hide-scrollbars",
        "--mute-audio",
        "--force-device-scale-factor=1",
        "--disable-features=Translate,MediaRouter",
        "about:blank",
    ]
    if not headed:
        args.insert(1, "--headless=new")

    chrome_process = subprocess.Popen(
        args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    ffmpeg = subprocess.Popen(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "image2pipe", "-vcodec", "mjpeg", "-framerate", str(FPS), "-i", "-",
            "-an",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "medium", "-crf", "20",
            "-movflags", "+faststart",
            str(temp_path),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    writer = FrameWriter(ffmpeg)

    cdp: CDP | None = None
    pump_task: asyncio.Task[None] | None = None
    finalized = False
    accepted = False
    try:
        ws_url = await wait_for_target(profile)
        cdp = await CDP.connect(ws_url)

        await cdp.send("Page.enable")
        await cdp.send("Runtime.enable")
        # A fixed viewport, so the frames are the same size whatever the window does.
        await cdp.send(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": WIDTH,
                "height": HEIGHT,
                "deviceScaleFactor": 1,
                "mobile": False,
            },
        )

        acks: list[asyncio.Task[None]] = []

        def on_frame(params: dict[str, Any]) -> None:
            writer.push(base64.b64decode(params["data"]))
            # Acknowledge, or the stream stalls. The task is held on to, or
            # the event loop is free to drop it before it is sent.
            acks.append(
                asyncio.create_task(
                    cdp.send(
                        "Page.screencastFrameAck",
                        {"sessionId": params["sessionId"]},
                    )
                )
            )

        cdp.on("Page.screencastFrame", on_frame)

        # Load the studio first and only then start recording, so the video is
        # exactly the timeline and no take is at the mercy of a slow load.
        print(f"navigating to {url}")
        await cdp.send("Page.navigate", {"url": url})
        ready = await cdp.wait_for("document.readyState === 'complete'", timeout=60)
        if not ready:
            raise SystemExit("The page did not finish loading.")

        # The provider badge is the trust signal, so the take waits for it --
        # and for the chip, not the wrapper, because the wrapper is already
        # there while the studio still says "connecting...".
        await cdp.wait_for(
            "!!document.querySelector("
            "'[title=\"Which provider served the last call.\"] .rounded-full')",
            timeout=45,
        )
        badge = await cdp.evaluate(READ_BADGE)
        print(f"provider badge: {badge!r}")
        if "live" not in (badge or "").lower():
            print("WARNING: the badge is not live. Do not publish this take.")

        # The reader is service state, so it survives a reload. Reset it, or
        # the take opens on whatever the last visitor left behind.
        print("resetting the reader to Analyst / English")
        await reset_audience(cdp)
        await asyncio.sleep(1.0)
        # The counters are read after the reset, so what moves them
        # during the capture is the take's own beats.
        counters_before = await read_provider_counters(cdp, seed)
        print(f"provider counters before the take: {counters_before}")
        await cdp.evaluate(CAPTION_HTML)

        await cdp.send(
            "Page.startScreencast",
            {
                "format": "jpeg",
                "quality": 92,
                "maxWidth": WIDTH,
                "maxHeight": HEIGHT,
                "everyNthFrame": 1,
            },
        )

        pump_task = asyncio.create_task(_pump(writer))

        beats = await _run_timeline(cdp, timeline, total_seconds)

        await set_caption(cdp, "")
        await set_title(cdp, "")

        # The capture ends here. The video is stopped and finalized
        # BEFORE the shared reader is reset: the reset's clicks are
        # cleanup, and they must not be part of the take.
        await _stop_capture(cdp, writer, pump_task)
        finalized = True

        counters_after = await read_provider_counters(cdp, seed)
        print(f"provider counters after the take: {counters_after}")
        validation = await validate_take(
            cdp,
            seed=seed,
            beats=beats,
            counters_before=counters_before,
            counters_after=counters_after,
            video_path=temp_path,
        )
        if validation.ok:
            temp_path.replace(out_path)
            print(f"take accepted and promoted to {out_path}")
            accepted = True
        else:
            print("take REJECTED:")
            for reason in validation.reasons:
                print(f"  - {reason}")
            print(f"the rejected take is kept at {temp_path}")
            accepted = False
    finally:
        if cdp:
            # The video is finalized before the shared reader is touched,
            # even when the take fell over half way through.
            if not finalized:
                await _stop_capture(cdp, writer, pump_task)
            # Leave the deployment the way a judge should find it, even if
            # the take fell over half way through -- and verify it, rather
            # than assuming the clicks landed.
            with contextlib.suppress(Exception):
                await reset_audience(cdp, replay=True)
            if await verify_reader(cdp):
                print("reader restored to Analyst / English and verified")
            else:
                print(
                    "WARNING: the reader could not be verified back on "
                    "Analyst / English"
                )
            await cdp.close()
        elif not finalized:
            writer.close()
        chrome_process.terminate()
        try:
            chrome_process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            chrome_process.kill()
        shutil.rmtree(profile, ignore_errors=True)

    errors = ffmpeg.stderr.read().decode() if ffmpeg.stderr else ""
    if errors.strip():
        print(errors, file=sys.stderr)
    return accepted


async def _run_timeline(
    cdp: CDP, timeline: Timeline, total_seconds: float
) -> list[BeatResult]:
    """Play every beat of the timeline, bounded by the capture deadline.

    The deadline is on the monotonic clock, and every beat -- its action,
    its waits, its hold -- is bounded by what is left of it. A beat that
    does not land is recorded as missing: the take is rejected rather
    than published without it.

    Returns one result per beat, in timeline order.
    """
    # The last two beats -- full time and the closing stack line -- are
    # held back from the rest, so a slow re-narration shortens the middle
    # of the video rather than cutting the ending off.
    closing = sum(shot.settle + shot.hold for shot in timeline.shots[-2:])
    reserved = closing

    capture_deadline = time.monotonic() + total_seconds

    beats: list[BeatResult] = []
    for index, shot in enumerate(timeline.shots, start=1):
        remaining = capture_deadline - time.monotonic()
        if remaining <= 0:
            print(f"    out of time before '{shot.name}'")
            beats.append(
                BeatResult(shot.name, False, "the capture deadline passed")
            )
            break
        budget = remaining
        if index < len(timeline.shots) - 2:
            budget = max(0.0, remaining - reserved)
        hold = min(shot.hold, budget)

        shot_started = time.monotonic()
        print(f"[{index}/{len(timeline.shots)}] {shot.name}  ({hold:.1f}s)")
        try:
            timeout = max(0.1, capture_deadline - time.monotonic())
            ok, detail = await asyncio.wait_for(
                _play_shot(cdp, shot, hold), timeout=timeout
            )
        except TimeoutError:
            ok, detail = False, "the capture deadline passed mid-beat"
        except Exception as exc:  # the beat failed; record why
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        beats.append(BeatResult(shot.name, ok, detail))
        if not ok:
            print(f"    beat '{shot.name}' did not land: {detail}")

        took = time.monotonic() - shot_started
        print(f"    {took:5.1f}s elapsed")

        badge_now = await cdp.evaluate(READ_BADGE)
        if "fallback" in (badge_now or "").lower():
            print(f"    WARNING: the provider fell back during '{shot.name}'")
        elif "live" not in (badge_now or "").lower():
            print(f"    note: the badge during '{shot.name}' was {badge_now!r}")
    return beats


async def _pump(writer: FrameWriter) -> None:
    while True:
        writer.pump()
        await asyncio.sleep(1.0 / FPS / 2)


def build_timeline() -> Timeline:
    """The beats of the video.

    The order follows the plan: the problem, the studio, the agent trace, the
    auto-eventing, the personalizer, and the closing stack line. Every caption
    says something that is true of what is on screen at that moment, and no
    caption claims a fallback, a veto or a number the studio does not show.

    The runtime is budgeted, because a reader change is not free: the service
    re-narrates the feed, which takes between two and sixteen seconds
    depending on the model. The holds below are what is left of the two
    minutes after those calls are paid for.
    """
    timeline = Timeline()

    # 0:00 - the problem.
    timeline.add(
        Shot(
            name="title card",
            caption="",
            title=(
                "A scoreline tells you nothing.",
                "Touchline Intelligence — five agents that explain a match, live.",
                "Synthetic data · Microsoft Foundry · Azure Container Apps",
            ),
            settle=0.3,
            hold=5.0,
        )
    )

    # 0:06 - the studio, live.
    timeline.add(
        Shot(
            name="the studio, live on Azure",
            caption=(
                "Live on Azure. The model writes the words; "
                "deterministic code writes the numbers."
            ),
            action=click_when_ready("60×"),
            settle=1.0,
            hold=5.0,
        )
    )

    # 0:12 - the match runs, and a key moment lands.
    timeline.add(
        Shot(
            name="a key moment arrives",
            caption=(
                "A key moment arrives with the metric, the value, "
                "and the unit behind it."
            ),
            action=click_when_ready("Next key moment"),
            settle=1.5,
            hold=5.0,
        )
    )

    # 0:19 - the citations.
    timeline.add(
        Shot(
            name="the citations",
            caption=(
                "Every number is recomputed from the event log — "
                "and shown with the event ids it came from."
            ),
            action=FOCUS_EVENT,
            settle=1.5,
            hold=5.0,
        )
    )

    # 0:26 - the agent trace.
    timeline.add(
        Shot(
            name="one moment, narrowed to its hand-offs",
            caption=(
                "Five agents, one shared state. "
                "The narrator asks the explainer over A2A."
            ),
            action=click_when_ready("show hand-offs"),
            settle=2.0,
            hold=5.5,
        )
    )

    # 0:34 - the whole trace.
    timeline.add(
        Shot(
            name="the whole trace",
            caption="340 hand-offs, every one of them typed and visible as it happens.",
            action=click_when_ready("show all"),
            settle=1.0,
            hold=4.0,
        )
    )

    # 0:39 - the auto-eventing.
    timeline.add(
        Shot(
            name="the vision tracker",
            caption=(
                "The synthetic feed is tracked and scored against its own "
                "ground truth: 100% precision, 85.4% recall."
            ),
            settle=0.5,
            hold=4.5,
        )
    )

    # 0:47 - full time. The clock reaches the end of the match, so the
    # feed shows the last moments and the recap on top of them.
    #
    # This beat comes before the reader changes on purpose. The service
    # rewrites the feed around the moment the reader is looking at, so
    # the reader beats want the clock at the end of the match -- where
    # the moments on screen are the ones the rewrite reaches first.
    timeline.add(
        Shot(
            name="full time",
            caption="Full time — the recap, from the same 90 minutes of events.",
            action=[SPRINT_TO_END, wait_for_idle],
            settle=3.0,
            hold=4.5,
        )
    )

    # 0:55 - the personalizer. The same moment, written for one side.
    timeline.add(
        Shot(
            name="read it as my team",
            caption="The same moment, rewritten for one side — over the same events.",
            action=[SWITCH_SIDE, wait_for_idle],
            settle=2.0,
            hold=4.0,
        )
    )

    # 1:03 - the language. The caption carries the English of the moment that
    # is on screen in another language, so nothing is shown without English
    # beside it.
    # The English is read before the switch and shown after it, so the
    # German on screen always has its English underneath it.
    captured: dict[str, str] = {}

    async def grab_english(cdp: CDP) -> str:
        text = await cdp.evaluate(READ_INSIGHT)
        excerpt = await limit_insight(cdp, text)
        captured["english"] = excerpt
        captured["body"] = await cdp.evaluate(READ_BODY)
        captured["id"] = await cdp.evaluate(READ_OVERLAY_ID)
        return ""

    async def show_english(cdp: CDP) -> str:
        english = captured.get("english", "")
        generic = (
            "The interface is English. "
            "The explanation is rewritten in the reader's language."
        )
        if not english:
            return generic
        script = SAME_CARD.replace("__ID__", json.dumps(captured.get("id", "")))
        if not await cdp.evaluate(script):
            # A different moment is on screen now, so the caption cannot
            # claim to be its translation.
            return generic
        if not await cdp.evaluate(LANGUAGE_LANDED):
            # The rewrite did not land -- the grounding check may have sent
            # the copy back -- so the screen is still English and the
            # caption must not claim otherwise.
            return generic
        return f"In English: {english}"

    timeline.add(
        Shot(
            name="the same moment in Deutsch",
            caption=grab_english,
            action=[SWITCH_LANGUAGE, wait_for_idle],
            caption_after=show_english,
            # The beat only counts when the card on screen actually
            # changed language. A caption that claims a translation the
            # screen does not show is a take that must be rejected.
            check=LANGUAGE_LANDED,
            settle=1.5,
            hold=5.0,
        )
    )

    # 1:11 - the stack.
    timeline.add(
        Shot(
            name="the stack",
            caption=(
                "Microsoft Foundry · Microsoft Agent Framework · A2A · "
                "Azure Container Apps — one deployment, one URL, no key anywhere."
            ),
            settle=0.5,
            hold=5.5,
        )
    )

    return timeline


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL, help="the URL to record")
    parser.add_argument(
        "--out", default="build/touchline-demo.mp4", help="the video to write"
    )
    parser.add_argument("--headed", action="store_true", help="show the browser window")
    parser.add_argument(
        "--seconds", type=float, default=110.0, help="the total length of the video"
    )
    parser.add_argument(
        "--seed", type=int, default=42, help="the match seed the studio shows"
    )
    args = parser.parse_args()

    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = REPO_ROOT / out_path

    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg is not on PATH.")
    if shutil.which("ffprobe") is None:
        raise SystemExit("ffprobe is not on PATH (it ships with ffmpeg).")

    timeline = build_timeline()
    if not timeline.shots:
        raise SystemExit("The timeline is empty.")

    accepted = asyncio.run(
        record(args.url, out_path, timeline, args.headed, args.seconds, args.seed)
    )
    if accepted:
        print(f"wrote {out_path}")
    else:
        print(
            f"take rejected; the rejected take is kept at {take_path(out_path)}",
            file=sys.stderr,
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
