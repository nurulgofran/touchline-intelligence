"""The FastAPI application.

Today this module serves two things: a health endpoint that the Azure
Container Apps probe hits, and the static frontend once ``web/dist`` exists.
The live match stream, the agent trace and the overlay feed are added on top of
it as the workflow lands.

The health endpoint deliberately reports **provider state**, not just "the
process is up". A probe that fails and a provider that has fallen back to the
template engine are different failures, and an operator looking at a red
Container App needs to be able to tell them apart without opening a shell.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from touchline.providers import status as provider_status

APP_VERSION = "0.1.0"

#: Where the built frontend lands. Absent until the web build runs.
WEB_DIST = Path(os.environ.get("TOUCHLINE_WEB_DIST", "web/dist"))

app = FastAPI(
    title="Touchline Intelligence",
    version=APP_VERSION,
    description=(
        "A multi-agent synthetic match insights engine running on Azure. "
        "All data is synthetic; all clubs and players are fictional."
    ),
)


def _model_configured() -> bool:
    """Whether a Foundry endpoint and deployment name are both present."""
    return bool(os.environ.get("FOUNDRY_PROJECT_ENDPOINT")) and bool(
        os.environ.get("FOUNDRY_MODEL")
    )


@app.get("/healthz")
async def healthz() -> JSONResponse:
    """Liveness and readiness, with the provider state attached.

    The Container App's liveness and readiness probes both point here. A 200
    means the process is serving; the body says which provider is configured
    and which provider actually served the last call.
    """
    snapshot = provider_status.current(_model_configured())
    return JSONResponse(
        status_code=200,
        content={
            "status": "ok",
            "version": APP_VERSION,
            "provider": snapshot.as_dict(),
        },
    )


@app.get("/api/healthz")
async def api_healthz() -> JSONResponse:
    """The same payload under /api, for clients that expect an API prefix."""
    return await healthz()


@app.get("/")
async def index() -> FileResponse | JSONResponse:
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
            "note": (
                "The frontend has not been built into this image yet. "
                "The API is live; see /docs."
            ),
        },
    )


# The built frontend, mounted when it exists. StaticFiles raises if the
# directory is missing, so this is conditional rather than unconditional.
if WEB_DIST.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
