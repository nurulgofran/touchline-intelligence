"""Tests for the deterministic, no-LLM narrative provider."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from touchline.models.insights import InsightCandidate, InsightKind, MetricValue
from touchline.models.overlay import Overlay, OverlayBundle, OverlayType
from touchline.providers.base import NarrativeProvider, ProviderName
from touchline.providers.template import TemplateProvider

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "overlay.schema.json"


def make_candidate(
    candidate_id: str = "cand-0001",
    kind: InsightKind = InsightKind.TACTICAL,
    clock: int = 1_530_000,
    cited: list[str] | None = None,
    metrics: list[MetricValue] | None = None,
) -> InsightCandidate:
    """A hand-made insight candidate."""
    return InsightCandidate(
        candidate_id=candidate_id,
        kind=kind,
        match_clock_ms=clock,
        headline="High press on the left flank",
        metrics=metrics
        if metrics is not None
        else [
            MetricValue(
                name="pass_accuracy", value=86.4, unit="%", label="pass accuracy"
            ),
            MetricValue(name="tackles", value=5, unit="", label="tackles"),
        ],
        cited_event_ids=cited if cited is not None else ["ev-000042", "ev-000043"],
        confidence=0.82,
        tags=["press", "left"],
    )


@pytest.fixture()
def provider() -> TemplateProvider:
    return TemplateProvider()


def narrate(
    provider: TemplateProvider, candidate: InsightCandidate, **kwargs
) -> Overlay:
    """Run the provider's coroutine without needing an async test plugin."""
    return asyncio.run(provider.narrate(candidate, **kwargs))


def test_provider_satisfies_the_protocol(provider: TemplateProvider) -> None:
    assert isinstance(provider, NarrativeProvider)
    assert provider.name == ProviderName.TEMPLATE.value == "template"


def test_rendering_is_deterministic(provider: TemplateProvider) -> None:
    candidate = make_candidate()
    first = narrate(provider, candidate)
    second = narrate(provider, candidate)
    assert first.model_dump_json() == second.model_dump_json()
    assert first == second


def test_overlay_carries_the_citations(provider: TemplateProvider) -> None:
    candidate = make_candidate(cited=["ev-000042", "ev-000043", "ev-000099"])
    overlay = narrate(provider, candidate)
    assert overlay.cited_event_ids == candidate.cited_event_ids
    assert overlay.confidence == candidate.confidence
    assert overlay.match_clock_ms == candidate.match_clock_ms
    assert overlay.start_ms == candidate.match_clock_ms
    assert overlay.end_ms > overlay.start_ms


def test_overlay_never_has_an_empty_title_or_body(
    provider: TemplateProvider,
) -> None:
    for kind in InsightKind:
        candidate = make_candidate(kind=kind)
        overlay = narrate(provider, candidate)
        assert overlay.title.strip()
        assert overlay.body.strip()
        assert overlay.type is not None
        assert overlay.schema_version == "1.0"


def test_every_locale_renders_distinct_prose(provider: TemplateProvider) -> None:
    candidate = make_candidate()
    english = narrate(provider, candidate, locale="en")
    german = narrate(provider, candidate, locale="de")
    bengali = narrate(provider, candidate, locale="bn")

    assert english.body != german.body != bengali.body
    assert english.locale == "en"
    assert german.locale == "de"
    assert bengali.locale == "bn"
    for overlay in (english, german, bengali):
        assert "{" not in overlay.title
        assert "{" not in overlay.body
        assert "None" not in overlay.body


def test_unsupported_locale_falls_back_to_english(
    provider: TemplateProvider,
) -> None:
    candidate = make_candidate()
    fallback = narrate(provider, candidate, locale="xx")
    english = narrate(provider, candidate, locale="en")
    assert fallback.locale == "en"
    assert fallback.body == english.body


def test_metric_labels_are_translated(provider: TemplateProvider) -> None:
    candidate = make_candidate()
    english = narrate(provider, candidate, locale="en")
    german = narrate(provider, candidate, locale="de")
    bengali = narrate(provider, candidate, locale="bn")

    assert english.metrics[0].label == "pass accuracy"
    assert german.metrics[0].label == "Passquote"
    assert bengali.metrics[0].label == "পাসের নির্ভুলতা"


def test_candidate_without_metrics_still_renders(
    provider: TemplateProvider,
) -> None:
    candidate = make_candidate(metrics=[])
    overlay = narrate(provider, candidate)
    assert overlay.body.strip()
    assert overlay.metrics == []


def test_overlay_schema_matches_the_model() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert set(schema["properties"]) == set(Overlay.model_fields)

    # Every field is required in the schema except the ones that carry a
    # default and may therefore be omitted by a producer.
    optional = {
        "confidence",
        "locale",
        "revision",
        "provenance_provider",
        "prose_substituted",
        "deterministic_prose",
    }
    assert set(schema["required"]) == set(Overlay.model_fields) - optional
    assert optional == {
        name
        for name, field in Overlay.model_fields.items()
        if name in optional and not field.is_required()
    }


def test_overlay_schema_accepts_a_valid_overlay() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    properties = schema["properties"]
    assert properties["type"]["$ref"] == "#/$defs/OverlayType"
    assert set(schema["$defs"]["OverlayType"]["enum"]) == {
        member.value for member in OverlayType
    }
    assert properties["locale"]["default"] == "en"
    assert properties["confidence"]["default"] == 0.5
    assert properties["schema_version"]["default"] == "1.0"


def test_bundle_timing_catches_an_overlap() -> None:
    first = Overlay(
        overlay_id="ovl-1",
        type=OverlayType.TACTICAL,
        start_ms=1_000,
        end_ms=5_000,
        match_clock_ms=1_000,
        title="First",
        body="First body",
    )
    overlapping = Overlay(
        overlay_id="ovl-2",
        type=OverlayType.ALERT,
        start_ms=4_000,
        end_ms=6_000,
        match_clock_ms=4_000,
        title="Second",
        body="Second body",
    )
    bundle = OverlayBundle(overlays=[first, overlapping])
    with pytest.raises(ValueError):
        bundle.check_timing()


def test_bundle_timing_allows_touching_intervals() -> None:
    first = Overlay(
        overlay_id="ovl-1",
        type=OverlayType.TACTICAL,
        start_ms=1_000,
        end_ms=5_000,
        match_clock_ms=1_000,
        title="First",
        body="First body",
    )
    second = Overlay(
        overlay_id="ovl-2",
        type=OverlayType.ALERT,
        start_ms=5_000,
        end_ms=6_000,
        match_clock_ms=5_000,
        title="Second",
        body="Second body",
    )
    OverlayBundle(overlays=[first, second]).check_timing()
