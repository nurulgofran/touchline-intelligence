"""The wire format between an agent and a chat client.

An agent does not call a model directly. It calls a **chat client**, and the
only thing the client receives is a list of messages. That means the request
has to carry everything the client needs in its text, in a form the client can
read back without guessing.

This module is that form. The payload is a handful of header lines followed by
the candidate as one line of JSON:

.. code-block:: text

    TASK: narrate
    LOCALE: en
    MODE: analyst
    CANDIDATE: {"candidate_id": "cand-0001", ...}
    READING: Northgate have broken the press ...

The JSON is written by :meth:`InsightCandidate.model_dump_json`, which is
compact and single-line, so the candidate is always exactly one line and the
reading -- which may run to several -- can safely come last.

Keeping this format in one module means the *no-model* client and the *model*
client read the same thing, so the template engine is a drop-in replacement
for Foundry rather than a different pipeline.
"""

from __future__ import annotations

import json
from typing import Any

from touchline.models.insights import InsightCandidate

__all__ = [
    "TASK_EXPLAIN",
    "TASK_NARRATE",
    "TASK_PERSONALISE",
    "Payload",
    "decode_payload",
    "encode_payload",
]

#: The agent asks for a reading: why the insight matters, and what it rests on.
TASK_EXPLAIN = "explain"

#: The agent asks for broadcast copy: a title and a body.
TASK_NARRATE = "narrate"

#: The agent asks for the final overlay in a mode and a language.
TASK_PERSONALISE = "personalise"

#: Every task a payload may carry.
TASKS: tuple[str, ...] = (TASK_EXPLAIN, TASK_NARRATE, TASK_PERSONALISE)


class PayloadError(ValueError):
    """Raised when a prompt does not carry a readable payload."""


class Payload:
    """A decoded request: what to do, and what to do it to."""

    __slots__ = ("candidate", "extra", "locale", "mode", "reading", "task")

    def __init__(
        self,
        task: str,
        candidate: InsightCandidate,
        *,
        locale: str = "en",
        mode: str = "analyst",
        reading: str = "",
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.task = task
        self.candidate = candidate
        self.locale = locale
        self.mode = mode
        self.reading = reading
        self.extra = extra or {}

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"Payload(task={self.task!r}, "
            f"candidate_id={self.candidate.candidate_id!r}, "
            f"locale={self.locale!r}, mode={self.mode!r})"
        )


def encode_payload(
    task: str,
    candidate: InsightCandidate,
    *,
    locale: str = "en",
    mode: str = "analyst",
    reading: str = "",
    extra: dict[str, Any] | None = None,
) -> str:
    """Write a payload into the text of a prompt.

    Args:
        task: One of :data:`TASKS`.
        candidate: The insight the agent is working on.
        locale: Language code for the requested prose.
        mode: Audience mode.
        reading: The reading the agent already has, if any.
        extra: Any further key/value pairs, one per line.

    Returns:
        The prompt text.
    """
    if task not in TASKS:
        raise PayloadError(f"unknown task {task!r}; expected one of {list(TASKS)}")

    lines = [
        f"TASK: {task}",
        f"LOCALE: {locale}",
        f"MODE: {mode}",
        f"CANDIDATE: {candidate.model_dump_json()}",
    ]
    for key, value in (extra or {}).items():
        lines.append(f"{key.upper()}: {value}")
    lines.append(f"READING: {reading}")
    return "\n".join(lines)


def decode_payload(text: str) -> Payload:
    """Read a payload back out of a prompt.

    The candidate is the only part that has to be exactly right: it is the
    evidence, and a client that mis-read it would narrate the wrong insight.
    Everything else falls back to a sane default rather than raising, because
    a missing mode should not stop the feed.

    Args:
        text: The prompt text.

    Returns:
        The decoded payload.

    Raises:
        PayloadError: If the prompt carries no candidate, or a candidate that
            does not validate.
    """
    task = ""
    locale = "en"
    mode = "analyst"
    reading = ""
    extra: dict[str, Any] = {}
    candidate_json = ""

    lines = text.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("CANDIDATE: "):
            candidate_json = stripped[len("CANDIDATE: ") :].strip()
        elif stripped.startswith("TASK: "):
            task = stripped[len("TASK: ") :].strip()
        elif stripped.startswith("LOCALE: "):
            locale = stripped[len("LOCALE: ") :].strip()
        elif stripped.startswith("MODE: "):
            mode = stripped[len("MODE: ") :].strip()
        elif stripped.startswith("READING: "):
            # The reading is always written last, and it may run to several
            # lines -- a reading is prose, and prose has paragraphs. So the
            # reading is everything after this line, not this line alone.
            reading = "\n".join(
                [stripped[len("READING: ") :], *lines[index + 1 :]]
            ).strip()
            break
        elif ": " in stripped:
            key, _, value = stripped.partition(": ")
            extra[key.strip().lower()] = value.strip()

    if not candidate_json:
        raise PayloadError("the prompt carries no CANDIDATE line")

    try:
        candidate = InsightCandidate.model_validate_json(candidate_json)
    except ValueError as exc:
        raise PayloadError(
            f"the CANDIDATE line is not a valid candidate: {exc}"
        ) from exc

    return Payload(
        task=task or TASK_NARRATE,
        candidate=candidate,
        locale=locale or "en",
        mode=mode or "analyst",
        reading=reading,
        extra=extra,
    )


def repair_truncated_json(text: str) -> str:
    """Close a JSON object that was cut off part-way through.

    A reply that runs out of output stops in the middle of a list or an
    object. The reading is written first, so it is intact and only the tail
    is missing -- and the tail is the part that can be checked anyway: the
    grounding step recomputes every number from the cited event ids, so a
    claim that lost its tail is refused rather than believed.

    Two things are repaired. A value that was only half written is dropped,
    because half an event id is not an event id. The open containers are
    then closed, which is what makes the text parse.

    Args:
        text: The reply, as the model wrote it.

    Returns:
        The reply with its open containers closed.
    """
    stack: list[str] = []
    in_string = False
    escaped = False
    string_start = 0
    cut = len(text)

    for index, character in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
            string_start = index
        elif character in "{[":
            stack.append("}" if character == "{" else "]")
        elif character in "}]":
            if stack:
                stack.pop()

    if in_string:
        # The string was cut in half. Drop it rather than keep half a value.
        cut = string_start

    head = text[:cut].rstrip()
    # A value that was cut off leaves its key and its comma behind, and
    # ``{"a":1,}`` is not JSON.
    if head.endswith(","):
        head = head[:-1].rstrip()
    if head.endswith(":"):
        # The key is there and the value never arrived. Drop the pair.
        head = head[: head.rfind(",")] if "," in head else ""

    return head + "".join(reversed(stack))


def parse_json_object(raw: str) -> dict[str, Any]:
    """Pull a JSON object out of a model's reply.

    The model is asked for JSON and usually complies. When it wraps the object
    in prose or a code fence the object is still recovered; when the reply was
    cut off part-way it is repaired and what arrived is read; when there is no
    object at all, an empty dict comes back and the caller decides what to do
    about it.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1] if "\n" in text else text
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
        text = text.strip()
        if text.startswith("json"):
            text = text[4:].strip()

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            # No closing brace at all: the reply was cut off. Close what is
            # open and read what arrived.
            repaired = repair_truncated_json(text)
            try:
                payload = json.loads(repaired)
            except json.JSONDecodeError:
                return {}
        else:
            try:
                payload = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                repaired = repair_truncated_json(text[start:])
                try:
                    payload = json.loads(repaired)
                except json.JSONDecodeError:
                    return {}

    return payload if isinstance(payload, dict) else {}
