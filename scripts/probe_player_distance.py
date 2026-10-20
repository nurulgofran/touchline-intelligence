"""Probe the player model's ground covered, against real football numbers.

Real professional output, for reference: an outfield player covers roughly
8-12 km per 90 minutes, a goalkeeper roughly 4-6 km. This prints what the
model produces so the constants can be judged against those numbers rather
than against a feeling.

Run it with ``python scripts/probe_player_distance.py``.
"""

from __future__ import annotations

import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from touchline.models.events import TeamSide
from touchline.sim.generator import simulate_with_tracker


def main() -> None:
    events, tracker = simulate_with_tracker(seed=42)

    by_role: dict[str, list[float]] = defaultdict(list)
    for runner in tracker._runners.values():
        role = runner.roles[0]
        by_role[role].append(runner.covered_m)

    print("per-player ground covered (m):")
    for role in ("GK", "DEF", "MID", "FWD"):
        values = sorted(by_role.get(role, []))
        if not values:
            continue
        print(
            f"  {role:4s} n={len(values):2d} "
            f"min={min(values):8.0f} p50={statistics.median(values):8.0f} "
            f"max={max(values):8.0f}"
        )

    outfield = [v for role, vs in by_role.items() if role != "GK" for v in vs]
    keepers = by_role.get("GK", [])
    print()
    print(f"  outfield p50 = {statistics.median(outfield):.0f} m "
          f"({statistics.median(outfield) / 1000:.2f} km)")
    if keepers:
        print(f"  keeper   p50 = {statistics.median(keepers):.0f} m "
              f"({statistics.median(keepers) / 1000:.2f} km)")
    home_total = sum(
        runner.covered_m
        for runner in tracker._runners.values()
        if runner.side is TeamSide.HOME
    )
    print(f"  team total (home) = {home_total / 1000:.1f} km")

    keyed = sum(1 for e in events if "player_distance_m" in e.metadata)
    print(f"  events carrying player_distance_m: {keyed} of {len(events)}")

    total = sum(r.covered_m for r in tracker._runners.values())
    minutes = 90
    print(f"  whole-pitch average speed = "
          f"{total / 22 / (minutes * 60):.2f} m/s")


if __name__ == "__main__":
    main()
