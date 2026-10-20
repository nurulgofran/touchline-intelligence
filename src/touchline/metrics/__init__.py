"""The deterministic analysis layer.

Two pieces live here, and they are deliberately not an agent:

* :mod:`touchline.metrics.definitions` -- the metric registry. One definition
  per number the system shows, each with the function that computes it. The
  analyst produces its numbers from it and the grounding step recomputes them
  from it, so the two can never disagree about what a metric means.
* :mod:`touchline.metrics.analyst` -- the analyst itself. Ordinary Python
  that reads the event log and decides what is worth saying, with every
  claim carrying the event ids it rests on.
* :mod:`touchline.metrics.thresholds` -- the tuning knobs, in one dataclass.

Microsoft's own Agent Framework guidance puts it plainly: if a function can
handle the task, write the function. Presenting the analyst as a deterministic
worker rather than an agent is the stronger engineering claim, not the weaker
one.
"""

from touchline.metrics.analyst import MatchAnalyst, MomentumPoint
from touchline.metrics.definitions import (
    METRICS,
    POSSESSION_EVENT_TYPES,
    MetricScope,
    MetricSpec,
    compute_metric,
    is_chaotic,
    is_controlled,
    metric_spec,
    other_side,
)
from touchline.metrics.thresholds import Thresholds

__all__ = [
    "METRICS",
    "POSSESSION_EVENT_TYPES",
    "MatchAnalyst",
    "MetricScope",
    "MetricSpec",
    "MomentumPoint",
    "Thresholds",
    "compute_metric",
    "is_chaotic",
    "is_controlled",
    "metric_spec",
    "other_side",
]
