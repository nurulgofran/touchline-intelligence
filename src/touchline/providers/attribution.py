"""Which request did a provider call belong to?

Which provider served a call is recorded globally -- see
:mod:`touchline.providers.status` -- but *which request* a call belonged
to is request-local: the pipeline stage that made it, the insight
candidate it was about, and the audience revision it was written for.
Two rewrites for two audiences must never mix attribution, so the request
context travels in a :class:`contextvars.ContextVar`: each asyncio task
sees its own copy, and a task that sets its context cannot change what a
concurrent task sees.

The pattern is: set the context around the model call, and the provider
clients stamp every recorded call with whatever the context holds at that
moment. The engine sets the audience revision around a whole analysis
run; the agents narrow the stage and the candidate around their own
call, inheriting the revision.
"""

from __future__ import annotations

import contextlib
import contextvars
from collections.abc import Iterator
from dataclasses import dataclass

__all__ = [
    "RequestContext",
    "current",
    "request_context",
]


@dataclass(frozen=True)
class RequestContext:
    """Where a provider call belongs.

    Attributes:
        stage: The pipeline stage that made the call -- ``explainer``,
            ``narrator`` or ``personalizer`` -- or ``None`` when the call
            was not made for a stage.
        candidate_id: The insight the call was about, or ``None``.
        revision: The audience revision the call was written for, or
            ``None``.
    """

    stage: str | None = None
    candidate_id: str | None = None
    revision: int | None = None


#: The request context of this task. A task that sets it gets its own
#: copy; concurrent tasks are unaffected. The default is None rather than
#: a RequestContext instance: ruff's B039 forbids mutable ContextVar
#: defaults, and unset tasks read as an empty context.
_current: contextvars.ContextVar[RequestContext | None] = contextvars.ContextVar(
    "touchline_request_context", default=None
)


def current() -> RequestContext:
    """The request context of the calling task, or an empty one when unset."""
    context = _current.get()
    return context if context is not None else RequestContext()


@contextlib.contextmanager
def request_context(
    *,
    stage: str | None = None,
    candidate_id: str | None = None,
    revision: int | None = None,
) -> Iterator[RequestContext]:
    """Set the request context for the calling task.

    A field left as ``None`` is inherited from the context already in
    force, so an agent can narrow the stage and the candidate while
    keeping the audience revision the engine set around the run.

    Args:
        stage: The pipeline stage making the call.
        candidate_id: The insight the call is about.
        revision: The audience revision the call is written for.

    Yields:
        The context in force inside the block.
    """
    parent = current()
    context = RequestContext(
        stage=stage if stage is not None else parent.stage,
        candidate_id=(
            candidate_id if candidate_id is not None else parent.candidate_id
        ),
        revision=revision if revision is not None else parent.revision,
    )
    token = _current.set(context)
    try:
        yield context
    finally:
        _current.reset(token)
