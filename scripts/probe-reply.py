#!/usr/bin/env python3
"""Show the raw reply the Explainer sends back, and its length.

The Explainer's reply has to be a JSON object. When it is not, the error
message only shows the first 120 characters, which is not enough to tell a
truncated reply from a reply that was never JSON. This prints the whole
reply -- its length and its tail -- for every candidate the live analysis
asks about.

Usage:
    python scripts/probe-reply.py
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import touchline.agents.explainer as explainer

_original = explainer.parse_explanation


def _probing(text: str, *, candidate_id: str):
    """Print the reply before the real parser sees it."""
    import json

    try:
        json.loads(text)
        verdict = "parses"
    except json.JSONDecodeError as exc:
        verdict = f"json error: {exc}"
    print(
        f"\n--- {candidate_id} --- "
        f"len={len(text)} ends_with_brace={text.rstrip().endswith('}')} "
        f"[{verdict}]\n"
        f"head: {text[:160]!r}\n"
        f"tail: {text[-200:]!r}",
        flush=True,
    )
    os.makedirs("/tmp/replies", exist_ok=True)
    with open(f"/tmp/replies/{candidate_id}.txt", "w") as handle:
        handle.write(text)
    return _original(text, candidate_id=candidate_id)


explainer.parse_explanation = _probing


async def main() -> None:
    """Run the live analysis with the probe in place."""
    from touchline.live.engine import LiveMatchEngine

    engine = LiveMatchEngine(seed=42)
    engine.start()
    while engine.computed_ms < engine.duration_ms:
        await asyncio.sleep(5)
    print("done", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
