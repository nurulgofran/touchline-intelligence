"""Tests for the grounding-recovery fixture generator.

Everything here runs against template/fake clients — no Foundry, no
network. The point under test is the generator *mechanics*: the fault is
injected exactly once, the real MatchWorkflow vetoes it, routes the
objection back, verifies the correction, and the fixture is only written
when that whole story holds. A genuine Foundry-backed fixture is produced
by running the generator itself on a machine with Foundry credentials.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from generate_recovery_fixture import (
    DEFAULT_FAULT_DELTA,
    FaultInjectingClient,
    FixtureError,
    build_fixture,
)

from touchline.metrics.analyst import MatchAnalyst
from touchline.providers.clients import TemplateChatClient, candidate_payload
from touchline.providers.payload import TASK_EXPLAIN
from touchline.sim.generator import simulate, team_names

FIXTURE_DIR = Path(__file__).resolve().parent
REPO_ROOT = FIXTURE_DIR.parents[1]


def _target_candidate() -> object:
    """The first feed insight of the 20-minute seed-42 slice."""
    events = [e for e in simulate(seed=42) if e.match_clock_ms <= 20 * 60 * 1000]
    analyst = MatchAnalyst(team_names=team_names(42))
    feed = analyst.select_for_feed(analyst.analyse(events))
    assert feed, "the slice produced no feed insights"
    return feed[0]


def _user_message(text: str) -> object:
    from agent_framework import Message

    return Message(role="user", contents=[text])


def test_fault_injector_corrupts_only_the_first_attempt():
    """The first claim about the target moves one metric; the retry is honest."""
    import asyncio

    candidate = _target_candidate()
    inner = TemplateChatClient(record=False)
    injector = FaultInjectingClient(
        inner, target_candidate_id=candidate.candidate_id
    )

    first_prompt = candidate_payload(TASK_EXPLAIN, candidate, reading="")
    retry_prompt = candidate_payload(
        TASK_EXPLAIN, candidate, reading="distance: claimed 99, recomputed 12"
    )

    async def go() -> tuple[str, str]:
        first = await injector.get_response([_user_message(first_prompt)])
        retry = await injector.get_response([_user_message(retry_prompt)])
        return first.messages[0].text, retry.messages[0].text

    first_text, retry_text = asyncio.run(go())

    first = json.loads(first_text)["metrics"][0]
    retry = json.loads(retry_text)["metrics"][0]
    assert first["value"] == pytest.approx(retry["value"] + DEFAULT_FAULT_DELTA)
    assert injector.fault is not None
    assert injector.fault["metric"] == first["name"]
    assert injector.fault["candidate_id"] == candidate.candidate_id
    assert [a["corrupted"] for a in injector.attempts] == [True, False]


def test_fault_injector_leaves_other_candidates_alone():
    """Only the target candidate's first claim is touched."""
    import asyncio

    candidate = _target_candidate()
    inner = TemplateChatClient(record=False)
    injector = FaultInjectingClient(
        inner, target_candidate_id="cand-does-not-exist"
    )

    prompt = candidate_payload(TASK_EXPLAIN, candidate, reading="")

    async def go() -> str:
        response = await injector.get_response([_user_message(prompt)])
        return response.messages[0].text

    text = asyncio.run(go())
    assert injector.fault is None
    assert injector.attempts[0]["corrupted"] is False
    # The honest template answer survives untouched.
    honest = json.loads(text)["metrics"][0]["value"]
    assert honest == pytest.approx(candidate.metrics[0].value)


def test_build_fixture_records_a_clean_recovery():
    """The real workflow vetoes, routes back, verifies, and accepts."""
    fixture = build_fixture(
        provider="template",
        seed=42,
        minutes=20,
        target_index=0,
        fault_delta=DEFAULT_FAULT_DELTA,
        max_grounding_attempts=2,
        command="pytest",
    )

    assert fixture["kind"] == "recorded-test-run"
    assert "RECORDED TEST RUN" in fixture["label"]
    assert "live incident" in fixture["label"].lower()

    fault = fixture["injected_fault"]
    veto = fixture["recovery"]["veto"]
    assert veto["accepted"] is False
    assert veto["reasons"], "the veto must carry specific reasons"
    assert any(fault["metric"] in reason for reason in veto["reasons"])
    assert veto["candidate_id"] == fault["candidate_id"]

    back_edge = fixture["recovery"]["veto_trace_entry"]
    assert back_edge["from_stage"] == "grounding"
    assert back_edge["to_stage"] == "explainer"
    assert back_edge["accepted"] is False

    acceptance = fixture["recovery"]["acceptance"]
    assert acceptance["accepted"] is True
    assert acceptance["candidate_id"] == fault["candidate_id"]

    invalid = fixture["recovery"]["invalid_claim"]["metrics"]
    corrected = fixture["recovery"]["corrected_claim"]["metrics"]
    by_name = {m["name"]: m["value"] for m in corrected}
    corrupted = next(m for m in invalid if m["name"] == fault["metric"])
    assert corrupted["value"] == pytest.approx(
        by_name[fault["metric"]] + DEFAULT_FAULT_DELTA
    )

    overlay = fixture["recovery"]["overlay"]
    assert overlay["overlay_id"].endswith(fault["candidate_id"])

    assert fixture["degradations"] == []
    assert fixture["providers"]["template_fallback_used"] is False
    assert fixture["providers"]["correction_provider"] == "Template engine"

    kinds = [entry["kind"] for entry in fixture["trace"]]
    assert "claim" in kinds and "veto" in kinds and "overlay" in kinds


def test_build_fixture_refuses_a_degraded_run():
    """One grounding attempt means the fallback fires — no fixture is written."""
    with pytest.raises(FixtureError):
        build_fixture(
            provider="template",
            seed=42,
            minutes=20,
            target_index=0,
            fault_delta=DEFAULT_FAULT_DELTA,
            max_grounding_attempts=1,
            command="pytest",
        )


def test_fixture_validates_against_its_schema():
    """The produced fixture satisfies recovery_fixture.schema.json."""
    jsonschema = pytest.importorskip("jsonschema")
    fixture = build_fixture(
        provider="template",
        seed=42,
        minutes=20,
        target_index=0,
        fault_delta=DEFAULT_FAULT_DELTA,
        max_grounding_attempts=2,
        command="pytest",
    )
    schema = json.loads(
        (FIXTURE_DIR / "recovery_fixture.schema.json").read_text()
    )
    jsonschema.validate(instance=fixture, schema=schema)


def test_schema_document_is_well_formed():
    """The schema is valid JSON Schema and pins the honesty fields."""
    schema = json.loads(
        (FIXTURE_DIR / "recovery_fixture.schema.json").read_text()
    )
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["properties"]["kind"]["const"] == "recorded-test-run"
    assert (
        schema["properties"]["degradations"]["maxItems"] == 0
    ), "a valid fixture must have no degradations"
    assert "injected_fault" in schema["required"]
