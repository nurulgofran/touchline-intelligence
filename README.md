# Touchline Intelligence

> Multi-agent match intelligence engine for the [Inside the Game: Developer Hackathon](https://innovationstudio.microsoft.com/hackathons/insidethegamedeveloperhackathon) (Microsoft + Premier League).

Five specialised agents share one live match state and turn a synthetic football event stream into explainable intelligence, broadcast-ready overlays, and personalised fan experiences — the same match, told three different ways.

| Stage | Agent | Output |
|---|---|---|
| (a) Ingest | Simulator | Seeded synthetic event stream |
| (b) Interpret | Analyst | Momentum, pass difficulty, control-vs-chaos |
| (c) Explain | Explainer | "Why it matters", cited to event IDs |
| (d) Render | Narrator | Timed overlay JSON, commentary, recap |
| (e) Personalize | Personalizer | Analyst / Fan / Player-focus, EN · DE · BN |

**Stack:** Microsoft Foundry (Azure OpenAI) · Microsoft Agent Framework · A2A · Azure Container Apps · FastAPI + WebSocket · React + TypeScript

## Status

Work in progress — the full build plan lives in [`inside-the-game-hackathon-plan.md`](./inside-the-game-hackathon-plan.md), and the official brief in [`inside-the-game-hackathon-description.md`](./inside-the-game-hackathon-description.md).

> All data is **synthetic**. No real Premier League match data is used anywhere in this project.
