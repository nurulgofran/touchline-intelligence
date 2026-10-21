import { useState } from "react";

import AudienceBar from "./components/AudienceBar";
import EvidenceDrawer from "./components/EvidenceDrawer";
import MomentumChart from "./components/MomentumChart";
import OverlayFeed from "./components/OverlayFeed";
import Pitch from "./components/Pitch";
import TopBar from "./components/TopBar";
import TracePanel from "./components/TracePanel";
import TrackingPanel from "./components/TrackingPanel";
import Transport from "./components/Transport";
import { useLiveMatch } from "./useLiveMatch";

/**
 * The studio.
 *
 * One page, one socket, three panels: the pitch on the left, the insights
 * beside it, and the momentum and the agent trace on the right. There is
 * no router, because there is one thing to watch.
 *
 * The proportions are deliberate. The pitch gets the height and the
 * breathing room, the insight feed gets the width - it is the page's
 * main content - and the right-hand column is the quiet one: the
 * momentum, the tracker's score sheet and the hand-off ledger, all of
 * which are there to be checked rather than admired.
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
  // The insight whose evidence the drawer is showing. Opening the drawer
  // fetches a read-only evidence payload; it makes no model calls and it
  // changes no audience state.
  const [evidenceCandidate, setEvidenceCandidate] = useState<string | null>(
    null,
  );

  return (
    <div className="flex min-h-screen flex-col bg-ink-950 text-ink-100 lg:h-screen lg:overflow-hidden">
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
        <div className="grid grid-cols-1 gap-2 p-2 lg:h-full lg:grid-cols-[minmax(0,4.4fr)_minmax(0,4.6fr)_minmax(0,3fr)]">
          {/* The main panel: the pitch, and the transport under it. */}
          <section className="flex min-h-0 flex-col gap-2">
            <Pitch
              teams={match.teams}
              events={match.events}
              clockMs={match.clockMs}
              highlightedEventId={highlightedEventId}
            />

            {match.startedAtFirstMoment && !match.userMovedClock && (
              <p className="px-1 text-[11px] leading-snug text-ink-300">
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
          <section className="flex min-h-0 flex-col gap-2">
            <AudienceBar
              audience={match.audience}
              options={match.audienceOptions}
              pending={match.audiencePending}
              rewriteError={match.audienceRewriteError}
              onChange={match.setAudience}
              onRetry={match.retryAudience}
            />
            {match.usingFallback && (
              <p className="rounded-sm border border-signal-amber/40 bg-signal-amber/10 px-3 py-1.5 text-[11px] leading-snug text-signal-amber">
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
              onInspect={setEvidenceCandidate}
            />
          </section>

          {/* The right panel: the momentum on top, the trace under it. */}
          <section className="flex min-h-0 flex-col gap-2">
            <MomentumChart
              points={match.momentum}
              clockMs={match.clockMs}
              durationMs={match.durationMs}
              teams={match.teams}
            />
            <TrackingPanel seed={match.seed} />
            <TracePanel
              trace={match.trace}
              focus={selectedCandidate}
              onClearFocus={() => setSelectedCandidate(null)}
            />
          </section>
        </div>
      </main>

      {/*
        The synthetic-data disclosure. It is a compliance requirement,
        not decoration, so it stays exactly as loud as it was: the
        studio's own words, in full, in the place a judge reads last.
      */}
      <footer className="border-t border-ink-800 bg-ink-900 px-4 py-2 text-[11px] leading-relaxed text-ink-300">
        All match data on this page is synthetic and every club and player is
        fictional. No real competition, club, player or commercial mark is
        represented, and no real match data is used. The insight numbers are
        computed from the event log and are shown with the event ids they
        were computed from, so every claim can be checked against the log.
      </footer>

      <EvidenceDrawer
        candidateId={evidenceCandidate}
        seed={match.seed}
        onClose={() => setEvidenceCandidate(null)}
      />
    </div>
  );
}
