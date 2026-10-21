# Testing instructions

> For a judge who has never seen the project. Everything below is on one public URL,
> needs no login, no signup and no payment, and is free of charge. Counters were
> last read 2026-10-21 and move as the demo is used.

## The URL

```
https://touchline-app.icywave-bfcbf183.germanywestcentral.azurecontainerapps.io
```

Open it in any browser. It is the Azure Container App itself, serving the studio.
There is no second host, no mirror and no staging copy.

If the page is blank at first, give it a second: the studio opens a WebSocket to the
same origin and the match is already analysed, so the feed fills in immediately.
(On a fresh deployment the engine analyses the full 90 minutes first — historically
about 17 minutes at the model's request rate — before the studio shows the match.)

## What you are looking at

One screen, three columns, all live at once:

| Column | Panel | What it does |
|---|---|---|
| **Left** | **Pitch** | The synthetic match from above. The two fictional clubs' colours, the event dots as they happen, and the player involved in the latest one. Below it, the **transport** controls. |
| **Middle** | **Read it as / In** | The audience and language switch. Below it, **Key moments** — the insights, as they are explained. |
| **Right** | **Momentum** | The control-versus-chaos curve over the 90 minutes, with a cursor on the clock. Below it, the **Vision tracker's** own score sheet. Below that, the **Agent trace**. |

The header carries the score, the match clock, and the **provider badge** on the right.

## The first thing to check: the provider badge

Top right. It reads:

```
PROVIDER  Microsoft Foundry  ● LIVE
```

That is not a label — it is the provider that served the most recent model call, read
back from the service. `live` means Foundry. If the service had to fall back to the
template engine, the badge would say **Template engine** with an amber **fallback**
chip, and that would be visible rather than hidden.

You can confirm the same thing from a terminal:

```bash
curl -s https://touchline-app.icywave-bfcbf183.germanywestcentral.azurecontainerapps.io/healthz
```

The response carries a `provider` block — `configured_provider` and
`last_call_provider` (`Microsoft Foundry` when the deployment is healthy),
`last_call_state`, a `call_count` that grows every time the demo is used, and a
`fallback_count` that counts how many times the template engine had to answer
instead of Foundry. At the 2026-10-21 audit the live figures were **132 calls and
0 fallbacks**; read them fresh on `/healthz` rather than trusting this document.
The `match` block carries `seed` (42), the computed match length, and the overlay
count — 44 insight cards plus one deterministic full-time recap — and `revision`
tells you the commit the running image was built from, so you can see which code
is answering you.

## Driving the match

Under the pitch:

- **Play / Pause** — starts and stops the clock.
- **Replay** — restarts from kick-off. The first load starts at the first key moment;
  press Replay to watch it from the beginning.
- **Next key moment** — jumps to the next insight.
- **The speed buttons (10× to 60×)** — match-seconds per second. At 60× the whole
  90 minutes plays in 90 seconds.
- **The scrubber** — click or drag to any point in the match.

The clock and the analysis are deliberately separate. The engine analyses the match
stretch by stretch as fast as the model allows and keeps the answers; the studio plays
those answers back on its own clock. So pausing, speeding up or jumping forward
changes the pacing and never the analysis — the analyst only ever looks backwards from
the moment it reports on. Jump to the end and the full-time recap is there.

## Reading it as somebody else

The **Read it as** row has five buttons — **Analyst**, **Fan**, **Player focus**, **My
team**, **Metric lens**:

- **My team** adds a side picker; pick Riverside United or Dovercrest Athletic and the
  same moments are rewritten around that side's numbers.
- **Player focus** adds a player picker; pick a player and the window is rewritten
  around their actions.
- **Metric lens** adds a metric picker; pick one metric and the feed is rewritten
  around it.

The **In** row switches the language: **English**, **Deutsch**, **বাংলা**. The
interface chrome stays English; the *explanation* is rewritten in the chosen language.
The audience selection is one shared state: the service acknowledges immediately,
rewrites the moments nearest your clock first, and replaces cards in place as each
one finishes. The button turns amber and the hint line says *rewriting the feed…*
while it works. In the 2026-10-21 checks the card on screen changed within about two
seconds; the rest of the feed follows over the next few minutes at the model
deployment's own rate. Two seconds is the observed measurement, not a guarantee —
whole-feed rewrites take minutes at the quota, and the hint clears when the last one
lands.

## Checking that the numbers are real

Every insight card shows its metrics in a table, and below them the ids of the events
the numbers were computed from — `ev-000144`, `ev-000145`. Those are real ids in the
event log, not decoration. Three ways to check:

1. **In the browser.** Hover or focus an event id chip and the event is ringed on the
   pitch. The card also says *computed from 2 events*.
2. **In the browser, deeper.** Press **Inspect evidence** on the card. It shows the
   calculated facts distinguished from the model interpretation, the cited event
   details and match times, the metric definition with the displayed and recomputed
   values, the grounding verdict, and the related hand-offs. The drawer makes no model
   calls and does not change audience state. (Missing evidence is labelled, never
   reconstructed.)
3. **From a terminal.** The same insight, with the metrics, the event ids and the
   confidence score:

```bash
curl -s "https://touchline-app.icywave-bfcbf183.germanywestcentral.azurecontainerapps.io/api/match/insights" | head -60
```

and the evidence behind one insight, by candidate id:

```bash
curl -s "https://touchline-app.icywave-bfcbf183.germanywestcentral.azurecontainerapps.io/api/evidence/cand-0003"
```

Unknown candidates return 404.

If a claim did not hold up, a grounding pass would have vetoed it and the trace would
say so. At the 2026-10-21 audit the match carried **340 hand-offs and 0 vetoes** —
every claim that was made survived being recomputed from the log.

## The agent trace

Bottom right. Every hand-off between the stages as it happens, newest first: which
stage talked to which, and why. Look for the **A2A** chip — that is the Narrator
asking the Explainer over A2A: a real JSON-RPC exchange, but through the deployed
app's own ASGI transport rather than a separate network socket (only the Explainer
is an A2A server; when the hop cannot be made the client asks in-process, and the
trace says so).

Click **show hand-offs** on any insight in the Key moments panel and the trace narrows
to that one moment's hand-offs. **show all** puts the whole trace back.

**Recovery example — recorded test run.** The trace panel offers a replay of a genuine
fixture: an isolated run through the actual workflow in which an invalid structured
claim was injected, rejected with its reason, routed back to the Explainer, and
corrected into an accepted overlay. It is visibly labelled as a recorded test, not a
current incident; it changes neither the live match nor shared audience state, and it
makes no model calls.

## For a technical judge

```bash
BASE=https://touchline-app.icywave-bfcbf183.germanywestcentral.azurecontainerapps.io

curl -s $BASE/healthz                       # provider state and the match summary
curl -s $BASE/.well-known/agent-card.json   # the A2A agent card
curl -s $BASE/api/match                     # teams, duration, insight count
curl -s $BASE/api/match/insights            # the insights, with metrics and citations
curl -s $BASE/api/evidence/cand-0003        # one insight's evidence, recomputed
curl -s $BASE/api/match/momentum            # the control-versus-chaos curve
curl -s $BASE/api/match/trace               # the hand-off trace
curl -s $BASE/api/match/tracking            # the vision tracker's score sheet
curl -s $BASE/api/match/audience            # the readers and the locales
```

The live feed is a WebSocket at the same origin: `wss://…/ws/match?seed=42`, sending
`hello`, `clock`, `events`, `momentum`, `overlay`, `trace`, `provider` and `end` frames.

The repository is public, and the four checks the project is held to — `pytest`,
`ruff`, `mypy`, the frontend build — run from the same commands locally. The full
Python suite needs the runtime *and* the development dependencies (`pytest` and `ruff`
ship in the `dev` extra; `mypy` is installed separately):

```bash
uv venv --python 3.12 && source .venv/bin/activate
uv pip install -r requirements.txt
uv pip install -e ".[dev]" mypy
pytest
ruff check .
mypy src/touchline --follow-imports=silent --ignore-missing-imports
npm --prefix web run build
```

The image is built by `scripts/build-image.sh` and the deployment by
`scripts/provision-aca.sh`, both committed and both idempotent, so the whole Azure
footprint is reproducible. The container app authenticates to Foundry with a managed
identity — there is no API key to configure, and nothing to sign up for.

## What the evaluation measured

The comparative evaluation (`evaluation/`) is honest about its limits: a bounded
engineering check, not statistically conclusive research. As of 2026-10-21 two runs
are complete — the template-local run (mechanics validation only; the template is
never presented as Microsoft output) and the live-API probe (**45/45 published
overlays pass citation validity and metric recomputation**). The Foundry comparison
was deferred under the quota exception (roughly 1,550 calls); until it exists, no superiority claim is
made. Method and results: `evaluation/methodology.md`, `evaluation/results/README.md`.
