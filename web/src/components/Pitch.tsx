import { useMemo } from "react";

import {
  EVENT_FADE_MS,
  PITCH_EVENT_LIMIT,
} from "../useLiveMatch";
import {
  rgbToCss,
  type MatchEvent,
  type Side,
  type Teams,
} from "../types";

/**
 * The pitch: a top-down view of the last thing that happened.
 *
 * The geometry is hand-written SVG - no kit designs, no crests, no marks
 * of anybody's. It is a football pitch, drawn in stripes, with the two
 * clubs' own plain colours on the ball.
 *
 * The one subtlety is the x axis. The service reports `x` as a percentage
 * of the pitch **from the perspective of the side that owns the event**:
 * `x = 0` is that team's own goal. So the same physical spot has the
 * complementary `x` for the two sides, and a home event and an away event
 * at the same place have to be flipped. The flip is done once, here, in
 * `screenX`, and everything else in the studio can think in screen terms.
 */

/** The pitch is 105 m by 68 m, so the SVG can be drawn in metres. */
const PITCH_LENGTH_M = 105;
const PITCH_WIDTH_M = 68;

/** The halfway line, in metres from the left touchline. */
const HALF_LENGTH_M = PITCH_LENGTH_M / 2;

/** The centre circle, in metres. */
const CENTRE_CIRCLE_M = 9.15;

/** The penalty area: 16.5 m deep, 40.32 m wide. */
const PENALTY_DEPTH_M = 16.5;
const PENALTY_WIDTH_M = 40.32;

/** The goal area: 5.5 m deep, 18.32 m wide. */
const GOAL_AREA_DEPTH_M = 5.5;
const GOAL_AREA_WIDTH_M = 18.32;

/** The penalty spot, in metres from the goal line. */
const PENALTY_SPOT_M = 11;

/** The goal, in metres. */
const GOAL_WIDTH_M = 7.32;
const GOAL_DEPTH_M = 2;

/** A neutral colour for the events that belong to nobody. */
const NEUTRAL = "#cbd5e1";

export interface PitchProps {
  teams: Teams | null;
  events: MatchEvent[];
  clockMs: number;
  /** The id of the event the viewer is pointing at, if any. */
  highlightedEventId: string | null;
}

/**
 * The x of an event on the screen, in metres.
 *
 * `x = 0` is the owning team's own goal, so an away event is flipped.
 */
function screenX(percent: number, side: Side | null): number {
  const flipped = side === "AWAY" ? 100 - percent : percent;
  return (flipped / 100) * PITCH_LENGTH_M;
}

/**
 * The y of an event on the screen, in metres.
 *
 * `y = 0` is one touchline and `y = 100` the other; the studio draws
 * `y = 0` along the bottom, so the screen's y is the complement.
 */
function screenY(percent: number): number {
  return ((100 - percent) / 100) * PITCH_WIDTH_M;
}

export default function Pitch({
  teams,
  events,
  clockMs,
  highlightedEventId,
}: PitchProps) {
  const homeColour = teams ? rgbToCss(teams.home.primary_colour) : "#38bdf8";
  const awayColour = teams ? rgbToCss(teams.away.primary_colour) : "#f472b6";

  const { bright, trail, latest, highlighted } = useMemo(() => {
    // Only the most recent events are kept, and only the ones the clock
    // has actually passed: the future is never on the pitch.
    const recent = events
      .slice(-PITCH_EVENT_LIMIT)
      .filter((event) => event.match_clock_ms <= clockMs);

    const fadeFrom = clockMs - EVENT_FADE_MS;
    const live = recent.filter((event) => event.match_clock_ms >= fadeFrom);
    const old = recent.filter((event) => event.match_clock_ms < fadeFrom);

    const last = recent.length > 0 ? recent[recent.length - 1] : undefined;
    const pointed =
      highlightedEventId === null
        ? undefined
        : events.find((event) => event.event_id === highlightedEventId);

    return { bright: live, trail: old, latest: last, highlighted: pointed };
  }, [events, clockMs, highlightedEventId]);

  const colourFor = (side: Side | null) =>
    side === "HOME" ? homeColour : side === "AWAY" ? awayColour : NEUTRAL;

  const lineOpacity = (event: MatchEvent) => {
    const age = clockMs - event.match_clock_ms;
    const fade = Math.max(0, Math.min(1, 1 - age / EVENT_FADE_MS));
    return 0.15 + 0.75 * fade;
  };

  return (
    <div className="panel flex min-h-0 flex-1 flex-col overflow-hidden">
      <div className="flex items-center justify-between px-3 pt-2.5">
        <h2 className="panel-title">Pitch</h2>
        <p className="text-[11px] text-studio-500">
          {events.length > 0
            ? `${events.length} events logged`
            : "waiting for the first pass"}
        </p>
      </div>

      <div className="min-h-0 flex-1 p-2.5">
        <svg
          viewBox={`0 0 ${PITCH_LENGTH_M} ${PITCH_WIDTH_M}`}
          className="h-full w-full rounded-md"
          preserveAspectRatio="xMidYMid meet"
          role="img"
          aria-label="A top-down view of the pitch, showing the most recent action."
        >
          <defs>
            <clipPath id="pitch-clip">
              <rect x={0} y={0} width={PITCH_LENGTH_M} height={PITCH_WIDTH_M} />
            </clipPath>
          </defs>

          {/* The grass: plain stripes, nothing else. */}
          <g clipPath="url(#pitch-clip)">
            {Array.from({ length: 10 }, (_, index) => (
              <rect
                key={index}
                x={index * (PITCH_LENGTH_M / 10)}
                y={0}
                width={PITCH_LENGTH_M / 10}
                height={PITCH_WIDTH_M}
                fill={index % 2 === 0 ? "#2f6f46" : "#296540"}
              />
            ))}
          </g>

          {/* The markings. */}
          <g
            fill="none"
            stroke="#ffffff"
            strokeOpacity={0.42}
            strokeWidth={0.22}
          >
            <rect
              x={0}
              y={0}
              width={PITCH_LENGTH_M}
              height={PITCH_WIDTH_M}
            />
            <line
              x1={HALF_LENGTH_M}
              y1={0}
              x2={HALF_LENGTH_M}
              y2={PITCH_WIDTH_M}
            />
            <circle
              cx={HALF_LENGTH_M}
              cy={PITCH_WIDTH_M / 2}
              r={CENTRE_CIRCLE_M}
            />
            <rect
              x={0}
              y={(PITCH_WIDTH_M - PENALTY_WIDTH_M) / 2}
              width={PENALTY_DEPTH_M}
              height={PENALTY_WIDTH_M}
            />
            <rect
              x={PITCH_LENGTH_M - PENALTY_DEPTH_M}
              y={(PITCH_WIDTH_M - PENALTY_WIDTH_M) / 2}
              width={PENALTY_DEPTH_M}
              height={PENALTY_WIDTH_M}
            />
            <rect
              x={0}
              y={(PITCH_WIDTH_M - GOAL_AREA_WIDTH_M) / 2}
              width={GOAL_AREA_DEPTH_M}
              height={GOAL_AREA_WIDTH_M}
            />
            <rect
              x={PITCH_LENGTH_M - GOAL_AREA_DEPTH_M}
              y={(PITCH_WIDTH_M - GOAL_AREA_WIDTH_M) / 2}
              width={GOAL_AREA_DEPTH_M}
              height={GOAL_AREA_WIDTH_M}
            />
            <circle
              cx={PENALTY_SPOT_M}
              cy={PITCH_WIDTH_M / 2}
              r={0.35}
              fill="#ffffff"
              fillOpacity={0.42}
              stroke="none"
            />
            <circle
              cx={PITCH_LENGTH_M - PENALTY_SPOT_M}
              cy={PITCH_WIDTH_M / 2}
              r={0.35}
              fill="#ffffff"
              fillOpacity={0.42}
              stroke="none"
            />
            <path
              d={`M ${PENALTY_DEPTH_M} ${PITCH_WIDTH_M / 2 - 9.15} A 9.15 9.15 0 0 1 ${PENALTY_DEPTH_M} ${PITCH_WIDTH_M / 2 + 9.15}`}
            />
            <path
              d={`M ${PITCH_LENGTH_M - PENALTY_DEPTH_M} ${PITCH_WIDTH_M / 2 - 9.15} A 9.15 9.15 0 0 0 ${PITCH_LENGTH_M - PENALTY_DEPTH_M} ${PITCH_WIDTH_M / 2 + 9.15}`}
            />
          </g>

          {/* The goals, drawn just outside the lines. */}
          <g fill="#ffffff" fillOpacity={0.22} stroke="#ffffff" strokeOpacity={0.5} strokeWidth={0.2}>
            <rect
              x={-GOAL_DEPTH_M}
              y={(PITCH_WIDTH_M - GOAL_WIDTH_M) / 2}
              width={GOAL_DEPTH_M}
              height={GOAL_WIDTH_M}
            />
            <rect
              x={PITCH_LENGTH_M}
              y={(PITCH_WIDTH_M - GOAL_WIDTH_M) / 2}
              width={GOAL_DEPTH_M}
              height={GOAL_WIDTH_M}
            />
          </g>

          <circle
            cx={HALF_LENGTH_M}
            cy={PITCH_WIDTH_M / 2}
            r={0.4}
            fill="#ffffff"
            fillOpacity={0.5}
          />

          {/* The faint trail: the shape of the last few minutes of action. */}
          <g>
            {trail.map((event) => (
              <circle
                key={event.event_id}
                cx={screenX(event.x, event.side)}
                cy={screenY(event.y)}
                r={event.type === "GOAL" ? 1.5 : 0.8}
                fill={colourFor(event.side)}
                fillOpacity={0.16}
              />
            ))}
          </g>

          {/* The recent action, fading out over eight seconds of match time. */}
          <g>
            {bright.map((event) => {
              const x = screenX(event.x, event.side);
              const y = screenY(event.y);
              const colour = colourFor(event.side);
              const opacity = lineOpacity(event);
              const hasEnd =
                event.end_x !== null && event.end_y !== null;

              return (
                <g key={event.event_id} opacity={opacity}>
                  {hasEnd && (
                    <line
                      x1={x}
                      y1={y}
                      x2={screenX(event.end_x as number, event.side)}
                      y2={screenY(event.end_y as number)}
                      stroke={colour}
                      strokeWidth={0.4}
                      strokeLinecap="round"
                      strokeOpacity={0.85}
                    />
                  )}
                  {event.type === "GOAL" ? (
                    <>
                      <circle
                        cx={x}
                        cy={y}
                        r={2.4}
                        fill="none"
                        stroke={colour}
                        strokeWidth={0.9}
                      />
                      <circle cx={x} cy={y} r={0.9} fill={colour} />
                    </>
                  ) : event.type === "SHOT" ? (
                    <circle
                      cx={x}
                      cy={y}
                      r={1.7}
                      fill="none"
                      stroke={colour}
                      strokeWidth={0.75}
                    />
                  ) : (
                    <circle cx={x} cy={y} r={0.95} fill={colour} />
                  )}
                </g>
              );
            })}
          </g>

          {/* The event the viewer is pointing at, however old it is. */}
          {highlighted && (
            <g>
              <circle
                cx={screenX(highlighted.x, highlighted.side)}
                cy={screenY(highlighted.y)}
                r={2.6}
                fill="none"
                stroke="#f8fafc"
                strokeWidth={0.5}
                strokeDasharray="1.2 0.9"
              />
            </g>
          )}

          {/* The name of the player involved, on the most recent event only. */}
          {latest && latest.player_name && (
            <text
              x={screenX(latest.x, latest.side) + 1.8}
              y={screenY(latest.y) - 1.6}
              fontSize={2}
              fontWeight={600}
              fill="#ffffff"
              stroke="#0b111b"
              strokeWidth={0.45}
              paintOrder="stroke"
              strokeLinejoin="round"
              textAnchor={
                screenX(latest.x, latest.side) > PITCH_LENGTH_M - 22
                  ? "end"
                  : "start"
              }
            >
              {latest.player_name}
            </text>
          )}
        </svg>
      </div>

      <p className="px-3 pb-2.5 text-[11px] text-studio-500">
        Home attacks left to right, away attacks right to left. The most
        recent action fades over eight seconds of match time.
      </p>
    </div>
  );
}
