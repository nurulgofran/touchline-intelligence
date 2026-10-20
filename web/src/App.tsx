import { useState } from "react";

import MomentumChart from "./components/MomentumChart";
import OverlayFeed from "./components/OverlayFeed";
import Pitch from "./components/Pitch";
import TopBar from "./components/TopBar";
import TracePanel from "./components/TracePanel";
import Transport from "./components/Transport";
import { useLiveMatch } from "./useLiveMatch";

/**
 * The studio.
 *
 * One page, one socket, three panels: the pitch on the left, the insights
 * beside it, and the momentum and the agent trace on the right. There is
 * no router, because there is one thing to watch.
 */
export default function App() {
  const match = useLiveMatch(42);
  const [highlightedEventId, setHighlightedEventId] = useState<string | null>(
    null,
  );
  // The insight whose hand-offs the trace is showing. Clicking an insight
  // narrows the agent trace to that moment - the A2A hop and any veto for
  // that one insight - and clicking it again puts the whole trace back.
  const [selectedCandidate, setSelectedCandidate] = useState<string | null>(
    null,
  );

  return (
    <div className="flex min-h-screen flex-col bg-studio-950 text-studio-100 lg:h-screen lg:overflow-hidden">
      <TopBar
        teams={match.teams}
        scoreHome={match.scoreHome}
        scoreAway={match.scoreAway}
        clockMs={match.clockMs}
        provider={match.provider}
        status={match.status}
        matchId={match.matchId}
      />

      <main className="min-h-0 flex-1 overflow-y-auto lg:overflow-hidden">
        <div className="grid grid-cols-1 gap-3 p-3 lg:h-full lg:grid-cols-12">
          {/* The main panel: the pitch, and the transport under it. */}
          <section className="flex min-h-0 flex-col gap-3 lg:col-span-5">
            <Pitch
              teams={match.teams}
              events={match.events}
              clockMs={match.clockMs}
              highlightedEventId={highlightedEventId}
            />

            {match.startedAtFirstMoment && !match.userMovedClock && (
              <p className="px-1 text-[11px] leading-snug text-amber-300/90">
                Started at the first key moment — press Replay to watch from
                kick-off.
              </p>
            )}

            <Transport
              playing={match.playing}
              speed={match.speed}
              clockMs={match.clockMs}
              durationMs={match.durationMs}
              nextMomentMs={match.nextMomentMs}
              onTogglePlay={match.togglePlay}
              onReplay={match.replay}
              onSpeed={match.changeSpeed}
              onSeek={match.seek}
              onNextMoment={match.nextMoment}
            />
          </section>

          {/* The insights, beside the pitch. */}
          <section className="flex min-h-0 flex-col gap-3 lg:col-span-4">
            {match.usingFallback && (
              <p className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-1.5 text-[11px] text-amber-200">
                The live socket has dropped, so the feed is not being pushed
                — this is what the match had reached. It will pick up again
                on its own.
              </p>
            )}
            <OverlayFeed
              overlays={match.overlays}
              total={match.knownInsightCount}
              teams={match.teams}
              highlightedEventId={highlightedEventId}
              selectedCandidate={selectedCandidate}
              onHighlight={setHighlightedEventId}
              onSelect={setSelectedCandidate}
            />
          </section>

          {/* The right panel: the momentum on top, the trace under it. */}
          <section className="flex min-h-0 flex-col gap-3 lg:col-span-3">
            <MomentumChart
              points={match.momentum}
              clockMs={match.clockMs}
              durationMs={match.durationMs}
              teams={match.teams}
            />
            <TracePanel
              trace={match.trace}
              focus={selectedCandidate}
              onClearFocus={() => setSelectedCandidate(null)}
            />
          </section>
        </div>
      </main>

      <footer className="border-t border-studio-700/70 bg-studio-900/95 px-4 py-2 text-[11px] leading-relaxed text-studio-500">
        All match data on this page is synthetic and every club and player is
        fictional. No real competition, club, player or commercial mark is
        represented, and no real match data is used. The insight numbers are
        computed from the event log and are shown with the event ids they
        were computed from, so every claim can be checked against the log.
      </footer>
    </div>
  );
}
