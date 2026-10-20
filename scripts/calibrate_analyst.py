"""Sweep the analyst's thresholds and report how much they say.

The analyst is meant to sound like a commentator, not a firehose: a handful of
moments a match, spaced far enough apart to be worth saying. This script
prints, for each candidate set of thresholds, how many insights a match
produces and how far apart they land, so the numbers are chosen against
evidence rather than by eye.

Run it with::

    python scripts/calibrate_analyst.py
"""

from __future__ import annotations

import sys
from collections import Counter
from dataclasses import replace
from itertools import pairwise
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from touchline.metrics.analyst import MatchAnalyst
from touchline.metrics.thresholds import Thresholds
from touchline.sim.generator import simulate, team_names

SEEDS = (42, 7, 99, 2026, 13)

BASE = Thresholds()

VARIANTS: dict[str, Thresholds] = {
    "current": BASE,
    "cooldown 3m": replace(BASE, cooldown_ms=180_000),
    "cooldown 4m": replace(BASE, cooldown_ms=240_000),
    "cooldown 5m": replace(BASE, cooldown_ms=300_000),
    "press run 7": replace(BASE, press_min_run=7, press_min_control=75.0),
    "press run 8": replace(BASE, press_min_run=8, press_min_control=80.0),
    "tempo 4": replace(BASE, tempo_min_breaks=4),
    "tempo 5": replace(BASE, tempo_min_breaks=5),
    "passing 15": replace(BASE, pass_accuracy_shift=15.0),
    "passing 18": replace(BASE, pass_accuracy_shift=18.0),
    "control 20": replace(BASE, control_shift=20.0),
    "broadcast": replace(
        BASE,
        cooldown_ms=240_000,
        press_min_run=7,
        press_min_control=75.0,
        tempo_min_breaks=4,
        pass_accuracy_shift=15.0,
        control_shift=20.0,
    ),
}


def describe(thresholds: Thresholds) -> str:
    """One line per seed: how many insights, and how far apart they land."""
    lines: list[str] = []
    for seed in SEEDS:
        events = simulate(seed=seed)
        candidates = MatchAnalyst(
            thresholds=thresholds, team_names=team_names(seed)
        ).analyse(events)
        clocks = [candidate.match_clock_ms for candidate in candidates]
        gaps = sorted(b - a for a, b in pairwise(clocks))
        median = gaps[len(gaps) // 2] / 1000 if gaps else 0.0
        lines.append(
            f"seed {seed:>4}: {len(candidates):>3} insights, "
            f"median gap {median:>5.0f}s, "
            f"quietest stretch {max(gaps) / 1000 if gaps else 0:.0f}s"
        )
    return "\n     ".join(lines)


def main() -> int:
    """Print the sweep."""
    print("A match is 90 minutes (5,400,000 ms).")
    print()
    for name, thresholds in VARIANTS.items():
        print(f"  {name}")
        print(f"     {describe(thresholds)}")

    print()
    print("Per-rule counts for the 'broadcast' set, seed 42:")
    events = simulate(seed=42)
    candidates = MatchAnalyst(
        thresholds=VARIANTS["broadcast"], team_names=team_names(42)
    ).analyse(events)
    rules = Counter()
    for candidate in candidates:
        for tag in candidate.tags:
            rules[tag] += 1
    for tag, count in rules.most_common():
        print(f"  {tag:<28} {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
