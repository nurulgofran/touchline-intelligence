# Submission checklist — evidence-backed, mirroring the plan's acceptance list

> PLAN.md §7 acceptance checklist, with what was actually checked and when.
> Everything below is dated 2026-10-21 unless it says otherwise. "All gates green"
> is **false** while the video, the Foundry evaluation run, the deployment
> verification, and the project-page requirements remain open — they are marked
> ⏳ below and nothing in this repository claims otherwise.

| # | Requirement (plan §7) | State | Evidence |
|---|---|---|---|
| 1 | Duplicate-request, supersession, failure retry, and reconnect regressions pass | ✅ | `tests/test_rewrite_lifecycle.py` and the related lifecycle tests pass in the local suite (2026-10-21) |
| 2 | Existing cards remain visible; each version is delivered once per session | ✅ | Covered by the rewrite-lifecycle implementation and tests (2026-10-21) |
| 3 | Provider/substitution attribution is correct under concurrency | ✅ | `tests/test_provider_attribution.py` passes (2026-10-21) |
| 4 | Deterministic fallback uses the requested audience/language | ✅ | Committed in the attribution workstream; tested (2026-10-21) |
| 5 | Evidence inspector recomputes values and handles missing candidates/evidence | ✅ | `tests/test_evidence.py` passes; `GET /api/evidence/{candidate_id}` returns 404 for unknown candidates (2026-10-21) |
| 6 | Recovery fixture is genuine, labelled, and isolated from live state | ⏳ | The replay panel and its missing-fixture handling are committed and tested; **the fixture itself is not yet generated** — needs Foundry credentials and a rebuild (`fixtures/recovery/generate_recovery_fixture.py --provider foundry --out web/public/fixtures/recovery-example.v1.json`) |
| 7 | Comparative evaluation is reproducible and uses equal acceptance checks | ⏳ | Template-local run and live-API probe are committed with methodology (`evaluation/methodology.md`, `evaluation/results/README.md`); **the Foundry comparison is pending the Mac run** (`evaluation/run_mac_foundry.sh`) |
| 8 | Recorder bounds duration, excludes cleanup, rejects invalid takes, and restores reader | ✅ | `tests/test_record_demo.py` passes; the recorder targets 110s, rejects ≥120s, and promotes only validated takes (2026-10-21) |
| 9 | Full pytest, Ruff, mypy, and frontend build pass | ✅* | All pass 2026-10-21 with two documented environmental exceptions: `test_the_a2a_hop_falls_back_to_the_explainer_in_process` fails only under this VM's bracketed-IPv6 `NO_PROXY`, and `test_the_controls_move_the_clock` is a 0.4s-window timing flake on this 2-CPU VM (both proven pre-existing via A/B runs on the pristine tree) |
| 10 | Deployed revision and complete analysis verified; browser acceptance passes | ⏳ | **Needs the feature-freeze deploy plus live verification** (`scripts/verify-live.py`, reader round trip, reconnect) |
| 11 | Documentation claims match implementation and dated evidence | ✅ | README.md and `submission/` rewritten to the audited implementation (2026-10-21); every counter dated |
| 12 | Video under 120 seconds, visually reviewed, public, and linked | ⏳ | **Not yet recorded**; the 2026-10-20 take is not approved for publication. See `VIDEO.md` — publication is on YouTube, by the owner |
| 13 | Public repository/demo and challenge 5078 association verified | ⏳ | The repo is public (`https://github.com/nurulgofran/touchline-intelligence`); **linking the project to challenge 5078 still needs the owner's account** |
| 14 | Submission confirmed and confirmation retained | ⏳ | **Not yet submitted** — target 2026-10-26; cutoff 2026-10-28 07:59 CET |

---

## Also on the list

- [ ] **Feature freeze (2026-10-24):** deploy the tested commit; re-pin
      `evaluation/manifest.json` to the built revision (it pins the revision the
      scenarios were measured against); verify `/healthz` revision, one replica,
      the managed identity; wait for the full analysis before recording.
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
