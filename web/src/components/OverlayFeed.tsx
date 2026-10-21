import { useEffect, useRef, useState } from "react";

import {
  formatClock,
  formatMetricValue,
  rgbToCss,
  type MetricValue,
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
 *
 * The rhythm: the newest card is the moment the match has reached, so it
 * gets the full treatment - the display face, a stat row, room to read.
 * Everything below it is a ledger entry: quiet, ruled, and identical in
 * shape, so the eye can scan forty of them for the one it wants.
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
  /** Open the evidence drawer for one insight. */
  onInspect: (candidateId: string) => void;
}

function sideColour(teams: Teams | null, side: Side | null): string {
  if (side === "HOME") {
    return teams ? rgbToCss(teams.home.primary_colour) : "#64748b";
  }
  if (side === "AWAY") {
    return teams ? rgbToCss(teams.away.primary_colour) : "#64748b";
  }
  return "#4f5f56";
}

/**
 * The routine facts about a card, on one quiet line.
 *
 * The clock, what kind of moment it is, and the card's own id - the
 * things a reader needs and never needs twice. The two prose markers are
 * on this line too, because they are also facts about the card, but only
 * the substituted one is marked: a card whose prose was rejected is not
 * routine.
 */
function MetaRow({
  overlay,
  className = "",
}: {
  overlay: Overlay;
  className?: string;
}) {
  return (
    <div
      className={`flex min-w-0 items-baseline gap-2 text-[10px] leading-none ${className}`}
    >
      <span className="font-display text-[13px] font-semibold tabular-nums text-ink-200">
        {formatClock(overlay.match_clock_ms)}
      </span>
      <span className="text-ink-500">·</span>
      <span className="font-semibold uppercase tracking-[0.12em] text-ink-400">
        {overlay.type}
      </span>
      {overlay.deterministic_prose && (
        <>
          <span className="text-ink-700">·</span>
          <span
            className="text-ink-500"
            title="No model call behind this card: its prose is deterministic."
          >
            deterministic
          </span>
        </>
      )}
      {overlay.prose_substituted && !overlay.deterministic_prose && (
        <>
          <span className="text-ink-700">·</span>
          <span
            className="font-semibold uppercase tracking-[0.12em] text-signal-amber"
            title="The model's prose was rejected by the figure check, so this card shows deterministic copy instead."
          >
            deterministic copy
          </span>
        </>
      )}
      <span className="ml-auto truncate font-mono text-ink-500">
        {overlay.overlay_id}
      </span>
    </div>
  );
}

/**
 * The cited events.
 *
 * They are links, not chips: the ids are the evidence, and a chip per id
 * turns a claim into a wall of small boxes. They are still in plain
 * sight, still one click from the pitch, and still named in the tooltip.
 */
function Citations({
  overlay,
  highlightedEventId,
  onHighlight,
  hero,
}: {
  overlay: Overlay;
  highlightedEventId: string | null;
  onHighlight: (eventId: string | null) => void;
  hero: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const eventIds = expanded ? overlay.cited_event_ids : overlay.cited_event_ids.slice(0, 4);
  if (overlay.cited_event_ids.length === 0) return null;

  return (
    <div className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5">
      <span className="micro-label">
        {hero
          ? `computed from ${overlay.cited_event_ids.length} event${overlay.cited_event_ids.length === 1 ? "" : "s"}`
          : "events"}
      </span>
      {eventIds.map((eventId) => {
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
            className={[
              "rounded-[2px] font-mono underline decoration-transparent underline-offset-2",
              "transition-colors focus-ring",
              hero ? "text-[11px]" : "text-[10px]",
              highlighted
                ? "text-pitch-200 decoration-pitch-400/70"
                : "text-ink-400 hover:text-ink-100 hover:decoration-ink-600",
            ].join(" ")}
          >
            {eventId}
          </button>
        );
      })}
      {overlay.cited_event_ids.length > 4 && (
        <button
          type="button"
          aria-expanded={expanded}
          onClick={() => setExpanded(!expanded)}
          className="rounded-sm px-1.5 py-0.5 text-[11px] font-medium text-pitch-200 hover:bg-pitch-400/10 focus-ring"
        >
          {expanded ? "show fewer" : `+${overlay.cited_event_ids.length - 4} events`}
        </button>
      )}
    </div>
  );
}

/**
 * The confidence: a thin, solid bar and the number.
 *
 * No glow, no gradient - a filled length on a hairline track says the
 * same thing and stays quiet while forty of them stack up.
 */
function Confidence({ value }: { value: number }) {
  const percent = Math.round(value * 100);
  return (
    <div className="flex items-center gap-1.5" title={`${percent}% confidence`}>
      <div
        className="h-[3px] w-10 overflow-hidden rounded-full bg-ink-700"
        role="meter"
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Confidence"
      >
        <div
          className="h-full rounded-full bg-pitch-400"
          style={{ width: `${percent}%` }}
        />
      </div>
      <span className="text-[10px] tabular-nums text-ink-500">{percent}%</span>
    </div>
  );
}

/**
 * The numbers behind the claim.
 *
 * A side's number carries that side's colour as a tab, so the reader can
 * see at a glance which side a figure is about. The figure itself is
 * always in plain ink: the clubs' colours are muted, and a muted number
 * on a dark ground is a number nobody can read.
 */
function Metrics({
  metrics,
  teams,
  hero,
}: {
  metrics: MetricValue[];
  teams: Teams | null;
  hero: boolean;
}) {
  if (metrics.length === 0) return null;

  if (hero) {
    return (
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2.5 sm:grid-cols-3">
        {metrics.map((metric) => (
          <div key={metric.name} className="min-w-0">
            <dt className="flex items-start gap-1.5 text-[10px] leading-[1.25] text-ink-400">
              <span
                className="mt-[1px] h-2.5 w-[2px] shrink-0 rounded-[1px]"
                style={{ backgroundColor: sideColour(teams, metric.side) }}
                aria-hidden="true"
              />
              <span className="line-clamp-2">{metric.label}</span>
            </dt>
            <dd className="mt-1 font-display text-[26px] font-semibold leading-none tabular-nums text-ink-50">
              {formatMetricValue(metric.value)}
              <span className="ml-1 font-sans text-[12px] font-medium text-ink-400">
                {metric.unit}
              </span>
            </dd>
            {metric.subject && (
              <dd className="mt-0.5 truncate text-[10px] text-ink-500">
                {metric.subject}
              </dd>
            )}
          </div>
        ))}
      </dl>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-ink-400">
      {metrics.map((metric) => (
        <span key={metric.name} className="inline-flex items-center gap-1">
          <span
            className="h-2.5 w-[2px] shrink-0 rounded-[1px]"
            style={{ backgroundColor: sideColour(teams, metric.side) }}
            aria-hidden="true"
          />
          {metric.label}
          <span className="font-semibold tabular-nums text-ink-100">
            {formatMetricValue(metric.value)}
          </span>
          <span className="text-ink-500">{metric.unit}</span>
        </span>
      ))}
    </div>
  );
}

/** The two card-level controls, as quiet text rather than as buttons. */
function Actions({
  overlay,
  selected,
  onSelect,
  onInspect,
}: {
  overlay: Overlay;
  selected: boolean;
  onSelect: (candidateId: string | null) => void;
  onInspect: (candidateId: string) => void;
}) {
  return (
    <div className="flex items-center gap-3">
      <button
        type="button"
        onClick={() => onInspect(overlay.candidate_id)}
        className="text-[10px] font-semibold uppercase tracking-[0.1em] text-ink-500 underline decoration-transparent underline-offset-2 transition-colors hover:text-ink-100 hover:decoration-ink-600 focus-ring"
        title="Open the evidence behind this insight: events, recomputed metrics and the grounding verdict"
      >
        inspect evidence
      </button>
      <button
        type="button"
        onClick={() =>
          onSelect(selected ? null : overlay.candidate_id)
        }
        data-active={selected}
        className={[
          "text-[10px] font-semibold uppercase tracking-[0.1em] underline",
          "decoration-transparent underline-offset-2 transition-colors focus-ring",
          selected
            ? "text-pitch-300 decoration-pitch-400/70"
            : "text-ink-500 hover:text-ink-100 hover:decoration-ink-600",
        ].join(" ")}
        title="Show only this moment's hand-offs in the agent trace"
      >
        {selected ? "hide hand-offs" : "show hand-offs"}
      </button>
    </div>
  );
}

/**
 * The card the match has reached.
 *
 * The newest insight is the moment on screen right now, so it is the one
 * that gets the display face, the figures at size, and the room. It is
 * separated from the ledger below it by its own surface rather than by a
 * rule, because it is a different kind of thing.
 */
function HeroCard({
  overlay,
  teams,
  highlightedEventId,
  selectedCandidate,
  onHighlight,
  onSelect,
  onInspect,
}: {
  overlay: Overlay;
  teams: Teams | null;
  highlightedEventId: string | null;
  selectedCandidate: string | null;
  onHighlight: (eventId: string | null) => void;
  onSelect: (candidateId: string | null) => void;
  onInspect: (candidateId: string) => void;
}) {
  return (
    <article className="animate-card-in border-l-[3px] border-l-pitch-400 bg-ink-850 px-4 py-4">
      <MetaRow overlay={overlay} />

      <h3 className="mt-2 font-display text-[28px] font-semibold leading-[1.1] text-ink-50">
        {overlay.title}
      </h3>
      <p className="mt-2 text-[14px] leading-[1.6] text-ink-200">
        {overlay.body}
      </p>

      {overlay.metrics.length > 0 && (
        <div className="mt-3">
          <Metrics metrics={overlay.metrics} teams={teams} hero />
        </div>
      )}

      <div className="mt-3">
        <Citations
          overlay={overlay}
          highlightedEventId={highlightedEventId}
          onHighlight={onHighlight}
          hero
        />
      </div>

      <div className="mt-2.5 flex items-center justify-between gap-3 border-t border-ink-800 pt-2.5">
        <Confidence value={overlay.confidence} />
        <Actions
          overlay={overlay}
          selected={selectedCandidate === overlay.candidate_id}
          onSelect={onSelect}
          onInspect={onInspect}
        />
      </div>
    </article>
  );
}

/**
 * A ledger entry: the rest of the feed.
 *
 * Ruled, not boxed. The rows are meant to be scanned, so the only
 * vertical structure is a hairline between them.
 */
function CompactCard({
  overlay,
  teams,
  highlightedEventId,
  selectedCandidate,
  onHighlight,
  onSelect,
  onInspect,
}: {
  overlay: Overlay;
  teams: Teams | null;
  highlightedEventId: string | null;
  selectedCandidate: string | null;
  onHighlight: (eventId: string | null) => void;
  onSelect: (candidateId: string | null) => void;
  onInspect: (candidateId: string) => void;
}) {
  const selected = selectedCandidate === overlay.candidate_id;

  return (
    <article
      className={[
        "border-b border-ink-800/80 px-3.5 py-2.5 transition-colors",
        selected ? "bg-ink-850/80" : "hover:bg-ink-850/40",
      ].join(" ")}
    >
      <div className="flex items-center gap-3">
        <MetaRow overlay={overlay} className="flex-1" />
        <Confidence value={overlay.confidence} />
      </div>

      <h3 className="mt-1.5 font-display text-[17px] font-semibold leading-[1.2] text-ink-50">
        {overlay.title}
      </h3>
      <p
        className="mt-1 line-clamp-3 text-[12.5px] leading-[1.5] text-ink-300"
        title={overlay.body}
      >
        {overlay.body}
      </p>

      {overlay.metrics.length > 0 && (
        <div className="mt-1.5">
          <Metrics metrics={overlay.metrics} teams={teams} hero={false} />
        </div>
      )}

      <div className="mt-1.5">
        <Citations
          overlay={overlay}
          highlightedEventId={highlightedEventId}
          onHighlight={onHighlight}
          hero={false}
        />
      </div>

      <div className="mt-1.5 flex items-center justify-end">
        <Actions
          overlay={overlay}
          selected={selected}
          onSelect={onSelect}
          onInspect={onInspect}
        />
      </div>
    </article>
  );
}

export default function OverlayFeed({
  overlays,
  total,
  teams,
  highlightedEventId,
  selectedCandidate,
  onHighlight,
  onSelect,
  onInspect,
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
    <div className="panel flex min-h-0 flex-1 flex-col overflow-hidden">
      <div className="flex items-baseline justify-between gap-2 border-b border-ink-800 px-3.5 py-2">
        <h2 className="panel-title">Key moments</h2>
        <span className="text-[11px] tabular-nums text-ink-500">
          {total} insight{total === 1 ? "" : "s"} in the match
        </span>
      </div>

      <div ref={listRef} className="min-h-0 flex-1 overflow-y-auto">
        {overlays.length === 0 && (
          <p className="px-3.5 py-10 text-center text-[11px] text-ink-500">
            waiting for the first insight
          </p>
        )}

        {overlays.map((overlay, index) =>
          index === 0 ? (
            <HeroCard
              key={overlay.overlay_id}
              overlay={overlay}
              teams={teams}
              highlightedEventId={highlightedEventId}
              selectedCandidate={selectedCandidate}
              onHighlight={onHighlight}
              onSelect={onSelect}
              onInspect={onInspect}
            />
          ) : (
            <CompactCard
              key={overlay.overlay_id}
              overlay={overlay}
              teams={teams}
              highlightedEventId={highlightedEventId}
              selectedCandidate={selectedCandidate}
              onHighlight={onHighlight}
              onSelect={onSelect}
              onInspect={onInspect}
            />
          ),
        )}
      </div>
    </div>
  );
}
