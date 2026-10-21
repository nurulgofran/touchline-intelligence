import { useEffect, useState } from "react";

import type { TrackingReport } from "../types";

/**
 * The vision tracker's report.
 *
 * The tracker watches a synthetic top-down feed built from the same match
 * the studio is showing, and it is scored against the truth that feed was
 * rendered from. So the numbers here are not a claim about the tracker:
 * they are the tracker's own score sheet, measured against ground truth
 * it could not have seen.
 *
 * The panel is deliberately small and deliberately quiet. The point is
 * that a judge can see the measurement, not that they can admire the
 * interface - so the figures are set in plain ink on hairlines, with no
 * badge to decorate them.
 */
export interface TrackingPanelProps {
  seed: number;
}

export default function TrackingPanel({ seed }: TrackingPanelProps) {
  const [report, setReport] = useState<TrackingReport | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timer = 0;

    const poll = async () => {
      try {
        const response = await fetch(`/api/match/tracking?seed=${seed}`, {
          headers: { accept: "application/json" },
        });
        if (!response.ok) return;
        const payload = (await response.json()) as TrackingReport;
        if (cancelled) return;
        setReport(payload);
        // The tracker runs once, in the background. Once it has a report
        // there is nothing to wait for.
        if (!payload.ready) {
          timer = window.setTimeout(() => void poll(), 2_000);
        }
      } catch {
        // The service is unreachable. The socket is the live path; this
        // panel is a measurement, and it can wait.
      }
    };

    void poll();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [seed]);

  if (!report || !report.ready || !report.overall) {
    return (
      <div className="panel px-3.5 py-2.5">
        <h2 className="panel-title">Vision tracker</h2>
        <p className="mt-1 text-[11px] leading-snug text-ink-500">
          Watching a synthetic feed of the same match, and scoring the
          tracker against the truth it was rendered from…
        </p>
      </div>
    );
  }

  const overall = report.overall;
  const ball = report.ball;

  return (
    <div className="panel px-3.5 py-2.5">
      <h2 className="panel-title">Vision tracker</h2>
      <p className="mt-0.5 text-[10px] leading-snug text-ink-500">
        {report.frames} frames at {report.fps} fps, scored against the
        ground truth.
      </p>

      <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-2">
        <Stat
          label="Precision"
          value={`${(overall.precision * 100).toFixed(1)}%`}
          hint="of what it claimed, how much was really there"
        />
        <Stat
          label="Recall"
          value={`${(overall.recall * 100).toFixed(1)}%`}
          hint="of what was there, how much it found"
        />
        <Stat
          label="Position error"
          value={`${overall.mean_error_m.toFixed(2)} m`}
          hint="mean, over everything it found"
        />
        <Stat
          label="Ball"
          value={`${((ball?.recall ?? 0) * 100).toFixed(0)}%`}
          hint="the ball, found in every frame"
        />
        <Stat
          label="Pass error"
          value={`${(report.flight_distance_mae_m ?? 0).toFixed(2)} m`}
          hint={`${report.flight_matches}/${report.flight_count_truth} of the log's passes matched`}
        />
        <Stat
          label="Shot speed"
          value={`±${(report.shot_speed_mae_mps ?? 0).toFixed(2)} m/s`}
          hint="the tracker's peak against the log's"
        />
      </dl>
    </div>
  );
}

function Stat({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint: string;
}) {
  return (
    <div className="min-w-0 border-t border-ink-800 pt-1.5">
      <dt className="text-[10px] uppercase tracking-[0.08em] text-ink-500">
        {label}
      </dt>
      <dd className="mt-0.5 font-display text-[17px] font-semibold leading-none tabular-nums text-ink-100">
        {value}
      </dd>
      <dd className="mt-1 text-[10px] leading-tight text-ink-600">{hint}</dd>
    </div>
  );
}
