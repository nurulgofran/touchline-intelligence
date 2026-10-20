# Touchline Intelligence

> Multi-agent match intelligence engine for the [Inside the Game: Developer Hackathon](https://innovationstudio.microsoft.com/hackathons/insidethegamedeveloperhackathon) (Microsoft + Premier League).

Five specialised agents share one live match state and turn a synthetic football event stream into
explainable intelligence, broadcast-ready overlays, and personalised fan experiences — the same
match, told three different ways.

| Stage | Agent | Output |
|---|---|---|
| (a) Ingest | Simulator | Seeded synthetic event stream |
| (b) Interpret | Analyst | Momentum, pass difficulty, control-vs-chaos |
| (c) Explain | Explainer | "Why it matters", cited to event IDs |
| (d) Render | Narrator | Timed overlay JSON, commentary, recap |
| (e) Personalize | Personalizer | Analyst / Fan / My-Team / Player-focus, EN · DE · BN |

**An AI insights engine that runs on Azure.** One Microsoft Foundry model deployment, one
Microsoft Agent Framework workflow, one Azure Container App — no mirror, no second host.

## Hero technologies

| Technology | How this project uses it |
|---|---|
| **Microsoft Foundry** | The one model provider, used by the deployed demo and by local development. `FoundryChatClient` against a Foundry project endpoint, with the model deployment created and managed by the Azure CLI script in [`scripts/provision-foundry.sh`](./scripts/provision-foundry.sh). The provider badge shows the actual provider of the last call — "Microsoft Foundry" plus a live/fallback state chip. |
| **Microsoft Agent Framework** | The orchestration runtime. A `WorkflowBuilder` graph of typed executors — Ingest → Analyst → Explainer → Grounding → Narrator → Personalizer — over one shared state store, with per-executor events powering the agent-trace view and checkpointing for recovery. |
| **A2A (Agent2Agent)** | The Explainer and Narrator are exposed as A2A servers with published agent cards (`/.well-known/agent-card.json`); the Narrator calls the Explainer over A2A HTTP+JSON. |
| **Azure Container Apps** | The only host. The container image built in GitHub Actions and pulled anonymously from a public GHCR package, deployed with a system-assigned managed identity, `minReplicas: 1` so the demo URL is always on with no cold start, `maxReplicas: 1` so the in-process shared state stays consistent, liveness and readiness probes on `/healthz`, and an external ingress carrying the live WebSocket feed. Provisioned by [`scripts/provision-aca.sh`](./scripts/provision-aca.sh) with the Azure CLI. |
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
   (auto-eventing)     (event log + derived state)
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
python -m touchline.sim.generator --seed 42 --out data/match.jsonl   # one full synthetic match
pytest                                                        # metrics + grounding tests
```

Local development uses the same Microsoft Foundry project as the deployed demo, authenticated
**keylessly** through the Azure CLI login (`az login`) — there is no API key to configure. Before
those credentials exist, the app runs on the template provider and says so in the UI.

Configuration — `FOUNDRY_PROJECT_ENDPOINT` and `FOUNDRY_MODEL`, neither of which is a secret —
lives in `~/.touchline-secrets.env` (mode 600, outside the repo).

## Demo

Live URL: **the Azure Container App FQDN** — `<app>.<env-id>.<region>.azurecontainerapps.io`, or
`demo.nurulgofran.dev` once the custom domain is bound. See [`PLAN.md`](./PLAN.md) §7.

One Azure Container App, one warm replica (`minReplicas: 1`) — no cold start, no login, no signup,
no paywall, free of charge through the end of the judging period. The default
`*.azurecontainerapps.io` FQDN works immediately; the custom domain is additive.

## Azure provisioning

Both scripts are idempotent and committed, so the whole Azure footprint is reproducible from a
judge's or a reviewer's point of view:

```bash
az login
scripts/provision-foundry.sh     # Foundry account + project + model deployment
scripts/provision-aca.sh        # Container Apps environment + app + managed identity + role
```

Both run against **one subscription**, so the role assignment and the billing live in one place.
The app reaches Foundry through its managed identity — no key is stored in the deployment, in the
repository, or anywhere else.

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

Work in progress. The full build plan is in [`PLAN.md`](./PLAN.md), the official brief in
[`inside-the-game-hackathon-description.md`](./inside-the-game-hackathon-description.md), and the
original concept in [`inside-the-game-hackathon-plan.md`](./inside-the-game-hackathon-plan.md).

> All data is **synthetic**. No real Premier League match data is used anywhere in this project.
> Clubs and players are fictional, and no Premier League or Microsoft trademarks, logos, or crests
> appear in this repository, the UI, or the demo video.

## Licence

MIT — see [`LICENSE`](./LICENSE). Third-party dependencies are listed in
[`THIRD_PARTY_NOTICES.md`](./THIRD_PARTY_NOTICES.md).
