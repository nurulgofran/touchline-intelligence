"""Live-API probe: read-only pipeline-side measurements.

Fetches the published overlays the deployed service computed for the live
demo match (seed 42) -- produced by the real Microsoft Foundry pipeline --
and runs the evaluation's independent validators against them, using the
locally simulated event log for seed 42 as the ground truth.

READ-ONLY. Only GET requests. It never touches the audience/reader, never
mutates shared state, and stays far below the service's rate limits.

What this is and is not:
- It IS a genuine Foundry-run measurement of the pipeline approach's
  published outputs (45 overlays, full match, Foundry explainer/narrator).
- It is NOT one of the 12 fixed scenarios (the live match is seed 42 with
  the studio's Analyst/English audience; faults cannot be injected through
  the API), and it says nothing about the single-agent baseline, which has
  no live endpoint.

Usage:
    PYTHONPATH=src <venv>/bin/python -m evaluation.live_api_probe \\
        --out evaluation/results/live_api_seed42.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO))

from evaluation import validators  # noqa: E402

BASE_URL = "https://touchline-app.icywave-bfcbf183.germanywestcentral.azurecontainerapps.io"


def _get(path: str) -> dict[str, Any]:
    request = urllib.request.Request(
        BASE_URL + path, headers={"User-Agent": "touchline-eval/1.0"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


@dataclass
class _OverlayLike:
    overlay_id: str
    title: str
    body: str
    reading: str
    metrics: list[Any]
    cited_event_ids: list[str]


@dataclass
class _MetricLike:
    name: str
    value: float
    unit: str
    side: Any


def probe(seed: int = 42) -> dict[str, Any]:
    from touchline.models.events import TeamSide
    from touchline.sim.generator import simulate

    fetched_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    payload = _get(f"/api/match/insights?seed={seed}")
    insights = payload.get("insights", [])

    events = simulate(seed)
    events_by_id = {event.event_id: event for event in events}

    rows: list[dict[str, Any]] = []
    for insight in insights:
        metrics = [
            _MetricLike(
                name=metric["name"],
                value=float(metric["value"]),
                unit=metric.get("unit") or "",
                side=(
                    TeamSide(metric["side"])
                    if metric.get("side")
                    else None
                ),
            )
            for metric in insight.get("metrics", [])
        ]
        overlay = _OverlayLike(
            overlay_id=insight.get("overlay_id", ""),
            title=insight.get("title", ""),
            body=insight.get("body", ""),
            reading="",
            metrics=metrics,
            cited_event_ids=list(insight.get("cited_event_ids", [])),
        )
        check = validators.validate_overlay(overlay, events_by_id, locale="en")
        rows.append(
            {
                "candidate_id": insight.get("candidate_id"),
                "overlay_id": overlay.overlay_id,
                "citations": len(overlay.cited_event_ids),
                "metrics": len(metrics),
                "ok": check.ok,
                "unknown_citations": check.unknown_citations,
                "metric_failures": check.metric_failures,
                "prose_advisories": [
                    {"text": issue.text, "context": issue.context}
                    for issue in check.prose_issues
                ],
            }
        )

    return {
        "source": f"{BASE_URL}/api/match/insights?seed={seed}",
        "fetched_at": fetched_at,
        "seed": seed,
        "approach": "pipeline",
        "provider": "Microsoft Foundry (live deployment)",
        "ground_truth": "locally simulated event log for the same seed "
        "(touchline.sim.generator.simulate)",
        "caveats": [
            "The live match is the fixed demo match (Analyst/English "
            "audience); it is not one of the 12 fixed scenarios.",
            "Fault scenarios cannot be injected through the API, so no "
            "veto/recovery behaviour is observable here.",
            "The single-agent baseline has no live endpoint and is not "
            "measured here.",
        ],
        "overlays": len(rows),
        "verification_success": sum(1 for row in rows if row["ok"]),
        "published_output_failures": sum(1 for row in rows if not row["ok"]),
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate the live deployment's published overlays."
    )
    parser.add_argument(
        "--out",
        default=str(HERE / "results" / "live_api_seed42.json"),
        help="Where to write the results JSON.",
    )
    args = parser.parse_args()
    report = probe()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(
        json.dumps(
            {
                "overlays": report["overlays"],
                "verification_success": report["verification_success"],
                "published_output_failures": report[
                    "published_output_failures"
                ],
            },
            indent=2,
        )
    )
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
