import { useEffect, useState } from "react";

import {
  formatClock,
  formatMetricValue,
  type EvidencePayload,
  type Handoff,
} from "../types";

/**
 * The evidence drawer: one insight, followed from explanation to events
 * to metric verification.
 *
 * A judge opens this with "Inspect evidence" on an insight card. The
 * drawer fetches `GET /api/evidence/{candidate_id}` and shows what the
 * service computed, recomputed from the event log with the deterministic
 * functions: calculated facts kept apart from model interpretation, the
 * cited events with their match times, each metric's definition with the
 * displayed and the recomputed value, the earlier baseline evidence where
 * the insight has one, and the grounding verdict with the hand-offs about
 * this moment.
 *
 * The drawer makes no model calls and changes no audience state: the
 * fetch is a plain GET on a read-only route. Semantic prose is not
 * formally verified, and the drawer says so.
 */

export interface EvidenceDrawerProps {
  /** The insight to inspect, or `null` for a closed drawer. */
  candidateId: string | null;
  /** The match the insight belongs to. */
  seed: number;
  /** Close the drawer. */
  onClose: () => void;
}

type LoadState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "failed"; message: string }
  | { kind: "ready"; evidence: EvidencePayload };

function SectionTitle({ children }: { children: string }) {
  return (
    <h3 className="panel-title mt-4 first:mt-0 border-b border-studio-800 pb-1">
      {children}
    </h3>
  );
}

function GroundingBadge({ status }: { status: string }) {
  if (status === "accepted") {
    return (
      <span className="rounded border border-emerald-500/60 bg-emerald-500/15 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider text-emerald-300">
        grounded
      </span>
    );
  }
  if (status === "vetoed") {
    return (
      <span className="rounded border border-red-500/70 bg-red-500/20 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider text-red-300">
        vetoed
      </span>
    );
  }
  return (
    <span className="rounded border border-studio-600 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider text-studio-400">
      not recorded
    </span>
  );
}

function HandoffRow({ entry }: { entry: Handoff }) {
  const veto = entry.accepted === false;
  return (
    <li
      className={[
        "rounded border-l-[3px] bg-studio-850/70 px-2 py-1",
        veto ? "border-l-red-500" : "border-l-studio-600",
      ].join(" ")}
    >
      <div className="flex flex-wrap items-center gap-x-1.5 text-[10px]">
        <span className="font-mono font-semibold text-studio-300">
          {entry.from_stage} → {entry.to_stage}
        </span>
        <span className="rounded border border-studio-600 bg-studio-800 px-1 py-px font-mono uppercase tracking-wide text-studio-500">
          {entry.kind}
        </span>
        {veto && (
          <span className="rounded border border-red-500/70 bg-red-500/20 px-1 py-px text-[10px] font-bold uppercase tracking-wider text-red-300">
            veto
          </span>
        )}
        {entry.provider && (
          <span className="ml-auto font-mono text-studio-500">
            {entry.provider}
          </span>
        )}
      </div>
      <p className="mt-0.5 text-[11px] leading-snug text-studio-400">
        {entry.summary}
      </p>
    </li>
  );
}

export default function EvidenceDrawer({
  candidateId,
  seed,
  onClose,
}: EvidenceDrawerProps) {
  const [state, setState] = useState<LoadState>({ kind: "idle" });

  // The fetch is a plain GET on a read-only route: it recomputes the
  // numbers and reads the trace, and it changes nothing it touches.
  useEffect(() => {
    if (candidateId === null) {
      setState({ kind: "idle" });
      return;
    }
    let cancelled = false;
    setState({ kind: "loading" });
    fetch(`/api/evidence/${encodeURIComponent(candidateId)}?seed=${seed}`)
      .then(async (response) => {
        if (!response.ok) {
          const detail = await response
            .json()
            .then((body) => (body as { detail?: string }).detail)
            .catch(() => undefined);
          throw new Error(
            detail ??
              `the service answered ${response.status} for ${candidateId}`,
          );
        }
        return response.json() as Promise<EvidencePayload>;
      })
      .then((evidence) => {
        if (!cancelled) setState({ kind: "ready", evidence });
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setState({
            kind: "failed",
            message:
              error instanceof Error ? error.message : "the evidence did not load",
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [candidateId, seed]);

  // Escape closes the drawer. Nothing else here needs a keyboard.
  useEffect(() => {
    if (candidateId === null) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [candidateId, onClose]);

  if (candidateId === null) return null;

  const evidence = state.kind === "ready" ? state.evidence : null;

  return (
    <div
      className="fixed inset-0 z-50"
      role="dialog"
      aria-modal="true"
      aria-label={`Evidence for ${candidateId}`}
    >
      <div
        className="absolute inset-0 bg-black/60"
        onClick={onClose}
        aria-hidden="true"
      />
      <aside className="panel absolute right-0 top-0 flex h-full w-full max-w-xl flex-col overflow-hidden !rounded-none border-y-0 border-r-0">
        <div className="flex items-center justify-between gap-2 border-b border-studio-800 px-4 py-3">
          <div>
            <h2 className="text-sm font-semibold text-white">
              Evidence
            </h2>
            <p className="font-mono text-[11px] text-studio-400">
              {candidateId}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded border border-studio-600 bg-studio-800 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wider text-studio-300 transition-colors hover:border-studio-400 hover:text-white"
          >
            Close
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3 text-[12px]">
          {state.kind === "loading" && (
            <p className="py-8 text-center text-studio-500">
              recomputing from the event log…
            </p>
          )}
          {state.kind === "failed" && (
            <p className="py-8 text-center text-[12px] text-red-300">
              {state.message}
            </p>
          )}
          {evidence && (
            <>
              {/* Calculated facts first, interpretation second: the two
                  are shown apart on purpose. */}
              <SectionTitle>Calculated — deterministic</SectionTitle>
              {evidence.calculated.present ? (
                <>
                  <p className="mt-1 leading-relaxed text-studio-200">
                    {evidence.calculated.headline}
                  </p>
                  <dl className="mt-1 grid grid-cols-2 gap-x-3 gap-y-0.5 text-[11px] text-studio-400">
                    <dt>kind</dt>
                    <dd className="font-mono">{evidence.calculated.kind}</dd>
                    <dt>match clock</dt>
                    <dd className="font-mono">
                      {evidence.calculated.match_clock_ms !== undefined &&
                        formatClock(evidence.calculated.match_clock_ms)}
                    </dd>
                    <dt>side</dt>
                    <dd className="font-mono">
                      {evidence.calculated.side ?? "—"}
                    </dd>
                    <dt>confidence</dt>
                    <dd className="font-mono">
                      {evidence.calculated.confidence !== undefined &&
                        `${Math.round(evidence.calculated.confidence * 100)}%`}
                    </dd>
                  </dl>
                </>
              ) : (
                <p className="mt-1 rounded border border-amber-500/40 bg-amber-500/10 px-2 py-1.5 text-[11px] leading-snug text-amber-200">
                  <span className="font-semibold uppercase tracking-wider">
                    missing evidence:{" "}
                  </span>
                  {evidence.calculated.note}
                </p>
              )}

              <SectionTitle>Interpretation — what the card says</SectionTitle>
              <div className="mt-1 rounded border border-studio-700 bg-studio-850/60 p-2">
                <p className="text-sm font-semibold text-white">
                  {evidence.interpretation.title}
                </p>
                <p className="mt-0.5 leading-relaxed text-studio-300">
                  {evidence.interpretation.body}
                </p>
                <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                  {evidence.interpretation.provenance_provider && (
                    <span className="rounded border border-studio-600 px-1.5 py-px text-[10px] font-medium text-studio-400">
                      {evidence.interpretation.provenance_provider}
                    </span>
                  )}
                  {evidence.interpretation.deterministic_prose && (
                    <span className="rounded border border-studio-600 px-1.5 py-px text-[10px] font-medium uppercase tracking-wider text-studio-400">
                      deterministic
                    </span>
                  )}
                  {evidence.interpretation.prose_substituted &&
                    !evidence.interpretation.deterministic_prose && (
                      <span className="rounded border border-sky-500/50 px-1.5 py-px text-[10px] font-medium uppercase tracking-wider text-sky-300">
                        deterministic copy
                      </span>
                    )}
                </div>
                <p className="mt-1.5 text-[11px] italic leading-snug text-studio-500">
                  {evidence.interpretation.note}
                </p>
              </div>

              <SectionTitle>Cited events</SectionTitle>
              <ul className="mt-1 space-y-1">
                {evidence.cited_events.map((event) =>
                  event.found ? (
                    <li
                      key={event.event_id}
                      className="rounded border border-studio-800 bg-studio-850/60 px-2 py-1"
                    >
                      <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px]">
                        <span className="font-mono font-semibold text-studio-200">
                          {event.type}
                        </span>
                        <span className="font-mono tabular-nums text-amber-200">
                          {event.match_clock_ms !== undefined &&
                            formatClock(event.match_clock_ms)}
                        </span>
                        {event.side && (
                          <span className="font-mono text-studio-400">
                            {event.side}
                          </span>
                        )}
                        {event.player_name && (
                          <span className="text-studio-300">
                            {event.player_name}
                          </span>
                        )}
                        {event.outcome && (
                          <span className="text-studio-500">
                            {event.outcome}
                          </span>
                        )}
                        <span className="ml-auto font-mono text-[10px] text-studio-500">
                          {event.event_id} · {event.score_home}–
                          {event.score_away}
                        </span>
                      </div>
                    </li>
                  ) : (
                    <li
                      key={event.event_id}
                      className="rounded border border-amber-500/40 bg-amber-500/10 px-2 py-1 text-[11px] text-amber-200"
                    >
                      <span className="font-semibold uppercase tracking-wider">
                        missing evidence:{" "}
                      </span>
                      {event.note}
                    </li>
                  ),
                )}
              </ul>

              <SectionTitle>Metric verification</SectionTitle>
              {evidence.metrics.length === 0 && (
                <p className="mt-1 text-[11px] text-studio-500">
                  no metrics on this card
                </p>
              )}
              {evidence.metrics.map((metric) => (
                <div
                  key={metric.name}
                  className="mt-1 rounded border border-studio-800 bg-studio-850/60 p-2"
                >
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <span className="font-semibold text-studio-200">
                      {metric.label}
                    </span>
                    {metric.subject && (
                      <span className="text-[11px] text-studio-500">
                        · {metric.subject}
                      </span>
                    )}
                    <span className="ml-auto">
                      {metric.note !== null ||
                      metric.recomputed_value === null ? (
                        <span className="rounded border border-amber-500/40 bg-amber-500/10 px-1.5 py-px text-[10px] font-bold uppercase tracking-wider text-amber-200">
                          not recomputed
                        </span>
                      ) : metric.match ? (
                        <span className="rounded border border-emerald-500/60 bg-emerald-500/15 px-1.5 py-px text-[10px] font-bold uppercase tracking-wider text-emerald-300">
                          verified
                        </span>
                      ) : (
                        <span className="rounded border border-red-500/70 bg-red-500/20 px-1.5 py-px text-[10px] font-bold uppercase tracking-wider text-red-300">
                          mismatch
                        </span>
                      )}
                    </span>
                  </div>
                  <p className="mt-0.5 text-[11px] leading-snug text-studio-400">
                    {metric.definition}
                  </p>
                  <dl className="mt-1 grid grid-cols-2 gap-x-3 text-[11px]">
                    <dt className="text-studio-500">displayed</dt>
                    <dd className="text-right font-mono font-semibold tabular-nums text-studio-200">
                      {formatMetricValue(metric.displayed_value)} {metric.unit}
                    </dd>
                    <dt className="text-studio-500">recomputed</dt>
                    <dd className="text-right font-mono font-semibold tabular-nums text-studio-200">
                      {metric.recomputed_value !== null
                        ? `${formatMetricValue(metric.recomputed_value)} ${metric.unit}`
                        : "—"}
                    </dd>
                  </dl>
                  {metric.note && (
                    <p className="mt-1 text-[11px] leading-snug text-amber-200">
                      <span className="font-semibold uppercase tracking-wider">
                        missing evidence:{" "}
                      </span>
                      {metric.note}
                    </p>
                  )}
                </div>
              ))}

              <SectionTitle>Baseline evidence</SectionTitle>
              {evidence.baseline.present ? (
                <div className="mt-1 rounded border border-studio-800 bg-studio-850/60 p-2">
                  <p className="text-[11px] leading-snug text-studio-400">
                    {evidence.baseline.definition}
                  </p>
                  <dl className="mt-1 grid grid-cols-2 gap-x-3 text-[11px]">
                    <dt className="text-studio-500">earlier value</dt>
                    <dd className="text-right font-mono font-semibold tabular-nums text-studio-200">
                      {evidence.baseline.earlier_value !== undefined &&
                        formatMetricValue(evidence.baseline.earlier_value)}{" "}
                      {evidence.baseline.unit}
                    </dd>
                    <dt className="text-studio-500">recomputed</dt>
                    <dd className="text-right font-mono font-semibold tabular-nums text-studio-200">
                      {evidence.baseline.recomputed_value !== undefined &&
                      evidence.baseline.recomputed_value !== null
                        ? formatMetricValue(evidence.baseline.recomputed_value)
                        : "—"}{" "}
                      {evidence.baseline.unit}
                    </dd>
                    <dt className="text-studio-500">delta</dt>
                    <dd
                      className="text-right font-mono tabular-nums text-studio-400"
                      title={evidence.baseline.delta_note}
                    >
                      {evidence.baseline.delta !== undefined &&
                        formatMetricValue(evidence.baseline.delta)}
                    </dd>
                    <dt className="text-studio-500">window</dt>
                    <dd className="text-right font-mono tabular-nums text-studio-400">
                      {evidence.baseline.window_ms !== undefined &&
                        formatClock(evidence.baseline.window_ms)}{" "}
                      earlier
                    </dd>
                  </dl>
                  <div className="mt-1 flex items-center justify-between">
                    <span className="text-[11px] text-studio-500">
                      {evidence.baseline.match ? "recomputes" : "does not recompute"}
                      {evidence.baseline.missing_event_ids &&
                        evidence.baseline.missing_event_ids.length > 0 &&
                        ` · ${evidence.baseline.missing_event_ids.length} cited event${evidence.baseline.missing_event_ids.length === 1 ? "" : "s"} missing`}
                    </span>
                    {evidence.baseline.match ? (
                      <span className="rounded border border-emerald-500/60 bg-emerald-500/15 px-1.5 py-px text-[10px] font-bold uppercase tracking-wider text-emerald-300">
                        verified
                      </span>
                    ) : (
                      <span className="rounded border border-amber-500/40 bg-amber-500/10 px-1.5 py-px text-[10px] font-bold uppercase tracking-wider text-amber-200">
                        check
                      </span>
                    )}
                  </div>
                  {evidence.baseline.note && (
                    <p className="mt-1 text-[11px] leading-snug text-amber-200">
                      <span className="font-semibold uppercase tracking-wider">
                        missing evidence:{" "}
                      </span>
                      {evidence.baseline.note}
                    </p>
                  )}
                </div>
              ) : (
                <p className="mt-1 text-[11px] text-studio-500">
                  {evidence.baseline.note}
                </p>
              )}

              <SectionTitle>Grounding verdict</SectionTitle>
              <div className="mt-1 flex items-center gap-2">
                <GroundingBadge status={evidence.grounding.status} />
                <p className="text-[11px] leading-snug text-studio-400">
                  {evidence.grounding.status_note}
                </p>
              </div>
              {evidence.grounding.handoffs.length > 0 && (
                <ul className="mt-2 space-y-1">
                  {evidence.grounding.handoffs.map((entry) => (
                    <HandoffRow key={entry.index} entry={entry} />
                  ))}
                </ul>
              )}
            </>
          )}
        </div>
      </aside>
    </div>
  );
}
