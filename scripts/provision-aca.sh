#!/usr/bin/env bash
#
# Provision the Azure Container Apps environment and application for
# Touchline Intelligence. This is the only host the project has - there is no
# mirror and no second deployment.
#
# Idempotent: re-running it updates what exists and reports it.
#
# Prerequisites:
#   - Azure CLI 2.80.0 or newer
#   - az login completed
#   - The Foundry account already provisioned (scripts/provision-foundry.sh)
#   - The image published (scripts/build-image.sh)
#
# The identity
# ------------
# The app reaches Foundry through a **user-assigned managed identity** and
# nothing else: no key is in the deployment, in the repository, or anywhere
# else. The Consumption (Express) environment refuses a *system-assigned*
# identity -- it answers `ExpressEnvironmentFeatureNotSupported` -- and a
# user-assigned one it accepts, for the app's own access to Foundry and for
# the registry pull alike.
#
# The user-assigned identity's client id goes into the app as AZURE_CLIENT_ID.
# That is enough: azure-identity's DefaultAzureCredential already defaults its
# managed-identity leg to that variable, so the code keeps calling
# DefaultAzureCredential() and the credential source changes under it --
# `az login` on a laptop, the Container App's identity in Azure.
#
# The image path
# --------------
# The image is built by scripts/build-image.sh and pushed to an Azure
# Container Registry in the same subscription. The Container App pulls it
# with the app's **managed identity** -- the registry's admin user is
# disabled, and the deployment holds no registry credential at all: no
# password, no secret reference, no PAT.
#
# Usage:
#   scripts/provision-aca.sh
#
# Overrides (all optional):
#   RG, ENV, APP, LOCATION, IMAGE, ACCOUNT, PROJECT, DEPLOYMENT
#   IDENTITY       the user-assigned managed identity to create or reuse
#   CUSTOM_HOST   the custom domain to bind, e.g. demo.nurulgofran.dev
#   Set CUSTOM_HOST to bind a custom domain; leave it unset to use the default
#   *.azurecontainerapps.io FQDN.

set -euo pipefail

RG="${RG:-touchline-rg}"
ENV="${ENV:-touchline-env}"
APP="${APP:-touchline-app}"
LOCATION="${LOCATION:-germanywestcentral}"
IMAGE="${IMAGE:-touchlineacr.azurecr.io/touchline-intelligence:v1}"
ACCOUNT="${ACCOUNT:-touchlinefoundry}"
PROJECT="${PROJECT:-touchline}"
DEPLOYMENT="${DEPLOYMENT:-gpt-5-mini}"
CUSTOM_HOST="${CUSTOM_HOST:-}"
IDENTITY="${IDENTITY:-touchline-identity}"

# The registry the app pulls from. Empty means an anonymous pull from a
# public package, which is the deployment's route: the registry flags are
# omitted entirely and no credential is stored.
REGISTRY_SERVER="${REGISTRY_SERVER:-}"
ACR="${ACR:-touchlineacr}"

# minReplicas 1  -> the demo URL never scales to zero, so a judge never waits on
#                    a cold start. This is the cost driver and it is deliberate.
# maxReplicas 1  -> MatchStateStore is in-process and is the shared state every
#                    executor reads. With more than one replica, ACA's ingress
#                    load-balances requests across them and the Explainer can
#                    ground against an empty event log, which silently collapses
#                    the grounding veto. Raise this only after the state store
#                    moves to a database.
MIN_REPLICAS="${MIN_REPLICAS:-1}"
MAX_REPLICAS="${MAX_REPLICAS:-1}"
CPU="${CPU:-0.5}"
MEMORY="${MEMORY:-1.0Gi}"
TARGET_PORT="${TARGET_PORT:-8000}"

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
note() { printf '    %s\n' "$*"; }

# --- preflight ---------------------------------------------------------------

say "Preflight"

if ! command -v az >/dev/null 2>&1; then
    echo "Azure CLI is not installed. Install it with: brew install --cask azure-cli" >&2
    exit 1
fi

if ! az account show >/dev/null 2>&1; then
    echo "Not signed in. Run 'az login' first." >&2
    exit 1
fi

SUB="$(az account show --query id -o tsv)"
SUB_NAME="$(az account show --query name -o tsv)"
note "subscription: $SUB_NAME ($SUB)"

# The role assignments below scope to resources in this subscription, and a
# managed identity can only be granted a role on a resource in its own
# subscription. Foundry and Container Apps therefore have to live together.
if ! az cognitiveservices account show -n "$ACCOUNT" -g "$RG" >/dev/null 2>&1; then
    echo "Foundry account '$ACCOUNT' not found in '$RG'." >&2
    echo "Run scripts/provision-foundry.sh first." >&2
    exit 1
fi

PROJECT_ENDPOINT="$(az cognitiveservices account project show \
    -n "$ACCOUNT" --project-name "$PROJECT" -g "$RG" \
    --query 'properties.endpoints."AI Foundry API"' -o tsv)"

# --- environment -------------------------------------------------------------

say "Container Apps environment"

# The environment is created with an explicit Log Analytics workspace rather
# than the auto-generated one. The auto-generation is what fails with
# "There is not enough space on the disk" -- it tries to create a workspace
# named workspace-<env><random> at the same moment as the environment, and the
# two collide on the subscription's workspace allocation. Creating the
# workspace first, and handing its id and key to the environment, keeps the
# two steps separate and gives the workspace a name that means something.
LAW="${LAW:-touchline-logs}"

if az monitor log-analytics workspace show -g "$RG" -n "$LAW" >/dev/null 2>&1; then
    note "$LAW already exists - leaving it alone"
else
    az monitor log-analytics workspace create -g "$RG" -l "$LOCATION" -n "$LAW" --output none
    note "created $LAW"
fi

LAW_ID="$(az monitor log-analytics workspace show -g "$RG" -n "$LAW" \
    --query customerId -o tsv)"
LAW_KEY="$(az monitor log-analytics workspace get-shared-keys -g "$RG" -n "$LAW" \
    --query primarySharedKey -o tsv)"

if az containerapp env show -n "$ENV" -g "$RG" >/dev/null 2>&1; then
    note "$ENV already exists - leaving it alone"
else
    # The first attempt on a fresh subscription fails with "There is not
    # enough space on the disk" and leaves the environment behind in a Failed
    # state. Deleting it and asking again succeeds -- the second allocation
    # finds the space the first one was holding.
    if ! az containerapp env create -n "$ENV" -g "$RG" -l "$LOCATION" \
            --logs-workspace-id "$LAW_ID" --logs-workspace-key "$LAW_KEY" \
            --output none 2>/dev/null; then
        note "the first attempt failed - deleting it and trying once more"
        az containerapp env delete -n "$ENV" -g "$RG" --yes --output none 2>/dev/null || true
        az containerapp env create -n "$ENV" -g "$RG" -l "$LOCATION" \
            --logs-workspace-id "$LAW_ID" --logs-workspace-key "$LAW_KEY" \
            --output none
    fi
    note "created $ENV"
fi

ENV_ID="/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.App/managedEnvironments/$ENV"

# --- the managed identity ---------------------------------------------------

say "Managed identity"

if az identity show -g "$RG" -n "$IDENTITY" >/dev/null 2>&1; then
    note "$IDENTITY already exists"
else
    az identity create -g "$RG" -n "$IDENTITY" --output none
    note "created $IDENTITY"
fi

UAI_ID="$(az identity show -g "$RG" -n "$IDENTITY" --query id -o tsv)"
UAI_CLIENT="$(az identity show -g "$RG" -n "$IDENTITY" --query clientId -o tsv)"
UAI_PRINCIPAL="$(az identity show -g "$RG" -n "$IDENTITY" --query principalId -o tsv)"
note "client id: $UAI_CLIENT"

# --- the application ---------------------------------------------------------

say "Container App"

DOMAIN="$(az containerapp env show -n "$ENV" -g "$RG" \
    --query "properties.defaultDomain" -o tsv)"
FQDN="$APP.$DOMAIN"
note "url: https://$FQDN"

REGISTRY_ARGS=()
if [ -n "$REGISTRY_SERVER" ]; then
    # The pull authenticates with the managed identity against the registry's
    # token service, so there is no --registry-username and no
    # --registry-password, and the deployment holds no registry credential.
    REGISTRY_ARGS=(--registry-server "$REGISTRY_SERVER" --registry-identity "$UAI_ID")
    note "pulling from $REGISTRY_SERVER with the managed identity"
fi

if az containerapp show -n "$APP" -g "$RG" >/dev/null 2>&1; then
    note "$APP already exists - recreating so the image and settings are current"
    az containerapp delete -n "$APP" -g "$RG" --yes --output none
fi

az containerapp create \
    -n "$APP" -g "$RG" --environment "$ENV" \
    --workload-profile-name Consumption \
    --image "$IMAGE" \
    ${REGISTRY_ARGS[@]+"${REGISTRY_ARGS[@]}"} \
    --target-port "$TARGET_PORT" --ingress external --transport auto \
    --min-replicas "$MIN_REPLICAS" --max-replicas "$MAX_REPLICAS" \
    --cpu "$CPU" --memory "$MEMORY" \
    --env-vars "FOUNDRY_PROJECT_ENDPOINT=$PROJECT_ENDPOINT" \
              "FOUNDRY_MODEL=$DEPLOYMENT" \
              "TOUCHLINE_PUBLIC_URL=https://$FQDN" \
              "AZURE_CLIENT_ID=$UAI_CLIENT" \
    --user-assigned "$UAI_ID" \
    --output none
note "created $APP"

# --- health probes -----------------------------------------------------------
#
# 'az containerapp create' has no probe flags. Probes live on
# properties.template.containers[0].probes[] and are patched in with a YAML
# fragment. Schema bounds: initialDelaySeconds 1-60, periodSeconds 1-240,
# timeoutSeconds 1-240, failureThreshold 1-10, successThreshold must be 1 for
# Liveness and Startup. One probe of each type per container.
#
# The fragment carries the **whole** container, not just the probes: a YAML
# patch replaces the container it names rather than merging into it, so a
# fragment with only the probes would take the environment variables and the
# resource limits with it.

say "Health probes"

CONTAINER_NAME="$(az containerapp show -n "$APP" -g "$RG" \
    --query "properties.template.containers[0].name" -o tsv)"

PROBE_FILE="$(mktemp)"
trap 'rm -f "$PROBE_FILE"' EXIT
cat > "$PROBE_FILE" <<EOF
properties:
  template:
    containers:
      - name: $CONTAINER_NAME
        image: $IMAGE
        env:
          - name: FOUNDRY_PROJECT_ENDPOINT
            value: $PROJECT_ENDPOINT
          - name: FOUNDRY_MODEL
            value: $DEPLOYMENT
          - name: TOUCHLINE_PUBLIC_URL
            value: https://$FQDN
          - name: AZURE_CLIENT_ID
            value: $UAI_CLIENT
        resources:
          cpu: $CPU
          memory: $MEMORY
        probes:
          - type: Liveness
            httpGet:
              path: /healthz
              port: $TARGET_PORT
            initialDelaySeconds: 10
            periodSeconds: 20
            timeoutSeconds: 5
            failureThreshold: 3
          - type: Readiness
            httpGet:
              path: /healthz
              port: $TARGET_PORT
            initialDelaySeconds: 3
            periodSeconds: 10
            timeoutSeconds: 5
            failureThreshold: 3
EOF

az containerapp update -n "$APP" -g "$RG" --yaml "$PROBE_FILE" --output none

PROBES="$(az containerapp show -n "$APP" -g "$RG" \
    --query "length(properties.template.containers[0].probes || [])" -o tsv)"
if [ "${PROBES:-0}" -lt 2 ]; then
    echo "WARNING: probes did not apply (found ${PROBES:-0})." >&2
    echo "Patch properties.template.containers[0].probes[] by hand and re-check." >&2
else
    note "$PROBES probes applied"
fi

# --- role assignments --------------------------------------------------------
#
# "Foundry User" (formerly "Azure AI User") is the role the Foundry
# documentation prescribes for a Foundry project. The Cognitive Services*
# roles are for AI Services resources accessed directly and do NOT apply here.

say "Role assignments"

assign() {
    local role="$1" scope="$2"
    if az role assignment list --scope "$scope" --assignee-object-id "$UAI_PRINCIPAL" \
            --query "[?contains(roleName, '$role')].id" -o tsv 2>/dev/null | grep -q .; then
        note "$role already assigned"
    else
        az role assignment create \
            --role "$role" \
            --assignee-object-id "$UAI_PRINCIPAL" --assignee-principal-type ServicePrincipal \
            --scope "$scope" \
            --output none
        note "assigned $role"
    fi
}

assign "Foundry User" \
    "/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.CognitiveServices/accounts/$ACCOUNT"

if [ -n "$REGISTRY_SERVER" ]; then
    assign "AcrPull" \
        "/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.ContainerRegistry/registries/$ACR"
fi

cat <<EOF
    If the app gets 401/403 from Foundry, work this order:
      1. Wait and retry. The managed-identity back end caches tokens per
         resource URI for up to ~24h, so a token minted before the role
         existed keeps its old permissions until it expires.
      2. Force a fresh token by restarting the revision:
           az containerapp revision restart -n "$APP" -g "$RG" --revision <name>
      3. Only then is the role wrong - try "Cognitive Services OpenAI User"
         on the same scope.
EOF

# --- wait for it to serve ----------------------------------------------------

say "Revision"

# The container starts and analyses the match in the background, so a running
# revision with a warm replica is what "the demo is live" means.
for _ in $(seq 1 40); do
    STATE="$(az containerapp revision list -n "$APP" -g "$RG" \
        --query "[?properties.active].properties.runningState" -o tsv 2>/dev/null | head -1)"
    if [ "${STATE:-}" = "Running" ]; then
        note "revision running"
        break
    fi
    sleep 10
done

# --- custom domain -----------------------------------------------------------
#
# demo.nurulgofran.dev is a subdomain, so the DNS records are a CNAME pointing
# directly at the app's generated FQDN, plus a TXT record at asuid.demo holding
# the app's customDomainVerificationId. A CNAME that points at an intermediary
# (a proxying CDN, say) blocks certificate issuance, as does a CAA record that
# does not allow digicert.com.

say "Custom domain"

if [ -n "$CUSTOM_HOST" ]; then
    VERIFY_ID="$(az containerapp show -n "$APP" -g "$RG" \
        --query "properties.customDomainVerificationId" -o tsv)"
    cat <<EOF
    Add these DNS records, then re-run this script to bind:
        CNAME  $CUSTOM_HOST  ->  $FQDN
        TXT    asuid.$CUSTOM_HOST  ->  $VERIFY_ID
EOF
    az containerapp hostname bind -n "$APP" -g "$RG" \
        --hostname "$CUSTOM_HOST" --environment "$ENV" --validation-method CNAME \
        --output none || true
    note "bind attempted - the managed certificate is issued asynchronously"
else
    note "CUSTOM_HOST not set - using the default FQDN"
fi

# --- report ------------------------------------------------------------------

say "Done"

cat <<EOF
    url            : https://$FQDN
    replicas       : min $MIN_REPLICAS / max $MAX_REPLICAS
    probe endpoint : https://$FQDN/healthz
    foundry model  : $DEPLOYMENT
    identity       : $IDENTITY (user-assigned, keyless)

    Watch the spend. The container costs about \$15 over the 16 days to the end
    of judging, but the retail price list also carries an "Environment Management
    Hour" meter at \$0.10/hour that may or may not apply to a Consumption
    environment - check the Azure Container Apps meter in Cost Management the
    day after this runs, and if it is charged, drop to --cpu 0.25 --memory 0.5Gi.
EOF
