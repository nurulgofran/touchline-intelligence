"""The Narrator: the words that go on screen.

The Explainer says what happened and why it matters. The Narrator says it in
a way a broadcast can use: a title, a body, and the one-line call a
commentator would make.

The Narrator is also the agent that makes the project's **A2A** hop real. It
does not explain the insight itself -- it *asks* the Explainer, over the
protocol, for the reading it is going to write from. That request goes out as
an agent-to-agent call to the Explainer's published endpoint, and the answer
comes back the same way. When the hop cannot be made, the Narrator falls back
to the reading it was already handed and says so, because a feed that quietly
changes its wording is a feed nobody can trust.

The body the Narrator writes is the **interpretive half only**. The headline
and the numbers are added afterwards, deterministically, by the personaliser.
So the Narrator can interpret the numbers but can never restate them wrongly:
the figures on screen are the analyst's, and the prose is only ever around
them.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from touchline.agents.figures import check_figures
from touchline.models.claims import Explanation, Narration
from touchline.models.insights import InsightCandidate
from touchline.providers import attribution, template
from touchline.providers import status as provider_status
from touchline.providers.payload import (
    TASK_NARRATE,
    encode_payload,
    parse_json_object,
)

logger = logging.getLogger(__name__)

__all__ = [
    "NARRATOR_INSTRUCTIONS",
    "NarrationError",
    "NarratorAgent",
    "deterministic_narration",
    "parse_narration",
]

#: The rules the Narrator works under.
NARRATOR_INSTRUCTIONS = """\
You are the Narrator in a football analysis system.

You are given a deterministic insight -- a headline, some numbers, and the
ids of the events behind them -- and a reading: an interpretation of what the
moment means, written by the Explainer. You write the words for an on-screen
overlay.

Rules:
- Write in the language named in the payload, and in the register the mode
  names: an analyst explains, a fan reacts, a player notices.
- The title is at most 60 characters. The body is at most 220 characters.
- The commentary is one line, the way a commentator would say it out loud.
- Do not repeat the numbers. The numbers are added to the overlay
  automatically and come from the event log. Describe what the moment means;
  do not quote figures, percentages or clock times.
- Do not invent events, players or claims that are not in the payload.
- Reply with one JSON object and nothing else, in exactly this shape:
  {"title": "...", "body": "...", "commentary": "..."}
"""

#: The longest title an overlay may carry.
TITLE_LIMIT = 60

#: The longest body an overlay may carry.
BODY_LIMIT = 220


class NarrationError(RuntimeError):
    """Raised when the Narrator's reply cannot be read as a narration."""


def _truncate(text: str, limit: int) -> str:
    """Cut a piece of copy to its limit, on a word boundary."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    space = cut.rfind(" ")
    if space > limit // 2:
        cut = cut[:space]
    return cut.rstrip() + "…"


def parse_narration(
    text: str,
    *,
    candidate_id: str,
    fallback_body: str = "",
) -> Narration:
    """Read a narration out of a reply.

    A reply that wraps the object in prose or a code fence is still read; a
    reply that is missing the body falls back to the reading the Narrator
    was given, because a feed with no words at all is worse than a feed with
    the interpretation it already had.

    Args:
        text: The reply.
        candidate_id: Which candidate the reply is about.
        fallback_body: The reading to fall back on.

    Returns:
        The narration.

    Raises:
        NarrationError: If the reply carries neither a body nor a fallback.
    """
    payload = parse_json_object(text)
    title = str(payload.get("title", "")).strip()
    body = str(payload.get("body", "")).strip()
    commentary = str(payload.get("commentary", "")).strip()

    if not body:
        body = fallback_body.strip()
    if not body:
        raise NarrationError(
            f"the Narrator's reply for {candidate_id} has no body"
        )

    return Narration(
        candidate_id=candidate_id,
        title=_truncate(title, TITLE_LIMIT),
        body=_truncate(body, BODY_LIMIT),
        commentary=_truncate(commentary, BODY_LIMIT),
    )


def deterministic_narration(
    candidate: InsightCandidate,
    *,
    locale: str = "en",
    mode: str = "analyst",
) -> Narration:
    """The narration the template engine renders, with no model involved.

    This is the copy that reaches the screen when the model's own copy is
    refused -- because it carried a figure the event log does not have, or
    because the model could not be reached at all. It is grounded by
    construction: every number in it comes out of the candidate.

    Args:
        candidate: The deterministic, fully-cited insight.
        locale: Language code for the prose.
        mode: Audience mode.

    Returns:
        The narration.
    """
    rendered = template.render_narration(candidate, locale=locale, mode=mode)
    return Narration(
        candidate_id=candidate.candidate_id,
        title=_truncate(str(rendered.get("title", "")), TITLE_LIMIT),
        body=_truncate(str(rendered.get("body", "")), BODY_LIMIT),
        commentary=_truncate(str(rendered.get("commentary", "")), BODY_LIMIT),
    )


class NarratorAgent:
    """Writes the words for an overlay, asking the Explainer over A2A."""

    def __init__(
        self,
        client: Any,
        *,
        explainer_client: Any = None,
        name: str = "narrator",
    ) -> None:
        """Create the agent.

        Args:
            client: The chat client to write on. Microsoft Foundry in the
                deployed demo; the template engine otherwise.
            explainer_client: The client that calls the Explainer over A2A.
                When it is absent the Narrator uses the reading it was
                handed, and the hop does not happen.
            name: The agent's name.
        """
        from agent_framework import Agent

        self._agent = Agent(
            client=client,
            instructions=NARRATOR_INSTRUCTIONS,
            name=name,
            description="Writes the words for a match overlay.",
        )
        self._explainer_client = explainer_client
        self._degradations: list[str] = []
        self._substitutions: list[str] = []

    @property
    def agent(self) -> Any:
        """The underlying Agent Framework agent."""
        return self._agent

    def set_explainer_client(self, client: Any) -> None:
        """Point the Narrator at a different Explainer client.

        The live engine builds its agents before the A2A server is mounted,
        because the server serves *that* Explainer -- the same object, so
        the memory it keeps is shared. The client that reaches it over the
        wire is built once the application exists, and is attached here.

        Args:
            client: The client the Narrator uses to reach the Explainer.
        """
        self._explainer_client = client

    @property
    def degradations(self) -> list[str]:
        """Every time the Narrator's A2A hop could not be made.

        This is only the call failing. A call that was served but whose
        copy carried a figure the event log does not hold is a
        substitution, not a failure, and it is recorded separately -- see
        :attr:`substitutions`.
        """
        return list(self._degradations)

    @property
    def substitutions(self) -> list[str]:
        """Every time validated prose was rejected and replaced.

        The grounding step checked the *claim*; the guard checks the prose
        around it. When the prose carries a figure the log does not hold,
        deterministic copy in the requested language and mode is used
        instead, and the substitution is recorded here and in the
        provider status, separately from model-call failures.
        """
        return list(self._substitutions)

    async def reading_for(
        self,
        candidate: InsightCandidate,
        explanation: Explanation,
        *,
        locale: str = "en",
        mode: str = "analyst",
    ) -> str:
        """Ask the Explainer, over A2A, for the reading to write from.

        Args:
            candidate: The deterministic, fully-cited insight.
            explanation: The reading the pipeline already has.
            locale: Language code for the reading.
            mode: Audience mode.

        Returns:
            The reading. When the hop fails it is the reading that was
            already in hand, and the failure is recorded.
        """
        client = self._explainer_client
        if client is None:
            return explanation.reading

        try:
            claimed = await client.explain(candidate, locale=locale, mode=mode)
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            self._degradations.append(reason)
            logger.warning(
                "the Narrator could not reach the Explainer over A2A (%s); "
                "writing from the reading already in hand",
                reason,
            )
            return explanation.reading

        return claimed.reading or explanation.reading

    async def narrate(
        self,
        candidate: InsightCandidate,
        explanation: Explanation,
        *,
        locale: str = "en",
        mode: str = "analyst",
        ask_explainer: bool = True,
    ) -> Narration:
        """Write the words for one insight.

        Args:
            candidate: The deterministic, fully-cited insight.
            explanation: The claim the grounding step accepted.
            locale: Language code for the prose.
            mode: Audience mode.
            ask_explainer: Whether to ask the Explainer over A2A for the
                reading to write from. It is off on the **veto** path: a
                claim that was refused has no reading worth asking for, and
                the Narrator writes from the deterministic interpretation
                instead.

        Returns:
            The title, the body and the commentary.

        Raises:
            NarrationError: If the reply carries no usable body.
        """
        reading = (
            await self.reading_for(candidate, explanation, locale=locale, mode=mode)
            if ask_explainer
            else explanation.reading
        )

        prompt = encode_payload(
            TASK_NARRATE,
            candidate,
            locale=locale,
            mode=mode,
            reading=reading,
        )
        # The call is attributed to this request: the stage and the
        # candidate travel in the task's context, so the provider status
        # can say which request each result belonged to.
        with attribution.request_context(
            stage="narrator", candidate_id=candidate.candidate_id
        ):
            response = await self._agent.run(prompt)
        text = getattr(response, "text", "") or ""
        narration = parse_narration(
            text,
            candidate_id=candidate.candidate_id,
            fallback_body=reading,
        )
        return self._guard(candidate, narration, locale=locale, mode=mode)

    def _guard(
        self,
        candidate: InsightCandidate,
        narration: Narration,
        *,
        locale: str,
        mode: str,
    ) -> Narration:
        """Replace a copy that carries a figure the log does not hold.

        The grounding step checked the *claim*. This checks the prose
        around it. A model handed a correct claim can still write a wrong
        number into the sentence that wraps it, and that sentence would
        reach the screen with the claim's good citation attached -- because
        the citation belongs to the claim and not to the sentence.

        So the sentence is checked too, and when it fails the deterministic
        copy is used instead. The reason is recorded, because a fallback
        that happens quietly is a fallback nobody trusts.
        """
        unsupported = check_figures(
            narration.title, candidate, locale=locale
        ) + check_figures(narration.body, candidate, locale=locale)
        if not unsupported:
            return narration

        reason = (
            "the Narrator's copy carries figures the event log does not: "
            + ", ".join(f"{value:g}" for value in unsupported)
        )
        self._substitutions.append(reason)
        provider_status.record_substitution()
        logger.warning("%s; using the template engine's copy instead", reason)
        return deterministic_narration(candidate, locale=locale, mode=mode)


def build_narrator(client: Any, *, explainer_client: Any = None) -> NarratorAgent:
    """Build the Narrator on a chat client."""
    return NarratorAgent(client, explainer_client=explainer_client)


def narration_to_dict(narration: Narration) -> dict[str, str]:
    """Render a narration as plain JSON data."""
    return json.loads(narration.model_dump_json())
