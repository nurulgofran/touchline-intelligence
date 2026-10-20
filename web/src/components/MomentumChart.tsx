import { useEffect, useMemo, useRef, useState } from "react";

import { rgbToCss, type MomentumPoint, type Teams } from "../types";

/**
 * The momentum chart.
 *
 * One line per side, in the clubs' own colours, with the area between
 * them filled in - so "who is on top" is readable at a glance.
 *
 * The important rule is that **the future is never on screen**: only the
 * points the clock has already reached are drawn, and a vertical line
 * marks where the match is now. The chart is revealed, not previewed.
 */

export interface MomentumChartProps {
  points: MomentumPoint[];
  clockMs: number;
  durationMs: number;
  teams: Teams | null;
}

/** The momentum index runs from -1 to +1. */
const DOMAIN_MIN = -1;
const DOMAIN_MAX = 1;

/** The drawing area inside the SVG, in pixels. */
const PADDING = { top: 10, right: 10, bottom: 20, left: 26 };

/** Measure the element, so the chart is drawn in pixels and stays crisp. */
function useElementSize<T extends HTMLElement>() {
  const ref = useRef<T | null>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });

  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) return;
      const { width, height } = entry.contentRect;
      setSize((prev) =>
        prev.width === width && prev.height === height
          ? prev
          : { width, height },
      );
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  return [ref, size] as const;
}

/** A path through the points, as an SVG path string. */
function linePath(
  points: Array<{ x: number; y: number }>,
): string {
  if (points.length === 0) return "";
  return points
    .map((point, index) => `${index === 0 ? "M" : "L"} ${point.x} ${point.y}`)
    .join(" ");
}

export default function MomentumChart({
  points,
  clockMs,
  durationMs,
  teams,
}: MomentumChartProps) {
  const [containerRef, size] = useElementSize<HTMLDivElement>();

  const homeColour = teams ? rgbToCss(teams.home.primary_colour) : "#38bdf8";
  const awayColour = teams ? rgbToCss(teams.away.primary_colour) : "#f472b6";

  const chart = useMemo(() => {
    const width = size.width;
    const height = size.height;
    if (width <= 0 || height <= 0) return null;

    const innerWidth = width - PADDING.left - PADDING.right;
    const innerHeight = height - PADDING.top - PADDING.bottom;
    if (innerWidth <= 0 || innerHeight <= 0) return null;

    const total = Math.max(1, durationMs);
    const xOf = (clock: number) =>
      PADDING.left + (Math.max(0, Math.min(clock, total)) / total) * innerWidth;
    const yOf = (value: number) =>
      PADDING.top +
      (1 - (Math.max(DOMAIN_MIN, Math.min(DOMAIN_MAX, value)) - DOMAIN_MIN) /
        (DOMAIN_MAX - DOMAIN_MIN)) *
        innerHeight;

    // Only the points the clock has reached. Nothing after it is drawn.
    const reached = points.filter((point) => point.match_clock_ms <= clockMs);

    const homeLine = reached.map((point) => ({
      x: xOf(point.match_clock_ms),
      y: yOf(point.home),
    }));
    const awayLine = reached.map((point) => ({
      x: xOf(point.match_clock_ms),
      y: yOf(point.away),
    }));

    // The area between the two lines: one path down the home line and
    // back up the away line.
    const area =
      homeLine.length > 1
        ? `${linePath(homeLine)} ${awayLine
            .slice()
            .reverse()
            .map((point) => `L ${point.x} ${point.y}`)
            .join(" ")} Z`
        : "";

    const nowX = xOf(clockMs);

    // The x-axis ticks: every fifteen minutes of match time.
    const ticks: number[] = [];
    for (let minute = 0; minute <= 90; minute += 15) {
      ticks.push(minute * 60 * 1000);
    }

    return {
      width,
      height,
      innerWidth,
      innerHeight,
      homeLine,
      awayLine,
      area,
      nowX,
      zeroY: yOf(0),
      topY: yOf(DOMAIN_MAX),
      bottomY: yOf(DOMAIN_MIN),
      ticks: ticks.map((clock) => ({ clock, x: xOf(clock) })),
      xOf,
    };
  }, [size, points, clockMs, durationMs]);

  return (
    <div className="panel flex flex-col overflow-hidden">
      <div className="flex items-center justify-between px-3 pt-2.5">
        <h2 className="panel-title">Momentum</h2>
        <div className="flex items-center gap-3 text-[11px]">
          <span className="flex items-center gap-1.5">
            <span
              className="h-0.5 w-4 rounded-full"
              style={{ backgroundColor: homeColour }}
            />
            <span className="text-studio-300">
              {teams?.home.short_name ?? "Home"}
            </span>
          </span>
          <span className="flex items-center gap-1.5">
            <span
              className="h-0.5 w-4 rounded-full"
              style={{ backgroundColor: awayColour }}
            />
            <span className="text-studio-300">
              {teams?.away.short_name ?? "Away"}
            </span>
          </span>
        </div>
      </div>

      <div ref={containerRef} className="relative h-44 px-1 pb-1 pt-1">
        {chart && (
          <svg
            width={chart.width}
            height={chart.height}
            viewBox={`0 0 ${chart.width} ${chart.height}`}
            className="block"
            role="img"
            aria-label="The momentum of the two sides over the match clock."
          >
            <defs>
              <linearGradient id="momentum-area" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={homeColour} stopOpacity={0.4} />
                <stop offset="50%" stopColor={homeColour} stopOpacity={0.06} />
                <stop offset="50%" stopColor={awayColour} stopOpacity={0.06} />
                <stop offset="100%" stopColor={awayColour} stopOpacity={0.4} />
              </linearGradient>
            </defs>

            {/* The zero line: the balance of the match. */}
            <line
              x1={PADDING.left}
              y1={chart.zeroY}
              x2={chart.width - PADDING.right}
              y2={chart.zeroY}
              stroke="#94a3b8"
              strokeOpacity={0.45}
              strokeWidth={1}
              strokeDasharray="3 3"
            />

            {/* The x-axis ticks. */}
            {chart.ticks.map((tick) => (
              <g key={tick.clock}>
                <line
                  x1={tick.x}
                  y1={chart.height - PADDING.bottom}
                  x2={tick.x}
                  y2={chart.height - PADDING.bottom + 4}
                  stroke="#64748b"
                  strokeOpacity={0.6}
                  strokeWidth={1}
                />
                <text
                  x={tick.x}
                  y={chart.height - PADDING.bottom + 14}
                  fontSize={9}
                  fill="#64748b"
                  textAnchor="middle"
                >
                  {Math.round(tick.clock / 60000)}′
                </text>
              </g>
            ))}

            <text
              x={PADDING.left - 4}
              y={chart.topY + 4}
              fontSize={9}
              fill="#64748b"
              textAnchor="end"
            >
              +1
            </text>
            <text
              x={PADDING.left - 4}
              y={chart.zeroY + 3}
              fontSize={9}
              fill="#64748b"
              textAnchor="end"
            >
              0
            </text>
            <text
              x={PADDING.left - 4}
              y={chart.bottomY}
              fontSize={9}
              fill="#64748b"
              textAnchor="end"
            >
              −1
            </text>

            {/* The area between the two lines. */}
            {chart.area && (
              <path d={chart.area} fill="url(#momentum-area)" />
            )}

            {/* The two lines. */}
            <path
              d={linePath(chart.homeLine)}
              fill="none"
              stroke={homeColour}
              strokeWidth={1.8}
              strokeLinejoin="round"
              strokeLinecap="round"
            />
            <path
              d={linePath(chart.awayLine)}
              fill="none"
              stroke={awayColour}
              strokeWidth={1.8}
              strokeLinejoin="round"
              strokeLinecap="round"
            />

            {/* Where the match is now. */}
            <line
              x1={chart.nowX}
              y1={PADDING.top}
              x2={chart.nowX}
              y2={chart.height - PADDING.bottom}
              stroke="#f8fafc"
              strokeOpacity={0.7}
              strokeWidth={1}
            />
          </svg>
        )}
        {(!chart || points.length === 0) && (
          <p className="absolute inset-0 flex items-center justify-center text-[11px] text-studio-500">
            waiting for the momentum curve
          </p>
        )}
      </div>
    </div>
  );
}
