# Submission checklist — evidence-backed, mirroring the plan's acceptance list

> PLAN.md §7 acceptance checklist, with what was actually checked and when.
> Everything below is dated 2026-10-21 unless it says otherwise. "All gates green"
> is **false** while video publication and the project-page requirements remain
> open; deployment verification is recorded below — they are marked
> ⏳ below and nothing in this repository claims otherwise.

| # | Requirement (plan §7) | State | Evidence |
|---|---|---|---|
| 1 | Duplicate-request, supersession, failure retry, and reconnect regressions pass | ✅ | `tests/test_rewrite_lifecycle.py` and the related lifecycle tests pass in the local suite (2026-10-21) |
| 2 | Existing cards remain visible; each version is delivered once per session | ✅ | Covered by the rewrite-lifecycle implementation and tests (2026-10-21) |
| 3 | Provider/substitution attribution is correct under concurrency | ✅ | `tests/test_provider_attribution.py` passes (2026-10-21) |
| 4 | Deterministic fallback uses the requested audience/language | ✅ | Committed in the attribution workstream; tested (2026-10-21) |
| 5 | Evidence inspector recomputes values and handles missing candidates/evidence | ✅ | `tests/test_evidence.py` passes; `GET /api/evidence/{candidate_id}` returns 404 for unknown candidates (2026-10-21) |
| 6 | Recovery fixture is genuine, labelled, and isolated from live state | ✅ | Generated on the Mac with Microsoft Foundry at revision `5e80eac`; real veto → back-edge → correction → acceptance, no template fallback. See `web/public/fixtures/recovery-example.v1.json`. |
| 7 | Comparative evaluation is reproducible and uses equal acceptance checks | ✅ | Complete candidate-matched Foundry run: 12 scenarios × 2 approaches, 24/24 published outputs pass citation/metric checks. Source `b75e550`; pipeline 42 calls / 300.7 s, single agent 18 calls / 138.1 s. Three prose advisories disclosed. Results, published copy and measurement manifest are committed under `evaluation/results/`. No correctness-superiority claim. |
| 8 | Recorder bounds duration, excludes cleanup, rejects invalid takes, and restores reader | ✅ | `tests/test_record_demo.py` passes; the recorder targets 110s, rejects ≥120s, and promotes only validated takes (2026-10-21). The timeline now drives the two beats the plan asks for — the evidence inspector and the labelled recovery replay — and each holds only if what its caption claims is on screen |
| 9 | Full pytest, Ruff, mypy, and frontend build pass | ✅ | Final local checks, 2026-10-21: 242 tests across application, evaluation and fixture suites; Ruff clean; mypy clean (56 application files); TypeScript + Vite build green. |
| 10 | Deployed revision and complete analysis verified; browser acceptance passes | ⏳ | Polished studio deployed as `7ba3d3a` using unique ACR tag `studio-clock-20261021-2314`; user-assigned identity and min/max replicas 1 verified. Waiting for full Foundry analysis before final recording and browser acceptance. |
| 11 | Documentation claims match implementation and dated evidence | ✅ | README.md and `submission/` rewritten to the audited implementation (2026-10-21); every counter dated |
| 12 | Video under 120 seconds, visually reviewed, public, and linked | ⏳ | **Not yet recorded**; the 2026-10-20 take is not approved for publication. See `VIDEO.md` — publication is on YouTube, by the owner |
| 13 | Public repository/demo and challenge 5078 association verified | ⏳ | The repo is public (`https://github.com/nurulgofran/touchline-intelligence`); **linking the project to challenge 5078 still needs the owner's account** |
| 14 | Submission confirmed and confirmation retained | ⏳ | **Not yet submitted** — target 2026-10-26; cutoff 2026-10-28 07:59 CET |

---

## Also on the list

- [ ] **Final deployment verification:** deployed application `7ba3d3a`; verify
      completed analysis and browser acceptance before recording. The evaluation
      measured source is `b75e550`, recorded in the results; docs-only changes
      do not require rebuilding the application image.
- [ ] Check the Azure credit balance and the demo URL on **1 November** and
      **8 November** — the demo has to stay live through **10 November 23:59 PT**.
- [ ] Keep the repository and the video public through 10 November.
- [ ] Re-take the video if a template fallback ever appears in the badge — the
      recorder rejects such a take itself, and it is not publishable.

### The November check, exactly

```bash
# 1. the subscription is still enabled
az account show --query '{name:name, state:state}'

# 2. the container app is still running, with its warm replica
az containerapp revision list -n touchline-app -g touchline-rg \
    --query "[?properties.active].properties.runningState" -o tsv

# 3. the demo actually answers
python scripts/verify-live.py \
    https://touchline-app.icywave-bfcbf183.germanywestcentral.azurecontainerapps.io

# 4. the repository is still public
gh repo view nurulgofran/touchline-intelligence --json visibility
```

**The credit balance itself is not in the CLI.** The Azure for Students allowance
is shown in the Education portal (Microsoft Azure for Students → credit), and the
Cost Management and Consumption APIs report no rows for this subscription. So the
balance has to be read in the portal — the four commands above are the signals
that something has gone wrong, and a lapsed subscription stops the revision
before it stops answering for long.
