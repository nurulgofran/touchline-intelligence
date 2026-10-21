# Results

## What actually ran (2026-10-21, this VM)

- **`template_local.json`** -- the full 12-scenario x 2-approach harness run
  with the deterministic template engine behind both approaches.
  Honest label: proves reproducibility mechanics (fault injection, the
  correction loop, validator behaviour), NOT model behaviour. The template
  is never presented as Microsoft/Foundry output.
- **`live_api_seed42.json`** -- read-only probe of the deployed service
  (`/api/match/insights?seed=42`): 45 Foundry-run pipeline overlays,
  validated against the locally simulated seed-42 event log. Real
  Foundry evidence for the *pipeline approach's published outputs* only.

## Foundry comparison (2026-10-21)

- **`foundry.json`** -- the complete, candidate-matched Foundry comparison.
  Both paths process the same single manifest candidate, not a full match on one
  path and a single insight on the other. Completion, measured source revision,
  per-scenario outputs, calls and latencies are recorded in the file. The measured
  summary below is updated only after all 24 scenario/approach pairs finish.
- The previous partial full-match Foundry report and the previous template run
  are preserved locally under `build/evaluation-archive/`. They are not combined
  with the candidate-matched results.

## Foundry summary — one candidate per approach and scenario

Measured on 2026-10-21 at source revision `b75e550`. All 24 pairs completed.
The exact manifest hashed in both reports is retained as
`manifest-at-measurement.json`; the current manifest corrects its historical
revision pin without changing any scenario inputs.

| Approach | First try | Corrected | Intentional fallback | Published failures | Calls | Total latency |
|---|---:|---:|---:|---:|---:|---:|
| Pipeline | 6 | 3 | 3 | 0 | 42 | 300.7 s |
| Single agent | 6 | 3 | 3 | 0 | 18 | 138.1 s |

Both approaches passed the decisive checks in all 12 scenarios. The pipeline
required more calls and time. These results support the correction/fallback
mechanics; they do **not** establish a correctness advantage for multiple agents.
Faults are injected into model replies on purpose. Three persistent faults per
approach intentionally exhaust retries and publish deterministic fallback copy;
this is separate from a provider outage during the live demo.

Latency includes model service time and the shared eight-request/minute pacer.
The run is sequential, small, not randomized, and is not a throughput benchmark.
The heuristic prose check produced three advisories across the 24 outputs;
they are retained for review and do not count as citation/metric failures.
The published copy is stored in each result. The EN/DE/BN review rubric is in
`../methodology.md`; independent human language scores have not been collected.
The public studio and video remain English-only.

## Template-local summary — mechanics only

The rerun also completes all 24 candidate-matched pairs, with the same expected
six first-try, three correction and three persistent-fault outcomes per approach.
It makes no model calls; its `model_calls` field counts chat-client invocations
(42 pipeline, 18 single-agent). It is never presented as Foundry evidence.

## Live-API summary (Foundry pipeline, fixed demo match)

45/45 published overlays pass citation validity and metric recomputation.
One prose advisory ("32.5 m/s", a derived figure not carried as a metric)
-- a known heuristic limit, not a defect.
