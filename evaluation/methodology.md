# Comparative evaluation: multi-agent pipeline vs single agent

A small engineering evaluation for the Touchline Intelligence hackathon
entry ("Inside the Game"). It compares two ways of turning a deterministic
match insight into a published overlay:

- **pipeline**: the project's real agents -- Explainer -> Grounding ->
  Narrator -> Personalizer -- wired as `MatchWorkflow`, with the Grounding
  step's veto back edge.
- **single_agent**: ONE model call that returns explanation AND presentation
  together (`{reading, metrics, cited_event_ids, title, body}`), checked by
  the SAME `ground_claim`, retried with the verdict reasons as feedback,
  and published from the same deterministic fallback on exhaustion.

This is a bounded engineering check, **not** statistically conclusive
research. Twelve fixed scenarios, three seeds, honest labels everywhere.

## What is fixed

`manifest.json` fixes the 12 scenarios: seed (42/7/99), candidate (a fixed
`candidate_id` in the deterministic analyst feed), audience (mode +
locale: en/de/bn), and the fault (`none`, `unknown_citation`,
`incorrect_metric`, each fault with `once` or `always` persistence).
Six scenarios are ordinary; six are controlled faults, evenly split between
unknown citations and incorrect metrics.

Both approaches process **one selected candidate per scenario**. The pipeline
still executes the real six-executor `MatchWorkflow`, with normal event ingestion,
deterministic analysis, grounding and the retry back edge. An evaluation-only
analyst filters the feed to the manifest candidate before any model is called;
metrics and citations are unchanged.

Both approaches receive **identical inputs**: the same synthetic events
from `touchline.sim.generator.simulate(seed)`, the same feed candidate,
the same audience requirements, the same model configuration, the same
output requirements, and the same correction allowance
(`max_grounding_attempts = 2`, the repo's `DEFAULT_MAX_GROUNDING_ATTEMPTS`,
with explicit feedback -- the veto's reasons -- after each rejection).

## The independent checks (`validators.py`)

Deterministic, local, no model needed. Applied identically to both
approaches:

1. **Citation validity** -- every `cited_event_id` on the published output
   exists in the event log.
2. **Metric recomputation** -- every published metric recomputes from the
   cited events through `touchline.metrics.definitions.compute_metric`,
   with the same tolerance the Grounding step uses (percentages and metres
   tolerate display rounding; everything else compares near-exactly).
3. **Numeric-prose consistency** -- numbers in the title/body/reading are
   matched against the claimed metric values. This one is a *heuristic* and
   is **advisory only**: it skips years, ordinals (incl. German "28."),
   minute marks (incl. Bengali "মিনিটে"), scorelines and half-phrasing;
   parses numbers with the prose locale's separators (German "3.020" is
   3020); and accepts fair roundings ("0.4" for 0.42). Anything unmatched is
   reported with context for human review. Prose legitimately states counts
   and derived figures ("32.6 m/s") the metric list does not carry, so this
   check does not decide pass/fail.

## Controlled faults

Faults are injected by editing the reply *text* -- the pattern the repo's
own tests use (`tests/test_workflow.py::_ScriptedClient`): the JSON shape
stays the real one and only the faulted field changes. The fault is
scenario-scoped to the scenario's candidate:

- `incorrect_metric`: the first metric value becomes 66.0; names, sides and
  citations are untouched (fails the *value* check, not the bookkeeping).
- `unknown_citation`: the cited ids become `ev-unknown-*` (fails the
  citation check).
- `once`: only the first attempt is faulted -- tests recovery *through
  correction*.
- `always`: every attempt is faulted -- tests recovery *through fallback*
  (the deterministic claim is published, the veto stays in the trace, a
  degradation is recorded).

## Reported metrics

Per scenario and approach: **verification success** (published output
passes the decisive checks), **recovery success** (vetoed then corrected,
or fell back to the deterministic claim), **published-output failures**,
**latency** (wall clock per scenario), **model-call count** (every chat
call, timed). Aggregates per approach in the results file's `summary`.

## The three layers

1. **Template-local** (`evaluation/results/template_local.json`) -- the
   full harness run with the deterministic template engine behind both
   approaches. Proves the mechanics: reproducibility, fault injection, the
   correction loop, the validators. Honest label, recorded in the file:
   *the template engine is deterministic and is never presented as
   Microsoft/Foundry output.* It cannot measure model behaviour -- a real
   model would distribute its failures differently across the three checks.

2. **Live API** (`evaluation/results/live_api_seed42.json`) -- read-only
   GETs against the deployed service's `/api/match/insights?seed=42`
   (Foundry-run pipeline, 46 published overlays), validated against the
   locally simulated seed-42 event log. Real Foundry evidence for the
   pipeline approach's published outputs. Limits: it is the fixed demo
   match (Analyst/English audience), not the 12 scenarios; faults cannot be
   injected through the API; the single-agent baseline has no live
   endpoint and is not measured here.

3. **Foundry run** -- see `results/foundry.json` for completion and results. `evaluation/run_mac_foundry.sh` runs the same
   12 scenarios with a real Foundry model behind both approaches, on the
   Mac where `az login` exists. Each result also saves the published copy so
   language/audience review can be performed. The runner accepts the Foundry chat client
   built by `touchline.providers.foundry.build_foundry_chat_client`
   (`FOUNDRY_PROJECT_ENDPOINT` / `FOUNDRY_MODEL`, keyless). Until that run
   completes, no comparative claim about model behaviour is made.

## Manual language/audience rubric (EN/DE/BN)

Language and audience quality are judged **manually**, not by this harness.
For each published overlay, in each of English, German and Bengali, score
1-5 on:

- **Correct language**: the overlay is written in the requested locale,
  with no other language leaking in (code-switching of football terms that
  are genuinely used in that language's coverage is acceptable; note it).
- **Audience register**: analyst = precise, tactical, no hedging; fan =
  direct, energetic, no jargon; player = centred on the named individual;
  my_team = written as if the side is the reader's own, with the
  positive/negative framing.
- **No invention**: every figure in the prose traces to a claimed metric
  (the automated prose check covers this; the manual pass judges the
  *sense*, e.g. that a rounded figure still means the same thing).
- **Localisation sense**: units, separators and phrasing read naturally in
  the target language (German decimal commas, Bengali phrasing).

The result files retain each published title/body for review. Record human
scores in a separate per-scenario sheet; the automatic acceptance totals do not
claim to measure language quality. The template's prose is known-good by construction and is not scored.

## Limitations (read before citing any number)

- 12 scenarios is a fixed, tiny set: report counts, never rates with
  confidence intervals, and never "statistically significant".
- The template run measures the harness, not the model. Identical outcomes
  across approaches there are *expected* -- the same deterministic engine
  answers both.
- The live-API layer measures only the pipeline's published outputs on the
  fixed demo match; it cannot exercise the correction loop.
- The numeric-prose check is heuristic (see above); one advisory on the
  live data ("32.5 m/s", a derived figure) is a known limitation, not a
  defect.
- Workload `one-candidate-v1` compares the same candidate on both paths.
  Pipeline latency includes deterministic ingestion/analysis and three model
  stages; single-agent latency includes its combined response and correction
  loop. These are end-to-end approach costs, not isolated inference benchmarks.
- Earlier full-match results used different workloads and are not mixed into
  the current comparison. The original partial report is retained in the local
  `build/evaluation-archive/` directory.
- No superiority claim is supported until the Foundry run exists, and even
  then only within these bounds.

## How to reproduce

```bash
cd <repo>
python3 -m venv ~/.venvs/touchline-eval
~/.venvs/touchline-eval/bin/pip install \
  agent-framework-core "a2a-sdk>=1.0.0" "sse-starlette>=2.1" \
  pydantic numpy pytest

# mechanics tests (deterministic, local)
~/.venvs/touchline-eval/bin/python -m pytest evaluation/tests/ -q

# full template-local run
~/.venvs/touchline-eval/bin/python -m evaluation.runner \
  --provider template --out evaluation/results/template_local.json

# live-API probe (read-only GETs)
~/.venvs/touchline-eval/bin/python -m evaluation.live_api_probe \
  --out evaluation/results/live_api_seed42.json

# definitive run: on the Mac, see evaluation/run_mac_foundry.sh
```

The `evaluation/` tree is strictly additive: nothing outside it is
modified. The venv lives outside the repo (`~/.venvs` or
`~/workspace/.venvs`); no secrets are written anywhere in the repo.

## Progress and resumption

Each completed scenario/approach pair is written atomically. `scenarios_planned`
is the manifest size; `scenarios_run` counts scenarios for which **both** outputs
exist. `complete` is true only when all 24 pairs are present. `--resume` accepts
only a matching provider, workload, source revision and manifest hash, and refuses
duplicate or unknown results. A normal run starts a fresh report; retain the
previous report under another filename before rerunning if it is needed.

```bash
python -m evaluation.runner --provider foundry --out evaluation/results/foundry.json --resume
```

The normal Foundry run is isolated from the live match and never changes its
reader. Do not run it while the Container App is analysing a fresh match or while
recording: local and cloud processes share the model deployment's quota.
