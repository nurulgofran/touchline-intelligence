"""The Explainer, published as an A2A agent.

A2A -- Agent2Agent -- is the protocol that lets one agent ask another agent
a question over the wire, in a shape both sides agree on without sharing any
code. The Explainer is published that way: it has an **agent card**, it
answers JSON-RPC, and the card says what it can do.

The card is not decoration. It is the thing a client reads to decide whether
to call, what to send, and what will come back. So it is built to the spec:
a protocol version, at least one skill with an id, a name, a description and
the input and output modes it works in. The A2A SDK warns when a required
field is missing, and this module is written so that it never warns.

Two routes are served:

``/.well-known/agent-card.json``
    The public card.

``/a2a``
    The JSON-RPC endpoint.

The server is mounted inside the same FastAPI application as the studio
view, on the same process that holds the shared state. That is deliberate:
the Explainer *remembers* what it has already said, and the Narrator asks it
for the same reading a moment later over A2A. A second process would be a
second memory, and the second question would cost a second model call for
an answer that already existed.
"""

from __future__ import annotations

from typing import Any

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH, PROTOCOL_VERSION_CURRENT

from touchline.agents.explainer import ExplainerAgent

__all__ = [
    "A2A_RPC_PATH",
    "AGENT_CARD_PATH",
    "build_agent_card",
    "build_explainer_app",
    "explainer_card",
]

#: Where the agent card is published. This is the path the A2A specification
#: fixes, so a client can find the card without being told where it is.
AGENT_CARD_PATH = AGENT_CARD_WELL_KNOWN_PATH

#: Where the JSON-RPC endpoint is served.
A2A_RPC_PATH = "/a2a"

#: The card's version. It moves when the skill's shape moves.
CARD_VERSION = "1.0.0"

#: The skill the card publishes: one skill, one promise.
_EXPLAIN_SKILL_ID = "explain-insight"


def build_agent_card(
    *,
    url: str,
    name: str = "Touchline Explainer",
    description: str = (
        "Explains one deterministic football insight and states the claim it "
        "rests on: the numbers, the side they describe, and the ids of the "
        "events they were computed from."
    ),
    version: str = CARD_VERSION,
    documentation_url: str | None = None,
) -> AgentCard:
    """Build the Explainer's agent card.

    Args:
        url: The URL the card is served from, and the URL a client should
            call. The JSON-RPC endpoint.
        name: The agent's name.
        description: The agent's description.
        version: The card's version.
        documentation_url: Where the documentation lives, if anywhere.

    Returns:
        A card that satisfies the A2A specification: a protocol version on
        the interface, and at least one fully-described skill.
    """
    card = AgentCard(
        name=name,
        description=description,
        version=version,
        supported_interfaces=[
            AgentInterface(
                url=url,
                protocol_binding="JSONRPC",
                protocol_version=PROTOCOL_VERSION_CURRENT,
            )
        ],
        capabilities=AgentCapabilities(streaming=False, push_notifications=False),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[
            AgentSkill(
                id=_EXPLAIN_SKILL_ID,
                name="Explain a match insight",
                description=(
                    "Given one insight, return a reading of what happened and "
                    "why it matters, together with the metrics it rests on "
                    "and the event ids those metrics were computed from."
                ),
                tags=["football", "insight", "explanation"],
                examples=[
                    "Explain cand-0007 in English for the analyst audience."
                ],
                input_modes=["text/plain"],
                output_modes=["text/plain"],
            )
        ],
    )
    if documentation_url:
        card.documentation_url = documentation_url
    return card


def explainer_card(*, url: str, **kwargs: Any) -> AgentCard:
    """Build the Explainer's card. A named alias of :func:`build_agent_card`."""
    return build_agent_card(url=url, **kwargs)


def build_explainer_app(
    explainer: ExplainerAgent,
    *,
    url: str,
    card: AgentCard | None = None,
) -> Any:
    """Build the ASGI application that serves the Explainer over A2A.

    Args:
        explainer: The agent to serve. It is the same object the workflow
            uses, so the memory it keeps is shared with the rest of the
            pipeline.
        url: The URL the card is served from.
        card: A card to publish instead of the default one.

    Returns:
        A Starlette application serving the card and the JSON-RPC endpoint.
    """
    from agent_framework.a2a import A2AExecutor
    from starlette.applications import Starlette

    public_card = card or build_agent_card(url=url)

    request_handler = DefaultRequestHandler(
        agent_executor=A2AExecutor(explainer, stream=False),
        task_store=InMemoryTaskStore(),
        agent_card=public_card,
    )

    return Starlette(
        routes=[
            *create_agent_card_routes(public_card, card_url=AGENT_CARD_PATH),
            *create_jsonrpc_routes(request_handler, A2A_RPC_PATH),
        ]
    )
