"""The comparative evaluation runner.

Runs the 12 fixed scenarios in ``manifest.json`` through BOTH approaches:

- ``pipeline``: the real agents (ExplainerAgent, NarratorAgent,
  PersonalizerAgent) wired as ``MatchWorkflow``, with the Grounding step's
  veto back edge -- the same code that serves the live feed.
- ``single_agent``: ONE chat call that returns explanation AND presentation
  together (``{reading, metrics, cited_event_ids, title, body}``), checked
  by the SAME ``ground_claim``, retried with the verdict reasons as
  feedback, and published from the same deterministic fallback on
  exhaustion.

Both approaches receive identical inputs: the same synthetic events, the
same feed candidate, the same audience requirements, the same model
configuration, the same output requirements, and the same correction
allowance (``max_grounding_attempts = 2``, explicit feedback).

The runner is provider-agnostic:

- ``--provider template`` runs locally with the deterministic template
  engine. Honest label: these runs prove the mechanics (reproducibility,
  fault injection, validator behaviour), NOT model behaviour. The template
  is never presented as Microsoft output.
- ``--provider foundry`` builds the Agent Framework Foundry chat client via
  ``touchline.providers.foundry.build_foundry_chat_client`` (reads
  ``FOUNDRY_PROJECT_ENDPOINT`` / ``FOUNDRY_MODEL``; keyless, ``az login``).
  Run this on the Mac.

Controlled faults are injected by *editing the reply text* (the pattern the
repo's own tests use in ``tests/test_workflow.py``): the JSON shape stays
the real one, and only the faulted field changes.

Usage:
    PYTHONPATH=src <venv>/bin/python -m evaluation.runner \\
        --provider template --out evaluation/results/template_local.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO))

from evaluation import validators  # noqa: E402

TEMPLATE_PROVIDER_LABEL = "template (deterministic fallback -- not a model)"


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def load_manifest() -> dict[str, Any]:
    return json.loads((HERE / "manifest.json").read_text())


# ---------------------------------------------------------------------------
# Chat-client wrappers: counting and fault injection
# ---------------------------------------------------------------------------


class CountingClient:
    """Wraps a chat client and counts model calls, with per-call latency."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls: int = 0
        self.call_latencies_ms: list[float] = []

    async def get_response(self, messages: Any, **kwargs: Any) -> Any:
        start = time.perf_counter()
        try:
            return await self._inner.get_response(messages, **kwargs)
        finally:
            self.calls += 1
            self.call_latencies_ms.append(
                (time.perf_counter() - start) * 1000.0
            )


def _edit_unknown_citations(text: str) -> str:
    payload = json.loads(text)
    if isinstance(payload.get("cited_event_ids"), list):
        payload["cited_event_ids"] = [
            f"ev-unknown-{index + 1}"
            for index, _ in enumerate(payload["cited_event_ids"])
        ]
    baseline = payload.get("baseline")
    if isinstance(baseline, dict) and isinstance(
        baseline.get("cited_event_ids"), list
    ):
        baseline["cited_event_ids"] = [
            f"ev-unknown-b{index + 1}"
            for index, _ in enumerate(baseline["cited_event_ids"])
        ]
    return json.dumps(payload, ensure_ascii=False)


def _edit_incorrect_metric(text: str) -> str:
    payload = json.loads(text)
    metrics = payload.get("metrics")
    if isinstance(metrics, list) and metrics:
        metrics[0]["value"] = 66.0
    return json.dumps(payload, ensure_ascii=False)


_FAULT_EDITORS = {
    "unknown_citation": _edit_unknown_citations,
    "incorrect_metric": _edit_incorrect_metric,
}


def _rebuild_response(response: Any, text: str) -> Any:
    """Rebuild a chat response carrying edited text.

    The construction mirrors the template engine's own
    ``_assistant_response`` (``tests/test_workflow.py`` uses the same shape).
    """
    from agent_framework import ChatResponse, Message

    return ChatResponse(messages=[Message(role="assistant", contents=[text])])


class FaultedClient:
    """Edits a chat client's reply text to inject a controlled fault.

    ``persistence="once"`` faults only the first reply (the retry comes
    back clean -- this tests recovery *through correction*);
    ``persistence="always"`` faults every reply (this tests recovery
    *through fallback*).
    """

    def __init__(
        self,
        inner: Any,
        *,
        kind: str,
        persistence: str,
        candidate_id: str | None = None,
    ) -> None:
        self._inner = inner
        self._kind = kind
        self._persistence = persistence
        self._candidate_id = candidate_id
        self._faulted: set[str] = set()

    @property
    def armed(self) -> bool:
        return self._kind in _FAULT_EDITORS

    @staticmethod
    def _prompt_candidate_id(messages: Any) -> str:
        """The candidate the prompt is about, best effort."""
        text = ""
        for message in messages or ():
            if getattr(message, "role", "") != "user":
                continue
            content = getattr(message, "text", None)
            if isinstance(content, str) and content.strip():
                text = content
        try:
            from touchline.providers.payload import decode_payload

            return decode_payload(text).candidate.candidate_id
        except Exception:
            return ""

    async def get_response(self, messages: Any, **kwargs: Any) -> Any:
        response = await self._inner.get_response(messages, **kwargs)
        if not self.armed:
            return response
        prompt_candidate = self._prompt_candidate_id(messages)
        target = self._candidate_id
        if target and prompt_candidate != target:
            # The fault is scenario-scoped: other candidates are untouched.
            return response
        key = prompt_candidate or "unknown"
        if self._persistence == "once" and key in self._faulted:
            return response
        self._faulted.add(key)
        text = response.messages[0].text
        try:
            edited = _FAULT_EDITORS[self._kind](text)
        except (ValueError, KeyError, TypeError, IndexError):
            # Not the JSON shape we edit: leave the reply alone rather than
            # corrupt a non-JSON answer.
            return response
        return _rebuild_response(response, edited)


def build_base_client(provider: str) -> Any:
    if provider == "template":
        from touchline.providers.clients import TemplateChatClient

        return TemplateChatClient()
    if provider == "foundry":
        from touchline.providers.clients import FoundryChatClient

        return FoundryChatClient()
    raise ValueError(f"unknown provider {provider!r}")


async def _read_text(client: Any, prompt: str) -> str:
    from agent_framework import Message

    response = await client.get_response([Message(role="user", contents=[prompt])])
    return response.messages[0].text


# ---------------------------------------------------------------------------
# Scenario context: the identical inputs both approaches get
# ---------------------------------------------------------------------------


@dataclass
class ScenarioContext:
    scenario_id: str
    seed: int
    locale: str
    mode: str
    events: list[Any]
    events_by_id: dict[str, Any]
    candidate: Any
    max_attempts: int


def build_context(scenario: dict[str, Any]) -> ScenarioContext:
    from touchline.metrics.analyst import MatchAnalyst
    from touchline.sim.generator import simulate, team_names

    seed = int(scenario["seed"])
    events = simulate(seed)
    analyst = MatchAnalyst(team_names=team_names(seed))
    feed = analyst.select_for_feed(analyst.analyse(events))
    candidate_id = scenario["candidate_id"]
    matches = [c for c in feed if c.candidate_id == candidate_id]
    if not matches:
        raise ValueError(
            f"scenario {scenario['id']}: {candidate_id} is not in the "
            f"deterministic feed for seed {seed}"
        )
    return ScenarioContext(
        scenario_id=scenario["id"],
        seed=seed,
        locale=scenario["locale"],
        mode=scenario["mode"],
        events=events,
        events_by_id={event.event_id: event for event in events},
        candidate=matches[0],
        max_attempts=int(
            load_manifest()["correction_policy"]["max_grounding_attempts"]
        ),
    )


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass
class ApproachResult:
    scenario_id: str
    approach: str
    outcome: str
    verification: dict[str, Any]
    model_calls: int = 0
    attempts: int = 0
    vetoes: int = 0
    fallback_used: bool = False
    latency_ms: float = 0.0
    call_latencies_ms: list[float] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# The pipeline approach: the real agents, wired as MatchWorkflow
# ---------------------------------------------------------------------------


async def run_pipeline(
    ctx: ScenarioContext,
    scenario: dict[str, Any],
    provider: str,
) -> ApproachResult:
    from touchline.agents.explainer import ExplainerAgent
    from touchline.agents.narrator import NarratorAgent
    from touchline.agents.personalizer import PersonalizerAgent
    from touchline.orchestration.workflow import MatchWorkflow
    from touchline.sim.generator import team_names

    fault = scenario["fault"]
    explainer_client = CountingClient(build_base_client(provider))
    explainer_client = FaultedClient(
        explainer_client,
        kind=fault["kind"] or "none",
        persistence=fault["persistence"] or "once",
        candidate_id=ctx.candidate.candidate_id,
    )
    narrator_client = CountingClient(build_base_client(provider))
    personalizer_client = CountingClient(build_base_client(provider))

    workflow = MatchWorkflow(
        explainer=ExplainerAgent(explainer_client),
        narrator=NarratorAgent(narrator_client),
        personalizer=PersonalizerAgent(
            personalizer_client,
            mode=ctx.mode,
            locale=ctx.locale,
            team_names=team_names(ctx.seed),
        ),
        locale=ctx.locale,
        mode=ctx.mode,
        max_grounding_attempts=ctx.max_attempts,
    )

    start = time.perf_counter()
    run = await workflow.run(ctx.events, match_id=f"eval-{ctx.scenario_id}")
    latency_ms = (time.perf_counter() - start) * 1000.0

    candidate_id = ctx.candidate.candidate_id
    overlays = [o for o in run.overlays if o.overlay_id == f"ovl-{candidate_id}"]
    vetoes = [v for v in run.vetoes if v.candidate_id == candidate_id]
    accepted = [
        v for v in run.verdicts if v.candidate_id == candidate_id and v.accepted
    ]
    fallback = any(candidate_id in note for note in run.degradations)

    if not overlays:
        return ApproachResult(
            scenario_id=ctx.scenario_id,
            approach="pipeline",
            outcome="no_output",
            verification={"ok": False, "summary": "the pipeline published no overlay"},
            latency_ms=latency_ms,
            notes=["no overlay published for the scenario candidate"],
        )

    overlay = overlays[0]
    check = validators.validate_overlay(overlay, ctx.events_by_id, locale=ctx.locale)

    if fallback:
        outcome = "fallback_published"
    elif vetoes:
        outcome = "verified_after_correction" if accepted else "published_invalid"
    else:
        outcome = "verified_first_try"

    model_calls = (
        explainer_client._inner.calls
        + narrator_client.calls
        + personalizer_client.calls
    )

    return ApproachResult(
        scenario_id=ctx.scenario_id,
        approach="pipeline",
        outcome=outcome,
        verification={
            "ok": check.ok,
            "summary": check.summary(),
            "unknown_citations": check.unknown_citations,
            "metric_failures": check.metric_failures,
            "prose_issues": [
                {"text": issue.text, "context": issue.context}
                for issue in check.prose_issues
            ],
        },
        model_calls=model_calls,
        attempts=len([v for v in run.verdicts if v.candidate_id == candidate_id])
        or 1,
        vetoes=len(vetoes),
        fallback_used=fallback,
        latency_ms=latency_ms,
        notes=list(run.degradations),
    )


# ---------------------------------------------------------------------------
# The single-agent approach: one call, same checks, same correction loop
# ---------------------------------------------------------------------------


@dataclass
class _OverlayLike:
    """The published output's checkable surface, whatever produced it."""

    overlay_id: str
    title: str
    body: str
    reading: str
    metrics: list[Any]
    cited_event_ids: list[str]

SINGLE_AGENT_INSTRUCTIONS = """\
You are a football analyst writing a broadcast overlay.

You are given a deterministic insight already computed from the event log:
its metrics (with exact values), the event ids those metrics were computed
from, the audience (mode) and the language (locale).

Rules:
- Use only the numbers given to you. Never invent a number, never change
  one, and never round one to a different figure.
- Cite only the event ids given to you.
- Do explanation AND presentation in this single response: say what
  happened and why it matters (reading), and write the on-screen card
  (title and body) for the audience, in the requested language.
- Reply with one JSON object and nothing else, in exactly this shape:
  {"candidate_id": "...", "reading": "...",
   "metrics": [{"name": "...", "value": 0, "unit": "...", "side": "HOME"}],
   "cited_event_ids": ["..."],
   "title": "...", "body": "..."}
- "metrics" must list every metric the payload gives you, with the same
  values; "cited_event_ids" must list the ids the payload gives you.
"""


def _single_agent_prompt(
    candidate: Any, *, locale: str, mode: str, feedback: str = ""
) -> str:
    from touchline.providers.payload import encode_payload

    prompt = encode_payload(
        "explain", candidate, locale=locale, mode=mode
    )
    instructions = SINGLE_AGENT_INSTRUCTIONS
    if feedback:
        instructions += (
            "\nYour previous reply was rejected for these reasons; "
            f"correct them: {feedback}\n"
        )
    return instructions + "\n" + prompt


def _template_single_agent_reply(
    candidate: Any, *, locale: str, mode: str, reply_text: str
) -> dict[str, Any]:
    """Extend the template engine's explain answer with presentation copy.

    The template engine is deterministic, so this is a stand-in, not a
    model. It exists so the runner's mechanics (one call, one response,
    same checks) can be validated locally; the honest measurement of the
    single-agent approach is the Foundry run.
    """
    from touchline.providers import template

    payload = json.loads(reply_text)
    narration = template.render_narration(candidate, locale=locale, mode=mode)
    payload["title"] = narration["title"]
    payload["body"] = narration["body"]
    return payload


async def run_single_agent(
    ctx: ScenarioContext,
    scenario: dict[str, Any],
    provider: str,
) -> ApproachResult:
    from touchline.agents.explainer import parse_explanation
    from touchline.models.claims import Explanation
    from touchline.orchestration.grounding import ground_claim
    from touchline.providers import template
    from touchline.providers.payload import parse_json_object
    from touchline.state.store import MatchStateStore

    fault = scenario["fault"]
    client = CountingClient(
        FaultedClient(
            build_base_client(provider),
            kind=fault["kind"] or "none",
            persistence=fault["persistence"] or "once",
            candidate_id=ctx.candidate.candidate_id,
        )
    )

    store = MatchStateStore()
    store.extend(ctx.events)
    candidate = ctx.candidate
    expected_names = [metric.name for metric in candidate.metrics]

    start = time.perf_counter()
    feedback = ""
    attempts = 0
    verdict = None
    published: dict[str, Any] | None = None
    fallback_used = False
    parse_error = ""

    while attempts < ctx.max_attempts:
        attempts += 1
        prompt = _single_agent_prompt(
            candidate, locale=ctx.locale, mode=ctx.mode, feedback=feedback
        )
        reply_text = await _read_text(client, prompt)
        try:
            if provider == "template":
                payload = _template_single_agent_reply(
                    candidate, locale=ctx.locale, mode=ctx.mode,
                    reply_text=reply_text,
                )
            else:
                payload = parse_json_object(reply_text) or {}
                if not payload.get("title") or not payload.get("body"):
                    raise ValueError("reply carries no title/body")
            explanation = parse_explanation(
                json.dumps(payload, ensure_ascii=False),
                candidate_id=candidate.candidate_id,
            )
        except Exception as exc:
            parse_error = str(exc)[:200]
            feedback = f"the reply was not usable: {parse_error}"
            continue
        verdict = ground_claim(
            store,
            candidate.candidate_id,
            explanation,
            expected_metric_names=expected_names,
        )
        if verdict.accepted:
            published = {
                "title": str(payload.get("title", "")),
                "body": str(payload.get("body", "")),
                "reading": explanation.reading,
                "metrics": explanation.metrics,
                "cited_event_ids": explanation.cited_event_ids,
                "locale": ctx.locale,
            }
            break
        feedback = "; ".join(verdict.reasons)

    latency_ms = (time.perf_counter() - start) * 1000.0

    if published is None:
        # The attempt bound is exhausted: publish the deterministic claim,
        # exactly as the pipeline's Grounding step does.
        fallback_used = True
        reading = template.render_reading(candidate, locale=ctx.locale)
        explanation = Explanation.from_candidate(candidate, reading)
        narration = template.render_narration(
            candidate, locale=ctx.locale, mode=ctx.mode
        )
        published = {
            "title": narration["title"],
            "body": narration["body"],
            "reading": reading,
            "metrics": explanation.metrics,
            "cited_event_ids": explanation.cited_event_ids,
            "locale": ctx.locale,
        }

    overlay_like = _OverlayLike(
        overlay_id=f"ovl-{candidate.candidate_id}",
        title=published["title"],
        body=published["body"],
        reading=published["reading"],
        metrics=list(published["metrics"]),
        cited_event_ids=list(published["cited_event_ids"]),
    )
    check = validators.validate_overlay(
        overlay_like, ctx.events_by_id, locale=ctx.locale
    )

    if fallback_used:
        outcome = "fallback_published"
    elif attempts > 1 or parse_error:
        outcome = "verified_after_correction"
    else:
        outcome = "verified_first_try"
    if not check.ok:
        outcome = "published_invalid"

    return ApproachResult(
        scenario_id=ctx.scenario_id,
        approach="single_agent",
        outcome=outcome,
        verification={
            "ok": check.ok,
            "summary": check.summary(),
            "unknown_citations": check.unknown_citations,
            "metric_failures": check.metric_failures,
            "prose_issues": [
                {"text": issue.text, "context": issue.context}
                for issue in check.prose_issues
            ],
        },
        model_calls=client.calls,
        attempts=attempts,
        vetoes=max(0, attempts - (0 if fallback_used else 1)),
        fallback_used=fallback_used,
        latency_ms=latency_ms,
        call_latencies_ms=list(client.call_latencies_ms),
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


async def run_all(
    provider: str,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run every scenario in the manifest, both approaches.

    ``on_progress`` is called with the report as it stands after each
    scenario/approach pair. The Foundry run is three hours of model quota
    against a ten-request-a-minute deployment, so the results are
    written as they are measured: a failure at hour three must not throw
    away the two hours and forty minutes that came before it.
    """
    import subprocess

    manifest = load_manifest()
    provider_label = (
        TEMPLATE_PROVIDER_LABEL
        if provider == "template"
        else f"foundry ({provider})"
    )
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
    ).stdout.strip()

    report: dict[str, Any] = {
        "evaluation": manifest["evaluation"],
        "manifest_version": manifest["version"],
        "project_revision": revision,
        "provider": provider,
        "provider_label": provider_label,
        "honest_label": (
            "template run: proves reproducibility mechanics only; "
            "the template engine is deterministic and is never presented "
            "as Microsoft/Foundry output."
            if provider == "template"
            else "foundry run: real model behind both approaches."
        ),
        "scenarios_run": len(manifest["scenarios"]),
        "results": [],
    }

    for scenario in manifest["scenarios"]:
        ctx = build_context(scenario)
        for approach in ("pipeline", "single_agent"):
            if approach == "pipeline":
                result = await run_pipeline(ctx, scenario, provider)
            else:
                result = await run_single_agent(ctx, scenario, provider)
            report["results"].append(asdict(result))
            report["summary"] = summarize(report)
            if on_progress is not None:
                on_progress(report)

    return report


def summarize(report: dict[str, Any]) -> dict[str, Any]:
    by_approach: dict[str, list[dict[str, Any]]] = {}
    for result in report["results"]:
        by_approach.setdefault(result["approach"], []).append(result)
    summary: dict[str, Any] = {}
    for approach, rows in by_approach.items():
        outcomes = [row["outcome"] for row in rows]
        summary[approach] = {
            "scenarios": len(rows),
            "verification_success": sum(
                1 for row in rows if row["verification"]["ok"]
            ),
            "published_output_failures": sum(
                1 for row in rows if not row["verification"]["ok"]
            ),
            "recovery_success": sum(
                1
                for row in rows
                if row["outcome"]
                in ("verified_after_correction", "fallback_published")
            ),
            "verified_first_try": outcomes.count("verified_first_try"),
            "verified_after_correction": outcomes.count(
                "verified_after_correction"
            ),
            "fallback_published": outcomes.count("fallback_published"),
            "total_model_calls": sum(row["model_calls"] for row in rows),
            "total_latency_ms": round(
                sum(row["latency_ms"] for row in rows), 1
            ),
        }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the pipeline-vs-single-agent comparative evaluation."
    )
    parser.add_argument(
        "--provider",
        choices=["template", "foundry"],
        default="template",
        help=(
            "template: local deterministic run; "
            "foundry: real model (needs az login / env)."
        ),
    )
    parser.add_argument(
        "--out",
        default=str(HERE / "results" / "results.json"),
        help="Where to write the results JSON.",
    )
    args = parser.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    def write(report: dict[str, Any]) -> None:
        """Write the report as it stands.

        Called after every scenario, so an interrupted run leaves a
        readable file behind rather than nothing at all.
        """
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False))

    report = asyncio.run(run_all(args.provider, on_progress=write))
    report["summary"] = summarize(report)
    write(report)
    print(json.dumps(report["summary"], indent=2))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
