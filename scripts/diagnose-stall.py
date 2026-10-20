#!/usr/bin/env python3
"""Find where the live analysis blocks.

The engine analyses a match stretch by stretch. When it stops advancing, the
question is *where* it is waiting -- the model call, the A2A hop, or the
lock. This runs the analysis in-process with faulthandler armed, so a
SIGUSR1 prints the stack of every thread while it is stuck.

It is how the truncation bug was found: the analysis stopped at minute
nineteen every time, and the stack said the Explainer's reply was being
parsed -- and the reply had been cut off mid-sentence by the output-token
ceiling.

Usage:
    python scripts/diagnose-stall.py
    kill -USR1 <pid>      # prints the stack of every thread
"""

from __future__ import annotations

import asyncio
import faulthandler
import logging
import os
import signal
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from touchline.live.engine import LiveMatchEngine


async def main() -> None:
    """Run the analysis and report how far it got, and when it stops."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    engine = LiveMatchEngine(seed=42)
    engine.start()

    started = time.monotonic()
    last = -1
    while True:
        await asyncio.sleep(10)
        reached = engine.computed_ms // 60_000
        if reached != last:
            print(
                f"[{time.monotonic() - started:7.1f}s] "
                f"match {reached:3d} min of {engine.duration_ms // 60_000} min",
                flush=True,
            )
            last = reached
        if reached >= engine.duration_ms // 60_000:
            print("the match is fully analysed", flush=True)
            return


if __name__ == "__main__":
    faulthandler.enable()
    faulthandler.register(signal.SIGUSR1, all_threads=True, chain=False)
    print(f"pid {os.getpid()} - send SIGUSR1 for a stack dump", flush=True)
    asyncio.run(main())
