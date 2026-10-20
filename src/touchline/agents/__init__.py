"""The three model-driven agents.

Three of the five stages of the pipeline are agents, and the fourth is a
deterministic worker and the fifth is arithmetic. The agents are:

``Explainer``
    Says what happened and why it matters, and states the claim it rests on.
    The claim is what the grounding step checks.

``Narrator``
    Writes the words for the screen, and asks the Explainer over A2A for the
    reading it writes from.

``Personalizer``
    Rewrites that copy for one audience, and assembles the overlay -- with
    the analyst's facts and the template engine's closing line around the
    model's interpretation.

Every one of them is an Agent Framework agent, so every one of them runs on
whichever chat client it is given: Microsoft Foundry in the deployed demo, the
template engine before any Azure credentials exist, and the template engine
again whenever inference is unavailable.

There is one more module here that is not an agent: :mod:`touchline.agents.figures`,
the guard that checks the *prose* for figures the event log does not hold. The
grounding step checks the claim; this checks the sentence around it.
"""

from touchline.agents.explainer import (
    ExplainerAgent,
    ExplanationError,
    align_claim_sides,
    build_explainer,
    explanation_to_dict,
    parse_explanation,
)
from touchline.agents.figures import (
    allowed_figures,
    check_figures,
    unsupported_figures,
)
from touchline.agents.narrator import (
    NarrationError,
    NarratorAgent,
    build_narrator,
    deterministic_narration,
    parse_narration,
)
from touchline.agents.personalizer import (
    PersonalisationError,
    PersonalizerAgent,
    build_personalizer,
    personalise_feed,
)

__all__ = [
    "ExplainerAgent",
    "ExplanationError",
    "NarrationError",
    "NarratorAgent",
    "PersonalisationError",
    "PersonalizerAgent",
    "align_claim_sides",
    "allowed_figures",
    "build_explainer",
    "build_narrator",
    "build_personalizer",
    "check_figures",
    "deterministic_narration",
    "explanation_to_dict",
    "parse_explanation",
    "parse_narration",
    "personalise_feed",
    "unsupported_figures",
]
