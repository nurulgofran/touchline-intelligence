import { useEffect, useMemo, useRef, useState } from "react";

import { rgbToDisplayCss, type MomentumPoint, type Teams } from "../types";

/**
 * The momentum chart.
 *
 * One line per side, in the clubs' own colours, with the area between
 * them filled in - so "who is on top" is readable at a glance.
 *
 * It is a diverging chart: the zero line is the balance of the match, and
 * the fill above it belongs to the home side and the fill below it to the
 * away side. The fill is flat, not a gradient - a gradient from one club
 * to the other muddies the very thing the chart is for. The axes and the
 * grid are hairlines, so the two lines are the only things with weight.
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

/**
 * A momentum value, written the way a broadcast writes it.
 */
function momentumValue(value: number): string {
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  return `${sign}${Math.abs(value).toFixed(2)}`;
}

export default function MomentumChart({
  points,
  clockMs,
  durationMs,
  teams,
}: MomentumChartProps) {
  const [containerRef, size] = useElementSize<HTMLDivElement>();
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);

  const homeColour = teams
    ? rgbToDisplayCss(teams.home.primary_colour, 0.3)
    : "#9fb3c7";
  const awayColour = teams
    ? rgbToDisplayCss(teams.away.primary_colour, 0.3)
    : "#c4b3c7";

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
      yOf,
      reached,
    };
  }, [size, points, clockMs, durationMs]);

  /**
   * The hover layer.
   *
   * The state holds the *index* of the point under the pointer, and it
   * only changes when the pointer crosses into a new point - so dragging
   * across the chart does not re-render it on every mouse move.
   */
  const handlePointerMove = (event: React.PointerEvent<SVGSVGElement>) => {
    if (!chart) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    const x = event.clientX - bounds.left;
    let nearest = -1;
    let best = Number.POSITIVE_INFINITY;
    chart.reached.forEach((point, index) => {
      const distance = Math.abs(chart.xOf(point.match_clock_ms) - x);
      if (distance < best) {
        best = distance;
        nearest = index;
      }
    });
    setHoverIndex(nearest >= 0 ? nearest : null);
  };

  const hovered =
    chart && hoverIndex !== null ? chart.reached[hoverIndex] : undefined;
  const hoverX =
    chart && hovered ? chart.xOf(hovered.match_clock_ms) : 0;

  return (
    <div className="panel flex flex-col overflow-hidden">
      <div className="flex items-center justify-between gap-2 px-3.5 pb-1 pt-2.5">
        <h2 className="panel-title">Momentum</h2>
        <div className="flex items-center gap-3 text-[11px]">
          <span className="flex items-center gap-1.5">
            <span
              className="h-[2px] w-4 rounded-full"
              style={{ backgroundColor: homeColour }}
            />
            <span className="text-ink-300">
              {teams?.home.short_name ?? "Home"}
            </span>
          </span>
          <span className="flex items-center gap-1.5">
            <span
              className="h-[2px] w-4 rounded-full"
              style={{ backgroundColor: awayColour }}
            />
            <span className="text-ink-300">
              {teams?.away.short_name ?? "Away"}
            </span>
          </span>
        </div>
      </div>

      <div ref={containerRef} className="relative h-36 min-h-0 flex-1 px-1 pb-1 pt-1">
        {chart && (
          <svg
            width={chart.width}
            height={chart.height}
            viewBox={`0 0 ${chart.width} ${chart.height}`}
            className="block"
            role="img"
            aria-label="The momentum of the two sides over the match clock."
            onPointerMove={handlePointerMove}
            onPointerLeave={() => setHoverIndex(null)}
          >
            <defs>
              {/* The fill is split at the zero line, so the area above it
                  belongs to the home side and the area below it to the
                  away side. */}
              <clipPath id="momentum-above">
                <rect x={0} y={0} width={chart.width} height={chart.zeroY} />
              </clipPath>
              <clipPath id="momentum-below">
                <rect
                  x={0}
                  y={chart.zeroY}
                  width={chart.width}
                  height={Math.max(0, chart.height - chart.zeroY)}
                />
              </clipPath>
            </defs>

            {/* The zero line: the balance of the match. */}
            <line
              x1={PADDING.left}
              y1={chart.zeroY}
              x2={chart.width - PADDING.right}
              y2={chart.zeroY}
              stroke="#3a4840"
              strokeWidth={1}
            />

            {/* The x-axis ticks. */}
            {chart.ticks.map((tick) => (
              <g key={tick.clock}>
                <line
                  x1={tick.x}
                  y1={chart.height - PADDING.bottom}
                  x2={tick.x}
                  y2={chart.height - PADDING.bottom + 3}
                  stroke="#2a3630"
                  strokeWidth={1}
                />
                <text
                  x={tick.x}
                  y={chart.height - PADDING.bottom + 13}
                  fontSize={9}
                  fill="#4f5f56"
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
              fill="#4f5f56"
              textAnchor="end"
            >
              +1
            </text>
            <text
              x={PADDING.left - 4}
              y={chart.zeroY + 3}
              fontSize={9}
              fill="#4f5f56"
              textAnchor="end"
            >
              0
            </text>
            <text
              x={PADDING.left - 4}
              y={chart.bottomY}
              fontSize={9}
              fill="#4f5f56"
              textAnchor="end"
            >
              −1
            </text>

            {/* The area between the two lines, split at the balance. */}
            {chart.area && (
              <>
                <g clipPath="url(#momentum-above)">
                  <path d={chart.area} fill={homeColour} fillOpacity={0.16} />
                </g>
                <g clipPath="url(#momentum-below)">
                  <path d={chart.area} fill={awayColour} fillOpacity={0.16} />
                </g>
              </>
            )}

            {/* The two lines. */}
            <path
              d={linePath(chart.homeLine)}
              fill="none"
              stroke={homeColour}
              strokeWidth={2}
              strokeLinejoin="round"
              strokeLinecap="round"
            />
            <path
              d={linePath(chart.awayLine)}
              fill="none"
              stroke={awayColour}
              strokeWidth={2}
              strokeLinejoin="round"
              strokeLinecap="round"
            />

            {/* The crosshair, when the reader is pointing at the chart. */}
            {hovered && (
              <g>
                <line
                  x1={hoverX}
                  y1={PADDING.top}
                  x2={hoverX}
                  y2={chart.height - PADDING.bottom}
                  stroke="#6d7e74"
                  strokeWidth={1}
                />
                <circle
                  cx={hoverX}
                  cy={chart.yOf(hovered.home)}
                  r={3}
                  fill={homeColour}
                  stroke="#121a16"
                  strokeWidth={2}
                />
                <circle
                  cx={hoverX}
                  cy={chart.yOf(hovered.away)}
                  r={3}
                  fill={awayColour}
                  stroke="#121a16"
                  strokeWidth={2}
                />
              </g>
            )}

            {/* Where the match is now. */}
            <line
              x1={chart.nowX}
              y1={PADDING.top}
              x2={chart.nowX}
              y2={chart.height - PADDING.bottom}
              stroke="#57cb87"
              strokeOpacity={0.6}
              strokeWidth={1}
            />
          </svg>
        )}

        {hovered && chart && (
          <div
            className="pointer-events-none absolute top-1 z-10 rounded-sm border border-ink-700 bg-ink-950/95 px-2 py-1 text-[10px] leading-tight shadow-card"
            style={{
              left: Math.min(
                Math.max(hoverX - 46, 0),
                Math.max(0, chart.width - 96),
              ),
            }}
          >
            <div className="font-display text-[13px] font-semibold tabular-nums text-ink-100">
              {Math.floor(hovered.match_clock_ms / 60000)}′
            </div>
            <div className="flex items-center gap-1.5">
              <span
                className="h-[2px] w-3 rounded-full"
                style={{ backgroundColor: homeColour }}
              />
              <span className="tabular-nums text-ink-200">
                {momentumValue(hovered.home)}
              </span>
            </div>
            <div className="flex items-center gap-1.5">
              <span
                className="h-[2px] w-3 rounded-full"
                style={{ backgroundColor: awayColour }}
              />
              <span className="tabular-nums text-ink-200">
                {momentumValue(hovered.away)}
              </span>
            </div>
          </div>
        )}

        {(!chart || points.length === 0) && (
          <p className="absolute inset-0 flex items-center justify-center text-[11px] text-ink-500">
            waiting for the momentum curve
          </p>
        )}
      </div>
    </div>
  );
}
