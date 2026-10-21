# Touchline Intelligence

> Multi-agent match intelligence engine for the [Inside the Game: Developer Hackathon](https://innovationstudio.microsoft.com/hackathons/insidethegamedeveloperhackathon) (Microsoft + Premier League).

Three model-driven agents — Explainer, Narrator, Personalizer — share one live match
state and turn a synthetic football event stream into explainable intelligence,
broadcast-ready overlays, and personalised fan experiences. They run inside a
five-stage pipeline (ingest → interpret → explain → render → personalize), with a
deterministic **grounding** step between explanation and rendering that recomputes
every cited metric from the event log and sends rejected claims back to the
Explainer. The same match, retold for five reader modes.

| Stage | What runs it | Output |
|---|---|---|
| (a) Ingest | Simulator (seeded, deterministic) | Synthetic event stream, ~1,051 events over 90 minutes |
| (b) Interpret | Analyst (deterministic metrics) | Momentum, pass difficulty, control-vs-chaos, scored key stretches |
| (c) Explain | **Explainer** (model-driven) | "Why it matters", cited to event IDs |
| — Grounding | Grounding (deterministic) | Recomputes cited metrics; vetoes and re-routes failures to the Explainer |
| (d) Render | **Narrator** (model-driven) | Timed overlay JSON, commentary, full-time recap |
| (e) Personalize | **Personalizer** (model-driven) | Analyst / Fan / My-Team / Player-focus / Metric-lens, EN · DE · BN |

**An AI insights engine that runs on Azure.** One Microsoft Foundry model deployment, one
Microsoft Agent Framework workflow, one Azure Container App — no mirror, no second host.
The seed-42 demo match produces **44 insight cards plus one deterministic full-time
recap (45 overlays)** — figures verified 2026-10-21.

## Hero technologies

| Technology | How this project uses it |
|---|---|
| **Microsoft Foundry** | The one model provider, used by the deployed demo and by local development. `FoundryChatClient` against a Foundry project endpoint, with the model deployment created and managed by the Azure CLI script in [`scripts/provision-foundry.sh`](./scripts/provision-foundry.sh). The provider badge shows the actual provider of the last call — "Microsoft Foundry" plus a live/fallback state chip. |
| **Microsoft Agent Framework** | The orchestration runtime. A `WorkflowBuilder` graph of typed executors — Ingest → Analyst → Explainer → Grounding → Narrator → Personalizer — over one shared state store, with per-executor events powering the agent-trace view and the grounding rejection edge routed back to the Explainer. |
| **A2A (Agent2Agent)** | Only the Explainer is an A2A server: an agent card at `/.well-known/agent-card.json` and a JSON-RPC endpoint at `/a2a`. The Narrator calls it as an A2A client, but the deployed default uses a genuine JSON-RPC exchange **through the app's own ASGI transport** (httpx ASGI transport into the same process) — not a network socket between services. The hop is visible in the agent trace, and when the hop cannot be made the client falls back to the Explainer in-process, recorded in the trace rather than hidden. |
| **Azure Container Apps** | The only host. The container image is pulled from an **Azure Container Registry** in the same subscription, with the app's **user-assigned managed identity** (`AcrPull` on the registry scope) — the registry's admin user is disabled and the deployment holds no registry credential at all. The Consumption environment refuses a system-assigned identity outright (`ExpressEnvironmentFeatureNotSupported`) and accepts a user-assigned one, for the app's access to Foundry and for the image pull alike. `minReplicas: 1` so the demo URL is always on with no cold start, `maxReplicas: 1` so the in-process shared state stays consistent, liveness and readiness probes on `/healthz`, and an external ingress carrying the live WebSocket feed. Provisioned by [`scripts/provision-aca.sh`](./scripts/provision-aca.sh) with the Azure CLI. |
| **Azure Container Registry** | The image registry, in the same subscription as the app. The Container App pulls from it with its managed identity (`AcrPull` on the registry scope), so the deployment holds no registry credential and the registry's own admin user is disabled. The image is built for `linux/amd64` and written in the OCI media types, because that is what the Container Apps runtime takes. |
| Azure Database for PostgreSQL | Not used — the shared state is in-process, and stating that honestly beats name-dropping. |
| Azure MCP | Not used — stated honestly rather than name-dropped. |
| Fabric, other listed hero tech | Not used — stated honestly rather than name-dropped. |

## Architecture

```
sim/generator.py ──► metrics/analyst.py ──► agents/explainer.py ──► orchestration/grounding.py ──► agents/narrator.py ──► agents/personalizer.py
        │                    │                     │  ▲                                            │
        │                    │                     │  └────── A2A (agent card + JSON-RPC, ASGI) ──────┘
        ▼                    ▼                     ▼
   vision/tracker.py   MatchStateStore ◄──────── shared state, typed handoffs
   (OpenCV, scored)    (event log + derived state)
        │
        └──────────────► providers/  ──► foundry.py (Microsoft Foundry)  ──► template.py (no model)
```

- **Metrics are computed in deterministic code, never by an LLM.** The model only writes prose.
  Every narrative claim carries `cited_event_ids`, and a grounding pass recomputes each cited
  metric from the event log and vetoes any claim that does not hold up. The grounding verifies
  numbers against the log — it is not a verification of the prose's meaning, and no claim in this
  repository says otherwise.
- **Failure recovery:** per-call timeouts and exponential backoff, a template provider that keeps
  the live feed running when inference is unavailable (and the badge flips to a visible `fallback`
  state), a veto that re-routes a rejected draft back to the Explainer, and a live analysis that
  skips a stretch it cannot explain rather than stopping there — the loss is recorded in the trace,
  and the rest of the match is still analysed. There is no checkpoint store; recovery is live
  re-routing, not resuming from snapshots.
- **The service keeps inside the deployment's request rate.** A shared pacer holds every call to
  the model — the analysis, the rewrites, everything — to eight requests a minute, because the
  deployment allows ten and a request over that comes back as a 429. A 429 that cannot be retried
  in time is a *fallback*: the template engine writes the card instead of the model and the badge
  says so. Waiting the model's turn is what keeps the badge green.
- **Provider routing:** one provider interface, two implementations — **Microsoft Foundry** (the
  only model provider, used by the deployed demo *and* by local development) and a **template
  provider** that needs no model at all. The template is the deterministic fallback and is never
  presented as Microsoft output.

## Evaluation — measured 2026-10-21

This is a small engineering evaluation, not statistically conclusive research. Honest labels
everywhere; full method in [`evaluation/methodology.md`](./evaluation/methodology.md).

| Run | What it proves | Result |
|---|---|---|
| **Template-local** (`evaluation/results/template_local.json`) | Mechanics only: fault injection, the veto-and-correct loop, validator behaviour. The deterministic template engine is behind both approaches and is **never presented as Foundry output**. | 12 scenarios × 2 approaches: 6 verified on the first try, 3 corrected after a veto, 3 published from the deterministic fallback on retry exhaustion, 0 published-output failures on the decisive checks (citation validity, metric recomputation). |
| **Live-API probe** (`evaluation/results/live_api_seed42.json`) | Real Foundry evidence for the *pipeline's published outputs only*: the deployed service's seed-42 overlays, validated against a locally simulated seed-42 event log. | **45/45 published overlays pass citation validity and metric recomputation.** One prose advisory (a derived figure the metric list does not carry) — a known heuristic limit, not a defect. |
| **Foundry comparison** | The definitive run: a real Foundry model behind *both* approaches (`evaluation/run_mac_foundry.sh`, needs `az login` + Foundry env, run on a machine with credentials). | **Pending.** Until it exists, no claim about model behaviour is made and no superiority claim for either approach is supported. |

The scenario manifest ([`evaluation/manifest.json`](./evaluation/manifest.json)) pins the 12
scenarios and the revision they were measured against; it must be re-pinned at feature freeze.

## Evidence and recovery replay

- **Inspect evidence.** Every insight card carries an *Inspect evidence* action. It shows the
  cited event details and match times, the metric definition with the displayed value and the
  recomputed value, earlier baseline evidence where applicable, and the grounding verdict with
  related hand-offs — calculated facts distinguished from model interpretation. The drawer makes
  no model calls and does not change audience state. Unknown candidates return 404; missing
  evidence is labelled, never reconstructed as if it were historical evidence.
- **Recovery example.** The trace panel offers *Recovery example — recorded test run*: a genuine
  trace/evidence fixture from an isolated run through the actual workflow, in which an invalid
  structured claim is injected, rejected with its reason, routed back to the Explainer, and
  corrected into an accepted overlay. Replay changes neither the live match nor shared audience
  state, makes no model calls, and is visibly identified as a recorded test, not a current
  incident.
- **Endpoints and fixture generation.** `GET /api/evidence/{candidate_id}` is the read-only
  evidence endpoint (unknown candidates return 404). A video-ready recovery fixture is generated
  with `python fixtures/recovery/generate_recovery_fixture.py --provider foundry --out web/public/fixtures/recovery-example.v1.json` — it needs Foundry credentials and a subsequent image rebuild; the fixture names the injected fault, provider, revision, and test context. The fixture was generated with Microsoft Foundry on 2026-10-21 and records a genuine veto, correction, and acceptance with no template fallback.

## Running locally

```bash
uv venv --python 3.12 && source .venv/bin/activate
uv pip install -r requirements.txt   # runtime dependencies
uv pip install -e ".[dev]" mypy      # development dependencies for the full suite

# The Foundry configuration. Neither value is a secret, and the file lives
# outside the repo, so it is read rather than committed.
set -a && source ~/.touchline-secrets.env && set +a

python -m touchline.sim.generator --seed 42 --out data/match.jsonl   # one full synthetic match
pytest                                                        # the test suite
ruff check .                                                  # lint
mypy src/touchline --follow-imports=silent --ignore-missing-imports  # types
```

The minimal simulator/template package depends on pydantic; the full test suite needs the
runtime *and* the development dependencies (pytest and ruff ship in the `dev` extra, mypy
is installed separately). Tests use the template/fake clients unless explicitly exercising
Foundry.

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

**Startup analysis time.** When the container starts, the engine analyses the full 90-minute
match before the studio shows it — historically about 17 minutes at the model's request rate
(the shared pacer holds all model traffic to 8 requests/minute against a 10-request quota).
The studio is usable while analysis proceeds; the demo URL is recorded only after the analysis
is complete.

### The studio

All six panels are on screen at once — there is no tab to click.

The studio is drawn as a broadcast graphic rather than a dashboard: one accent (the pitch
green) against a neutral ramp, a condensed display face for the score, the headlines and the
clock, a grotesque for the copy, and tabular figures everywhere a number appears. The two
typefaces are self-hosted under `web/public/fonts/`, so the studio looks the same on every
machine and loads no font from a third party.

| Panel | What it shows |
|---|---|
| **Pitch** | The synthetic match, top-down, with the two fictional clubs' colours |
| **Read it as** | Who the match is being explained to — analyst, fan, player-focus, my-team or metric-lens. The same moments, rewritten; the numbers on each card still come from the same cited events |
| **Key moments** | Each insight as it is explained, with the metric and the event ids it rests on |
| **Momentum** | The control-vs-chaos curve, in match time, with a cursor on the clock |
| **Vision tracker** | The tracker's own score sheet: precision, recall, position error, pass error and shot speed, measured against the ground truth the synthetic feed was rendered from |
| **Agent trace** | Every hand-off between the stages, as it happens — including the A2A hop and the grounding verdict, and a veto if a claim had to be sent back. "Show hand-offs" on an insight narrows the trace to that one moment |
| **Transport** | Play, pause, replay, speed, and "jump to the next key moment" |

**Shared reader state, progressive rewrites.** The audience selection is one
shared state: changing it starts a rewrite of the whole feed at the model's rate. The service
acknowledges immediately, rewrites the moments nearest the viewer's clock first, and replaces
cards in place as each finishes — in the 2026-10-21 checks the visible card was rewritten
within about two seconds, while the rest of the feed followed over the next few minutes. Two
seconds is the observed measurement, not a guarantee: whole-feed rewrites take minutes at the
model quota, and that is by design rather than by failure.

The clock and the analysis are deliberately separate. The engine analyses the match stretch by
stretch as fast as the model allows and keeps the answers; the studio plays those answers back on
its own clock. So the demo is not a video of a script — pausing, speeding up or jumping forward
changes the pacing and never the analysis, because the analyst only ever looks backwards from the
moment it reports on.

## Azure provisioning

The three scripts are committed and re-runnable, so the whole Azure footprint is reproducible from a
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

The demo is deployed and running (audit 2026-10-21: seed 42, 90 minutes computed, 44 insight
cards plus one deterministic recap; 132 model calls served by Microsoft Foundry with 0 recorded
fallbacks; 340 handoffs, 0 vetoes). The build plan and the hackathon brief are kept off the
public repository; the source, the scripts, this README and [`submission/`](./submission/) are
the whole of it. [`submission/`](./submission/) holds the pitch, the testing instructions for a
judge, the submission checklists with what was verified and when, and the demo-video plan.

Open items, stated plainly (2026-10-21): the Foundry comparative evaluation was deferred under the
quota exception (roughly 1,550 calls at eight requests per minute); the demo video has
not yet been recorded or published; the project is not yet linked to challenge 5078 and the
submission has not been made — those need the owner's account. See
[`submission/CHECKLIST.md`](./submission/CHECKLIST.md) for the full evidence-backed list.

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
