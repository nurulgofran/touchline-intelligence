"""The live match socket.

One WebSocket per viewer. The server pushes frames — the match clock, the
events, the momentum points, the overlays, the hand-off trace — and the
viewer pushes back controls: play, pause, replay, speed, seek, next.

The socket is the only place the studio's state and the server's state
have to agree, so the protocol is small and explicit. Every frame carries
a ``type``; every control carries a ``type`` of ``control`` and an
``action``.

**The clock never waits for the model.** The server sends the clock frame
on every tick whether or not anything else happened, so the studio can
always show where the match is. An overlay that the model produced late
arrives late, with its own match clock attached, and the studio shows it
when it arrives — because a feed that stalls waiting for a model call
looks like a broken feed, and this one is not broken.

**The client reconnects.** The Container App's ingress has a 240-second
request timeout that cannot be changed, so a long-lived socket may be cut.
The studio reopens the socket and asks the server to resume from the clock
it last saw, which is why ``seek`` is part of the protocol and not an
internal detail.

The frames:

``hello``
    Once, at the start: the match, the two clubs, the provider state, the
    speed, and where the clock is.
``clock``
    On every tick: where the match is, the score, and how far the analysis
    has got.
``events``
    The events the clock has just passed.
``momentum``
    The momentum points the clock has just reached.
``overlay``
    An insight, with its metrics and the events it was computed from.
``trace``
    The hand-offs made since the last frame.
``provider``
    Which provider actually served the last call, and whether it was live
    or a fallback.
``end``
    The final whistle.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from touchline.api.live import LiveSession, get_engine, provider_payload

logger = logging.getLogger(__name__)

__all__ = ["WS_PATH", "apply_control", "router"]

#: Where the live match socket lives.
WS_PATH = "/ws/match"

#: How often the server pushes a frame. Ten a second is enough for a smooth
#: clock and cheap enough to leave running all match.
TICK_SECONDS = 0.1

router = APIRouter()


def apply_control(session: LiveSession, message: dict[str, Any]) -> str:
    """Apply one control message to a session.

    The control surface is deliberately small, and every action is safe to
    send at any time. Nothing here can break the analysis: the analysis
    for a stretch does not depend on when it was computed, so moving the
    clock cannot change what the match says.

    Args:
        session: The session to control.
        message: The control message.

    Returns:
        A short description of what was done, for the log.
    """
    action = str(message.get("action", "")).lower()
    value = message.get("value")

    if action == "play":
        session.play()
        return "play"
    if action == "pause":
        session.pause()
        return "pause"
    if action == "replay":
        session.replay()
        return "replay"
    if action == "speed":
        try:
            session.set_speed(float(value))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return "speed ignored"
        return f"speed {session.speed}"
    if action == "seek":
        try:
            session.seek(int(value))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return "seek ignored"
        return f"seek {int(session.clock_ms)}"
    if action == "next":
        moment = session.next_moment()
        if moment is None:
            return "no further moment"
        session.seek(moment)
        return f"next {moment}"
    return f"unknown action {action!r}"


async def _send(websocket: WebSocket, frame: dict[str, Any]) -> None:
    """Send one frame, swallowing a socket that has already gone."""
    with contextlib.suppress(WebSocketDisconnect, RuntimeError):
        await websocket.send_json(frame)


async def _read_controls(
    websocket: WebSocket,
    session: LiveSession,
    closed: asyncio.Event,
) -> None:
    """Read control messages until the socket closes."""
    try:
        while True:
            message = await websocket.receive_json()
            if not isinstance(message, dict):
                continue
            if message.get("type") not in (None, "control"):
                continue
            action = apply_control(session, message)
            logger.info("live control: %s", action)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.debug("the control channel closed", exc_info=True)
    finally:
        closed.set()


@router.websocket(WS_PATH)
async def live_match(websocket: WebSocket, seed: int = Query(default=42)) -> None:
    """Serve one viewer's watch of one match.

    Args:
        websocket: The socket.
        seed: Which match to watch. The same seed is the same match.
    """
    await websocket.accept()
    engine = get_engine(seed)
    session = LiveSession(engine=engine)
    closed = asyncio.Event()

    await _send(websocket, session.hello())
    await _send(websocket, {"type": "provider", "provider": provider_payload()})

    controls = asyncio.create_task(
        _read_controls(websocket, session, closed), name="touchline-controls"
    )
    try:
        while not closed.is_set():
            for frame in session.tick():
                await _send(websocket, frame)
                if frame["type"] == "clock" and frame.get("complete"):
                    await _send(
                        websocket,
                        {
                            "type": "end",
                            "clock_ms": frame["clock_ms"],
                            "score_home": frame["score_home"],
                            "score_away": frame["score_away"],
                        },
                    )
            await asyncio.sleep(TICK_SECONDS)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("the live socket failed")
    finally:
        closed.set()
        controls.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await controls
