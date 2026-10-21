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
| 7 | Comparative evaluation is reproducible and uses equal acceptance checks | ✅ | Complete candidate-matched Foundry run: 12 scenarios × 2 approaches, 24/24 published outputs pass citation/metric checks. Final live-API probe: 46/46 overlays pass; one prose advisory. Source `b75e550`; pipeline 42 calls / 300.7 s, single agent 18 calls / 138.1 s. Three prose advisories disclosed. Results, published copy and measurement manifest are committed under `evaluation/results/`. No correctness-superiority claim. |
| 8 | Recorder bounds duration, excludes cleanup, rejects invalid takes, and restores reader | ✅ | `tests/test_record_demo.py` passes; the recorder targets 110s, rejects ≥120s, and promotes only validated takes (2026-10-21). The timeline now drives the two beats the plan asks for — the evidence inspector and the labelled recovery replay — and each holds only if what its caption claims is on screen |
| 9 | Full pytest, Ruff, mypy, and frontend build pass | ✅ | Final local checks, 2026-10-21: 242 tests across application, evaluation and fixture suites; Ruff clean; mypy clean (56 application files); TypeScript + Vite build green. |
| 10 | Deployed revision and complete analysis verified; browser acceptance passes | ✅ | `7ba3d3a`, unique ACR tag `studio-clock-20261021-2314`; user-assigned identity and min/max replicas 1 verified. Full 90 minutes, 1,051 events, 45 insight cards plus recap. Live browser checks and all 11 recording beats passed. |
| 11 | Documentation claims match implementation and dated evidence | ✅ | README.md and `submission/` aligned to the audited implementation (2026-10-21); every counter dated; final reader restored and verified. See `VERIFICATION.md` |
| 12 | Video under 120 seconds, visually reviewed, public, and linked | ⏳ | Accepted MP4: 76.333 seconds, 1600 × 900, all 11 beats passed, zero capture fallbacks/substitutions. Full decode and storyboard/key-frame review complete. Owner full-video review, public upload and link remain; see `VIDEO.md` |
| 13 | Public repository/demo and challenge 5078 association verified | ⏳ | The repo is public (`https://github.com/nurulgofran/touchline-intelligence`); **linking the project to challenge 5078 still needs the owner's account** |
| 14 | Submission confirmed and confirmation retained | ⏳ | **Not yet submitted** — target 2026-10-26; cutoff 2026-10-28 07:59 CET |

---

## Also on the list

- [x] **Final deployment verification:** deployed application `7ba3d3a`; full
      analysis, browser acceptance and all 11 recorded beats verified. The evaluation
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

## Supplied official requirements — delivery evidence

Cross-checked against the official rules and challenge text supplied by the owner
on 2026-10-21. This records implementation evidence; account eligibility and final
submission are confirmed by the owner.

| Requirement | Evidence / remaining action |
|---|---|
| Five functional stages (§4.1) | Synthetic ingestion → deterministic interpretation → explanation → on-screen rendering → personalization. Grounding is an additional validation executor; there are three model-driven agents, not five AI agents. |
| Synthetic data and original footage (§4.7) | Seeded simulator, fictional clubs/players, custom synthetic pitch rendering. The video records this application, with no real match footage. |
| Explainability and recovery | Read-only evidence inspector and genuine Foundry recovery fixture; the recorded test is explicitly labelled and isolated from live state. |
| Multiple-language capability (§4.1 vi) | EN/DE/BN remain covered in service tests and evaluation. The studio and submission video intentionally use English only. |
| Pitch and real-functioning video (§4.4) | `PITCH.md` includes a short project-page elevator pitch. The final MP4 is recorded from the Azure deployment and must pass the recorder's sub-120-second and provider checks. |
| Public video URL (§4.4) | Owner selected delivery of the approved MP4 for manual upload. Upload to a listed public video platform, then paste the public URL into the project page and pitch. |
| Public GitHub repository (§4.4) | Verified public: https://github.com/nurulgofran/touchline-intelligence |
| Unrestricted judging access (§4.8) | Live ACA FQDN, no login or paywall, min/max replicas 1. Keep it available through the end of judging; November credit checks remain operational obligations. |
| English materials (§4.9) | English pitch, judge instructions, interface and video captions. |
| Dependency licences (§4.11) | MIT project licence and `THIRD_PARTY_NOTICES.md`, including the self-hosted fonts' SIL licences. |
| Existing registration and submission (§1, §4) | Supplied rules close registration on Oct 20 and submissions on Oct 27 at 23:59 Pacific (Oct 28, 07:59 CET in Berlin). Owner confirms existing registration, challenge association and submission receipt. Target remains Oct 26. |

The Foundry comparison supports the correction and fallback mechanics. Both
approaches passed the same checks; it does not establish a correctness advantage
for multiple agents. The category case is the explicit responsibilities, typed
handoffs, shared state and inspectable recovery shown in the application.
