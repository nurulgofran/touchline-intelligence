"""Print what the analyst says about a match, and check that it all holds up.

The check at the bottom is the one that matters: every number the analyst
reports is recomputed from the event ids it cites, and any number that does
not reproduce is a bug. The grounding executor does the same thing at run
time, but doing it here means a broken metric shows up at the point it was
written rather than three stages later.

Run it with::

    python scripts/probe_candidates.py [--seed 42]
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from touchline.metrics.analyst import MatchAnalyst
from touchline.sim.generator import simulate, team_names
from touchline.state.store import MatchStateStore


def main() -> int:
    """Run the analyst, print a summary, and verify every cited number."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--show", type=int, default=0, help="How many headlines to print."
    )
    args = parser.parse_args()

    events = simulate(seed=args.seed)
    store = MatchStateStore()
    store.extend(events)

    analyst = MatchAnalyst(team_names=team_names(args.seed))
    candidates = analyst.analyse(events)

    print(f"seed {args.seed}: {len(events)} events, {len(candidates)} candidates")
    print()
    by_kind = Counter(candidate.kind.value for candidate in candidates)
    for kind, count in by_kind.most_common():
        print(f"  {kind:<18} {count}")

    failures: list[str] = []
    for candidate in candidates:
        for metric in candidate.metrics:
            recomputed = store.recompute(
                metric.name, candidate.cited_event_ids, metric.side
            )
            if recomputed is None:
                failures.append(
                    f"{candidate.candidate_id}: cited ids unknown for {metric.name}"
                )
                continue
            if abs(recomputed - metric.value) > 1e-6:
                failures.append(
                    f"{candidate.candidate_id}: {metric.name} reported "
                    f"{metric.value!r}, recomputed {recomputed!r}"
                )
        baseline = candidate.baseline
        if baseline is not None:
            recomputed = store.recompute(
                baseline.metric_name, baseline.cited_event_ids, baseline.side
            )
            if recomputed is None:
                failures.append(
                    f"{candidate.candidate_id}: baseline ids unknown for "
                    f"{baseline.metric_name}"
                )
            elif abs(recomputed - baseline.value) > 1e-6:
                failures.append(
                    f"{candidate.candidate_id}: baseline {baseline.metric_name} "
                    f"reported {baseline.value!r}, recomputed {recomputed!r}"
                )

    print()
    if args.show:
        for candidate in candidates[: args.show]:
            print(f"  [{candidate.kind.value}] {candidate.headline}")
            for metric in candidate.metrics:
                print(f"      {metric.name} = {metric.value} {metric.unit}")
        print()

    if failures:
        print(f"FAILED: {len(failures)} numbers did not reproduce")
        for failure in failures[:20]:
            print(f"  {failure}")
        return 1

    print(
        f"OK: all {len(candidates)} candidates recompute from their citations"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
