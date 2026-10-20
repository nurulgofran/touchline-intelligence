import { useEffect, useRef } from "react";

import {
  formatClock,
  formatMetricValue,
  rgbToCss,
  type Overlay,
  type Side,
  type Teams,
} from "../types";

/**
 * The overlay feed: the insights, newest first.
 *
 * The point of this panel is that a claim is **checkable**. Every number
 * carries the ids of the events it was computed from, and those ids are
 * on the card, in plain sight - not behind a click. Point at one and the
 * pitch lights up the event it names.
 *
 * The second thing it does is narrow the **agent trace** to one moment.
 * A hundred hand-offs is a wall of text; the four that belong to the
 * insight you are looking at are the story, and the A2A hop and any veto
 * are in them.
 */

export interface OverlayFeedProps {
  overlays: Overlay[];
  /** How many key moments the service has computed in all. */
  total: number;
  teams: Teams | null;
  /** The event the viewer is pointing at, if any. */
  highlightedEventId: string | null;
  /** The insight whose hand-offs the trace is showing, if any. */
  selectedCandidate: string | null;
  onHighlight: (eventId: string | null) => void;
  onSelect: (candidateId: string | null) => void;
}

function sideColour(teams: Teams | null, side: Side | null): string {
  if (side === "HOME") {
    return teams ? rgbToCss(teams.home.primary_colour) : "#7dd3fc";
  }
  if (side === "AWAY") {
    return teams ? rgbToCss(teams.away.primary_colour) : "#f9a8d4";
  }
  return "#cbd5e1";
}

export default function OverlayFeed({
  overlays,
  total,
  teams,
  highlightedEventId,
  selectedCandidate,
  onHighlight,
  onSelect,
}: OverlayFeedProps) {
  const listRef = useRef<HTMLDivElement | null>(null);

  // The newest card is at the top, so the panel follows it as the match
  // produces one. Without this the list would stay where the reader left
  // it and a new insight would arrive silently above the fold.
  useEffect(() => {
    const list = listRef.current;
    if (list) list.scrollTop = 0;
  }, [overlays.length]);

  return (
    <div className="panel flex min-h-0 flex-col overflow-hidden">
      <div className="flex items-center justify-between gap-2 px-3 pt-2.5 pb-1.5">
        <h2 className="panel-title">Key moments</h2>
        <span className="rounded-full border border-studio-600 bg-studio-850 px-2 py-0.5 text-[11px] text-studio-300">
          {total} insight{total === 1 ? "" : "s"}
        </span>
      </div>

      <div
        ref={listRef}
        className="min-h-0 flex-1 space-y-2 overflow-y-auto px-2 pb-2"
      >
        {overlays.length === 0 && (
          <p className="px-1 py-8 text-center text-[11px] text-studio-500">
            waiting for the first insight
          </p>
        )}

        {overlays.map((overlay) => (
          <article
            key={overlay.overlay_id}
            className="animate-card-in rounded-md border border-studio-700/80 bg-studio-850/70 p-2.5"
          >
            <header className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <span
                className="rounded bg-studio-800 px-1.5 py-0.5 font-mono text-[11px] font-semibold tabular-nums text-studio-200"
                title="The match clock this insight belongs to"
              >
                {formatClock(overlay.match_clock_ms)}
              </span>
              <span className="rounded border border-studio-600 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-studio-400">
                {overlay.type}
              </span>
              <span className="ml-auto font-mono text-[10px] text-studio-600">
                {overlay.overlay_id}
              </span>
            </header>

            <h3 className="mt-1.5 text-sm font-semibold leading-snug text-white">
              {overlay.title}
            </h3>
            <p className="mt-1 text-[12px] leading-relaxed text-studio-300">
              {overlay.body}
            </p>

            {overlay.metrics.length > 0 && (
              <table className="mt-2 w-full border-collapse text-[11px]">
                <thead>
                  <tr className="text-left text-[10px] uppercase tracking-wider text-studio-500">
                    <th className="w-1/2 pb-1 font-medium">Metric</th>
                    <th className="w-1/4 pb-1 text-right font-medium">Value</th>
                    <th className="w-1/4 pb-1 text-right font-medium">Unit</th>
                  </tr>
                </thead>
                <tbody>
                  {overlay.metrics.map((metric) => (
                    <tr
                      key={`${overlay.overlay_id}-${metric.name}`}
                      className="border-t border-studio-800"
                    >
                      <td className="py-0.5 pr-2 text-studio-300">
                        {metric.label}
                      </td>
                      <td
                        className="py-0.5 text-right font-mono font-semibold tabular-nums"
                        style={{ color: sideColour(teams, metric.side) }}
                      >
                        {formatMetricValue(metric.value)}
                      </td>
                      <td className="py-0.5 text-right font-mono text-studio-500">
                        {metric.unit || "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}

            {overlay.cited_event_ids.length > 0 && (
              <div className="mt-2">
                <p className="text-[10px] uppercase tracking-wider text-studio-500">
                  Computed from {overlay.cited_event_ids.length} event
                  {overlay.cited_event_ids.length === 1 ? "" : "s"}
                </p>
                <div className="mt-1 flex flex-wrap gap-1">
                  {overlay.cited_event_ids.map((eventId) => {
                    const highlighted = highlightedEventId === eventId;
                    return (
                      <button
                        key={eventId}
                        type="button"
                        onMouseEnter={() => onHighlight(eventId)}
                        onMouseLeave={() => onHighlight(null)}
                        onFocus={() => onHighlight(eventId)}
                        onBlur={() => onHighlight(null)}
                        title={`Show ${eventId} on the pitch`}
                        className={
                          highlighted
                            ? "rounded border border-amber-400/80 bg-amber-400/20 px-1.5 py-px font-mono text-[10px] text-amber-200"
                            : "rounded border border-studio-600 bg-studio-800 px-1.5 py-px font-mono text-[10px] text-studio-300 transition-colors hover:border-studio-400 hover:text-white"
                        }
                      >
                        {eventId}
                      </button>
                    );
                  })}
                </div>
              </div>
            )}

            <div className="mt-2.5 flex items-center gap-2">
              <div
                className="h-1 flex-1 overflow-hidden rounded-full bg-studio-700"
                role="meter"
                aria-valuenow={Math.round(overlay.confidence * 100)}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-label="Confidence"
              >
                <div
                  className="h-full rounded-full bg-emerald-400/80"
                  style={{ width: `${Math.round(overlay.confidence * 100)}%` }}
                />
              </div>
              <span className="font-mono text-[10px] tabular-nums text-studio-400">
                {Math.round(overlay.confidence * 100)}% confidence
              </span>
            </div>

            <div className="mt-2 flex items-center justify-end gap-2">
              <button
                type="button"
                onClick={() =>
                  onSelect(
                    selectedCandidate === overlay.candidate_id
                      ? null
                      : overlay.candidate_id,
                  )
                }
                data-active={selectedCandidate === overlay.candidate_id}
                className="rounded border border-studio-600 bg-studio-800 px-1.5 py-px text-[10px] font-semibold uppercase tracking-wider text-studio-300 transition-colors hover:border-studio-400 hover:text-white data-[active=true]:border-amber-400/80 data-[active=true]:bg-amber-400/20 data-[active=true]:text-amber-200"
                title="Show only this moment's hand-offs in the agent trace"
              >
                {selectedCandidate === overlay.candidate_id
                  ? "hide hand-offs"
                  : "show hand-offs"}
              </button>
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}
