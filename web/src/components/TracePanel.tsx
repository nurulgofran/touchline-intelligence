import { useEffect, useRef } from "react";

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
 * Two things are made loud on purpose:
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

/** The colour each stage is drawn in. */
const STAGE_COLOURS: Record<string, string> = {
  ingest: "#94a3b8",
  analyst: "#38bdf8",
  explainer: "#a78bfa",
  grounding: "#34d399",
  narrator: "#fbbf24",
  personalizer: "#f472b6",
  screen: "#94a3b8",
};

const FALLBACK_STAGE_COLOUR = "#94a3b8";

/** The kinds that are worth calling out. */
const A2A_KIND = "a2a";

function stageColour(stage: string): string {
  return STAGE_COLOURS[stage] ?? FALLBACK_STAGE_COLOUR;
}

export default function TracePanel({
  trace,
  focus,
  onClearFocus,
}: TracePanelProps) {
  const listRef = useRef<HTMLOListElement | null>(null);

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
    <div className="panel flex min-h-0 flex-1 flex-col overflow-hidden">
      <div className="flex items-center justify-between gap-2 px-3 pt-2.5 pb-1.5">
        <h2 className="panel-title">Agent trace</h2>
        <div className="flex items-center gap-2 text-[11px]">
          <span className="rounded-full border border-studio-600 bg-studio-850 px-2 py-0.5 text-studio-300">
            {visible.length} hand-off{visible.length === 1 ? "" : "s"}
          </span>
          <span
            className={
              vetoes > 0
                ? "rounded-full border border-red-500/60 bg-red-500/15 px-2 py-0.5 font-semibold text-red-300"
                : "rounded-full border border-studio-600 bg-studio-850 px-2 py-0.5 text-studio-400"
            }
          >
            {vetoes} veto{vetoes === 1 ? "" : "es"}
          </span>
        </div>
      </div>

      {focus && (
        <div className="mx-2 mb-1 flex items-center justify-between gap-2 rounded border border-amber-400/40 bg-amber-400/10 px-2 py-1 text-[11px] text-amber-200">
          <span className="font-mono">
            showing {focus} only
          </span>
          <button
            type="button"
            onClick={onClearFocus}
            className="rounded border border-amber-400/50 bg-transparent px-1.5 py-px text-[10px] font-semibold uppercase tracking-wider text-amber-200 transition-colors hover:border-amber-300 hover:text-amber-100"
            title="Show the whole trace again"
          >
            show all
          </button>
        </div>
      )}

      <ol
        ref={listRef}
        className="min-h-0 flex-1 overflow-y-auto px-2 pb-2"
        aria-label="The hand-off trace, newest first"
      >
        {visible.length === 0 && (
          <li className="px-1 py-6 text-center text-[11px] text-studio-500">
            {focus
              ? "no hand-off for this moment yet"
              : "waiting for the first hand-off"}
          </li>
        )}

        {visible.map((entry) => {
          const veto = entry.accepted === false;
          const isA2A = entry.kind === A2A_KIND;
          const fromColour = stageColour(entry.from_stage);
          const toColour = stageColour(entry.to_stage);

          return (
            <li
              key={entry.index}
              className={[
                "mb-1.5 rounded-md border-l-[3px] bg-studio-850/70 px-2 py-1.5",
                veto
                  ? "border-l-red-500 bg-red-500/[0.07]"
                  : isA2A
                    ? "border-l-amber-400 bg-amber-400/[0.07] ring-1 ring-amber-400/25"
                    : "border-l-studio-600",
              ].join(" ")}
            >
              <div className="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-[11px]">
                <span
                  className="font-mono font-semibold"
                  style={{ color: fromColour }}
                >
                  {entry.from_stage}
                </span>
                <span className="text-studio-600" aria-hidden="true">
                  →
                </span>
                <span
                  className="font-mono font-semibold"
                  style={{ color: toColour }}
                >
                  {entry.to_stage}
                </span>
                <span className="rounded border border-studio-600 bg-studio-800 px-1 py-px font-mono text-[10px] uppercase tracking-wide text-studio-400">
                  {entry.kind}
                </span>
                {isA2A && (
                  <span className="rounded border border-amber-400/60 bg-amber-400/15 px-1 py-px text-[10px] font-bold uppercase tracking-wider text-amber-300">
                    A2A
                  </span>
                )}
                {veto && (
                  <span className="rounded border border-red-500/70 bg-red-500/20 px-1 py-px text-[10px] font-bold uppercase tracking-wider text-red-300">
                    veto
                  </span>
                )}
                {entry.candidate_id && (
                  <span
                    className="ml-auto font-mono text-[10px] text-studio-500"
                    title="The insight this hand-off was about"
                  >
                    {entry.candidate_id}
                  </span>
                )}
              </div>
              <p
                className={
                  veto
                    ? "mt-0.5 text-[11px] leading-snug text-red-200"
                    : "mt-0.5 text-[11px] leading-snug text-studio-300"
                }
              >
                {entry.summary}
              </p>
            </li>
          );
        })}
      </ol>

      {/* A recorded grounding-recovery run, replayed from a fixture:
          purely presentational, visibly a test rather than an incident. */}
      <RecoveryReplaySection />
    </div>
  );
}
