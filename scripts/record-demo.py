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
        response = await asyncio.wait_for(future, timeout=60)
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
        deadline = time.time() + timeout
        while time.time() < deadline:
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
# there yet is not a failure -- it is a wait -- and the feed is briefly empty
# while the service re-narrates.
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


# The server re-narrates when the reader changes, and the panel says so
# while it does. Waiting for that to finish keeps the take free of a
# half-rewritten card.
SETTLED = """
(async () => {{
  let last = -1;
  let stable = 0;
  for (let i = 0; i < 240; i += 1) {{
    const busy = document.body.innerText.includes('rewriting the feed');
    const count = document.querySelectorAll('article').length;
    if (!busy && count === last) {{
      stable += 1;
      if (stable >= 8) break;
    }} else {{
      stable = 0;
    }}
    last = count;
    await new Promise((r) => setTimeout(r, 250));
  }}
  return true;
}})()
"""


async def reset_audience(cdp: CDP) -> None:
    """Put the reader back to the default, and wait for the feed to settle.

    The audience is service state, not page state, so it survives a reload.
    Without this, a take opens on whatever the last visitor left behind.
    """
    for label in ("Analyst", "English"):
        await cdp.evaluate(CLICK.format(text=json.dumps(label)))
        await cdp.evaluate(SETTLED)


def click_and_wait(text: str) -> list[str]:
    """Click something and then wait for the studio to finish re-narrating."""
    return [CLICK.format(text=json.dumps(text)), SETTLED]


FOCUS_EVENT = """
(() => {
  const chip = document.querySelector('button[title^="Show ev-"]');
  if (!chip) return false;
  chip.focus();
  chip.scrollIntoView({block: 'center'});
  return true;
})()
"""


SEEK_TO_END = """
(() => {
  const scrubber = document.querySelector('input.scrubber');
  if (!scrubber) return false;
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype, 'value').set;
  setter.call(scrubber, scrubber.max);
  scrubber.dispatchEvent(new Event('input', {bubbles: true}));
  scrubber.dispatchEvent(new Event('pointerup', {bubbles: true}));
  scrubber.dispatchEvent(new Event('change', {bubbles: true}));
  scrubber.dispatchEvent(new Event('keyup', {bubbles: true}));
  scrubber.blur();
  return true;
})()
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
  const card = document.querySelector('article');
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
    deadline = time.time() + timeout
    port = 0
    while time.time() < deadline:
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

    while time.time() < deadline:
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


async def record(
    url: str,
    out_path: Path,
    timeline: Timeline,
    headed: bool,
    total_seconds: float,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)

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
            str(out_path),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    writer = FrameWriter(ffmpeg)

    cdp: CDP | None = None
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

        # The last two beats -- full time and the closing stack line -- are
        # held back from the rest, so a slow re-narration shortens the middle
        # of the video rather than cutting the ending off.
        closing = sum(
            shot.settle + shot.hold for shot in timeline.shots[-2:]
        )
        reserved = closing

        started = time.time()
        for index, shot in enumerate(timeline.shots, start=1):
            elapsed = time.time() - started
            remaining = total_seconds - elapsed
            if remaining <= 0:
                print(f"    out of time before '{shot.name}'")
                break
            budget = remaining
            if index < len(timeline.shots) - 2:
                budget = max(0.0, remaining - reserved)
            hold = min(shot.hold, budget)

            shot_started = time.time()
            caption = shot.caption
            if callable(caption):
                caption = await caption(cdp)
            print(f"[{index}/{len(timeline.shots)}] {shot.name}  ({hold:.1f}s)")
            if shot.title is not None:
                await set_title(cdp, *shot.title)
            else:
                await set_title(cdp, "", "", "")
            await set_caption(cdp, caption or "")

            if shot.action is not None:
                steps = shot.action if isinstance(shot.action, list) else [shot.action]
                for step in steps:
                    result = await cdp.evaluate(step)
                    if result is False:
                        print(f"    note: a step of '{shot.name}' found nothing")

            if shot.caption_after is not None:
                after = shot.caption_after
                text = await after(cdp) if callable(after) else after
                await set_caption(cdp, text or "")

            await asyncio.sleep(shot.settle)
            await asyncio.sleep(hold)

            took = time.time() - shot_started
            print(f"    {took:5.1f}s elapsed")

            badge_now = await cdp.evaluate(READ_BADGE)
            if "fallback" in (badge_now or "").lower():
                print(f"    WARNING: the provider fell back during '{shot.name}'")
            elif "live" not in (badge_now or "").lower():
                print(f"    note: the badge during '{shot.name}' was {badge_now!r}")

        await set_caption(cdp, "")
        await set_title(cdp, "")

        # Leave the deployed app the way a judge should find it.
        print("resetting the reader back to Analyst / English")
        await reset_audience(cdp)

        pump_task.cancel()
    finally:
        if cdp:
            await cdp.close()
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

    # 0:47 - the personalizer. The first change is the slow one.
    timeline.add(
        Shot(
            name="read it as my team",
            caption="The same moment, rewritten for one side — over the same events.",
            action=[
                *click_and_wait("My team"),
                click_when_ready("Riverside United"),
                SETTLED,
            ],
            settle=2.0,
            hold=4.0,
        )
    )
    timeline.add(
        Shot(
            name="read it as one player",
            caption="Or for one player — one person's actions in the same window.",
            action=[
                *click_and_wait("Player focus"),
                PICK_PLAYER,
                SETTLED,
            ],
            settle=2.0,
            hold=4.5,
        )
    )

    # 1:00 - the language. The caption carries the English of the moment that
    # is on screen in another language, so nothing is shown without English
    # beside it.
    # The English is read before the switch and shown after it, so the
    # German on screen always has its English underneath it.
    captured: dict[str, str] = {}

    async def grab_english(cdp: CDP) -> str:
        text = await cdp.evaluate(READ_INSIGHT)
        excerpt = await limit_insight(cdp, text)
        captured["english"] = excerpt
        return ""

    async def show_english(cdp: CDP) -> str:
        english = captured.get("english", "")
        return f"In English: {english}" if english else ""

    timeline.add(
        Shot(
            name="the same moment in Deutsch",
            caption=grab_english,
            action=click_and_wait("Deutsch"),
            caption_after=show_english,
            settle=1.5,
            hold=5.0,
        )
    )

    # 1:08 - full time.
    timeline.add(
        Shot(
            name="full time",
            caption="Full time — the recap, from the same 90 minutes of events.",
            action=SEEK_TO_END,
            settle=3.0,
            hold=4.5,
        )
    )

    # 1:16 - the stack.
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
    args = parser.parse_args()

    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = REPO_ROOT / out_path

    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg is not on PATH.")

    timeline = build_timeline()
    if not timeline.shots:
        raise SystemExit("The timeline is empty.")

    asyncio.run(
        record(args.url, out_path, timeline, args.headed, args.seconds)
    )
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
