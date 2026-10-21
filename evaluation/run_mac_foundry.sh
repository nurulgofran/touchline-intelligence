#!/usr/bin/env bash
# The definitive Foundry run of the comparative evaluation.
#
# Run this ON THE MAC, where `az login` has been done and the Foundry
# project is reachable. It re-runs the same 12 fixed scenarios in
# evaluation/manifest.json, but with a real Microsoft Foundry model behind
# BOTH approaches (the multi-agent pipeline and the single-agent baseline),
# instead of the deterministic template engine.
#
# Prerequisites on the Mac:
#   1. The repo checked out at the same revision recorded in
#      evaluation/results/template_local.json ("project_revision"), or the
#      current branch head -- the manifest is revision-pinned either way.
#   2. `az login` completed (DefaultAzureCredential resolves it; no API key).
#   3. FOUNDRY_PROJECT_ENDPOINT and FOUNDRY_MODEL set, e.g.:
#        export FOUNDRY_PROJECT_ENDPOINT="https://<project>.openai.azure.com/..."
#        export FOUNDRY_MODEL="<deployment-name>"
#   4. The project dependencies installed (agent-framework-core,
#      agent-framework-foundry, azure-identity, a2a-sdk, sse-starlette,
#      pydantic, numpy):
#        python3 -m venv ~/.venvs/touchline-eval
#        ~/.venvs/touchline-eval/bin/pip install \
#          agent-framework-core agent-framework-foundry azure-identity \
#          "a2a-sdk>=1.0.0" "sse-starlette>=2.1" pydantic numpy
#
# Cost note: both paths process one selected candidate per scenario. Expect
# roughly 50-60 model calls for the 12 scenarios, including controlled retries,
# paced sequentially at eight requests per minute. The runner checkpoints each
# completed pair and resumes only compatible checkpoints; keep this workload
# separate from video recording and production startup analysis.
set -euo pipefail

cd "$(dirname "$0")/.."

: "${FOUNDRY_PROJECT_ENDPOINT:?set FOUNDRY_PROJECT_ENDPOINT first}"
: "${FOUNDRY_MODEL:?set FOUNDRY_MODEL first}"

VENV="${VENV:-$HOME/.venvs/touchline-eval}"

"$VENV/bin/python" -m evaluation.runner \
  --provider foundry \
  --out evaluation/results/foundry.json

echo
echo "Done. Compare with the template baseline:"
echo "  $VENV/bin/python - <<'EOF'"
echo "  import json"
echo "  t = json.load(open('evaluation/results/template_local.json'))['summary']"
echo "  f = json.load(open('evaluation/results/foundry.json'))['summary']"
echo "  print(json.dumps({'template': t, 'foundry': f}, indent=2))"
echo "  EOF"
