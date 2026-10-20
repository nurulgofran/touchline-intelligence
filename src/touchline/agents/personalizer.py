"""The Personaliser: the same moment, written for a particular audience.

An analyst wants the deviation and the numbers behind it. A fan wants to know
what just happened. A player wants to know what it felt like. A supporter
wants their own side's numbers. A statistician wants one number and nothing
else. The insight is the same one; the copy is not. That is what this agent
does.

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

**The audience changes the facts, and only deterministically.** Before the
model is asked anything, the candidate is *projected* through the audience
(:mod:`touchline.agents.audience`): the my-team audience gets that side's
numbers over the same cited events, the player audience gets that player's
actions over the same cited events, the metric-lens audience gets one metric
recomputed from the same cited events. The projection is a pure function of
the cited events, so the numbers on an audience overlay are as checkable as
the analyst's own -- and the figure guard is pointed at the projected
candidate, so the model's prose is checked against the numbers the viewer
will actually see.

Because the modes are a property of the *overlay* and not of the insight, a
mode change re-runs this agent alone. The Explainer and the Narrator are not
asked again, so switching to "my team" costs one pass, not three.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from touchline.agents.audience import project_candidate
from touchline.agents.figures import check_figures
from touchline.models.audience import Audience, AudienceMode
from touchline.models.claims import Narration
from touchline.models.events import MatchEvent, TeamSide
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

The five audiences:
- analyst: explains what the numbers show, and the deviation behind them.
- fan: reacts to what just happened, in the voice of somebody watching.
- player: notices what it felt like from one player's place on the pitch.
- my_team: writes for a supporter, about their own side's numbers.
- metric_lens: writes about one number and what it says.

Rules:
- Write in the language named in the payload.
- Match the mode. The MODE line names the audience you are writing for.
- The title is at most 60 characters. The body is at most 220 characters.
- Do not repeat the numbers. The headline and the figures are added to the
  overlay automatically, straight from the event log. Describe what the moment
  means; do not quote figures, percentages or clock times.
- The numbers in the payload are the audience's own numbers. Do not describe
  a number that is not in the payload.
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
        team_names: Mapping[TeamSide, str] | None = None,
    ) -> None:
        """Create the agent.

        Args:
            client: The chat client to write on.
            mode: The audience mode.
            locale: The language code for the prose.
            name: The agent's name.
            team_names: The two fictional clubs, by side. The my-team
                audience names the supporter's club in its headline.
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
        self._side: TeamSide | None = None
        self._player_id: str | None = None
        self._player_name: str | None = None
        self._metric: str | None = None
        self._team_names: dict[TeamSide, str] = dict(team_names or {})
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

    @property
    def audience(self) -> Audience:
        """The audience this agent writes for by default."""
        return Audience(
            mode=AudienceMode(self.mode)
            if self.mode in {m.value for m in AudienceMode}
            else AudienceMode.ANALYST,
            locale=self.locale,
            side=self._side,
            player_id=self._player_id,
            player_name=self._player_name,
            metric=self._metric,
        )

    def set_audience(
        self,
        *,
        mode: str | None = None,
        locale: str | None = None,
        side: TeamSide | None = None,
        player_id: str | None = None,
        player_name: str | None = None,
        metric: str | None = None,
    ) -> None:
        """Change who the next overlays are written for.

        Args:
            mode: The new audience mode.
            locale: The new language code.
            side: Whose side, for the my-team audience.
            player_id: Which player, for the player audience.
            player_name: That player's name, for the headline.
            metric: Which metric, for the metric-lens audience.
        """
        if mode is not None:
            self.mode = mode
        if locale is not None:
            self.locale = locale
        self._side = side
        self._player_id = player_id
        self._player_name = player_name
        self._metric = metric

    async def personalise(
        self,
        candidate: InsightCandidate,
        narration: Narration,
        *,
        audience: Audience | None = None,
        events: Sequence[MatchEvent] = (),
        mode: str | None = None,
        locale: str | None = None,
        revision: int = 0,
    ) -> Overlay:
        """Rewrite one narration and assemble the overlay that goes on screen.

        Args:
            candidate: The deterministic, fully-cited insight.
            narration: The copy that was written and checked.
            audience: Who the overlay is written for. The agent's own
                audience is used when absent.
            events: The events the candidate cites. The audience reads
                exactly these, so the numbers it produces are recomputable
                from the overlay's own citations.
            mode: The audience mode. Overrides the audience's mode.
            locale: The language code. Overrides the audience's locale.
            revision: Which version of the overlay this is.

        Returns:
            The overlay, carrying the audience's metrics and the
            candidate's citations.
        """
        resolved = self._resolve_audience(audience, mode=mode, locale=locale)
        resolved_locale = resolved.locale
        resolved_mode = resolved.mode.value

        # The projection is deterministic: the audience reads the same cited
        # events, so every number on the overlay is recomputable from the
        # event ids the overlay carries.
        projected = project_candidate(
            candidate, resolved, events, team_names=self._team_names
        )

        prompt = encode_payload(
            TASK_PERSONALISE,
            projected,
            locale=resolved_locale,
            mode=resolved_mode,
            reading=narration.body,
            extra={
                "NARRATION_TITLE": narration.title,
                "AUDIENCE_MODE": resolved_mode,
                "AUDIENCE_SIDE": resolved.side.value if resolved.side else "-",
                "AUDIENCE_PLAYER": resolved.player_name or "-",
                "AUDIENCE_METRIC": resolved.metric or "-",
            },
        )
        response = await self._agent.run(prompt)
        text = getattr(response, "text", "") or ""

        payload = parse_json_object(text)
        title = str(payload.get("title", "")).strip() or narration.title
        body = str(payload.get("body", "")).strip() or narration.body

        unsupported = check_figures(
            title, projected, locale=resolved_locale
        ) + check_figures(body, projected, locale=resolved_locale)
        if unsupported:
            reason = (
                "the Personaliser's copy carries figures the event log does "
                "not: " + ", ".join(f"{value:g}" for value in unsupported)
            )
            self._degradations.append(reason)
            logger.warning("%s; using the narration's copy instead", reason)
            title = narration.title
            body = narration.body

        prefix = template.mode_title_prefix(
            resolved_mode, locale=resolved_locale, audience=resolved
        )
        facts = template.render_facts(projected, locale=resolved_locale)
        closer = template.render_closer(resolved_mode, locale=resolved_locale)

        return template.build_overlay(
            projected,
            title=prefix + title,
            body=f"{facts} {body} {closer}",
            locale=resolved_locale,
            mode=resolved_mode,
            revision=revision,
        )

    def _resolve_audience(
        self,
        audience: Audience | None,
        *,
        mode: str | None = None,
        locale: str | None = None,
    ) -> Audience:
        """The audience to write for, with the overrides applied."""
        resolved = self.audience if audience is None else audience
        if mode is None and locale is None:
            return resolved
        return resolved.model_copy(
            update={
                "mode": AudienceMode(mode) if mode else resolved.mode,
                "locale": locale or resolved.locale,
            }
        )


async def personalise_feed(
    narrations: list[tuple[InsightCandidate, Narration]],
    agent: PersonalizerAgent,
    *,
    audience: Audience | None = None,
    events: Sequence[MatchEvent] = (),
    mode: str | None = None,
    locale: str | None = None,
) -> list[Overlay]:
    """Rewrite a whole feed for one audience.

    Args:
        narrations: The candidates and the copy written for each.
        agent: The personaliser to use.
        audience: Who the feed is written for. The agent's own audience is
            used when absent.
        events: The events the candidates cite.
        mode: The audience mode. Overrides the audience's mode.
        locale: The language code. Overrides the audience's locale.

    Returns:
        The overlays, in the order they were given.
    """
    overlays: list[Overlay] = []
    for candidate, narration in narrations:
        overlays.append(
            await agent.personalise(
                candidate,
                narration,
                audience=audience,
                events=events,
                mode=mode,
                locale=locale,
            )
        )
    return overlays


def build_personalizer(
    client: Any,
    *,
    mode: str = "analyst",
    locale: str = "en",
    team_names: Mapping[TeamSide, str] | None = None,
) -> PersonalizerAgent:
    """Build the Personaliser on a chat client."""
    return PersonalizerAgent(
        client, mode=mode, locale=locale, team_names=team_names
    )
