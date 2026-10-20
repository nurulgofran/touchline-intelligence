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

## Hero technologies

| Technology | How this project uses it |
|---|---|
| **Microsoft Foundry** | Deployed inference. `FoundryChatClient` against a Foundry project endpoint, with the model deployment managed in Foundry. The active provider is shown live in the UI. |
| **Microsoft Agent Framework** | The orchestration runtime: a `WorkflowBuilder` graph of typed executors — Ingest → Analyst → Explainer → Grounding → Narrator → Personalizer — over one shared state store, with per-executor events powering the agent-trace view and checkpointing for recovery. |
| **A2A (Agent2Agent)** | The Explainer and Narrator are exposed as A2A servers with published agent cards (`/.well-known/agent-card.json`); the Narrator calls the Explainer over A2A HTTP+JSON. |
| **GitHub Copilot** | Used to build the project. |
| **GitHub CLI / GitHub SDK** | Repo automation, CI wiring, release and submission checklists. |
| **Azure Container Apps** | The Azure-hosted deployment of the same container image, event-driven, with managed identity. |
| **Azure Database for PostgreSQL** | Durable store behind the shared state — event log and agent handoff trace. Writes are best-effort; the live path never depends on it. |

## Architecture

```
sim/generator.py ──► metrics/analyst.py ──► agents/explainer.py ──► grounding.py ──► agents/narrator.py ──► agents/personalizer.py
        │                    │                     │  ▲                                            │
        │                    │                     │  └────── A2A (agent card + HTTP/JSON) ────────────┘
        ▼                    ▼                     ▼
   vision/tracker.py   MatchStateStore ◄──────── shared state, typed handoffs
   (auto-eventing)     (event log + derived state)
```

- **Metrics are computed in deterministic code, never by an LLM.** The model only writes prose.
  Every narrative claim carries `cited_event_ids`, and a grounding pass recomputes each cited
  metric from the event log and vetoes any claim that does not hold up.
- **Failure recovery:** per-call timeouts and exponential backoff, a template provider that keeps
  the live feed running when inference is unavailable, and a veto that re-routes a rejected draft
  back to the Explainer.
- **Provider routing:** the deployed configuration runs on Microsoft Foundry. For local development
  and as a resilience fallback, the same interface also targets any OpenAI-compatible endpoint.

## Running locally

```bash
python -m touchline.simulate --seed 42 --out data/match.jsonl   # one full synthetic match
pytest                                                        # metrics + grounding tests
```

Local development uses an OpenAI-compatible endpoint via `~/.touchline-secrets.env`; the deployed
configuration uses Microsoft Foundry.

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
