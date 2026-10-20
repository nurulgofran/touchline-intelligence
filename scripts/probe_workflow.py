"""Run a whole match through the workflow, and check that it holds up.

This is the Day 3 probe. It drives the Agent Framework graph over a full
simulated match and prints three things:

* **The feed.** How many insights reached the screen, how many hand-offs
  the trace recorded, and how long the run took.
* **The grounding.** Every number on every overlay is recomputed from the
  event ids that overlay cites. A number that does not reproduce is a
  failure, and the probe says which one.
* **The veto.** A client that states the wrong number is put in front of
  the Explainer, so the Grounding step has something real to refuse. The
  refusal has to be visible -- in the trace, as a hand-off that was not
  accepted -- and the feed has to keep running afterwards.

Run it with::

    python scripts/probe_workflow.py [--seed 42] [--a2a]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from touchline.agents.explainer import ExplainerAgent
from touchline.metrics.analyst import MatchAnalyst
from touchline.metrics.definitions import compute_metric
from touchline.orchestration.a2a_server import A2A_RPC_PATH, build_explainer_app
from touchline.orchestration.workflow import (
    STAGE_EXPLAINER,
    STAGE_GROUNDING,
    MatchWorkflow,
    build_explainer_client,
)
from touchline.providers.clients import TemplateChatClient
from touchline.sim.generator import simulate, team_names

# The veto logs a warning for every claim it refuses, which is the right
# behaviour in the service and far too much noise in a report.
logging.getLogger("touchline").setLevel(logging.ERROR)


class _LyingClient:
    """A client that states every number wrongly.

    The template engine answers correctly by construction, so editing its
    reply is the honest way to make a model misbehave on purpose: the
    shape of the answer is the real one, and only the number moves.
    """

    def __init__(self) -> None:
        self._inner = TemplateChatClient()

    async def get_response(self, messages: Any, **kwargs: Any) -> Any:
        """Answer, with every metric value replaced."""
        from agent_framework import ChatResponse, Message

        response = await self._inner.get_response(messages, **kwargs)
        payload = json.loads(response.messages[0].text)
        for metric in payload.get("metrics", []):
            metric["value"] = 66.0
        return ChatResponse(
            messages=[Message(role="assistant", contents=[json.dumps(payload)])]
        )


def _workflow(seed: int, **kwargs: Any) -> MatchWorkflow:
    """A workflow over the template engine."""
    kwargs.setdefault("analyst", MatchAnalyst(team_names=team_names(seed)))
    return MatchWorkflow(**kwargs)


async def _run(seed: int, **kwargs: Any) -> Any:
    """Run a whole match through the graph."""
    workflow = _workflow(seed, **kwargs)
    return await workflow.run(simulate(seed=seed), match_id=f"seed-{seed}")


def main() -> int:
    """Run the probe, print the report, and return an exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--a2a",
        action="store_true",
        help="Also make the hop over A2A, against the ASGI application.",
    )
    args = parser.parse_args()

    events = simulate(seed=args.seed)
    by_id = {event.event_id: event for event in events}

    # -- the feed, and the grounding -------------------------------------
    started = time.perf_counter()
    run = asyncio.run(_run(args.seed))
    elapsed = time.perf_counter() - started

    print(f"seed {args.seed}: {len(events)} events")
    print(
        f"  {len(run.candidates)} insights found, {len(run.overlays)} on the feed, "
        f"{len(run.trace)} hand-offs, {elapsed:.2f}s"
    )
    print(
        f"  {len(run.verdicts)} verdicts, {len(run.vetoes)} vetoes, "
        f"{len(run.degradations)} degradations"
    )
    print()

    failures: list[str] = []
    for overlay in run.overlays:
        unknown = [eid for eid in overlay.cited_event_ids if eid not in by_id]
        if unknown:
            failures.append(f"{overlay.overlay_id}: cites unknown events {unknown}")
            continue
        cited = [by_id[eid] for eid in overlay.cited_event_ids]
        for metric in overlay.metrics:
            recomputed = compute_metric(metric.name, cited, metric.side)
            if recomputed is None:
                failures.append(
                    f"{overlay.overlay_id}: {metric.name} cannot be recomputed"
                )
            elif abs(recomputed - metric.value) > 0.01:
                failures.append(
                    f"{overlay.overlay_id}: {metric.name} is {metric.value}, "
                    f"the log says {recomputed}"
                )

    print("grounding: every number on the feed recomputes from its citations")
    if failures:
        for failure in failures[:10]:
            print(f"  FAIL {failure}")
        print(f"  {len(failures)} failures")
    else:
        print(f"  OK -- {len(run.overlays)} overlays, every number reproduces")

    if run.overlays:
        first = run.overlays[0]
        print()
        print("first overlay:")
        print(f"  title:  {first.title}")
        print(f"  body:   {first.body}")
        print(f"  cites:  {', '.join(first.cited_event_ids)}")
        for metric in first.metrics:
            print(f"  metric: {metric.label} {metric.value:g}{metric.unit}")
    print()

    # -- the veto --------------------------------------------------------
    lying = asyncio.run(_run(args.seed, explainer=ExplainerAgent(_LyingClient())))

    refusals = [
        handoff
        for handoff in lying.trace
        if handoff.accepted is False
        and handoff.from_stage == STAGE_GROUNDING
        and handoff.to_stage == STAGE_EXPLAINER
    ]
    print("the veto, with a client that states the wrong numbers:")
    print(
        f"  {len(lying.vetoes)} claims refused, "
        f"{len(refusals)} refusals in the trace"
    )
    if refusals:
        print(f"  first refusal: {refusals[0].summary}")
    print(f"  {len(lying.overlays)} overlays still on the feed afterwards")
    print(f"  {len(lying.degradations)} degradations recorded")
    if not lying.vetoes:
        print("  FAIL -- the wrong claim was not refused")
    if not lying.overlays:
        print("  FAIL -- the feed stopped")
    print()

    # -- the hop, optionally ----------------------------------------------
    if args.a2a:
        import httpx

        async def hop() -> tuple[Any, Any]:
            explainer = ExplainerAgent(TemplateChatClient())
            app = build_explainer_app(
                explainer, url=f"http://testserver{A2A_RPC_PATH}"
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as http:
                card = await http.get("/.well-known/agent-card.json")
                client = build_explainer_client(
                    explainer,
                    url=f"http://testserver{A2A_RPC_PATH}",
                    http_client=http,
                )
                hop_run = await _run(
                    args.seed,
                    explainer=explainer,
                    explainer_client=client,
                )
                return card, client, hop_run

        card, client, hop_run = asyncio.run(hop())
        body = card.json()
        print("the hop, over A2A:")
        print(f"  agent card: {card.status_code}, {body['name']}")
        print(f"  skills: {', '.join(s['id'] for s in body['skills'])}")
        print(f"  transport: {client.last_transport}")
        print(
            f"  {len(hop_run.overlays)} overlays, "
            f"{len(client.degradations)} degradations"
        )
        if client.last_transport != "a2a":
            print("  FAIL -- the hop did not go over the wire")
        print()

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
