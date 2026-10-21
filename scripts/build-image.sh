#!/usr/bin/env bash
#
# Build the production image and push it to the registry the Container App
# pulls from.
#
# Two destinations, one Dockerfile:
#
#   DEST=acr     an Azure Container Registry in the same subscription, pulled
#                by the Container App with its managed identity. No registry
#                credential exists anywhere in the deployment. This is the
#                default and the route the deployment uses.
#   DEST=ghcr    a public GitHub Container Registry package, which Azure
#                Container Apps pulls anonymously. The alternative route.
# Two transports for the build:
#
#   MODE=docker  the build runs in a local container runtime.
#   MODE=acr     the build runs on Azure's own agents (ACR Tasks). Not
#                available on an Azure for Students subscription -- it answers
#                `TasksOperationsNotAllowed` for the registry and the
#                subscription pair.
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
#   scripts/build-image.sh                          # local build, push to ACR
#   MODE=acr DEST=acr scripts/build-image.sh        # ACR Tasks, push to ACR
#   TAG=v2 scripts/build-image.sh                   # build and tag v2
#
# **Use a new tag for every build.** The Container App pulls the image by
# tag, and a tag that points somewhere new is not a change its template
# can see: `az containerapp update --image ...:v4` with the same tag
# changes nothing, the control plane does not roll the container, and the
# old code keeps serving. A new tag is a new template, and a new template
# is a new revision. The image carries the commit it was built from, so
# the running version can be read from the outside in `/healthz`.
#
# Overrides:
#   DEST, MODE, IMAGE_NAME, TAG, ACR, RG, GHCR_OWNER

set -euo pipefail

RG="${RG:-touchline-rg}"
ACR="${ACR:-touchlineacr}"
GHCR_OWNER="${GHCR_OWNER:-nurulgofran}"
IMAGE_NAME="${IMAGE_NAME:-touchline-intelligence}"
TAG="${TAG:-v1}"
LOCATION="${LOCATION:-germanywestcentral}"
MODE="${MODE:-docker}"
DEST="${DEST:-acr}"

# The platform the image is built for.
#
# Azure Container Apps' Consumption (Express) environment runs on amd64 nodes,
# so an amd64 image is what the deployment needs. The build machine may be
# anything -- on Apple silicon the amd64 build runs under Rosetta, which is
# near-native -- so the platform is pinned here rather than left to whatever
# the local runtime happens to be.
PLATFORM="${PLATFORM:-linux/amd64}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
note() { printf '    %s\n' "$*"; }

# --- preflight ---------------------------------------------------------------

say "Preflight"

command -v az >/dev/null 2>&1 || { echo "Azure CLI is not installed." >&2; exit 1; }
az account show >/dev/null 2>&1 || { echo "Not signed in. Run 'az login' first." >&2; exit 1; }

case "$DEST" in
    ghcr)
        command -v gh >/dev/null 2>&1 || {
            echo "The GitHub CLI is needed to push to GHCR." >&2; exit 1;
        }
        gh auth status >/dev/null 2>&1 || {
            echo "Not signed in to GitHub. Run 'gh auth login' first." >&2; exit 1;
        }
        IMAGE_REF="ghcr.io/$GHCR_OWNER/$IMAGE_NAME:$TAG"
        ;;
    acr)
        az acr show -g "$RG" -n "$ACR" >/dev/null 2>&1 || {
            echo "Container registry '$ACR' not found in '$RG'." >&2
            echo "Create it first:" >&2
            echo "  az acr create -g $RG -n $ACR --sku Basic -l $LOCATION" >&2
            exit 1
        }
        IMAGE_REF="$ACR.azurecr.io/$IMAGE_NAME:$TAG"
        ;;
    *)
        echo "Unknown DEST '$DEST'. Use 'ghcr' or 'acr'." >&2
        exit 1
        ;;
esac

note "destination : $DEST"
note "image       : $IMAGE_REF"
note "mode        : $MODE"

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

say "Build"

cd "$STAGE"

# The commit the image is built from. It is passed in rather than read
# inside the build, because the staged context has no repository
# metadata in it.
APP_REVISION="$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
note "revision : $APP_REVISION"

if [ "$MODE" = "acr" ]; then
    # ACR Tasks runs the build on Azure's own agents.
    az acr build \
        --registry "$ACR" \
        --image "$IMAGE_NAME:$TAG" \
        --build-arg "APP_REVISION=$APP_REVISION" \
        --file Dockerfile \
        .
else
    command -v docker >/dev/null 2>&1 || {
        echo "docker is not on PATH. Install a runtime, e.g. colima." >&2
        exit 1
    }
    docker info >/dev/null 2>&1 || {
        echo "The docker daemon is not reachable. Start it, e.g. 'colima start'." >&2
        exit 1
    }

    # A throwaway DOCKER_CONFIG keeps the credential out of the user's own
    # docker config, and keeps a credential helper that is not installed
    # (Docker Desktop's) from being invoked on the way.
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
    # The CLI looks for its plugins under the config directory, so the
    # user's own plugin directory is carried over -- buildx lives there.
    if [ -d "$HOME/.docker/cli-plugins" ]; then
        ln -s "$HOME/.docker/cli-plugins" "$DOCKER_CONFIG_DIR/cli-plugins"
    fi
    trap 'rm -rf "$STAGE" "$DOCKER_CONFIG_DIR"' EXIT

    if [ "$DEST" = "ghcr" ]; then
        # The token comes from a file if one is given, otherwise from the
        # GitHub CLI's own session.
        #
        # A classic PAT with the `write:packages` scope is what the registry
        # wants: the GitHub CLI's OAuth token cannot carry that scope, so a
        # refresh of the CLI's own credentials is not enough. The token is
        # read from a file rather than passed in, so it never lands in the
        # environment, in a shell history, or in this repository.
        if [ -n "${GHCR_TOKEN_FILE:-}" ]; then
            if [ ! -r "$GHCR_TOKEN_FILE" ]; then
                echo "GHCR_TOKEN_FILE '$GHCR_TOKEN_FILE' is not readable." >&2
                exit 1
            fi
            GHCR_TOKEN="$(cat "$GHCR_TOKEN_FILE")"
        else
            GHCR_TOKEN="$(gh auth token)"
        fi
        printf '%s' "$GHCR_TOKEN" \
            | docker login ghcr.io --username "$GHCR_OWNER" --password-stdin >/dev/null
        unset GHCR_TOKEN
    else
        # The signed-in Azure session. `az acr login --expose-token` hands back
        # a refresh token, which the registry's token service exchanges for
        # the short-lived access token the registry API wants.
        REFRESH_TOKEN="$(az acr login --expose-token -n "$ACR" 2>/dev/null \
            | python3 -c 'import json,sys; print(json.load(sys.stdin)["refreshToken"])')"

        ACCESS_TOKEN="$(curl -sS -X POST "https://$ACR.azurecr.io/oauth2/token" \
            --data-urlencode "grant_type=refresh_token" \
            --data-urlencode "service=$ACR.azurecr.io" \
            --data-urlencode "scope=repository:$IMAGE_NAME:pull,push" \
            --data-urlencode "refresh_token=$REFRESH_TOKEN" \
            | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')"

        printf '%s' "$ACCESS_TOKEN" \
            | docker login --username 00000000-0000-0000-0000-000000000000 \
                  --password-stdin "$ACR.azurecr.io" >/dev/null
    fi

    # The platform is pinned, because the deployment's nodes are amd64 even
    # when the build machine is not.
    #
    # The **docker driver** builds it, not the docker-container one. The
    # container driver runs BuildKit, and BuildKit installs its own
    # qemu-user emulator for a foreign platform; esbuild is a Go binary and
    # it segfaults under qemu-user, which fails the frontend build. The
    # docker driver leaves the platform to the runtime's own binfmt
    # handler, which on Apple silicon is Rosetta -- near-native, and it
    # handles esbuild.
    HOST_PLATFORM="linux/$(docker version --format '{{.Server.Arch}}' 2>/dev/null)"
    note "building for $PLATFORM (local runtime is $HOST_PLATFORM)"

    # OCI media types: the Container Apps runtime converts an image into the
    # OCI layout on import, and that conversion rejects the gzip layers of
    # the Docker format -- it answers "unsupported MIME type for
    # compression: application/vnd.docker.image.rootfs.diff.tar.gzip".
    if ! docker buildx build --builder default --platform "$PLATFORM" \
            --output "type=image,push=true,oci-mediatypes=true" \
            --build-arg "APP_REVISION=$APP_REVISION" \
            --tag "$IMAGE_REF" --file Dockerfile . 2>/dev/null; then
        note "the registry exporter is unavailable - loading and pushing instead"
        docker buildx build --builder default --platform "$PLATFORM" \
            --output "type=docker" --build-arg "APP_REVISION=$APP_REVISION" \
            --tag "$IMAGE_REF" --file Dockerfile .
        docker push "$IMAGE_REF"
    fi
fi

# --- report ------------------------------------------------------------------

say "Done"

echo "    image : $IMAGE_REF"
echo

if [ "$DEST" = "ghcr" ]; then
    cat <<EOF
    Make the package public before the Container App pulls it. A public
    package is pulled anonymously, so no registry credential is stored in
    the deployment:

        gh api --method PATCH /user/packages/container/$IMAGE_NAME \
            -f visibility=public
EOF
else
    cat <<EOF
    The Container App pulls this image with its managed identity, so there
    is no package visibility to set and no registry credential to store.
    The registry's admin user stays disabled.
EOF
fi

cat <<EOF

    Then point the app at it:

        scripts/provision-aca.sh
EOF

# The package is private until it is made public, and a private package
# cannot be pulled anonymously.
if [ "$DEST" = "ghcr" ]; then
    say "Package visibility"
    if gh api --method PATCH "/user/packages/container/$IMAGE_NAME" \
            -f visibility=public >/dev/null 2>&1; then
        note "the package is public"
    else
        note "could not set the visibility - set it to public by hand:"
        note "  gh api --method PATCH /user/packages/container/$IMAGE_NAME -f visibility=public"
    fi
fi
