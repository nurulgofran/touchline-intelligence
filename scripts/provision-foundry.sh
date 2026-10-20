#!/usr/bin/env bash
#
# Provision the Microsoft Foundry account, project, and model deployment for
# Touchline Intelligence.
#
# Idempotent: re-running it updates nothing that already exists and reports
# what is in place. Safe to read top-to-bottom as the record of exactly what
# Azure resources this project creates.
#
# Prerequisites:
#   - Azure CLI 2.80.0 or newer  (az cognitiveservices account project ...)
#   - az login completed, with a subscription selected
#
# Usage:
#   scripts/provision-foundry.sh
#
# Overrides (all optional):
#   RG, ACCOUNT, PROJECT, LOCATION, DEPLOYMENT, MODEL, MODEL_VERSION, CAPACITY

set -euo pipefail

RG="${RG:-touchline-rg}"
ACCOUNT="${ACCOUNT:-touchlinefoundry}"
PROJECT="${PROJECT:-touchline}"
# The region is not a preference. The Azure for Students subscription carries an
# "Allowed resource deployment regions" policy, and only these five regions are
# permitted:
#   germanywestcentral, spaincentral, italynorth, polandcentral, swedencentral
# germanywestcentral is used because it carries both Microsoft Foundry
# (GlobalStandard deployments) and Azure Container Apps. A region policy
# refusal looks exactly like a blocked subscription, so this default exists to
# keep the two from being confused.
LOCATION="${LOCATION:-germanywestcentral}"
# The model. Three things narrow this down, and only the last one is obvious:
#   1. gpt-4o-mini 2024-07-18 (the original pin) is in the deprecating state and
#      Azure refuses new deployments of it ("ServiceModelDeprecating").
#   2. gpt-5.4-mini is GenerallyAvailable, but this subscription's quota for it
#      is 0 -- "InsufficientQuota ... the quota limit is 0".
#   3. So: the model has to be one this subscription actually has quota for.
#      `az cognitiveservices usage list -l <region>` is the authority. On this
#      subscription the only GenerallyAvailable chat models with a non-zero
#      GlobalStandard quota are gpt-5-mini (500k TPM), gpt4.1-mini (200k, but
#      Lifecycle=Legacy) and o4-mini (100k, a reasoning model and slower).
#      gpt-5-mini it is: current, small, fast, and cheap on a $100 credit.
DEPLOYMENT="${DEPLOYMENT:-gpt-5-mini}"
MODEL="${MODEL:-gpt-5-mini}"
MODEL_VERSION="${MODEL_VERSION:-2025-08-07}"
CAPACITY="${CAPACITY:-10}"

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
note() { printf '    %s\n' "$*"; }

# --- preflight ---------------------------------------------------------------

say "Preflight"

if ! command -v az >/dev/null 2>&1; then
    echo "Azure CLI is not installed. Install it with: brew install --cask azure-cli" >&2
    exit 1
fi

AZ_VERSION="$(az version --query '"azure-cli"' -o tsv 2>/dev/null || echo 0.0.0)"
if [ "$(printf '%s\n2.80.0\n' "$AZ_VERSION" | sort -V | head -n1)" != "2.80.0" ]; then
    echo "Azure CLI $AZ_VERSION is too old. The 'az cognitiveservices account project'" >&2
    echo "commands need 2.80.0 or newer. Run: brew upgrade azure-cli" >&2
    exit 1
fi
note "azure-cli $AZ_VERSION"

if ! az account show >/dev/null 2>&1; then
    echo "Not signed in. Run 'az login' first." >&2
    exit 1
fi

SUB="$(az account show --query id -o tsv)"
SUB_NAME="$(az account show --query name -o tsv)"
note "subscription: $SUB_NAME ($SUB)"
note "region: $LOCATION"

# --- resource group ----------------------------------------------------------

say "Resource group"

az group create -n "$RG" -l "$LOCATION" --output none
note "$RG"

# --- Foundry account ---------------------------------------------------------
#
# --kind AIServices           : a Foundry (AI Services) resource, not a plain
#                               Azure OpenAI one - this is what supports projects.
# --custom-domain             : required for token-based auth. A Foundry project
#                               endpoint does not accept an API key, so the custom
#                               subdomain is not cosmetic.
# --allow-project-management  : enables the project sub-resource. Cannot be
#                               changed after creation.
# --assign-identity           : mandatory - project creation fails without it.

say "Foundry account"

if az cognitiveservices account show -n "$ACCOUNT" -g "$RG" >/dev/null 2>&1; then
    note "$ACCOUNT already exists - leaving it alone"
else
    az cognitiveservices account create \
        -n "$ACCOUNT" -g "$RG" -l "$LOCATION" \
        --kind AIServices --sku S0 \
        --custom-domain "$PROJECT" \
        --allow-project-management true \
        --assign-identity --yes \
        --output none
    note "created $ACCOUNT"
fi

# --- Foundry project ---------------------------------------------------------

say "Foundry project"

if az cognitiveservices account project show -n "$ACCOUNT" --project-name "$PROJECT" -g "$RG" \
        >/dev/null 2>&1; then
    note "$PROJECT already exists - leaving it alone"
else
    az cognitiveservices account project create \
        -n "$ACCOUNT" --project-name "$PROJECT" \
        -g "$RG" -l "$LOCATION" \
        --output none
    note "created $PROJECT"
fi

# --- model deployment --------------------------------------------------------
#
# --sku-capacity is set explicitly. Left at its default of 1 the deployment is
# capped near 1,000 TPM whatever the subscription quota allows, and the live demo
# throttles itself under judge traffic - which reads as a slow model rather than
# a quota ceiling. Verified with 'az cognitiveservices usage list' below.

say "Model deployment"

if az cognitiveservices account deployment show -n "$ACCOUNT" -g "$RG" \
        --deployment-name "$DEPLOYMENT" >/dev/null 2>&1; then
    note "$DEPLOYMENT already exists - leaving it alone"
else
    az cognitiveservices account deployment create \
        -n "$ACCOUNT" -g "$RG" \
        --deployment-name "$DEPLOYMENT" \
        --model-name "$MODEL" --model-version "$MODEL_VERSION" \
        --model-format OpenAI --sku-name GlobalStandard \
        --sku-capacity "$CAPACITY" \
        --output none
    note "created $DEPLOYMENT (capacity $CAPACITY)"
fi

# --- report ------------------------------------------------------------------

say "Endpoints"

PROJECT_ENDPOINT="$(az cognitiveservices account project show \
    -n "$ACCOUNT" --project-name "$PROJECT" -g "$RG" \
    --query 'properties.endpoints."AI Foundry API"' -o tsv)"
RESOURCE_ENDPOINT="$(az cognitiveservices account show \
    -n "$ACCOUNT" -g "$RG" --query "properties.endpoint" -o tsv)"

note "project endpoint : $PROJECT_ENDPOINT"
note "resource endpoint: $RESOURCE_ENDPOINT"
echo
echo "    Add to ~/.touchline-secrets.env (mode 600, outside the repository):"
echo "        FOUNDRY_PROJECT_ENDPOINT=$PROJECT_ENDPOINT"
echo "        FOUNDRY_MODEL=$DEPLOYMENT"

say "Throughput ceiling"

# The quota is per region, so the region has to be named. A model with a zero
# quota here is one that cannot be deployed at all.
#
# The CLI renders this listing as a tree even under "-o tsv" -- the box
# characters in the output are the tree renderer, not separators -- so the
# report is built from the JSON with a JMESPath query instead. "join" is what
# turns a list of objects into the flat "name  limit" rows a table needs.
az cognitiveservices usage list -l "$LOCATION" \
    --query "reverse(sort_by([?limit > \`0\`], &limit))[].[join('  ', [name.value, to_string(limit)])]" \
    -o tsv 2>/dev/null | head -20 || true

note "the deployment above must appear in that list; a model with no quota row"
note "cannot be deployed at all"

# --- data-plane role ---------------------------------------------------------
#
# Owner on the subscription is a *management-plane* role. It lets you create
# and read resources, and it grants nothing on the Cognitive Services data
# plane, so inference answers 403 with it. The data-plane role for a Foundry
# project is "Foundry User" (formerly "Azure AI User"), and it has to be
# assigned explicitly on the account.
#
# This assignment is what lets the signed-in developer call the model from a
# laptop, with no key of any kind. The Container App's managed identity gets
# the same role in scripts/provision-aca.sh.

say "Data-plane role"

SCOPE="/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.CognitiveServices/accounts/$ACCOUNT"
DEVELOPER_OID="$(az ad signed-in-user show --query id -o tsv 2>/dev/null || true)"

if [ -z "$DEVELOPER_OID" ]; then
    note "no signed-in user found - skip the developer role assignment"
elif az role assignment list --scope "$SCOPE" --assignee-object-id "$DEVELOPER_OID" \
        --include-inherited \
        --query "[?contains(roleName, 'Foundry User')].id" -o tsv 2>/dev/null | grep -q .; then
    note "Foundry User already assigned to the signed-in user"
else
    az role assignment create \
        --role "Foundry User" \
        --assignee-object-id "$DEVELOPER_OID" --assignee-principal-type User \
        --scope "$SCOPE" \
        --output none
    note "assigned Foundry User to the signed-in user"
    note "RBAC can take a few minutes to propagate; a 403 straight after this"
    note "is the propagation delay, not a wrong role"
fi

say "Smoke test"

cat <<EOF
    cd "$(cd "$(dirname "$0")/.." && pwd)"
    uv venv --python 3.12 && source .venv/bin/activate
    uv pip install -r requirements.txt
    python -m touchline.providers.smoke

    The gate: if Foundry answers, proceed. If resource creation failed, check for
    an "Allowed resource deployment regions" policy on the subscription and retry
    once in an allowed region before concluding anything is blocked:

        az policy assignment list --scope "/subscriptions/$SUB" \\
            --query "[].{name:displayName, params:parameters}" -o json
EOF

say "Done"
