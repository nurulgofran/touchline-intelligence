"""Print the distribution of each rule's trigger statistic over a match.

A threshold that fires on most windows is not a threshold, it is a default.
This script slides the analyst's five-minute window across every match and
prints what each rule's statistic actually looks like -- the median, the
eightieth and ninetieth percentiles -- so a threshold can be set where the
interesting windows start rather than where the noise ends.

Run it with::

    python scripts/measure_rule_triggers.py
"""

from __future__ import annotations

import sys
from bisect import bisect_left, bisect_right
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from touchline.metrics.definitions import compute_metric
from touchline.metrics.thresholds import Thresholds
from touchline.models.events import MATCH_DURATION_MS, MatchEvent, TeamSide
from touchline.sim.generator import simulate

SEEDS = (42, 7, 99, 2026, 13)


def percentile(values: list[float], fraction: float) -> float:
    """The ``fraction`` percentile of a list of numbers."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, round(fraction * (len(ordered) - 1)))
    return ordered[index]


def windows(events: list[MatchEvent], thresholds: Thresholds):
    """Yield the (recent, baseline) windows the analyst evaluates."""
    ordered = sorted(events, key=lambda e: (e.match_clock_ms, e.event_id))
    clocks = [event.match_clock_ms for event in ordered]
    clock = thresholds.window_ms + thresholds.baseline_ms
    while clock <= MATCH_DURATION_MS:
        start = clock - thresholds.window_ms
        recent = ordered[bisect_left(clocks, start) : bisect_right(clocks, clock)]
        baseline = ordered[
            bisect_left(clocks, start - thresholds.baseline_ms) : bisect_left(
                clocks, start
            )
        ]
        if len(recent) >= thresholds.min_window_events:
            yield clock, recent, baseline
        clock += thresholds.eval_interval_ms


def main() -> int:
    """Print the distributions."""
    thresholds = Thresholds()
    stats: dict[str, list[float]] = {
        "longest_run": [],
        "passes_completed": [],
        "control_pct": [],
        "tempo_breaks": [],
        "pass_accuracy_delta": [],
        "control_delta": [],
        "momentum_delta": [],
        "shots": [],
        "xg": [],
        "max_speed_mps": [],
        "fouls": [],
    }

    for seed in SEEDS:
        for _clock, recent, baseline in windows(simulate(seed=seed), thresholds):
            for side in (TeamSide.HOME, TeamSide.AWAY):
                stats["longest_run"].append(
                    compute_metric("longest_run", recent, side)
                )
                stats["passes_completed"].append(
                    compute_metric("passes_completed", recent, side)
                )
                stats["control_pct"].append(compute_metric("control_pct", recent, side))
                stats["tempo_breaks"].append(
                    compute_metric("tempo_breaks", recent, side)
                )
                stats["pass_accuracy_delta"].append(
                    compute_metric("pass_accuracy", recent, side)
                    - compute_metric("pass_accuracy", baseline, side)
                )
                stats["control_delta"].append(
                    compute_metric("control_pct", recent, side)
                    - compute_metric("control_pct", baseline, side)
                )
                stats["momentum_delta"].append(
                    compute_metric("momentum_index", recent, side)
                    - compute_metric("momentum_index", baseline, side)
                )
                stats["shots"].append(compute_metric("shots", recent, side))
                stats["xg"].append(compute_metric("xg", recent, side))
                stats["max_speed_mps"].append(
                    compute_metric("max_speed_mps", recent, side)
                )
                stats["fouls"].append(compute_metric("fouls", recent, side))

    print(f"{len(stats['shots'])} side-windows over {len(SEEDS)} matches")
    print()
    print(f"{'statistic':<22} {'p50':>8} {'p80':>8} {'p90':>8} {'p95':>8} {'max':>8}")
    for name, values in stats.items():
        print(
            f"{name:<22} "
            f"{percentile(values, 0.50):>8.1f} "
            f"{percentile(values, 0.80):>8.1f} "
            f"{percentile(values, 0.90):>8.1f} "
            f"{percentile(values, 0.95):>8.1f} "
            f"{max(values):>8.1f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
