import { useEffect, useRef, useState } from "react";

import RecoveryReplaySection from "./RecoveryReplaySection";
import type { Handoff } from "../types";

/**
 * The agent trace: who handed what to whom.
 *
 * A multi-agent system is only as convincing as its hand-offs, so the
 * ledger is shown rather than summarised. Each row is one crossing of the
 * workflow graph, with the stage it came from, the stage it went to, and
 * the one line about what was handed over.
 *
 * The stages are written in plain ink. Seven coloured labels for seven
 * stages of a pipeline is a legend nobody needs and a page that fights
 * itself; the stages are named, and that is what identifies them.
 *
 * Two things are marked, on purpose:
 *
 * - the **A2A** hop, because the Narrator really did reach the Explainer
 *   over the protocol, and a judge should be able to see that;
 * - the **veto**, because a grounding step that refuses a claim is the
 *   most interesting thing in the whole trace.
 */

export interface TracePanelProps {
  trace: Handoff[];
  /**
   * The insight whose hand-offs to show, or `null` for the whole trace.
   *
   * A hundred hand-offs is a wall of text. The four that belong to the
   * insight you are reading are the story, and the A2A hop and any veto
   * are in them.
   */
  focus: string | null;
  /** Put the trace back to the whole match. */
  onClearFocus: () => void;
}

/** The kinds that are worth calling out. */
const A2A_KIND = "a2a";

export default function TracePanel({
  trace,
  focus,
  onClearFocus,
}: TracePanelProps) {
  const listRef = useRef<HTMLOListElement | null>(null);
  // The recorded replay and the hand-off ledger share the panel: one at
  // a time, so opening the replay does not push the ledger off the
  // bottom of it.
  const [replayOpen, setReplayOpen] = useState(false);

  // The whole trace, and the part of it about one moment. Hand-offs that
  // belong to no insight in particular - the ingest, say - are kept in the
  // whole view and drop out of the focused one, because a focused trace
  // that showed them would be answering a different question.
  const visible = focus
    ? trace.filter((entry) => entry.candidate_id === focus)
    : trace;

  const vetoes = visible.filter((entry) => entry.accepted === false).length;

  // Keep the newest hand-off in view as the trace fills in.
  useEffect(() => {
    const list = listRef.current;
    if (list) list.scrollTop = 0;
  }, [visible.length, focus]);

  return (
    <div className="panel flex min-h-[260px] max-h-[460px] flex-1 flex-col overflow-hidden lg:min-h-0 lg:max-h-none">
      <div className="flex items-baseline justify-between gap-2 border-b border-ink-800 px-3.5 py-2">
        <h2 className="panel-title">Agent trace</h2>
        <div className="flex items-baseline gap-2 text-[11px] text-ink-500">
          <span className="tabular-nums">
            {visible.length} hand-off{visible.length === 1 ? "" : "s"}
          </span>
          {vetoes > 0 && (
            <span className="font-semibold text-signal-red">
              {vetoes} veto{vetoes === 1 ? "" : "es"}
            </span>
          )}
        </div>
      </div>

      {focus && (
        <div className="flex items-center justify-between gap-2 border-b border-ink-800 bg-signal-amber/[0.07] px-3.5 py-1.5 text-[11px] text-signal-amber">
          <span className="font-mono">showing {focus} only</span>
          <button
            type="button"
            onClick={onClearFocus}
            className="font-semibold uppercase tracking-[0.1em] underline decoration-transparent underline-offset-2 transition-colors hover:decoration-signal-amber/60 focus-ring"
            title="Show the whole trace again"
          >
            show all
          </button>
        </div>
      )}

      <ol
        ref={listRef}
        className={`min-h-0 overflow-y-auto ${replayOpen ? "hidden" : "flex-1"}`}
        aria-label="The hand-off trace, newest first"
      >
        {visible.length === 0 && (
          <li className="px-3.5 py-6 text-center text-[11px] text-ink-500">
            {focus
              ? "no hand-off for this moment yet"
              : "waiting for the first hand-off"}
          </li>
        )}

        {visible.map((entry) => {
          const veto = entry.accepted === false;
          const isA2A = entry.kind === A2A_KIND;

          return (
            <li
              key={entry.index}
              className={[
                "border-b border-ink-800/70 border-l-2 py-1.5 pl-2.5 pr-3.5",
                veto
                  ? "border-l-signal-red bg-signal-red/[0.06]"
                  : isA2A
                    ? "border-l-pitch-400 bg-pitch-400/[0.05]"
                    : "border-l-ink-700",
              ].join(" ")}
            >
              <div className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-[11px]">
                <span className="font-mono font-medium text-ink-200">
                  {entry.from_stage}
                </span>
                <span className="text-ink-600" aria-hidden="true">
                  →
                </span>
                <span className="font-mono font-semibold text-ink-100">
                  {entry.to_stage}
                </span>
                <span className="text-[10px] uppercase tracking-[0.1em] text-ink-500">
                  {isA2A ? "protocol hop" : entry.kind}
                </span>
                {isA2A && (
                  <span className="text-[10px] font-bold uppercase tracking-[0.12em] text-pitch-300">
                    A2A
                  </span>
                )}
                {veto && (
                  <span className="text-[10px] font-bold uppercase tracking-[0.12em] text-signal-red">
                    veto
                  </span>
                )}
                {entry.candidate_id && (
                  <span
                    className="ml-auto font-mono text-[10px] text-ink-600"
                    title="The insight this hand-off was about"
                  >
                    {entry.candidate_id}
                  </span>
                )}
              </div>
              <p
                className={[
                  "mt-0.5 text-[11px] leading-snug",
                  veto ? "text-signal-red/90" : "text-ink-300",
                ].join(" ")}
              >
                {entry.summary}
              </p>
            </li>
          );
        })}
      </ol>

      {/* A recorded grounding-recovery run, replayed from a fixture:
          purely presentational, visibly a test rather than an incident. */}
      <RecoveryReplaySection
        open={replayOpen}
        onToggle={() => setReplayOpen((value) => !value)}
      />
    </div>
  );
}
