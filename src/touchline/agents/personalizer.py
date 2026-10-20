"""The Personaliser: the same moment, written for a particular audience.

An analyst wants the deviation and the numbers behind it. A fan wants to know
what just happened. A player wants to know what it felt like. The insight is
the same one; the copy is not. That is what this agent does.

The assembly is the load-bearing part, and it is deliberately lopsided:

* The **headline and the numbers** come from the deterministic analyst and
  are put in first, unchanged. They are the facts.
* The **interpretation** comes from the model, and it is the only part the
  model is allowed to write.
* The **closing line** comes from the template engine, in the audience's
  register. It is deterministic because it makes a claim about the numbers
  above it, and that claim has to stay true.

So the figures a viewer reads are always the analyst's, recomputed from the
event log and checked by the grounding step. A model can interpret them; it
cannot restate them.

Because the modes are a property of the *overlay* and not of the insight, a
mode change re-runs this agent alone. The Explainer and the Narrator are not
asked again, so switching to "my team" costs one pass, not three.
"""

from __future__ import annotations

import logging
from typing import Any

from touchline.agents.figures import check_figures
from touchline.models.claims import Narration
from touchline.models.insights import InsightCandidate
from touchline.models.overlay import Overlay
from touchline.providers import template
from touchline.providers.payload import (
    TASK_PERSONALISE,
    encode_payload,
    parse_json_object,
)

logger = logging.getLogger(__name__)

__all__ = [
    "PERSONALISER_INSTRUCTIONS",
    "PersonalisationError",
    "PersonalizerAgent",
    "personalise_feed",
]

#: The rules the Personaliser works under.
PERSONALISER_INSTRUCTIONS = """\
You are the Personaliser in a football analysis system.

You are given a deterministic insight, a narration that has already been
written and checked, and an audience mode. You rewrite the narration for that
audience.

Rules:
- Write in the language named in the payload.
- Match the mode: an analyst explains what the numbers show, a fan reacts to
  what just happened, a player notices what it felt like on the pitch.
- The title is at most 60 characters. The body is at most 220 characters.
- Do not repeat the numbers. The headline and the figures are added to the
  overlay automatically, straight from the event log. Describe what the moment
  means; do not quote figures, percentages or clock times.
- Do not invent events, players or claims that are not in the payload.
- Reply with one JSON object and nothing else, in exactly this shape:
  {"title": "...", "body": "..."}
"""


class PersonalisationError(RuntimeError):
    """Raised when the Personaliser's reply cannot be read."""


class PersonalizerAgent:
    """Rewrites a narration for one audience, and assembles the overlay."""

    def __init__(
        self,
        client: Any,
        *,
        mode: str = "analyst",
        locale: str = "en",
        name: str = "personalizer",
    ) -> None:
        """Create the agent.

        Args:
            client: The chat client to write on.
            mode: The audience mode.
            locale: The language code for the prose.
            name: The agent's name.
        """
        from agent_framework import Agent

        self._agent = Agent(
            client=client,
            instructions=PERSONALISER_INSTRUCTIONS,
            name=name,
            description="Rewrites a match insight for a particular audience.",
        )
        self.mode = mode
        self.locale = locale
        self._degradations: list[str] = []

    @property
    def agent(self) -> Any:
        """The underlying Agent Framework agent."""
        return self._agent

    @property
    def degradations(self) -> list[str]:
        """Every time the Personaliser had to fall back on the narration.

        The copy the Personaliser writes is checked the same way the
        Narrator's is: a figure in the prose has to be a figure the event
        log already carries. When it is not, the narration's own copy is
        used instead, and the fallback is recorded rather than hidden.
        """
        return list(self._degradations)

    def set_audience(
        self, *, mode: str | None = None, locale: str | None = None
    ) -> None:
        """Change who the next overlays are written for.

        Args:
            mode: The new audience mode.
            locale: The new language code.
        """
        if mode is not None:
            self.mode = mode
        if locale is not None:
            self.locale = locale

    async def personalise(
        self,
        candidate: InsightCandidate,
        narration: Narration,
        *,
        mode: str | None = None,
        locale: str | None = None,
    ) -> Overlay:
        """Rewrite one narration and assemble the overlay that goes on screen.

        Args:
            candidate: The deterministic, fully-cited insight.
            narration: The copy that was written and checked.
            mode: The audience mode. Defaults to the agent's.
            locale: The language code. Defaults to the agent's.

        Returns:
            The overlay, carrying the candidate's metrics and citations.
        """
        resolved_mode = mode or self.mode
        resolved_locale = locale or self.locale

        prompt = encode_payload(
            TASK_PERSONALISE,
            candidate,
            locale=resolved_locale,
            mode=resolved_mode,
            reading=narration.body,
            extra={"NARRATION_TITLE": narration.title},
        )
        response = await self._agent.run(prompt)
        text = getattr(response, "text", "") or ""

        payload = parse_json_object(text)
        title = str(payload.get("title", "")).strip() or narration.title
        body = str(payload.get("body", "")).strip() or narration.body

        unsupported = check_figures(
            title, candidate, locale=resolved_locale
        ) + check_figures(body, candidate, locale=resolved_locale)
        if unsupported:
            reason = (
                "the Personaliser's copy carries figures the event log does "
                "not: " + ", ".join(f"{value:g}" for value in unsupported)
            )
            self._degradations.append(reason)
            logger.warning("%s; using the narration's copy instead", reason)
            title = narration.title
            body = narration.body

        facts = template.render_facts(candidate, locale=resolved_locale)
        closer = template.render_closer(resolved_mode, locale=resolved_locale)

        return template.build_overlay(
            candidate,
            title=title,
            body=f"{facts} {body} {closer}",
            locale=resolved_locale,
            mode=resolved_mode,
        )


async def personalise_feed(
    narrations: list[tuple[InsightCandidate, Narration]],
    agent: PersonalizerAgent,
    *,
    mode: str | None = None,
    locale: str | None = None,
) -> list[Overlay]:
    """Rewrite a whole feed for one audience.

    Args:
        narrations: The candidates and the copy written for each.
        agent: The personaliser to use.
        mode: The audience mode. Defaults to the agent's.
        locale: The language code. Defaults to the agent's.

    Returns:
        The overlays, in the order they were given.
    """
    overlays: list[Overlay] = []
    for candidate, narration in narrations:
        overlays.append(
            await agent.personalise(
                candidate, narration, mode=mode, locale=locale
            )
        )
    return overlays


def build_personalizer(
    client: Any, *, mode: str = "analyst", locale: str = "en"
) -> PersonalizerAgent:
    """Build the Personaliser on a chat client."""
    return PersonalizerAgent(client, mode=mode, locale=locale)
