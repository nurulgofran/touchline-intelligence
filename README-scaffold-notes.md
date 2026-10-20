# Scaffold notes

Short operating notes for the core Python package: the seeded simulator, the
shared state store and the template narrative provider.

## Layout

```
src/touchline/
  models/      events.py · insights.py · overlay.py     (Pydantic v2 contracts)
  sim/         teams.py · generator.py                  (fictional squads, seeded match)
  state/       store.py                                 (MatchStateStore, grounding primitive)
  providers/   base.py · template.py                    (NarrativeProvider, no-LLM renderer)
schemas/       overlay.schema.json                     (JSON Schema, draft 2020-12)
```

Heavy and optional dependencies (`agent_framework`, `azure.identity`, `numpy`
heavy paths, `cv2`) are imported **inside functions** or behind
`TYPE_CHECKING`, so the simulator, the state store, the template provider and
the whole test suite run with `pydantic` alone.

## Set up

```bash
uv venv --python 3.12
source .venv/bin/activate
```

For the full runtime (Foundry client, FastAPI, Azure SDKs, OpenCV):

```bash
uv pip install -r requirements.txt
```

`requirements.txt` installs the real dependency set. Note that
`agent-framework-openai` and the `openai` SDK arrive transitively through
`agent-framework-foundry` -- the Foundry chat client is built on the OpenAI
transport -- so `openai` stays in the lockfile even though no
OpenAI-compatible *endpoint* is used. The `agent-framework` meta-package is
deliberately avoided because it pulls in every provider.

For development, only the light dependencies are needed:

```bash
uv pip install "pydantic>=2.9" "numpy>=1.26" pytest ruff
uv pip install -e .
```

## Run the simulator

```bash
python -m touchline.sim.generator --seed 42 --out data/match.jsonl
head -3 data/match.jsonl
wc -l data/match.jsonl
```

The output is JSON Lines, one event per line, ~1,000 events for a 90-minute
match. The same seed always produces the same file, byte for byte. `data/` is
gitignored: the log is regenerable, never a committed artefact.

## Run the tests

```bash
pytest -q
ruff check src tests
```

The suite covers the simulator (length, unique ids, monotonic clock, score
consistency, determinism, fictional clubs), the state store (append, window,
score, and the `recompute` grounding primitive) and the template provider
(determinism, citation carry-over, and the JSON Schema / model drift check).

## Notes for the next layer

- `MatchEvent.x` / `y` are **percentages of the pitch** (105 m x 68 m), seen
  from the perspective of the side the event belongs to: `x` 0 is own goal,
  100 is opponent goal, `y` 0 is one touchline. Distances in metres are
  derived from the constants in `models/events.py`, never hard-coded.
- The score is registered on the `GOAL` event, so the score of any event
  equals the number of `GOAL` events at or before it.
- `MatchStateStore.recompute(metric, cited_event_ids)` is the grounding
  primitive: it re-derives a metric from **only** the cited events and returns
  `None` if any cited id is unknown to the log.
- `MatchStateStore` is in-process and single-instance, which is why the
  deployment runs exactly one replica.
