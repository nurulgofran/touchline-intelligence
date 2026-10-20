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
| **Microsoft Agent Framework** | The orchestration runtime. A `WorkflowBuilder` graph of typed executors — Ingest → Analyst → Explainer → Grounding → Narrator → Personalizer — over one shared state store, with per-executor events powering the agent-trace view and checkpointing for recovery. |
| **A2A (Agent2Agent)** | The Explainer and Narrator are exposed as A2A servers with published agent cards (`/.well-known/agent-card.json`); the Narrator calls the Explainer over A2A HTTP+JSON. |
| **Azure Container Apps** | The only host. The container image is published as a **public** GitHub Container Registry package and pulled **anonymously**, so no registry credential is stored in the deployment. Deployed with a system-assigned managed identity, `minReplicas: 1` so the demo URL is always on with no cold start, `maxReplicas: 1` so the in-process shared state stays consistent, liveness and readiness probes on `/healthz`, and an external ingress carrying the live WebSocket feed. Provisioned by [`scripts/provision-aca.sh`](./scripts/provision-aca.sh) with the Azure CLI. |
| **GitHub CLI / GitHub SDK** | Repo automation, CI wiring, release and submission checklists. |
| **GitHub Copilot** | Used to build the project (agent mode) — and named in the pitch per the category description. *Stretch: a Copilot-backed agent inside the workflow via `agent_framework.github.GitHubCopilotAgent`.* |
| **Azure Database for PostgreSQL** *(Day 6, bonus)* | Durable store behind the shared state — event log and agent handoff trace. Writes are best-effort; the live path never depends on it. |
| **Azure MCP** *(stretch, only if ≥2h spare)* | Optional MCP surface over the match store for agent tool use. |
| Fabric, other listed hero tech | Not used — stated honestly rather than name-dropped. |

## Architecture

```
sim/generator.py ──► metrics/analyst.py ──► agents/explainer.py ──► grounding.py ──► agents/narrator.py ──► agents/personalizer.py
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
  state), and a veto that re-routes a rejected draft back to the Explainer.
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

**Demo URL: `demo.nurulgofran.dev`** — a custom domain bound to the Azure Container App's
generated FQDN (`<app>.<env-id>.<region>.azurecontainerapps.io`, which works on its own and is the
fallback). The container app is provisioned by
[`scripts/provision-aca.sh`](./scripts/provision-aca.sh); until it is deployed, the studio runs
locally:

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
| **Trace** | Every hand-off between the stages, as it happens — including the A2A hop and the grounding veto. "Show hand-offs" on an insight narrows the trace to that one moment |
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
[`Dockerfile`](./Dockerfile) and pushes it to a public GitHub Container Registry package, which Azure
Container Apps pulls anonymously. The platform is pinned to `linux/amd64`, because that is what the
Container App's nodes are, whatever the build machine is.

The CI workflow in [`.github/workflows/image.yml`](./.github/workflows/image.yml) runs the same three
checks this project is held to — `pytest`, `ruff`, `mypy` — and then the frontend build, before its
image job publishes the same package. It is the path the project takes when the account's Actions
runner is available; until then the same commands are run locally and the image is built by the
script above.

The footprint itself:

| Resource | Name | Detail |
|---|---|---|
| Foundry account | `touchlinefoundry` | `AIServices`, `germanywestcentral` |
| Foundry project | `touchline` | Project endpoint `https://touchline.services.ai.azure.com/api/projects/touchline` |
| Model deployment | `gpt-5-mini` | `gpt-5-mini` / `2025-08-07`, `GlobalStandard`, capacity 10 |
| Container Apps env | `touchline-env` | `germanywestcentral`, with an explicit Log Analytics workspace |
| Container App | `touchline-app` | `minReplicas: 1`, `maxReplicas: 1`, system-assigned managed identity |

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
