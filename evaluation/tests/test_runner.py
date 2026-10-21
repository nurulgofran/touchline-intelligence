"""Tests for the runner mechanics (template provider only, deterministic).

These prove the harness itself: manifest integrity, identical candidate
selection for both approaches, fault injection through both paths, and the
correction loop with the same attempt bound.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO))

from evaluation import runner  # noqa: E402


def test_manifest_has_twelve_balanced_scenarios():
    manifest = runner.load_manifest()
    scenarios = manifest["scenarios"]
    assert len(scenarios) == 12
    seeds = {scenario["seed"] for scenario in scenarios}
    assert seeds == {42, 7, 99}
    kinds = [scenario["fault"]["kind"] for scenario in scenarios]
    assert kinds.count("none") == 6
    assert kinds.count("unknown_citation") == 3
    assert kinds.count("incorrect_metric") == 3
    locales = {scenario["locale"] for scenario in scenarios}
    assert locales == {"en", "de", "bn"}


def test_every_scenario_candidate_is_in_the_deterministic_feed():
    manifest = runner.load_manifest()
    for scenario in manifest["scenarios"]:
        ctx = runner.build_context(scenario)
        assert ctx.candidate.candidate_id == scenario["candidate_id"]
        assert len(ctx.candidate.metrics) > 0
        assert len(ctx.candidate.cited_event_ids) > 0


def _scenario(**overrides):
    scenario = {
        "id": "test",
        "seed": 42,
        "locale": "en",
        "mode": "analyst",
        "candidate_id": "cand-0003",
        "fault": {"kind": "none", "persistence": None},
    }
    scenario.update(overrides)
    return scenario


def test_ordinary_single_agent_verifies_first_try():
    ctx = runner.build_context(_scenario())
    result = asyncio.run(runner.run_single_agent(ctx, _scenario(), "template"))
    assert result.outcome == "verified_first_try", result.verification
    assert result.verification["ok"]
    assert result.model_calls == 1
    assert not result.fallback_used


def test_fault_once_recovers_through_correction():
    scenario = _scenario(fault={"kind": "incorrect_metric", "persistence": "once"})
    ctx = runner.build_context(scenario)
    result = asyncio.run(runner.run_single_agent(ctx, scenario, "template"))
    assert result.outcome == "verified_after_correction", result.verification
    assert result.verification["ok"]
    assert result.model_calls == 2
    assert not result.fallback_used


def test_fault_always_publishes_the_deterministic_fallback():
    scenario = _scenario(fault={"kind": "unknown_citation", "persistence": "always"})
    ctx = runner.build_context(scenario)
    result = asyncio.run(runner.run_single_agent(ctx, scenario, "template"))
    assert result.outcome == "fallback_published", result.verification
    assert result.verification["ok"]
    assert result.fallback_used


def test_pipeline_ordinary_scenario_verifies():
    pytest.importorskip("agent_framework")
    ctx = runner.build_context(_scenario())
    result = asyncio.run(runner.run_pipeline(ctx, _scenario(), "template"))
    assert result.verification["ok"], result.verification
    assert result.outcome in ("verified_first_try", "verified_after_correction")
    assert result.model_calls == 3  # one insight, one call per model-driven stage
    assert len(result.call_latencies_ms) == result.model_calls
    assert result.published_output["overlay_id"] == "ovl-cand-0003"


def test_pipeline_fault_always_recovers_through_fallback():
    pytest.importorskip("agent_framework")
    scenario = _scenario(fault={"kind": "unknown_citation", "persistence": "always"})
    ctx = runner.build_context(scenario)
    result = asyncio.run(runner.run_pipeline(ctx, scenario, "template"))
    assert result.outcome == "fallback_published", result.verification
    assert result.verification["ok"]
    assert result.vetoes > 0


def test_progress_is_reported_after_every_scenario():
    """The run is written as it is measured.

    The Foundry run is hours of model quota, so a failure at hour three
    must leave the first two hours on disk. The progress callback is
    what does that, and it has to fire once per scenario - not once at
    the end.
    """
    seen: list[dict] = []

    async def collect() -> None:
        await runner.run_all("template", on_progress=seen.append)

    asyncio.run(collect())

    manifest = runner.load_manifest()
    assert len(seen) == len(manifest["scenarios"]) * 2
    # Each report is a prefix of the next: the results only ever grow.
    counts = [len(report["results"]) for report in seen]
    assert counts == sorted(counts)
    assert counts[-1] == len(manifest["scenarios"]) * 2
    assert counts == list(range(1, len(manifest["scenarios"]) * 2 + 1))
    assert seen[0]["scenarios_run"] == 0
    assert seen[1]["scenarios_run"] == 1
    assert seen[-1]["scenarios_run"] == 12
    assert not seen[0]["complete"]
    assert seen[-1]["complete"]
    # And each one is already a readable report on its own.
    assert "summary" in seen[-1]
    assert seen[0]["results"]


def test_resume_preserves_measured_results_and_runs_only_missing_pairs(monkeypatch):
    reports = []
    asyncio.run(runner.run_all("template", on_progress=reports.append))
    checkpoint = reports[-2]
    calls = []
    original = runner.run_single_agent

    async def single(ctx, scenario, provider):
        calls.append((scenario["id"], "single_agent"))
        return await original(ctx, scenario, provider)

    async def unexpected_pipeline(*args):
        pytest.fail("completed pipeline result must not be measured again")

    monkeypatch.setattr(runner, "run_pipeline", unexpected_pipeline)
    monkeypatch.setattr(runner, "run_single_agent", single)
    resumed = asyncio.run(runner.run_all("template", checkpoint=checkpoint))
    assert calls == [("s12", "single_agent")]
    assert resumed["results"][:-1] == checkpoint["results"]
    assert resumed["complete"] and resumed["scenarios_run"] == 12
    with pytest.raises(ValueError, match="incompatible provider"):
        asyncio.run(runner.run_all("foundry", checkpoint=checkpoint))
