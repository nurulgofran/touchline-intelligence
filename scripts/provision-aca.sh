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
#   - The container image published as a PUBLIC package on GHCR, so the pull
#     below is anonymous and no registry credential is needed
#
# Usage:
#   scripts/provision-aca.sh
#
# Overrides (all optional):
#   RG, ENV, APP, LOCATION, IMAGE, ACCOUNT, PROJECT, DEPLOYMENT, HOSTNAME
#   Set HOSTNAME to bind a custom domain; leave it unset to use the default
#   *.azurecontainerapps.io FQDN.

set -euo pipefail

RG="${RG:-touchline-rg}"
ENV="${ENV:-touchline-env}"
APP="${APP:-touchline-app}"
LOCATION="${LOCATION:-germanywestcentral}"
IMAGE="${IMAGE:-ghcr.io/nurulgofran/touchline-intelligence:v1}"
ACCOUNT="${ACCOUNT:-touchlinefoundry}"
PROJECT="${PROJECT:-touchline}"
DEPLOYMENT="${DEPLOYMENT:-gpt-5-mini}"
HOSTNAME="${HOSTNAME:-}"

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

# The role assignment below scopes to the Foundry account's resource group, and
# a managed identity can only be granted a role on a resource in its own
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
    az containerapp env create -n "$ENV" -g "$RG" -l "$LOCATION" \
        --logs-workspace-id "$LAW_ID" --logs-workspace-key "$LAW_KEY" \
        --output none
    note "created $ENV"
fi

# --- the application ---------------------------------------------------------
#
# No --registry-server / --registry-username / --registry-password: the GHCR
# package is public, so the image is pulled anonymously and no registry
# credential exists anywhere in the chain.

say "Container App"

if az containerapp show -n "$APP" -g "$RG" >/dev/null 2>&1; then
    note "$APP already exists - recreating so the image and settings are current"
    az containerapp delete -n "$APP" -g "$RG" --yes --output none
fi

az containerapp create \
    -n "$APP" -g "$RG" --environment "$ENV" \
    --image "$IMAGE" \
    --target-port "$TARGET_PORT" --ingress external --transport auto \
    --min-replicas "$MIN_REPLICAS" --max-replicas "$MAX_REPLICAS" \
    --cpu "$CPU" --memory "$MEMORY" \
    --env-vars "FOUNDRY_PROJECT_ENDPOINT=$PROJECT_ENDPOINT" "FOUNDRY_MODEL=$DEPLOYMENT" \
    --system-assigned \
    --output none
note "created $APP"

FQDN="$(az containerapp show -n "$APP" -g "$RG" --query "properties.configuration.ingress.fqdn" -o tsv)"
note "url: https://$FQDN"

# The agent card publishes the address the service is reachable at, and the
# A2A client dials it. Neither can be guessed before the app exists, so the
# public URL is set once the FQDN is known and the revision is restarted.
# Without it the card would publish http://localhost:8000, which is right in
# a dev shell and wrong on the demo URL.
az containerapp update -n "$APP" -g "$RG" \
    --set-env-vars "TOUCHLINE_PUBLIC_URL=https://$FQDN" \
    --output none
note "public url set to https://$FQDN"

# --- health probes -----------------------------------------------------------
#
# 'az containerapp create' has no probe flags. Probes live on
# properties.template.containers[0].probes[] and are patched in with a YAML
# fragment. Schema bounds: initialDelaySeconds 1-60, periodSeconds 1-240,
# timeoutSeconds 1-240, failureThreshold 1-10, successThreshold must be 1 for
# Liveness and Startup. One probe of each type per container.

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

# --- managed identity -> Foundry --------------------------------------------
#
# "Foundry User" (formerly "Azure AI User") is the role the Foundry
# documentation prescribes for a Foundry project. The Cognitive Services*
# roles are for AI Services resources accessed directly and do not apply here.

say "Managed identity and role assignment"

PRINCIPAL="$(az containerapp show -n "$APP" -g "$RG" --query identity.principalId -o tsv)"
note "principal: $PRINCIPAL"

SCOPE="/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.CognitiveServices/accounts/$ACCOUNT"

if az role assignment list --scope "$SCOPE" --assignee-object-id "$PRINCIPAL" \
        --query "[?contains(roleName, 'Foundry User')].id" -o tsv 2>/dev/null | grep -q .; then
    note "Foundry User already assigned"
else
    az role assignment create \
        --role "Foundry User" \
        --assignee-object-id "$PRINCIPAL" --assignee-principal-type ServicePrincipal \
        --scope "$SCOPE" \
        --output none
    note "assigned Foundry User"
fi

cat <<EOF
    If the app gets 401/403 from Foundry, work this order:
      1. Wait and retry. The managed-identity back end caches tokens per resource
         URI for up to ~24h, so a token minted before the role existed keeps its
         old permissions until it expires.
      2. Force a fresh token by restarting the revision or redeploying.
      3. Only then is the role wrong - try "Cognitive Services OpenAI User" on
         the same scope:
           az role assignment create --role "Cognitive Services OpenAI User" \\
             --assignee-object-id "$PRINCIPAL" \\
             --assignee-principal-type ServicePrincipal --scope "$SCOPE"
EOF

# --- custom domain -----------------------------------------------------------
#
# demo.nurulgofran.dev is a subdomain, so the DNS records are a CNAME pointing
# directly at the app's generated FQDN, plus a TXT record at asuid.demo holding
# the app's customDomainVerificationId. A CNAME that points at an intermediary
# (a proxying CDN, say) blocks certificate issuance, as does a CAA record that
# does not allow digicert.com.

say "Custom domain"

if [ -n "$HOSTNAME" ]; then
    VERIFY_ID="$(az containerapp show -n "$APP" -g "$RG" \
        --query "properties.customDomainVerificationId" -o tsv)"
    cat <<EOF
    Add these DNS records, then re-run this script to bind:
        CNAME  $HOSTNAME  ->  $FQDN
        TXT    asuid.$HOSTNAME  ->  $VERIFY_ID
EOF
    az containerapp hostname bind -n "$APP" -g "$RG" \
        --hostname "$HOSTNAME" --environment "$ENV" --validation-method CNAME \
        --output none || true
    note "bind attempted - the managed certificate is issued asynchronously"
else
    note "HOSTNAME not set - using the default FQDN"
fi

# --- report ------------------------------------------------------------------

say "Done"

cat <<EOF
    url            : https://$FQDN
    replicas       : min $MIN_REPLICAS / max $MAX_REPLICAS
    probe endpoint : https://$FQDN/healthz
    foundry model  : $DEPLOYMENT

    Watch the spend. The container costs about \$15 over the 16 days to the end
    of judging, but the retail price list also carries an "Environment Management
    Hour" meter at \$0.10/hour that may or may not apply to a Consumption
    environment - check the Azure Container Apps meter in Cost Management the
    day after this runs, and if it is charged, drop to --cpu 0.25 --memory 0.5Gi.
EOF
