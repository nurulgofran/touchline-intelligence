# Grounding recovery fixture — recorded test run

A **recorded test run** of the real `MatchWorkflow`: a deliberately invalid
structured claim is injected, the Grounding step vetoes it with a specific
reason, the objection is routed back to the Explainer along the workflow's
back edge, the corrected claim is verified, and the accepted overlay is
produced. The trace comes out of the run itself — it is never hand-authored.

## Status

The showcase fixture was generated on the Mac with Microsoft Foundry on
2026-10-21 from revision `5e80eac`. It records the genuine grounding veto,
back-edge, model correction, and acceptance with no template fallback.
The isolated run did not modify the live match or shared reader.

The fixture is committed at `web/public/fixtures/recovery-example.v1.json`.
The schema, generator, and local tests remain here for reproduction.

## How the generator works

`FaultInjectingClient` wraps the Explainer's chat client. It decodes the
payload out of the prompt: an empty `READING` line means the first attempt,
a non-empty one means the retry carries the veto's reasons. Only the first
attempt about the target insight is corrupted — one metric value moved
beyond the grounding tolerance, the shape of the answer untouched (the same
technique as the project's own probe script). The retry is answered honestly
by the underlying provider, so the *correction* is genuinely model-driven.

The generator **refuses to write the fixture** unless: exactly two verdicts
exist for the target (veto, then acceptance), the veto reasons name the
corrupted metric, the trace shows the grounding → explainer refusal,
an overlay was produced, and `degradations` is empty (any template fallback
invalidates the fixture).

## Producing the showcase fixture (Mac, with Foundry)

```bash
cd ~/workspace/touchline-intelligence   # branch: final-delivery
git pull
# Foundry credentials must be present: FOUNDRY_PROJECT_ENDPOINT + FOUNDRY_MODEL
uv pip install jsonschema   # only needed for the schema-validation test
.venv/bin/python fixtures/recovery/generate_recovery_fixture.py \
  --provider foundry \
  --out web/public/fixtures/recovery-example.v1.json
.venv/bin/python -m pytest fixtures/recovery/test_recovery_fixture.py -q
```

`--provider foundry` uses the Foundry client directly, **not** the fallback
chain: a Foundry outage fails loudly instead of producing a template-backed
fixture labelled as Foundry. If the model does not correct on the first
retry, no fixture is written — re-run; the veto feedback names the exact
numbers.

Local mechanics check (no credentials needed):

```bash
.venv/bin/python fixtures/recovery/generate_recovery_fixture.py \
  --provider template --out /tmp/recovery-example.template.json
```

## Fixture location and serving

Default `--out` is `web/public/fixtures/recovery-example.v1.json`. Verified:
Vite copies `web/public/` to the dist root, the Docker build copies
`web/dist` into the image, and FastAPI serves it via `StaticFiles` — so the
deployed frontend serves it at `/fixtures/recovery-example.v1.json`. The
image must be **rebuilt** after the file is placed. Loading it in the trace
panel is a `web/src` change (separate work); the schema's `label` field is
the copy any UI must surface verbatim.

## Versioning

- Files are named `recovery-example.v<major>.json`; the document carries
  `fixture_version` (semver) and `generator.version`.
- Bump **major** on breaking schema changes, **minor** on additive fields.
- Every fixture records `repo.revision`, per-agent `providers`,
  `injected_fault`, and the exact `generator.command` for reproducibility.

## Labelling rules

- The fixture and any UI copy must identify it as a **RECORDED TEST RUN**,
  never a live incident.
- Never present template output as Microsoft/Foundry output: `providers`
  names the provider that served every agent and the correction.
- The video may only use a fixture whose `providers.correction_provider`
  is `Microsoft Foundry` and `template_fallback_used` is `false`.
