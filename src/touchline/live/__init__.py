"""The live match: the graph, driven by the match clock.

The batch run analyses a match that has been played. The live run analyses
one that is being played, and the two have to agree -- which they do,
because the analyst is causal: it only ever looks backwards from the
moment it reports on.

The engine in :mod:`touchline.live.engine` computes the stretches of a
match and keeps the answers. The studio plays them back on its own clock.
"""

from touchline.live.engine import LiveMatchEngine, StretchResult, build_teams_payload

__all__ = ["LiveMatchEngine", "StretchResult", "build_teams_payload"]
