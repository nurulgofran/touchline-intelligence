#!/usr/bin/env bash
#
# Build the production image and push it to the Azure Container Registry.
#
# Two transports, one Dockerfile:
#
#   MODE=acr     the build runs on Azure's own agents (ACR Tasks). Nothing
#                local is needed beyond the Azure CLI.
#   MODE=docker  the build runs in a local container runtime and the result is
#                pushed to the same registry.
#
# ACR Tasks is not available on an Azure for Students subscription -- it
# answers `TasksOperationsNotAllowed` for the registry and the subscription
# pair -- so MODE=docker is the route that works there. The registry, and
# therefore how the Container App pulls the image, is identical either way.
#
# Why the context is staged
# -------------------------
# The build context is staged into a clean temporary directory first,
# containing only what the Dockerfile actually copies. Two things in this
# repo break an unstaged build:
#
#   - .git/ holds a `fsmonitor--daemon.ipc` socket, and the tarball the
#     Azure CLI packs before uploading it cannot hold a socket ("unsupported
#     type").
#   - .venv and web/node_modules are 350 MB and 80 MB of files the image does
#     not need.
#
# The staged context is a few hundred kilobytes instead of a few hundred
# megabytes.
#
# Usage:
#   scripts/build-image.sh                       # ACR Tasks, tag v1
#   MODE=docker scripts/build-image.sh           # local build, push to ACR
#   TAG=v2 scripts/build-image.sh                # build and tag v2
#
# Overrides:
#   MODE, ACR, IMAGE_NAME, TAG, LOCATION, RG

set -euo pipefail

RG="${RG:-touchline-rg}"
ACR="${ACR:-touchlineacr}"
IMAGE_NAME="${IMAGE_NAME:-touchline-intelligence}"
TAG="${TAG:-v1}"
LOCATION="${LOCATION:-germanywestcentral}"
MODE="${MODE:-acr}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
note() { printf '    %s\n' "$*"; }

# --- preflight ---------------------------------------------------------------

say "Preflight"

command -v az >/dev/null 2>&1 || { echo "Azure CLI is not installed." >&2; exit 1; }
az account show >/dev/null 2>&1 || { echo "Not signed in. Run 'az login' first." >&2; exit 1; }

if ! az acr show -g "$RG" -n "$ACR" >/dev/null 2>&1; then
    echo "Container registry '$ACR' not found in '$RG'." >&2
    echo "Run scripts/provision-aca.sh, or create it first:" >&2
    echo "  az acr create -g $RG -n $ACR --sku Basic -l $LOCATION" >&2
    exit 1
fi

note "registry : $ACR"
note "image    : $IMAGE_NAME:$TAG"

# --- stage the context -------------------------------------------------------
#
# Exactly the paths the Dockerfile copies, and nothing else.

say "Staging the build context"

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

rsync -a --quiet \
    --exclude 'node_modules' \
    --exclude 'dist' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    "$REPO_ROOT/web/" "$STAGE/web/"

for path in src schemas requirements.txt pyproject.toml README.md LICENSE .dockerignore Dockerfile; do
    cp -R "$REPO_ROOT/$path" "$STAGE/$path"
done

# The frontend build writes into web/dist, and web/node_modules may have been
# installed for the local build. Neither belongs in the context.
rm -rf "$STAGE/web/dist"

note "staged $(du -sh "$STAGE" | cut -f1) into $STAGE"

# --- build -------------------------------------------------------------------
#
# The build runs from inside the staged directory, so --file and the context
# resolve against the staged copy rather than against the original
# repository.

say "Build (mode: $MODE)"

cd "$STAGE"

if [ "$MODE" = "docker" ]; then
    command -v docker >/dev/null 2>&1 || {
        echo "docker is not on PATH. Install a runtime, e.g. colima." >&2
        exit 1
    }
    docker info >/dev/null 2>&1 || {
        echo "The docker daemon is not reachable. Start it, e.g. 'colima start'." >&2
        exit 1
    }

    # The registry the image is pushed to. The credential is the signed-in
    # Azure session, not a stored password: `az acr login --expose-token`
    # hands back a refresh token, which the registry's token service exchanges
    # for the short-lived access token that `docker login` wants.
    #
    # A throwaway DOCKER_CONFIG keeps that token out of the user's own docker
    # config, and keeps a credential helper that is not installed (Docker
    # Desktop's) from being invoked on the way.
    #
    # The daemon is addressed by socket rather than by context, because the
    # context store lives inside DOCKER_CONFIG too and a fresh one would not
    # know the runtime's context.
    DOCKER_HOST_VALUE="$(docker context inspect "$(docker context show)" \
        --format '{{.Endpoints.docker.Host}}' 2>/dev/null || true)"
    DOCKER_CONFIG_DIR="$(mktemp -d)"
    export DOCKER_CONFIG="$DOCKER_CONFIG_DIR"
    if [ -n "$DOCKER_HOST_VALUE" ]; then
        export DOCKER_HOST="$DOCKER_HOST_VALUE"
    fi
    trap 'rm -rf "$STAGE" "$DOCKER_CONFIG_DIR"' EXIT

    REFRESH_TOKEN="$(az acr login --expose-token -n "$ACR" 2>/dev/null \
        | python3 -c 'import json,sys; print(json.load(sys.stdin)["refreshToken"])')"

    # The registry's own token service exchanges that refresh token for the
    # short-lived access token the registry API wants. The scope is the one
    # repository this build pushes.
    ACCESS_TOKEN="$(curl -sS -X POST "https://$ACR.azurecr.io/oauth2/token" \
        --data-urlencode "grant_type=refresh_token" \
        --data-urlencode "service=$ACR.azurecr.io" \
        --data-urlencode "scope=repository:$IMAGE_NAME:pull,push" \
        --data-urlencode "refresh_token=$REFRESH_TOKEN" \
        | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')"

    printf '%s' "$ACCESS_TOKEN" \
        | docker login --username 00000000-0000-0000-0000-000000000000 \
              --password-stdin "$ACR.azurecr.io" >/dev/null

    docker build -t "$ACR.azurecr.io/$IMAGE_NAME:$TAG" -f Dockerfile .
    docker push "$ACR.azurecr.io/$IMAGE_NAME:$TAG"
else
    az acr build \
        --registry "$ACR" \
        --image "$IMAGE_NAME:$TAG" \
        --file Dockerfile \
        .
fi

# --- report ------------------------------------------------------------------

say "Done"

cat <<EOF
    image : $ACR.azurecr.io/$IMAGE_NAME:$TAG

    The Container App pulls it with its managed identity (AcrPull on this
    registry), so no registry credential is stored in the deployment.
EOF
