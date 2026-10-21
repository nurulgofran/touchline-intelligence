# Delivery verification — 2026-10-21

## Application

- Public demo: https://touchline-app.icywave-bfcbf183.germanywestcentral.azurecontainerapps.io
- `/healthz.revision`: `7ba3d3a`.
- Image: `touchlineacr.azurecr.io/touchline-intelligence:studio-clock-20261021-2314`.
- Azure Container Apps, user-assigned managed identity, min/max replicas 1.
- Seed 42, 5,400,000 ms computed, 1,051 events, 45 insight cards and one deterministic recap.
- Before recording setup: 166 Foundry calls, zero fallbacks or prose substitutions.
- 340 typed handoffs; no grounding veto in the normal match. The recovery fixture
  is a separate, explicitly labelled recorded test, not a live incident.

The application source and web assets have not changed since this deployed
revision. Later commits change developer tools, evaluation reports, documentation
and Dockerfile comments; they do not require another deployment.

## Validation

- 242 Python tests passed across `tests`, `evaluation/tests` and the recovery fixture suite.
- Ruff clean; mypy clean across 56 application source files.
- Frontend clock regression test and TypeScript/Vite production build passed.
- Read-only evidence endpoint, unknown-candidate 404 and A2A agent card checked.
- Browser checks covered the evidence drawer, focused trace and recorded recovery.
- Foundry evaluation: all 24 scenario/approach pairs measured at `b75e550`; both
  approaches passed the published citation/metric checks. Pipeline: 42 calls,
  300.7 s; single agent: 18 calls, 138.1 s. No correctness advantage is claimed.
  Three prose advisories and the absence of independent human language scores
  remain disclosed in the evaluation report.

The final read-only probe of revision `7ba3d3a` validates **46/46** published
overlays against the simulated event log, with zero citation/metric failures and
one prose advisory (a derived speed). The report is committed as
`evaluation/results/live_api_seed42.json`.

## Accepted recording

- Local file: `build/touchline-demo-final.mp4`; identical canonical copy:
  `build/touchline-demo.mp4`. Video files and capture logs remain gitignored.
- Duration 76.333 seconds; 1600 × 900; H.264; 30 fps; 2,290 frames; silent.
- Size 6,046,119 bytes.
- SHA-256: `595fe1887ffdc0710b65995e8257b4c86e326d300cc295cd7de3fe39b668ed05`.
- All 11 recorder beats passed and the take was promoted only after validation.
- Capture provider counters: 210 → 214; fallback 0 → 0; substitution 0 → 0.
- Every encoded frame decoded without errors. The one-second storyboard and
  full-resolution evidence, recovery, recap, personalization and closing frames
  were inspected. A complete normal-speed watch by the owner remains part of
  the pre-upload review.

Cleanup finished and the shared reader was verified as Analyst/English with no
rewrite pending. The post-recording health read showed 258 Foundry calls, zero
fallbacks and zero substitutions.

The owner requested the MP4 for manual upload. Public video publication, its link
on the project page, challenge 5078 association and final submission remain owner
actions; no submission or public video URL is claimed here.
