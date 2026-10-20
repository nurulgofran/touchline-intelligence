# Touchline Intelligence

> Multi-agent match intelligence engine for the [Inside the Game: Developer Hackathon](https://innovationstudio.microsoft.com/hackathons/insidethegamedeveloperhackathon) (Microsoft + Premier League).

Five specialised agents share one live match state and turn a synthetic football event stream into
explainable intelligence, broadcast-ready overlays, and personalised fan experiences — the same
match, told five different ways.

| Stage | Agent | Output |
|---|---|---|
| (a) Ingest | Simulator | Seeded synthetic event stream |
| (b) Interpret | Analyst | Momentum, pass difficulty, control-vs-chaos |
| (c) Explain | Explainer | "Why it matters", cited to event IDs |
| (d) Render | Narrator | Timed overlay JSON, commentary, recap |
| (e) Personalize | Personalizer | Analyst / Fan / My-Team / Player-focus / Metric-lens, EN · DE · BN |

**An AI insights engine that runs on Azure.** One Microsoft Foundry model deployment, one
Microsoft Agent Framework workflow, one Azure Container App — no mirror, no second host.

## Hero technologies

| Technology | How this project uses it |
|---|---|
| **Microsoft Foundry** | The one model provider, used by the deployed demo and by local development. `FoundryChatClient` against a Foundry project endpoint, with the model deployment created and managed by the Azure CLI script in [`scripts/provision-foundry.sh`](./scripts/provision-foundry.sh). The provider badge shows the actual provider of the last call — "Microsoft Foundry" plus a live/fallback state chip. |
| **Microsoft Agent Framework** | The orchestration runtime. A `WorkflowBuilder` graph of typed executors — Ingest → Analyst → Explainer → Grounding → Narrator → Personalizer — over one shared state store, with per-executor events powering the agent-trace view and a second edge from Grounding back to the Explainer, so a vetoed claim is re-routed to the agent that wrote it. |
| **A2A (Agent2Agent)** | The Explainer and Narrator are exposed as A2A servers with published agent cards (`/.well-known/agent-card.json`); the Narrator calls the Explainer over A2A HTTP+JSON. |
| **Azure Container Apps** | The only host. The container image is pulled from an **Azure Container Registry** in the same subscription, with the app's **user-assigned managed identity** (`AcrPull` on the registry scope) — the registry's admin user is disabled and the deployment holds no registry credential at all. The Consumption environment refuses a system-assigned identity outright (`ExpressEnvironmentFeatureNotSupported`) and accepts a user-assigned one, for the app's access to Foundry and for the image pull alike. `minReplicas: 1` so the demo URL is always on with no cold start, `maxReplicas: 1` so the in-process shared state stays consistent, liveness and readiness probes on `/healthz`, and an external ingress carrying the live WebSocket feed. Provisioned by [`scripts/provision-aca.sh`](./scripts/provision-aca.sh) with the Azure CLI. |
| **Azure Container Registry** | The image registry, in the same subscription as the app. The Container App pulls from it with its managed identity (`AcrPull` on the registry scope), so the deployment holds no registry credential and the registry's own admin user is disabled. The image is built for `linux/amd64` and written in the OCI media types, because that is what the Container Apps runtime takes. |
| **GitHub CLI / GitHub SDK** | Repo automation, CI wiring, release and submission checklists. |
| **GitHub Copilot** | Used to build the project (agent mode) — and named in the pitch per the category description. *Stretch: a Copilot-backed agent inside the workflow via `agent_framework.github.GitHubCopilotAgent`.* |
| Azure Database for PostgreSQL | Not used — the shared state is in-process, and stating that honestly beats name-dropping. |
| Azure MCP | Not used — stated honestly rather than name-dropped. |
| Fabric, other listed hero tech | Not used — stated honestly rather than name-dropped. |

## Architecture

```
sim/generator.py ──► metrics/analyst.py ──► agents/explainer.py ──► orchestration/grounding.py ──► agents/narrator.py ──► agents/personalizer.py
        │                    │                     │  ▲                                            │
        │                    │                     │  └────── A2A (agent card + HTTP/JSON) ────────────┘
        ▼                    ▼                     ▼
   vision/tracker.py   MatchStateStore ◄──────── shared state, typed handoffs
   (OpenCV, scored)    (event log + derived state)
        │
        └──────────────► providers/  ──► foundry.py (Microsoft Foundry)  ──► template.py (no model)
```

- **Metrics are computed in deterministic code, never by an LLM.** The model only writes prose.
  Every narrative claim carries `cited_event_ids`, and a grounding pass recomputes each cited
  metric from the event log and vetoes any claim that does not hold up.
- **Failure recovery:** per-call timeouts and exponential backoff, a template provider that keeps
  the live feed running when inference is unavailable (and the badge flips to a visible `fallback`
  state), a veto that re-routes a rejected draft back to the Explainer, and a live analysis that
  skips a stretch it cannot explain rather than stopping there — the loss is recorded in the trace,
  and the rest of the match is still analysed.
- **Provider routing:** one provider interface, two implementations — **Microsoft Foundry** (the
  only model provider, used by the deployed demo *and* by local development) and a **template
  provider** that needs no model at all.

## Running locally

```bash
uv venv --python 3.12 && source .venv/bin/activate
uv pip install -r requirements.txt

# The Foundry configuration. Neither value is a secret, and the file lives
# outside the repo, so it is read rather than committed.
set -a && source ~/.touchline-secrets.env && set +a

python -m touchline.sim.generator --seed 42 --out data/match.jsonl   # one full synthetic match
pytest                                                        # metrics + grounding tests
```

Local development uses the same Microsoft Foundry project as the deployed demo, authenticated
**keylessly** through the Azure CLI login (`az login`) — there is no API key to configure. Before
those credentials exist, the app runs on the template provider and says so in the UI.

Configuration — `FOUNDRY_PROJECT_ENDPOINT` and `FOUNDRY_MODEL`, neither of which is a secret —
lives in `~/.touchline-secrets.env` (mode 600, outside the repo).

## Demo

The studio is the demo: a live match on a pitch, the momentum curve, the insights as they are
explained, and the agent trace showing every hand-off.

**Demo URL: `https://touchline-app.icywave-bfcbf183.germanywestcentral.azurecontainerapps.io`**

That is the published URL, and it is the only one. Azure Container Apps' **Consumption
(Express) environment does not support custom domains** — the control plane refuses the binding
outright with *"Custom Domains is not supported for container app … on express environments"*, and
its hostname-analysis endpoint answers `500` for every hostname, including hosts inside the
environment's own DNS suffix. That is a property of the environment tier, not of the DNS: no
CNAME or TXT record makes it work. A custom domain would need a Dedicated (workload-profiles)
environment, which is a different hosting tier and a different bill — so the app's own FQDN is
the published URL. The container app is provisioned by
[`scripts/provision-aca.sh`](./scripts/provision-aca.sh); the studio also runs locally:

```bash
uv venv --python 3.12 && source .venv/bin/activate
uv pip install -r requirements.txt
set -a && source ~/.touchline-secrets.env && set +a   # the Foundry deployment to talk to
cd web && npm install && npm run build && cd ..
uvicorn touchline.api.main:app --reload --port 8000   # http://localhost:8000
```

One Azure Container App, one warm replica (`minReplicas: 1`) — no cold start, no login, no signup,
no paywall, free of charge through the end of the judging period.

### The studio

| View | What it shows |
|---|---|
| **Pitch** | The synthetic match, top-down, with the two fictional clubs' colours |
| **Audience** | Who the match is being explained to — analyst, fan, player-focus, my-team or metric-lens, in English, German or Bengali. The same moments, rewritten; the numbers on each card still come from the same cited events |
| **Feed** | Each insight as it is explained, with the metric and the event ids it rests on |
| **Momentum** | The control-vs-chaos curve, in match time, with a cursor on the clock |
| **Tracker** | The vision tracker's own score sheet: precision, recall, position error and pass distance, measured against the ground truth the synthetic feed was rendered from |
| **Trace** | Every hand-off between the stages, as it happens — including the A2A hop and the grounding verdict, and a veto if a claim had to be sent back. "Show hand-offs" on an insight narrows the trace to that one moment |
| **Transport** | Play, pause, replay, speed, and "jump to the next key moment" |

The clock and the analysis are deliberately separate. The engine analyses the match stretch by
stretch as fast as the model allows and keeps the answers; the studio plays those answers back on
its own clock. So the demo is not a video of a script — pausing, speeding up or jumping forward
changes the pacing and never the analysis, because the analyst only ever looks backwards from the
moment it reports on.

## Azure provisioning

Both scripts are idempotent and committed, so the whole Azure footprint is reproducible from a
judge's or a reviewer's point of view:

```bash
az login
scripts/provision-foundry.sh     # Foundry account + project + model deployment
scripts/build-image.sh           # the image, built and pushed to the registry
scripts/provision-aca.sh         # Container Apps environment + app + managed identity + roles
```

All three run against **one subscription**, so the role assignments and the billing live in one
place. The app reaches Foundry through its managed identity — no key is stored in the deployment, in
the repository, or anywhere else.

### Build and CI

[`scripts/build-image.sh`](./scripts/build-image.sh) builds the image from the committed
[`Dockerfile`](./Dockerfile) and pushes it to the container registry the Container App pulls from. The
platform is pinned to `linux/amd64`, because that is what the Container App's nodes are, whatever the
build machine is, and the image is written in the OCI media types, because the Container Apps
runtime converts an image into the OCI layout on import and that conversion rejects the gzip layers
of the Docker format.

The CI workflow in [`.github/workflows/image.yml`](./.github/workflows/image.yml) runs the same three
checks this project is held to — `pytest`, `ruff`, `mypy` — and then the frontend build, before its
image job publishes the same image. It is the path the project takes when the account's Actions
runner is available; until then the same commands are run locally and the image is built by the
script above.

The footprint itself:

| Resource | Name | Detail |
|---|---|---|
| Foundry account | `touchlinefoundry` | `Microsoft.CognitiveServices/accounts`, `germanywestcentral` |
| Foundry project | `touchline` | Project endpoint `https://touchline.services.ai.azure.com/api/projects/touchline` |
| Model deployment | `gpt-5-mini` | `gpt-5-mini` / `2025-08-07`, `GlobalStandard`, capacity 10 |
| Managed identity | `touchline-identity` | User-assigned; "Foundry User" on the Foundry account, and the image pull |
| Container registry | `touchlineacr` | `Basic`, `germanywestcentral`, admin user disabled, pulled with the managed identity |
| Container Apps env | `touchline-env` | `germanywestcentral`, with an explicit Log Analytics workspace |
| Container App | `touchline-app` | `minReplicas: 1`, `maxReplicas: 1`, the managed identity attached |

One thing worth knowing if you read the scripts. The identity is **user-assigned**, not
system-assigned, and that is not a preference: the Consumption environment refuses a
system-assigned identity outright (`ExpressEnvironmentFeatureNotSupported`), and a user-assigned
one it accepts — for the app's access to Foundry and for the image pull alike. The identity's client
id reaches the app as `AZURE_CLIENT_ID`, and `DefaultAzureCredential()` already defaults its
managed-identity leg to that variable, so the code calls it the same way locally and in Azure.

Two things are worth knowing if you read the scripts. First, **the region is a policy, not a
preference** — the subscription carries an "Allowed resource deployment regions" assignment that
permits only five regions, and a refusal in a non-permitted region is indistinguishable from a
blocked subscription. Second, **inference needs a data-plane role**: `Owner` on the subscription is
a management-plane role and grants no access to the Cognitive Services data plane, so the scripts
assign **"Foundry User"** on the Foundry account scope — to the signed-in developer for local use,
and to the Container App's managed identity for the deployment.

## Status

Work in progress. The build plan and the hackathon brief are kept off the public repository; the
source, the scripts and this README are the whole of it.

One account-level caveat, stated plainly: the GitHub Actions runner for this account is
billing-locked, so the workflow above does not currently execute. The three checks and the frontend
build are run locally from the same commands, and the image is built by
[`scripts/build-image.sh`](./scripts/build-image.sh).

> All data is **synthetic**. No real Premier League match data is used anywhere in this project.
> Clubs and players are fictional, and no Premier League or Microsoft trademarks, logos, or crests
> appear in this repository, the UI, or the demo video.

## Licence

MIT — see [`LICENSE`](./LICENSE). Third-party dependencies are listed in
[`THIRD_PARTY_NOTICES.md`](./THIRD_PARTY_NOTICES.md).
