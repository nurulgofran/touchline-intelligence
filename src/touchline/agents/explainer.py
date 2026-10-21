"""The Explainer: why this moment matters, and what it rests on.

The Explainer is the first of the three model-driven agents, and it is the
one that carries the risk. Everything downstream inherits its numbers, so if
the Explainer is allowed to state a figure without checking it, the whole
feed is a rumour.

So the Explainer does not assert. It *claims*: it returns the reading, and
alongside it the numbers and the event ids it says the reading rests on.
The grounding step then recomputes those numbers from the log, and a claim
that does not reproduce is vetoed and sent back here.

The agent is an Agent Framework ``Agent``, so it runs on whichever chat
client it is given: Microsoft Foundry in the deployed demo, the template
engine before any Azure credentials exist, and the template engine again
whenever inference is unavailable.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from touchline.models.claims import ClaimedBaseline, ClaimedMetric, Explanation
from touchline.models.events import TeamSide
from touchline.models.insights import InsightCandidate
from touchline.providers import attribution
from touchline.providers.payload import (
    TASK_EXPLAIN,
    decode_payload,
    encode_payload,
    parse_json_object,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ExplainerAgent",
    "ExplanationError",
    "align_claim_sides",
    "build_explainer",
    "explanation_to_dict",
    "parse_explanation",
]

#: The rules the Explainer works under.
EXPLAINER_INSTRUCTIONS = """\
You are the Explainer in a football analysis system.

You are given a deterministic insight that has already been computed from the
event log. You explain what happened and why it matters, in the language
named in the payload.

Rules:
- Use only the numbers given to you. Never invent a number, never change one,
  and never round one to a different figure.
- Cite only the event ids given to you.
- Reply with one JSON object and nothing else, in exactly this shape:
  {"reading": "...", "metrics": [{"name": "...", "value": 0, "unit": "...",
   "side": "HOME"}], "cited_event_ids": ["..."], "baseline": null}
- "reading" is one or two plain sentences: what happened, and what it means
  for the match. Do not put ids, metric names or numbers from the payload
  into it beyond the figures it is about.
- "metrics" must list every metric the payload gives you, with the same
  values, and "cited_event_ids" must list the ids the payload gives you.
- "baseline" is null unless the payload carries a baseline, in which case it
  repeats that baseline's metric, value, unit, side and cited event ids.
"""


class ExplanationError(RuntimeError):
    """Raised when the Explainer's reply cannot be read as a claim."""


def align_claim_sides(
    candidate: InsightCandidate, explanation: Explanation
) -> Explanation:
    """Fill in a claim's missing *side* from the candidate it is about.

    A metric that is about one side is about that side whether or not the
    model bothered to say so, and a claim that names the figure but leaves
    the side out is an **incomplete** claim, not a wrong one. So the side is
    filled in, and the claim then reproduces.

    What is deliberately *not* filled in is a side that conflicts. A claim
    that says the away side's figure where the candidate's figure is the
    home side's is a wrong claim, and a wrong claim has to be vetoed and
    shown, not tidied up in the agent's own defence.

    Args:
        candidate: The deterministic, fully-cited insight.
        explanation: The claim as the agent stated it.

    Returns:
        The claim, with any missing side filled in. The claim is returned
        unchanged when there was nothing to fill in.
    """
    sides = {metric.name: metric.side for metric in candidate.metrics}
    aligned: list[ClaimedMetric] = []
    changed = False

    for metric in explanation.metrics:
        side = sides.get(metric.name)
        if metric.side is None and side is not None:
            aligned.append(metric.model_copy(update={"side": side}))
            changed = True
        else:
            aligned.append(metric)

    if not changed:
        return explanation
    return explanation.model_copy(update={"metrics": aligned})


def explanation_to_dict(explanation: Explanation) -> dict[str, Any]:
    """Render a claim as the JSON the agent card's skill promises.

    The A2A skill publishes one shape, so the client on the other side of
    the wire and the agent behind it agree on it by construction.
    """
    baseline = explanation.baseline
    return {
        "candidate_id": explanation.candidate_id,
        "reading": explanation.reading,
        "metrics": [
            {
                "name": metric.name,
                "value": metric.value,
                "unit": metric.unit,
                "side": metric.side.value if metric.side else None,
            }
            for metric in explanation.metrics
        ],
        "cited_event_ids": list(explanation.cited_event_ids),
        "baseline": (
            {
                "metric_name": baseline.metric_name,
                "value": baseline.value,
                "unit": baseline.unit,
                "side": baseline.side.value if baseline.side else None,
                "cited_event_ids": list(baseline.cited_event_ids),
            }
            if baseline is not None
            else None
        ),
    }


def parse_explanation(text: str, *, candidate_id: str) -> Explanation:
    """Read a claim out of a reply.

    Args:
        text: The reply. It should be one JSON object; a reply that wraps
            the object in prose or a code fence is still read.
        candidate_id: Which candidate the reply is about.

    Returns:
        The claim.

    Raises:
        ExplanationError: If the reply carries no reading.
    """
    payload = parse_json_object(text)
    if not payload:
        raise ExplanationError(
            f"the Explainer's reply for {candidate_id} is not JSON: {text[:120]!r}"
        )

    reading = str(payload.get("reading", "")).strip()
    if not reading:
        raise ExplanationError(
            f"the Explainer's reply for {candidate_id} has no reading"
        )

    ids = payload.get("cited_event_ids")
    cited = [str(item) for item in ids] if isinstance(ids, list) else []

    return Explanation(
        candidate_id=str(payload.get("candidate_id") or candidate_id),
        reading=reading,
        metrics=_claimed_metrics(payload.get("metrics")),
        cited_event_ids=cited,
        baseline=_claimed_baseline(payload.get("baseline")),
    )


def _side(value: Any) -> TeamSide | None:
    """Read a side out of a model's reply."""
    if not value:
        return None
    try:
        return TeamSide(str(value).upper())
    except ValueError:
        return None


def _claimed_metrics(raw: Any) -> list[ClaimedMetric]:
    """Read the claimed metrics out of a model's reply."""
    if not isinstance(raw, list):
        return []
    metrics: list[ClaimedMetric] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        raw_value = item.get("value")
        if raw_value is None:
            continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        metrics.append(
            ClaimedMetric(
                name=name,
                value=value,
                unit=str(item.get("unit", "") or ""),
                side=_side(item.get("side")),
            )
        )
    return metrics


def _claimed_baseline(raw: Any) -> ClaimedBaseline | None:
    """Read the claimed baseline out of a model's reply."""
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("metric_name", "")).strip()
    if not name:
        return None
    raw_value = raw.get("value")
    if raw_value is None:
        return None
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return None
    ids = raw.get("cited_event_ids")
    return ClaimedBaseline(
        metric_name=name,
        value=value,
        unit=str(raw.get("unit", "") or ""),
        side=_side(raw.get("side")),
        cited_event_ids=[str(item) for item in ids] if isinstance(ids, list) else [],
    )


class ExplainerAgent:
    """Explains an insight, and states what the explanation rests on."""

    def __init__(self, client: Any, *, name: str = "explainer") -> None:
        """Create the agent.

        Args:
            client: The chat client to answer on. Microsoft Foundry in the
                deployed demo; the template engine otherwise.
            name: The agent's name, which is what an agent card publishes.
        """
        from agent_framework import Agent

        self._agent = Agent(
            client=client,
            instructions=EXPLAINER_INSTRUCTIONS,
            name=name,
            description="Explains a match insight and states the claim it rests on.",
        )

        # The Agent Framework's agent contract, restated on this class.
        # The A2A executor is handed *this* object rather than the
        # framework agent underneath it, because the Explainer's memory
        # is the reason the A2A hop is worth making: the claim that comes
        # back over the wire is the claim that was already accepted.
        self.id: str = self._agent.id
        self.name: str | None = self._agent.name
        self.description: str | None = self._agent.description
        #: The last claim made for each candidate, by candidate id.
        #:
        #: The Explainer is a service, and a service remembers what it has
        #: already said. The Narrator asks it for the same reading over A2A
        #: a moment later, and the answer it gives is the answer it gave
        #: before -- which is both true and one model call cheaper.
        self._remembered: dict[tuple[str, str, str], Explanation] = {}

    @property
    def agent(self) -> Any:
        """The underlying Agent Framework agent, for the A2A server."""
        return self._agent

    async def explain(
        self,
        candidate: InsightCandidate,
        *,
        locale: str = "en",
        mode: str = "analyst",
        feedback: str = "",
    ) -> Explanation:
        """Explain one candidate.

        Args:
            candidate: The deterministic, fully-cited insight.
            locale: Language code for the reading.
            mode: Audience mode.
            feedback: Why the last attempt was vetoed. When it is set, the
                answer is worked out afresh rather than remembered, because
                the point is to answer the objection.

        Returns:
            The reading and the claim it rests on.

        Raises:
            ExplanationError: If the reply cannot be read as a claim.
        """
        key = (candidate.candidate_id, locale, mode)
        if not feedback and key in self._remembered:
            return self._remembered[key]

        prompt = encode_payload(
            TASK_EXPLAIN,
            candidate,
            locale=locale,
            mode=mode,
            reading=feedback,
        )
        # The call is attributed to this request: the stage and the
        # candidate travel in the task's context, so the provider status
        # can say which request each result belonged to.
        with attribution.request_context(
            stage="explainer", candidate_id=candidate.candidate_id
        ):
            response = await self._agent.run(prompt)
        text = getattr(response, "text", "") or ""
        explanation = self._parse(candidate, text)

        # The newest claim is the current one, and it is what the Explainer
        # remembers. A retry -- a claim written in answer to a veto -- is the
        # claim the pipeline is now working on, so it is the one the Narrator
        # must be given when it asks over A2A a moment later. Leaving the
        # vetoed draft in the memory would hand the Narrator a claim that
        # was already refused.
        self._remembered[key] = explanation
        return explanation

    def _parse(self, candidate: InsightCandidate, text: str) -> Explanation:
        """Read the claim out of the reply, and complete an incomplete one."""
        explanation = parse_explanation(text, candidate_id=candidate.candidate_id)
        return align_claim_sides(candidate, explanation)

    def remember(
        self,
        candidate: InsightCandidate,
        explanation: Explanation,
        *,
        locale: str = "en",
        mode: str = "analyst",
    ) -> None:
        """Put a claim into the memory, so the next question is cheap."""
        self._remembered[(candidate.candidate_id, locale, mode)] = explanation

    def forget(self, candidate_id: str) -> None:
        """Drop every claim remembered about one candidate."""
        for key in [key for key in self._remembered if key[0] == candidate_id]:
            del self._remembered[key]

    # -- the A2A surface ---------------------------------------------------

    def create_session(self, *, session_id: str | None = None) -> Any:
        """Create a session. Part of the Agent Framework's agent contract.

        The A2A executor asks the agent for one of these before it runs it.
        """
        from agent_framework import AgentSession

        return AgentSession(session_id=session_id)

    def get_session(
        self, service_session_id: Any, *, session_id: str | None = None
    ) -> Any:
        """Return a session. The other half of the agent contract.

        The Explainer keeps no conversation of its own -- its memory is
        per-candidate, not per-thread -- so this hands back the session
        the framework made.
        """
        return self._agent.get_session(service_session_id, session_id=session_id)

    def run(
        self,
        messages: str | Any = None,
        *,
        stream: bool = False,
        session: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Answer one request, given as a payload.

        This is what the A2A server calls. The request is a prompt in the
        project's payload format, so the same agent answers over HTTP and
        in-process, and the two answers are the same answer.

        The method is not declared ``async`` on purpose. The Agent
        Framework's agent contract offers two shapes for ``run`` -- a
        coroutine for a complete answer, and a stream for a partial one --
        and the Explainer only has the first. Returning the coroutine
        rather than being an ``async def`` keeps one method honest under
        both, and the caller awaits it either way.

        Args:
            messages: The prompt text.
            stream: Unused. The agent does not stream: a claim is only
                readable whole.
            session: The conversation so far. Unused: the Explainer's memory
                is its own.
            **kwargs: Ignored.

        Returns:
            A coroutine resolving to the agent response, carrying the claim
            as JSON.

        Raises:
            ExplanationError: If the prompt carries no usable payload, or
                the reply cannot be read as a claim.
        """
        return self._run(messages)

    async def _run(self, messages: str | Any) -> Any:
        """Answer one request. The body of :meth:`run`."""
        text = messages if isinstance(messages, str) else str(messages)
        try:
            payload = decode_payload(text)
        except ValueError as exc:
            raise ExplanationError(
                f"the request carries no usable payload: {exc}"
            ) from exc

        explanation = await self.explain(
            payload.candidate,
            locale=payload.locale,
            mode=payload.mode,
            feedback=payload.reading,
        )
        answer = json.dumps(explanation_to_dict(explanation), ensure_ascii=False)

        from agent_framework import AgentResponse, Message

        return AgentResponse(
            messages=[Message(role="assistant", contents=[answer])]
        )


def build_explainer(client: Any) -> ExplainerAgent:
    """Build the Explainer on a chat client."""
    return ExplainerAgent(client)
