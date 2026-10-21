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

## Deferred under the quota exception (2026-10-21)

- **`foundry.json`** -- the definitive run with a real Foundry model behind
  both approaches. The owner deferred this run: roughly 1,550 calls would take
  over three hours at eight requests per minute. No Foundry comparison results
  have been generated. Run `../run_mac_foundry.sh` on the Mac (needs `az login`
  plus `FOUNDRY_PROJECT_ENDPOINT` / `FOUNDRY_MODEL`). Until this exists, no
  claim about model behaviour is made, and no superiority claim for either
  approach is supported.

## Template-local summary (mechanics only)

| approach     | verified 1st try | corrected | fallback | published failures | model calls | latency |
|--------------|-----------------|-----------|----------|--------------------|-------------|---------|
| pipeline     | 6               | 3         | 3        | 0                  | 1566        | ~6.0 s  |
| single_agent | 6               | 3         | 3        | 0                  | 18          | ~0.01 s |

(6 ordinary scenarios verify first try; 3 fault-once scenarios recover
through correction; 3 fault-always scenarios publish the deterministic
fallback; 0 published outputs fail the decisive checks. Pipeline latency
and calls cover a full match per scenario -- ~44 feed candidates x 3
agents -- vs the single agent's one candidate; the comparison is
scenario-framed, not per-candidate-normalised.)

## Live-API summary (Foundry pipeline, fixed demo match)

45/45 published overlays pass citation validity and metric recomputation.
One prose advisory ("32.5 m/s", a derived figure not carried as a metric)
-- a known heuristic limit, not a defect.
