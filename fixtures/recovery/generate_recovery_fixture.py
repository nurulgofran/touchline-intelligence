"""Record a genuine grounding-recovery run as a versioned fixture.

This runs the REAL :class:`touchline.orchestration.workflow.MatchWorkflow`
end to end over a short synthetic match. The Explainer's chat client is
wrapped so that its FIRST claim about one target insight carries a
deliberately invalid structured claim (one metric value perturbed beyond
the grounding tolerance); every later attempt is answered honestly by the
underlying provider. The trace, the veto, the back-edge routing, the
corrected claim and the accepted overlay are all recorded from that run —
never hand-authored.

The generator REFUSES to write the fixture unless the recovery story holds:

* the target's first verdict is a veto with a specific reason,
* the objection is routed back to the Explainer (trace hand-off
  grounding -> explainer, accepted=false),
* the retry's verdict is accepted,
* NO degradation happened anywhere (the deterministic template fallback
  would invalidate the fixture),
* an overlay was produced for the target insight.

Provider modes:

``template`` (default)
    Every agent answers through the template engine. Fully local and
    deterministic — this proves the generator mechanics, and the fixture
    says ``Template engine`` everywhere it matters.

``foundry``
    Every agent answers through Microsoft Foundry. This is the showcase
    mode: the correction is a real model correction. It needs
    ``FOUNDRY_PROJECT_ENDPOINT`` and ``FOUNDRY_MODEL`` in the environment
    (the user's Mac). The Foundry client is used directly, NOT the
    fallback chain, so a Foundry outage fails loudly instead of producing
    a template-backed fixture labelled as Foundry.

Run it with::

    python fixtures/recovery/generate_recovery_fixture.py --provider template
    python fixtures/recovery/generate_recovery_fixture.py --provider foundry \\
        --out web/public/fixtures/recovery-example.v1.json

The fixture is a RECORDED TEST RUN on synthetic data. It must never be
presented as a live incident.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from touchline.agents.explainer import ExplainerAgent, parse_explanation
from touchline.api.live import overlay_candidate_id
from touchline.metrics.analyst import MatchAnalyst
from touchline.models.claims import Explanation
from touchline.orchestration.workflow import (
    STAGE_EXPLAINER,
    STAGE_GROUNDING,
    MatchWorkflow,
)
from touchline.providers import status as provider_status
from touchline.providers.base import ProviderName
from touchline.providers.clients import FoundryChatClient, TemplateChatClient
from touchline.providers.payload import TASK_EXPLAIN, decode_payload, parse_json_object
from touchline.sim.generator import simulate, team_names

# The veto logs a warning per refused claim — right in the service, noise here.
logging.getLogger("touchline").setLevel(logging.ERROR)

REPO_ROOT = Path(__file__).resolve().parents[2]

GENERATOR_VERSION = "1.0.0"
FIXTURE_VERSION = "1.0.0"

#: How far the injected claim moves one metric value. The grounding
#: tolerance is 0.5 for %/m and 0.01 otherwise, so this is unambiguously a
#: wrong number and unambiguously not a rounding wobble.
DEFAULT_FAULT_DELTA = 25.0

PROVIDER_LABELS = {
    ProviderName.FOUNDRY.value: provider_status.FOUNDRY_PROVIDER,
    ProviderName.TEMPLATE.value: provider_status.TEMPLATE_PROVIDER,
}


class FixtureError(RuntimeError):
    """The run did not produce a clean recovery story; no fixture written."""


class FaultInjectingClient:
    """Corrupt the Explainer's first claim about the target, then go honest.

    The wrapper sits between the Explainer agent and its chat client. It
    decodes the payload out of the prompt: an empty READING line means the
    first attempt, a non-empty one means the retry carries the veto's
    reasons. Only the first attempt about the target candidate is corrupted,
    and only one metric value moves — the shape of the answer stays the real
    one, exactly like the project's own probe does with its lying client.
    """

    def __init__(
        self,
        inner: Any,
        *,
        target_candidate_id: str,
        fault_delta: float = DEFAULT_FAULT_DELTA,
    ) -> None:
        self._inner = inner
        self._target = target_candidate_id
        self._delta = fault_delta
        #: Evidence, in call order: one entry per Explainer call.
        self.attempts: list[dict[str, Any]] = []
        #: The injected fault, once it has been injected.
        self.fault: dict[str, Any] | None = None

    @property
    def name(self) -> str:
        """The wrapped client's provider id, for honest labelling."""
        return getattr(self._inner, "name", "unknown")

    async def get_response(self, messages: Any, **kwargs: Any) -> Any:
        """Answer, corrupting the target's first claim only."""
        from agent_framework import ChatResponse, Message

        response = await self._inner.get_response(messages, **kwargs)
        text = response.messages[0].text

        payload = self._decode(messages)
        attempt = 1 if not payload or not payload.reading.strip() else 2
        corrupt = (
            attempt == 1
            and payload is not None
            and payload.task == TASK_EXPLAIN
            and payload.candidate.candidate_id == self._target
            and self.fault is None
        )

        if corrupt:
            assert payload is not None
            mutated, fault = self._corrupt(text, payload.candidate.candidate_id)
            self.fault = fault
            self.attempts.append(
                {
                    "attempt": attempt,
                    "provider": self.name,
                    "corrupted": True,
                    "claim": mutated,
                }
            )
            return ChatResponse(
                messages=[Message(role="assistant", contents=[mutated])]
            )

        self.attempts.append(
            {
                "attempt": attempt,
                "provider": self.name,
                "corrupted": False,
                "claim": text,
            }
        )
        return response

    def _decode(self, messages: Any) -> Any | None:
        """Read the payload out of the last user message, if there is one."""
        text = ""
        for message in messages or ():
            if getattr(message, "role", "") != "user":
                continue
            content = getattr(message, "text", None)
            if isinstance(content, str) and content.strip():
                text = content
        if not text:
            return None
        try:
            return decode_payload(text)
        except ValueError:
            return None

    def _corrupt(self, text: str, candidate_id: str) -> tuple[str, dict[str, Any]]:
        """Move one metric value beyond the grounding tolerance."""
        payload = parse_json_object(text)
        metrics = payload.get("metrics") if isinstance(payload, dict) else None
        if not metrics:
            raise FixtureError(
                "the honest reply carries no metrics to corrupt; "
                "the fault cannot be injected"
            )
        first = metrics[0]
        true_value = float(first["value"])
        claimed_value = true_value + self._delta
        first["value"] = claimed_value
        fault = {
            "type": "metric_value_perturbation",
            "description": (
                f"The Explainer's first claim stated {first['name']} as "
                f"{claimed_value:g} instead of the {true_value:g} the event "
                f"log recomputes — a deliberately invalid structured claim."
            ),
            "candidate_id": candidate_id,
            "metric": str(first["name"]),
            "true_value": true_value,
            "claimed_value": claimed_value,
            "attempt": 1,
        }
        return json.dumps(payload), fault


def _parse_claim(text: str, candidate_id: str) -> Explanation:
    """Read a claim out of a reply, the way the Explainer agent does."""
    return parse_explanation(text, candidate_id=candidate_id)


def _app_version() -> str:
    """The service version, read without importing the app module.

    Importing :mod:`touchline.api.main` builds the FastAPI application and
    wires the live engine as a side effect; the fixture generator must stay
    isolated from all of that, so the constant is read out of the source.
    """
    import re

    source = (REPO_ROOT / "src" / "touchline" / "api" / "main.py").read_text()
    match = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', source, re.MULTILINE)
    return match.group(1) if match else "unknown"


def _git_revision() -> str:
    """The commit the code runs from, or 'unknown' outside a checkout."""
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return "unknown"


def _git_branch() -> str:
    try:
        return subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return "unknown"


def build_fixture(
    *,
    provider: str,
    seed: int,
    minutes: int,
    target_index: int,
    fault_delta: float,
    max_grounding_attempts: int,
    command: str,
) -> dict[str, Any]:
    """Run the real workflow and record the recovery as a fixture dict.

    Raises:
        FixtureError: If the run does not produce a clean recovery story.
    """
    app_version = _app_version()

    cutoff_ms = int(minutes * 60 * 1000)
    events = [e for e in simulate(seed=seed) if e.match_clock_ms <= cutoff_ms]
    if not events:
        raise FixtureError("the simulation produced no events")

    # The target is the insight the fault is injected into. The analyst is
    # deterministic, so running it here picks the same feed the workflow's
    # own analyst will find.
    analyst = MatchAnalyst(team_names=team_names(seed))
    feed = analyst.select_for_feed(analyst.analyse(events))
    if target_index >= len(feed):
        raise FixtureError(
            f"target index {target_index} is out of range: "
            f"the feed holds {len(feed)} insights"
        )
    target = feed[target_index].candidate_id

    if provider == "foundry":
        from touchline.providers import foundry

        if not foundry.is_available():
            raise FixtureError(
                "foundry mode needs FOUNDRY_PROJECT_ENDPOINT and FOUNDRY_MODEL"
            )
        explainer_inner: Any = FoundryChatClient()
        narrator_client: Any = FoundryChatClient()
        personalizer_client: Any = FoundryChatClient()
    elif provider == "template":
        explainer_inner = TemplateChatClient(record=False)
        narrator_client = TemplateChatClient(record=False)
        personalizer_client = TemplateChatClient(record=False)
    else:
        raise FixtureError(f"unknown provider {provider!r}")

    injector = FaultInjectingClient(
        explainer_inner, target_candidate_id=target, fault_delta=fault_delta
    )

    from touchline.agents.narrator import NarratorAgent
    from touchline.agents.personalizer import PersonalizerAgent

    workflow = MatchWorkflow(
        analyst=analyst,
        explainer=ExplainerAgent(injector),
        narrator=NarratorAgent(narrator_client),
        personalizer=PersonalizerAgent(personalizer_client),
        max_grounding_attempts=max_grounding_attempts,
    )
    run = asyncio.run(workflow.run(events, match_id=f"seed-{seed}-recovery"))

    return _assemble(
        run,
        injector=injector,
        target=target,
        provider=provider,
        seed=seed,
        minutes=minutes,
        app_version=app_version,
        command=command,
        narrator_name=getattr(narrator_client, "name", "unknown"),
        personalizer_name=getattr(personalizer_client, "name", "unknown"),
    )


def _assemble(
    run: Any,
    *,
    injector: FaultInjectingClient,
    target: str,
    provider: str,
    seed: int,
    minutes: int,
    app_version: str,
    command: str,
    narrator_name: str,
    personalizer_name: str,
) -> dict[str, Any]:
    """Check the recovery story and build the fixture document."""
    verdicts = [v for v in run.verdicts if v.candidate_id == target]
    if len(verdicts) != 2:
        raise FixtureError(
            f"expected exactly two verdicts for {target} "
            f"(veto, then acceptance); got {len(verdicts)}"
        )
    veto, acceptance = verdicts
    if veto.accepted:
        raise FixtureError("the first claim was not vetoed")
    if not veto.reasons:
        raise FixtureError("the veto carries no reasons")
    if not acceptance.accepted:
        raise FixtureError("the corrected claim was not accepted")

    if run.degradations:
        raise FixtureError(
            "the run fell back to the deterministic claim; "
            f"degradations: {run.degradations}"
        )

    fault = injector.fault
    if fault is None:
        raise FixtureError("the fault was never injected")
    if not any(a["corrupted"] for a in injector.attempts):
        raise FixtureError("no corrupted attempt was recorded")
    if not any(
        not a["corrupted"] and a["attempt"] > 1 for a in injector.attempts
    ):
        raise FixtureError("no honest retry attempt was recorded")

    metric = fault["metric"]
    if not any(metric in reason for reason in veto.reasons):
        raise FixtureError(
            f"the veto reasons do not name the corrupted metric {metric!r}: "
            f"{veto.reasons}"
        )

    back_edge = [
        h
        for h in run.trace.for_candidate(target)
        if h.from_stage == STAGE_GROUNDING
        and h.to_stage == STAGE_EXPLAINER
        and h.accepted is False
    ]
    if not back_edge:
        raise FixtureError(
            "the trace shows no grounding -> explainer refusal; "
            "the objection was not routed back"
        )

    overlays = [
        o for o in run.overlays if overlay_candidate_id(o.overlay_id) == target
    ]
    if not overlays:
        raise FixtureError(f"no overlay was produced for {target}")
    overlay = overlays[0]

    attempts = injector.attempts
    invalid_claim = _parse_claim(attempts[0]["claim"], target)
    honest_retries = [a for a in attempts if not a["corrupted"]]
    corrected_claim = _parse_claim(honest_retries[-1]["claim"], target)

    explainer_label = PROVIDER_LABELS.get(injector.name, injector.name)
    narrator_label = PROVIDER_LABELS.get(narrator_name, narrator_name)
    personalizer_label = PROVIDER_LABELS.get(personalizer_name, personalizer_name)
    if provider == "foundry" and not all(
        name == ProviderName.FOUNDRY.value
        for name in (injector.name, narrator_name, personalizer_name)
    ):
        raise FixtureError(
            "foundry mode requires every agent on Foundry; "
            f"got explainer={injector.name}, narrator={narrator_name}, "
            f"personalizer={personalizer_name}"
        )
    correction_provider = PROVIDER_LABELS.get(
        honest_retries[-1]["provider"], honest_retries[-1]["provider"]
    )

    trace_entries = [
        h.model_dump(mode="json") for h in run.trace.for_candidate(target)
    ]

    return {
        "fixture_format": "touchline.recovery-example",
        "fixture_version": FIXTURE_VERSION,
        "kind": "recorded-test-run",
        "label": (
            "RECORDED TEST RUN — synthetic data; the invalid claim was "
            "injected on purpose to exercise the grounding veto. "
            "Not a live incident."
        ),
        "generated_at": datetime.now(UTC).isoformat(),
        "generator": {
            "name": "fixtures/recovery/generate_recovery_fixture.py",
            "version": GENERATOR_VERSION,
            "command": command,
        },
        "repo": {
            "revision": _git_revision(),
            "branch": _git_branch(),
            "app_version": app_version,
        },
        "test_context": {
            "seed": seed,
            "match_minutes": minutes,
            "match_id": run.match_id,
            "audience": "analyst",
            "locale": "en",
            "target_candidate_id": target,
            "isolation": (
                "Synthetic fixture match simulated in-process; no network "
                "calls to the live deployment, no shared reader state touched."
            ),
        },
        "providers": {
            "explainer": explainer_label,
            "narrator": narrator_label,
            "personalizer": personalizer_label,
            "correction_provider": correction_provider,
            "template_fallback_used": False,
        },
        "injected_fault": fault,
        "recovery": {
            "invalid_claim": invalid_claim.model_dump(mode="json"),
            "veto": veto.model_dump(mode="json"),
            "veto_trace_entry": back_edge[0].model_dump(mode="json"),
            "corrected_claim": corrected_claim.model_dump(mode="json"),
            "acceptance": acceptance.model_dump(mode="json"),
            "overlay": overlay.model_dump(mode="json"),
        },
        "trace": trace_entries,
        "degradations": [],
    }


def main() -> int:
    """Parse arguments, run the workflow, write the fixture."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--provider",
        choices=("template", "foundry"),
        default="template",
        help="Which chat client backs the agents. 'foundry' needs "
        "FOUNDRY_PROJECT_ENDPOINT and FOUNDRY_MODEL.",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--minutes",
        type=float,
        default=20,
        help="How much of the synthetic match to analyse.",
    )
    parser.add_argument(
        "--target-index",
        type=int,
        default=0,
        help="Which feed insight to inject the fault into.",
    )
    parser.add_argument(
        "--fault-delta",
        type=float,
        default=DEFAULT_FAULT_DELTA,
        help="How far the injected claim moves one metric value.",
    )
    parser.add_argument(
        "--max-grounding-attempts",
        type=int,
        default=2,
        help="Veto bound, as in the workflow.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT
        / "web"
        / "public"
        / "fixtures"
        / f"recovery-example.v{FIXTURE_VERSION.split('.')[0]}.json",
        help="Where to write the fixture.",
    )
    args = parser.parse_args()

    command = " ".join([sys.executable, *sys.argv])
    try:
        fixture = build_fixture(
            provider=args.provider,
            seed=args.seed,
            minutes=args.minutes,
            target_index=args.target_index,
            fault_delta=args.fault_delta,
            max_grounding_attempts=args.max_grounding_attempts,
            command=command,
        )
    except FixtureError as exc:
        print(f"no fixture written: {exc}", file=sys.stderr)
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(fixture, indent=2) + "\n")

    veto = fixture["recovery"]["veto"]
    print(f"fixture written to {args.out}")
    print(f"  target:   {fixture['test_context']['target_candidate_id']}")
    print(f"  provider: {fixture['providers']['correction_provider']}")
    print(f"  veto:     {'; '.join(veto['reasons'])}")
    print(f"  overlay:  {fixture['recovery']['overlay']['title']}")
    print(f"  trace:    {len(fixture['trace'])} hand-offs about the target")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
