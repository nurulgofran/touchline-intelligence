# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Touchline Intelligence — production image
#
# One image, one process, one container. Azure Container Apps runs it as a
# single replica (minReplicas 1 so the demo URL is never cold, maxReplicas 1
# because MatchStateStore is in-process), pulling it anonymously from a public
# GHCR package.
#
# The frontend build stage is added here when `web/` lands (Day 4); until then
# the image serves the API only and the ACA health probe has something to hit.
# ---------------------------------------------------------------------------

# --- dependencies -----------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# The runtime dependency set is requirements.txt, not pyproject's minimal
# [project].dependencies — the latter lists only what the simulator needs so
# the test suite can run on pydantic alone.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# The project itself, installed without re-resolving dependencies.
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir --no-deps .

# --- runtime ----------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

# libglib2.0-0 and libgomp1 are what opencv-python-headless needs at import
# time; there is no curl in the image, the health check uses Python instead.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# A non-root user: the container runs with no shell access to the host and
# writes only to its own filesystem.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY --chown=appuser:appuser src ./src
COPY --chown=appuser:appuser schemas ./schemas

ENV PYTHONPATH=/app/src

USER appuser
EXPOSE 8000

# Mirrors the ACA readiness probe, so `docker run` alone still fails loudly if
# the app is broken.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).status == 200 else 1)"]

# --proxy-headers because the request arrives through the ACA ingress, and
# --forwarded-allow-ips "*" because that ingress is the only thing in front.
CMD ["uvicorn", "touchline.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
