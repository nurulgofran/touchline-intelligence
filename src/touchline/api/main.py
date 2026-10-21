"""The FastAPI application.

This module serves the studio: the live match over a WebSocket, the match
and its feed over REST, the Explainer published as an A2A agent, and the
built frontend.

The health endpoint deliberately reports **provider state**, not just "the
process is up". A probe that fails and a provider that has fallen back to
the template engine are different failures, and an operator looking at a
red Container App needs to be able to tell them apart without opening a
shell.

Three things are wired here that are worth knowing about:

**The engine starts at startup, not at the first connection.** A judge who
opens the studio thirty seconds into the match finds the analysis already
warm, because the match has been being analysed since the container
started. The socket is a window onto work in progress, not a trigger for
it.

**The Explainer is published as an A2A agent, at the paths the
specification fixes** — ``/.well-known/agent-card.json`` and ``/a2a`` — so
any A2A client can find it. The Narrator is the client, and it is given
the Explainer as its in-process fallback, so a wire that cannot be used
degrades the transport rather than the feed.

**The A2A hop is made against the running application.** By default the
client speaks JSON-RPC into this same ASGI application, which is a real
protocol exchange and works in a container with no public DNS and no
certificate. Setting ``TOUCHLINE_A2A_TRANSPORT=socket`` makes the hop go
over a real socket to ``TOUCHLINE_PUBLIC_URL`` instead, for a deployment
that wants the traffic to leave the process.
"""

from __future__ import annotations

import contextlib
import logging
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.routing import Mount

from touchline.api import ws as ws_module
from touchline.api.live import get_engine, set_engine_hook
from touchline.api.routes import match as match_routes
from touchline.live.engine import LiveMatchEngine
from touchline.orchestration.a2a_server import A2A_RPC_PATH, build_explainer_app
from touchline.orchestration.workflow import build_explainer_client
from touchline.providers import status as provider_status

logger = logging.getLogger(__name__)

APP_VERSION = "0.2.0"

#: Where the built frontend lands. Absent until the web build runs.
WEB_DIST = Path(os.environ.get("TOUCHLINE_WEB_DIST", "web/dist"))

#: Which match the studio shows by default.
DEFAULT_SEED = int(os.environ.get("TOUCHLINE_SEED", "42"))

#: The public address of this service. It is what the agent card publishes
#: and what the A2A client dials when the hop goes over a socket.
PUBLIC_URL = os.environ.get(
    "TOUCHLINE_PUBLIC_URL", "http://localhost:8000"
).rstrip("/")

#: How the A2A hop is made. ``asgi`` speaks JSON-RPC into this same
#: application; ``socket`` dials ``TOUCHLINE_PUBLIC_URL`` over the network.
A2A_TRANSPORT = os.environ.get("TOUCHLINE_A2A_TRANSPORT", "asgi").lower()


def _model_configured() -> bool:
    """Whether a Foundry endpoint and deployment name are both present."""
    return bool(os.environ.get("FOUNDRY_PROJECT_ENDPOINT")) and bool(
        os.environ.get("FOUNDRY_MODEL")
    )


def _configure_logging() -> None:
    """Let the service's own log lines reach the container's log.

    Uvicorn configures its own loggers and leaves the rest of the tree
    alone, so the application's lines sat below the root's default level
    and never appeared. That mattered: the lines that say the service
    received a control were invisible, so a control that was lost and a
    control that was never sent looked the same from the outside.

    The project has a handful of these lines, and the operator needs
    them, so they are turned on rather than left to chance.
    """
    logger = logging.getLogger("touchline")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(levelname)s %(name)s: %(message)s")
        )
        logger.addHandler(handler)
    logger.propagate = False


_configure_logging()


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Start the analysis when the service starts, and stop it when it ends.

    The analysis runs in the background for as long as the container is
    alive, so a judge never waits for it. That is what ``minReplicas: 1``
    buys: the match is already half-analysed by the time the link is
    opened.
    """
    get_engine(DEFAULT_SEED, start=False).start()
    logger.info("the live analysis of seed %s is running", DEFAULT_SEED)
    try:
        yield
    finally:
        await get_engine(DEFAULT_SEED, start=False).close()
        client = _http_client
        if client is not None:
            with contextlib.suppress(Exception):
                await client.aclose()


app = FastAPI(
    title="Touchline Intelligence",
    version=APP_VERSION,
    description=(
        "A multi-agent synthetic match insights engine running on Azure. "
        "All data is synthetic; all clubs and players are fictional."
    ),
    lifespan=lifespan,
)

#: The HTTP client the A2A hop uses. One for the whole process.
_http_client: httpx.AsyncClient | None = None


def _a2a_client() -> httpx.AsyncClient:
    """The client the Narrator reaches the Explainer with.

    The client is built once. In the default ``asgi`` transport it speaks
    JSON-RPC into this application through an ASGI transport, so the hop
    is a genuine protocol exchange that needs neither a socket nor a
    public name. In the ``socket`` transport it dials the public address
    over the network instead.
    """
    global _http_client
    if _http_client is None:
        if A2A_TRANSPORT == "socket":
            _http_client = httpx.AsyncClient(base_url=PUBLIC_URL, timeout=30.0)
        else:
            _http_client = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url=PUBLIC_URL,
                timeout=30.0,
            )
    return _http_client


def _publish_explainer(engine: LiveMatchEngine) -> None:
    """Publish an engine's Explainer as an A2A agent, and wire the hop.

    The default match is published at the paths the A2A specification
    fixes. Any other match is published under its own prefix, so a card
    never claims to be another match's.

    Args:
        engine: The engine whose Explainer is published.
    """
    seed = engine.seed
    prefix = "" if seed == DEFAULT_SEED else f"/a2a/seed-{seed}"
    url = f"{PUBLIC_URL}{prefix}{A2A_RPC_PATH}"

    a2a_app = build_explainer_app(engine.explainer, url=url)
    if prefix:
        # The routes are put in front of the static mount, or the mount at
        # "/" would answer first and the card would never be served.
        app.router.routes.insert(0, Mount(prefix, app=a2a_app))
    else:
        app.router.routes.extend(a2a_app.routes)

    engine.attach_explainer_client(
        build_explainer_client(
            engine.explainer,
            url=url,
            http_client=_a2a_client(),
        )
    )
    logger.info("published the explainer for seed %s at %s", seed, url)


set_engine_hook(_publish_explainer)

#: The match the studio shows, made now so the analysis can start the
#: moment the container does.
_default_engine = get_engine(DEFAULT_SEED, start=False)


@app.get("/healthz")
async def healthz() -> JSONResponse:
    """Liveness and readiness, with the provider state attached.

    The Container App's liveness and readiness probes both point here. A 200
    means the process is serving; the body says which provider is configured
    and which provider actually served the last call.
    """
    snapshot = provider_status.current(_model_configured())
    engine = get_engine(DEFAULT_SEED, start=False)
    return JSONResponse(
        status_code=200,
        content={
            "status": "ok",
            "version": APP_VERSION,
            # The commit the image was built from. The image is pulled by
            # tag, and a tag that points somewhere new is not a change
            # the Container App's template can see -- so a rebuild under
            # the same tag does not roll the container, and the old code
            # keeps serving. This is how you can tell which one is
            # running without going into the container.
            "revision": os.environ.get("APP_REVISION", "unknown"),
            "provider": snapshot.as_dict(),
            "match": {
                "seed": engine.seed,
                "computed_ms": engine.computed_ms,
                "insights": len(engine.overlays),
            },
        },
    )


@app.get("/api/healthz")
async def api_healthz() -> JSONResponse:
    """The same payload under /api, for clients that expect an API prefix."""
    return await healthz()


@app.get("/")
async def index() -> Response:
    """Serve the frontend if it has been built, otherwise describe the service."""
    index_file = WEB_DIST / "index.html"
    if index_file.is_file():
        return FileResponse(index_file)

    return JSONResponse(
        {
            "service": "Touchline Intelligence",
            "version": APP_VERSION,
            "docs": "/docs",
            "health": "/healthz",
            "live": ws_module.WS_PATH,
            "match": "/api/match",
            "agent_card": "/.well-known/agent-card.json",
            "note": (
                "The frontend has not been built into this image yet. "
                "The API is live; see /docs."
            ),
        },
    )


app.include_router(match_routes.router)
app.include_router(ws_module.router)

# The built frontend, mounted when it exists. StaticFiles raises if the
# directory is missing, so this is conditional rather than unconditional.
# It is mounted last, so the API and the A2A routes are matched first.
if WEB_DIST.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")


def a2a_url(seed: int = DEFAULT_SEED) -> str:
    """The URL a client calls to reach an engine's Explainer."""
    prefix = "" if seed == DEFAULT_SEED else f"/a2a/seed-{seed}"
    return f"{PUBLIC_URL}{prefix}{A2A_RPC_PATH}"


def describe() -> dict[str, Any]:
    """A short description of the deployment, for the log."""
    return {
        "version": APP_VERSION,
        "seed": DEFAULT_SEED,
        "public_url": PUBLIC_URL,
        "a2a_transport": A2A_TRANSPORT,
        "a2a_url": a2a_url(),
        "web_dist": str(WEB_DIST),
    }
