# Touchline Intelligence — project pitch

> Paste this into the project page. It is written in English, it names only
> technologies that are actually running, and every number in it is dated to the
> audit it was read from (2026-10-21).

## One line

Three model-driven agents share one live match state and turn a synthetic football
event stream into explainable intelligence, broadcast-ready overlays, and
personalised fan experiences — the same match, retold for five reader modes.

## The problem

A football scoreline tells you nothing. A full event log tells you everything and
explains nothing: 1,051 events over 90 minutes, each one a row of a table, each row
needing a human to work out what it meant. Broadcasters, analysts and fans each want
the same events explained differently, and today that explanation is a human being in
a room.

Touchline Intelligence is that explanation, as a running system. It takes a match as it
happens, works out what mattered, says why, cites the events it rests on, renders it
as a broadcast overlay, and rewrites it for whoever is reading.

## What it does

- **Reads the match.** A seeded simulator produces a synthetic event stream; a vision
  tracker turns rendered frames into tracked players and the ball, and is scored
  against the ground truth the feed was rendered from.
- **Finds the moments that mattered.** A deterministic analyst computes momentum,
  pass difficulty and control-versus-chaos, and scores each stretch.
- **Explains them, with receipts.** The Explainer writes what happened and why. Every
  claim carries the ids of the events it rests on, and a grounding pass recomputes
  every cited metric from the event log and vetoes any claim that does not hold up.
- **Renders it.** The Narrator produces timed broadcast overlays, commentary and a
  full-time recap. It asks the Explainer for each reading over A2A — a genuine
  JSON-RPC exchange through the deployed app's own ASGI transport, visible in the
  agent trace.
- **Personalises it.** The Personalizer rewrites the same moments for an analyst, a
  fan, one player, one side, or one metric — in English, German or Bengali.

**The numbers are never invented by the model.** Metric values are computed in
deterministic code; the model only writes prose around them, and the grounding pass
recomputes every cited metric from the event log and vetoes any claim that does not
hold up. (The grounding verifies numbers against the log; it is not a verification
of what the prose means.)

## Which Microsoft and Azure technologies are used

| Technology | How this project uses it |
|---|---|
| **Microsoft Foundry** | The one model provider, in the deployed demo and in local development. `FoundryChatClient` against a Foundry project endpoint, one deployment (`gpt-5-mini`, GlobalStandard, capacity 10). The provider badge in the UI shows the provider that actually served the last call, with a live/fallback state chip. |
| **Microsoft Agent Framework** | The orchestration runtime. A `WorkflowBuilder` graph of typed executors — Ingest → Analyst → Explainer → Grounding → Narrator → Personalizer — over one shared state store, with per-executor events powering the agent-trace view and the grounding rejection edge routed back to the Explainer that wrote the claim. |
| **A2A (Agent2Agent)** | Only the Explainer is an A2A server, with a published agent card at `/.well-known/agent-card.json`. The Narrator calls it as an A2A client over JSON-RPC, transported through the app's own ASGI stack rather than a separate network socket — and that hop is visible in the agent trace. |
| **Azure Container Apps** | The only host. One Container App on a Consumption (Express) environment, `minReplicas: 1` so the demo URL never scales to zero, `maxReplicas: 1` so the in-process shared state stays consistent, liveness and readiness probes on `/healthz`, and an external ingress carrying the live WebSocket feed. |
| **Azure Container Registry** | The image registry, in the same subscription as the app. |
| **Azure AI Foundry projects** | The Foundry account and project hold the model deployment the app talks to. |
| **Azure Key Vault** | Not used. The deployment holds no key at all: the app authenticates to Foundry and pulls its image with a managed identity. |
| **Managed identity** | A user-assigned identity (`touchline-identity`) carries both the Foundry access and the registry pull, so the Container App stores no registry credential and no model key — `configuration.secrets` is null. |

Foundry is the only model provider in the project. The deterministic template engine is
the fallback that keeps the feed alive when inference is unavailable; it is never
presented as Microsoft output. There is no second host, no mirror, and no fallback
deployment.

## What is running right now

The demo URL below is a live deployment, not a recording (figures from the 2026-10-21
audit; live counters on `/healthz` move as the demo is used):

- **Every model call served by Foundry.** At the audit the deployment had served **132
  calls with 0 recorded fallbacks** — the badge reads *Microsoft Foundry · live*, and
  the counter behind it is dated, not "never".
- **44 insight cards plus one deterministic full-time recap** from 1,051 synthetic
  events over 90 minutes.
- **340 typed hand-offs** between the stages, every one visible in the agent trace, and
  **0 vetoes** in the normal live match — every claim that was made survived being
  recomputed from the log.
- **The vision tracker, scored against ground truth** (read 2026-10-20): 100%
  precision, 85.4% recall, 0.07 m mean position error, 4 of 4 shots tracked.
- **Five reader modes, three languages**, all rewriting the same events.

## Links

| | |
|---|---|
| **Live demo** | `https://touchline-app.icywave-bfcbf183.germanywestcentral.azurecontainerapps.io` |
| **Demo video** | *(add the public URL when it is uploaded)* |
| **Repository** | `https://github.com/nurulgofran/touchline-intelligence` |
| **Challenge** | Inside the Game Developer Hackathon — challenge 5078, *The Synthetic Match Insights Engine for Premier League Studio* |

The demo needs no login, no signup and no payment, and it is free of charge for the
whole judging period. It is the Azure Container App's own FQDN: the Consumption
environment does not support custom domains — the control plane refuses the binding
outright — so the app's FQDN is the published URL.

## A note on the data

All data is synthetic. The clubs (Riverside United, Dovercrest Athletic) and every
player are fictional, no real match data is used, and no Premier League, Microsoft or
Azure trademark, logo or wordmark appears in the repository, the interface or the
video. The interface chrome is English, and any non-English text in the video carries
English alongside it.
