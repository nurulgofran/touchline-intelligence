"""Sweep the player model's constants against real football output.

Reference figures for a Premier League outfield player over 90 minutes:
roughly 8-12 km covered, with a goalkeeper nearer 4-6 km. The keeper's figure
matters as much as the outfielder's, because the gap between them is what
tells you the model has a shape rather than a scale factor.

Run it with ``python scripts/calibrate_player_model.py``.
"""

from __future__ import annotations

import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from touchline.sim import players
from touchline.sim.generator import simulate_with_tracker

SEEDS = (42, 7, 99, 2026, 13)

#: (label, attribute, value) triples to try.
SWEEP = [
    ("base", None, None),
    ("press 34", "_PRESS_RANGE_M", 34.0),
    ("press 38", "_PRESS_RANGE_M", 38.0),
    ("pressers 3", "_PRESSERS_PER_SIDE", 3),
    ("wander 0.30", "_WANDER_STEP_M", 0.30),
    ("wander 0.38", "_WANDER_STEP_M", 0.38),
    ("shift 0.65", "_BLOCK_SHIFT", 0.65),
    ("lateral 0.50", "_LATERAL_SHIFT", 0.50),
    ("combo a", None, None),
    ("combo b", None, None),
    ("combo c", None, None),
]

COMBO_A = {"_PRESS_RANGE_M": 34.0, "_PRESSERS_PER_SIDE": 3, "_WANDER_STEP_M": 0.30}
COMBO_B = {
    "_PRESS_RANGE_M": 34.0,
    "_PRESSERS_PER_SIDE": 3,
    "_WANDER_STEP_M": 0.30,
    "_BLOCK_SHIFT": 0.65,
    "_LATERAL_SHIFT": 0.50,
}
COMBO_C = {
    "_PRESS_RANGE_M": 36.0,
    "_PRESSERS_PER_SIDE": 3,
    "_WANDER_STEP_M": 0.32,
    "_BLOCK_SHIFT": 0.62,
    "_LATERAL_SHIFT": 0.48,
}


def measure() -> tuple[float, float]:
    """Return the outfield median and the keeper median, in kilometres."""
    outfield: list[float] = []
    keepers: list[float] = []
    for seed in SEEDS:
        _, tracker = simulate_with_tracker(seed=seed)
        for runner in tracker._runners.values():
            if runner.is_keeper:
                keepers.append(runner.covered_m)
            else:
                outfield.append(runner.covered_m)
    return (
        statistics.median(outfield) / 1000.0,
        statistics.median(keepers) / 1000.0,
    )


def main() -> None:
    original = {
        name: getattr(players, name)
        for name in (
            "_PRESS_RANGE_M",
            "_WANDER_STEP_M",
            "_BLOCK_SHIFT",
            "_LATERAL_SHIFT",
            "_PRESSERS_PER_SIDE",
        )
    }
    print(f"{'variant':16s} {'outfield km':>12s} {'keeper km':>10s}")
    print(f"{'outfield avg m/s':>34s}")
    for label, attribute, value in SWEEP:
        for name, current in original.items():
            setattr(players, name, current)
        if label == "combo a":
            for name, val in COMBO_A.items():
                setattr(players, name, val)
        elif label == "combo b":
            for name, val in COMBO_B.items():
                setattr(players, name, val)
        elif label == "combo c":
            for name, val in COMBO_C.items():
                setattr(players, name, val)
        elif attribute is not None:
            setattr(players, attribute, value)
        outfield_km, keeper_km = measure()
        speed = outfield_km * 1000.0 / (90 * 60)
        print(f"{label:16s} {outfield_km:12.2f} {keeper_km:10.2f} {speed:18.2f}")
    for name, current in original.items():
        setattr(players, name, current)


if __name__ == "__main__":
    main()
