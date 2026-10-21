#!/usr/bin/env python3
"""Check a deployed Touchline Intelligence URL end to end.

The studio is the demo, so the thing that matters is not that the process
starts -- it is that the whole chain answers on the public URL: the health
endpoint and its provider state, the A2A agent card, the REST payload, the
WebSocket frames the studio actually renders, and the studio itself served
from the same origin.

Usage:
    python scripts/verify-live.py \
        https://<app>.<env>.<region>.azurecontainerapps.io
    python scripts/verify-live.py \
        https://<app>.<env>.<region>.azurecontainerapps.io --wait 180

Every check prints PASS or FAIL. The exit status is 0 only when every check
passed, so the script can be used as a gate.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from typing import Any
from urllib.parse import urlparse

import httpx
import websockets

#: The frame types the studio renders. A live match sends all of them.
EXPECTED_FRAMES = {"hello", "provider", "clock", "events", "momentum", "trace"}

#: The WebSocket path the studio connects to.
WS_PATH = "/ws/match"


class Report:
    """Collects the checks and prints them as they happen."""

    def __init__(self) -> None:
        self.failures: list[str] = []

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        mark = "PASS" if ok else "FAIL"
        line = f"  [{mark}] {name}"
        if detail:
            line += f"  -- {detail}"
        print(line, flush=True)
        if not ok:
            self.failures.append(name)
        return ok

    def section(self, title: str) -> None:
        print(f"\n{title}", flush=True)


async def wait_for_health(client: httpx.AsyncClient, base: str, seconds: int) -> bool:
    """Poll the health endpoint until it answers or the budget runs out.

    Args:
        client: The HTTP client.
        base: The base URL.
        seconds: How long to keep trying.

    Returns:
        Whether the service came up.
    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            response = await client.get(f"{base}/healthz", timeout=10.0)
            if response.status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        await asyncio.sleep(5.0)
    return False


async def check_rest(client: httpx.AsyncClient, base: str, report: Report) -> None:
    """Check the REST surface and the agent card."""
    report.section("REST")

    response = await client.get(f"{base}/healthz", timeout=30.0)
    report.check(
        "GET /healthz",
        response.status_code == 200,
        f"HTTP {response.status_code}",
    )
    if response.status_code == 200:
        health = response.json()
        provider = health.get("provider", {})
        configured = provider.get("configured_provider")
        served = provider.get("last_call_provider")
        state = provider.get("last_call_state")
        report.check(
            "the health endpoint names the provider",
            bool(configured) and bool(served),
            f"configured={configured} served={served} state={state}",
        )
        report.check(
            "the last call was served by the configured provider",
            configured == served,
            f"{served} ({state})",
        )

    response = await client.get(f"{base}/.well-known/agent-card.json", timeout=30.0)
    report.check(
        "GET /.well-known/agent-card.json",
        response.status_code == 200,
        f"HTTP {response.status_code}",
    )
    if response.status_code == 200:
        card = response.json()
        skills = card.get("skills") or []
        skill_names = [s.get("id") or s.get("name") for s in skills]
        report.check(
            "the agent card publishes a skill",
            bool(skill_names),
            f"{card.get('name')} skills={skill_names}",
        )

    for path in (
        "/api/match",
        "/api/match/insights",
        "/api/match/momentum",
        "/api/match/trace",
        "/api/match/tracking",
        "/api/match/audience",
    ):
        response = await client.get(f"{base}{path}", timeout=60.0)
        ok = response.status_code == 200
        detail = f"HTTP {response.status_code}"
        if ok:
            try:
                body = response.json()
                if isinstance(body, list):
                    detail = f"{len(body)} entries"
                elif isinstance(body, dict):
                    detail = f"{len(body)} keys"
            except ValueError:
                detail = "not json"
        report.check(f"GET {path}", ok, detail)

    response = await client.get(f"{base}/", timeout=30.0)
    html = response.text if response.status_code == 200 else ""
    report.check(
        "GET / serves the studio",
        response.status_code == 200 and "<html" in html.lower(),
        f"HTTP {response.status_code}",
    )


async def check_websocket(base: str, report: Report, seconds: int) -> None:
    """Watch the live socket and collect the frame types it sends.

    Args:
        base: The base URL.
        report: Where the checks go.
        seconds: How long to watch.
    """
    report.section("WebSocket")

    url = base.replace("https://", "wss://").replace("http://", "ws://") + WS_PATH
    seen: dict[str, Any] = {}
    overlays = 0

    try:
        async with websockets.connect(url, open_timeout=20.0) as socket:
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                try:
                    raw = await asyncio.wait_for(socket.recv(), timeout=10.0)
                except TimeoutError:
                    break
                frame = json.loads(raw)
                kind = frame.get("type", "?")
                if kind not in seen:
                    seen[kind] = frame
                if kind == "overlay":
                    overlays += 1
    except Exception as exc:
        report.check(f"the socket at {WS_PATH}", False, repr(exc))
        return

    report.check(f"the socket at {WS_PATH}", True, f"{len(seen)} frame types")
    for kind in sorted(EXPECTED_FRAMES):
        report.check(
            f"a '{kind}' frame arrived",
            kind in seen,
            "" if kind in seen else f"only saw {sorted(seen)}",
        )
    report.check(
        "at least one overlay was delivered",
        overlays > 0,
        f"{overlays} overlays",
    )

    hello = seen.get("hello") or {}
    if hello:
        report.check(
            "the hello frame carries the match clock",
            "clock_ms" in hello or "clock" in hello,
            f"keys={sorted(hello)[:6]}",
        )

    await check_reader(base, report)


async def check_reader(base: str, report: Report) -> None:
    """Change the reader, and check that the service answers at once.

    The reader is the service's state, so a control that says "explain it
    as a fan" has to be answered with the fan's reading -- and answered *at
    once*. A service that waits for the whole rewrite before it answers
    cannot answer a second control at all if the socket is cut first,
    which is how a reader change used to be lost.
    """
    report.section("The reader")

    url = base.replace("https://", "wss://").replace("http://", "ws://") + WS_PATH

    try:
        async with websockets.connect(url, open_timeout=20.0) as socket:
            hello = json.loads(await asyncio.wait_for(socket.recv(), timeout=20.0))
            was = hello.get("audience") or {}
            current = was.get("mode")
            # The studio is English only, so the check changes the reading
            # rather than the language: the deployment must come back the
            # way it was found, and a judge has no language switcher to
            # undo one that was left behind.
            wanted = "fan" if current != "fan" else "analyst"

            await socket.send(
                json.dumps(
                    {
                        "type": "control",
                        "action": "audience",
                        "value": {
                            "mode": wanted,
                            "locale": "en",
                            "side": None,
                            "player_id": None,
                            "player_name": None,
                            "metric": None,
                        },
                    }
                )
            )

            deadline = time.monotonic() + 15.0
            applied = None
            while time.monotonic() < deadline:
                try:
                    raw = await asyncio.wait_for(socket.recv(), timeout=5.0)
                except TimeoutError:
                    break
                frame = json.loads(raw)
                if frame.get("type") == "audience":
                    applied = (frame.get("audience") or {}).get("mode")
                    if applied:
                        break

            # The reader is the service's state and it is shared, so it
            # is put back the way it was found. A judge should find the
            # default, not the reading a check left behind.
            if current:
                await socket.send(
                    json.dumps(
                        {
                            "type": "control",
                            "action": "audience",
                            "value": {
                                "mode": was.get("mode", "analyst"),
                                "locale": "en",
                                "side": was.get("side"),
                                "player_id": was.get("player_id"),
                                "player_name": was.get("player_name"),
                                "metric": was.get("metric"),
                            },
                        }
                    )
                )
    except Exception as exc:
        report.check("the reader can be changed", False, repr(exc))
        return

    report.check(
        "the reader can be changed",
        applied == wanted,
        f"asked for {wanted}, the service is in {applied}",
    )


async def main() -> int:
    """Run every check against the URL.

    Returns:
        The exit status.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="the public URL to check")
    parser.add_argument(
        "--wait",
        type=int,
        default=120,
        help="seconds to wait for the service to come up",
    )
    parser.add_argument(
        "--watch",
        type=int,
        default=45,
        help="seconds to watch the WebSocket",
    )
    args = parser.parse_args()

    base = args.url.rstrip("/")
    if not urlparse(base).scheme:
        base = "https://" + base

    print(f"Checking {base}", flush=True)
    report = Report()

    async with httpx.AsyncClient(follow_redirects=True) as client:
        report.section("Availability")
        up = await wait_for_health(client, base, args.wait)
        report.check("the service came up", up)
        if not up:
            print("\nThe service did not answer /healthz in time.")
            return 1

        await check_rest(client, base, report)
        await check_websocket(base, report, args.watch)

    print()
    if report.failures:
        print(f"{len(report.failures)} check(s) failed:")
        for name in report.failures:
            print(f"  - {name}")
        return 1

    print("Every check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
