/**
 * The wire contract with the Touchline Intelligence service.
 *
 * Everything the studio renders comes from one of these shapes, and every
 * one of them is defined by the service - not by this file. The service is
 * the source of truth for the protocol; these are only its TypeScript
 * spelling, so that the studio can never drift into rendering a field the
 * service does not send.
 */

/** Which of the two sides an event, metric or club belongs to. */
export type Side = "HOME" | "AWAY";

/** A plain RGB triple, 0..255 per channel. */
export type Rgb = [number, number, number];

/** One fictional club, as the service describes it. */
export interface Club {
  name: string;
  short_name: string;
  primary_colour: Rgb;
  secondary_colour: Rgb;
}

/** The two clubs, by side. */
export interface Teams {
  home: Club;
  away: Club;
}

/** The two clubs' full names, by side. */
export interface TeamNames {
  home: string;
  away: string;
}

/**
 * Who actually answered the last model call.
 *
 * The two facts are deliberately separate: `configured_provider` is what the
 * service intends to use, `last_call_provider` is what really served the
 * most recent request. The badge shows the second one, so it can never
 * claim a provider that did not answer.
 */
export interface ProviderState {
  configured_provider: string;
  model_configured: boolean;
  last_call_provider: string | null;
  last_call_state: "live" | "fallback" | null;
  last_call_at: string | null;
  call_count: number;
  fallback_count: number;
}

/** One entry of the match log. */
export interface MatchEvent {
  event_id: string;
  type: string;
  match_clock_ms: number;
  period: number;
  side: Side | null;
  player_name: string | null;
  target_player_name: string | null;
  x: number;
  y: number;
  end_x: number | null;
  end_y: number | null;
  outcome: string | null;
  value: number | null;
  distance_m: number | null;
  difficulty: number | null;
  speed_mps: number | null;
  card: string | null;
  score_home: number;
  score_away: number;
}

/** One number behind an insight, with the side it is about. */
export interface MetricValue {
  name: string;
  value: number;
  unit: string;
  label: string;
  side: Side | null;
  /**
   * The person the number is about, when it is about a player rather than
   * a side. The player-focus audience counts one player's actions inside
   * the cited window.
   */
  subject?: string | null;
}

/** A timed piece of on-screen narrative. */
export interface Overlay {
  schema_version: string;
  overlay_id: string;
  /**
   * The insight this overlay came from. The hand-off trace names the same
   * id, so this is what lets the studio narrow the trace to one moment.
   */
  candidate_id: string;
  type: string;
  start_ms: number;
  end_ms: number;
  match_clock_ms: number;
  title: string;
  body: string;
  metrics: MetricValue[];
  cited_event_ids: string[];
  locale: string;
  confidence: number;
  /**
   * Which version of this overlay this is. An overlay keeps its id when
   * the audience changes - it is the same moment, written for somebody
   * else - so the studio needs a number to tell the new card from the old
   * one.
   */
  revision?: number;
}

/**
 * An overlay as the service sends it over the socket: the same thing,
 * without the insight it came from, which travels on the frame.
 */
export type OverlayPayload = Omit<Overlay, "candidate_id">;

/**
 * One hand-off between two stages of the workflow.
 *
 * `accepted === false` is a veto: the receiving stage refused the hand-off,
 * which is the moment the grounding step says no to a claim.
 */
export interface Handoff {
  index: number;
  from_stage: string;
  to_stage: string;
  kind: string;
  summary: string;
  candidate_id: string | null;
  accepted: boolean | null;
}

/** One point on the momentum curve. */
export interface MomentumPoint {
  match_clock_ms: number;
  home: number;
  away: number;
}

/* ------------------------------------------------------------------ */
/* Server -> client frames                                            */
/* ------------------------------------------------------------------ */

export interface HelloFrame {
  type: "hello";
  match_id: string;
  seed: number;
  teams: Teams;
  team_names: TeamNames;
  duration_ms: number;
  stretch_ms: number;
  speed: number;
  clock_ms: number;
  provider: ProviderState;
  event_count: number;
  /** Who the overlays are written for. */
  audience: Audience;
  /** Whether the feed is still being rewritten for that reader. */
  rewriting?: boolean;
}

export interface ClockFrame {
  type: "clock";
  clock_ms: number;
  score_home: number;
  score_away: number;
  period: number;
  computed_ms: number;
  complete: boolean;
}

export interface EventsFrame {
  type: "events";
  events: MatchEvent[];
}

export interface MomentumFrame {
  type: "momentum";
  points: MomentumPoint[];
}

export interface OverlayFrame {
  type: "overlay";
  clock_ms: number;
  candidate_id: string;
  /**
   * The overlay as the service sends it. The insight it came from is on
   * the frame, not inside the overlay, so the studio links the two.
   */
  overlay: OverlayPayload;
}

export interface TraceFrame {
  type: "trace";
  entries: Handoff[];
}

export interface ProviderFrame {
  type: "provider";
  provider: ProviderState;
}

export interface AudienceFrame {
  type: "audience";
  /**
   * Who the overlays are written for, once the change has been applied.
   *
   * The service says what it is actually doing, so the studio shows the
   * truth rather than what it asked for: a language the service does not
   * write is refused, and the picker follows the service.
   */
  audience: Audience;
  /**
   * Whether the feed is still being rewritten.
   *
   * A rewrite is one model call per insight, and the deployment's rate
   * makes it a matter of minutes. The studio says so while it is going
   * on, because a feed that is still being rewritten is not a finished
   * feed.
   */
  rewriting: boolean;
}

export interface EndFrame {
  type: "end";
  clock_ms: number;
  score_home: number;
  score_away: number;
}

export type ServerFrame =
  | HelloFrame
  | ClockFrame
  | EventsFrame
  | MomentumFrame
  | OverlayFrame
  | TraceFrame
  | ProviderFrame
  | AudienceFrame
  | EndFrame;

/* ------------------------------------------------------------------ */
/* The audience                                                       */
/* ------------------------------------------------------------------ */

/**
 * Who the overlays are written for.
 *
 * The audience is a property of the *analysis*, not of the viewer: one
 * engine, one match, one audience at a time. Changing it rewrites the
 * overlays that have already been computed, and only the Personaliser
 * runs again.
 */
export interface Audience {
  mode: AudienceMode;
  locale: string;
  side: Side | null;
  player_id: string | null;
  player_name: string | null;
  metric: string | null;
}

export type AudienceMode =
  | "analyst"
  | "fan"
  | "player"
  | "my_team"
  | "metric_lens";

/** One choice in one of the studio's pickers. */
export interface Choice {
  id: string;
  label: string;
}

/** One fictional player, for the player-focus picker. */
export interface RosterPlayer {
  player_id: string;
  name: string;
  number: number;
  position: string;
  side: Side;
  club: string;
}

/** One metric a lens can look through. */
export interface LensMetric {
  name: string;
  label: string;
  unit: string;
}

/** The audiences, the languages, the squad and the metrics. */
export interface AudienceOptions {
  audience: Audience;
  modes: Choice[];
  locales: Choice[];
  teams: Array<{ side: Side; name: string; short_name: string }>;
  players: RosterPlayer[];
  metrics: LensMetric[];
}

/* ------------------------------------------------------------------ */
/* Client -> server controls                                          */
/* ------------------------------------------------------------------ */

export type ControlAction =
  | "play"
  | "pause"
  | "replay"
  | "speed"
  | "seek"
  | "next"
  | "audience";

export interface ControlMessage {
  type: "control";
  action: ControlAction;
  value?: number | Partial<Audience> | null;
}

/* ------------------------------------------------------------------ */
/* REST payloads                                                      */
/* ------------------------------------------------------------------ */

/** `GET /api/match` */
export interface MatchSummary {
  match_id: string;
  seed: number;
  teams: Teams;
  duration_ms: number;
  stretch_ms: number;
  computed_ms: number;
  insights: number;
  events: number;
  provider: ProviderState;
  seeds: number[];
}

/** `GET /api/match/insights` */
export interface InsightsPayload {
  count: number;
  computed_ms: number;
  insights: Array<{
    candidate_id: string;
    overlay_id: string;
    type: string;
    match_clock_ms: number;
    title: string;
    body: string;
    metrics: MetricValue[];
    cited_event_ids: string[];
    confidence: number;
  }>;
}

/** `GET /api/match/momentum` */
export interface MomentumPayload {
  count: number;
  points: MomentumPoint[];
}

/** `GET /api/match/trace` */
export interface TracePayload {
  count: number;
  vetoes: number;
  entries: Handoff[];
}

/* ------------------------------------------------------------------ */
/* The vision tracker                                                 */
/* ------------------------------------------------------------------ */

/** How the tracker did on one class of thing. */
export interface TrackerScores {
  true_positives: number;
  false_positives: number;
  false_negatives: number;
  precision: number;
  recall: number;
  f1: number;
  mean_error_m: number;
}

/**
 * The tracker's cross-validation report.
 *
 * The tracker watches a synthetic feed built from the same match, and it
 * is scored against the truth that feed was rendered from. `ready` is
 * false while the tracker is still working.
 */
export interface TrackingReport {
  ready: boolean;
  reason?: string;
  frames?: number;
  fps?: number;
  duration_ms?: number;
  ball?: TrackerScores;
  home?: TrackerScores;
  away?: TrackerScores;
  overall?: TrackerScores;
  ball_speed_mae_mps?: number;
  flight_distance_mae_m?: number;
  flight_count_truth?: number;
  flight_count_detected?: number;
  flight_matches?: number;
  shot_speed_mae_mps?: number;
  id_switches?: number;
  tracks_home?: number;
  tracks_away?: number;
  ball_top_speed_mps?: number;
}

/* ------------------------------------------------------------------ */
/* Connection state                                                  */
/* ------------------------------------------------------------------ */

/**
 * How the socket is doing.
 *
 * `reconnecting` is the one the viewer needs to see: a dropped connection
 * must never look like a stalled match.
 */
export type ConnectionStatus = "connecting" | "open" | "reconnecting" | "closed";

/* ------------------------------------------------------------------ */
/* Formatting                                                         */
/* ------------------------------------------------------------------ */

/** The clock at which the second half begins. */
export const HALF_TIME_MS = 2_700_000;

/**
 * The match clock, as `mm:ss`.
 *
 * The second half is shown as `45:00 + mm:ss`, the way a broadcast clock
 * shows the time added on top of the forty-five.
 */
export function formatClock(clockMs: number): string {
  const clamped = Math.max(0, Math.floor(clockMs));
  const inSecondHalf = clamped >= HALF_TIME_MS;
  const base = inSecondHalf ? clamped - HALF_TIME_MS : clamped;
  const totalSeconds = Math.floor(base / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  const stamp = `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  return inSecondHalf ? `45:00 + ${stamp}` : stamp;
}

/**
 * The length of the whole match, as `mm:ss`.
 *
 * This is the total, not a moment in the match, so it is never given the
 * second-half treatment: a ninety-minute match reads as `90:00`.
 */
export function formatDuration(durationMs: number): string {
  const totalSeconds = Math.max(0, Math.floor(durationMs / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

/**
 * A metric's value, rounded to two decimals at most, with no trailing
 * zeroes. `88.23529` reads as `88.24`; `15.0` reads as `15`.
 */
export function formatMetricValue(value: number): string {
  if (!Number.isFinite(value)) return "-";
  const rounded = Math.round(value * 100) / 100;
  if (Number.isInteger(rounded)) return String(rounded);
  return rounded.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
}

/** A plain colour value from the API, as a CSS colour. */
export function rgbToCss(colour: Rgb): string {
  return `rgb(${colour[0]}, ${colour[1]}, ${colour[2]})`;
}

/** A plain colour value from the API, as a translucent CSS colour. */
export function rgbToCssWithAlpha(colour: Rgb, alpha: number): string {
  return `rgba(${colour[0]}, ${colour[1]}, ${colour[2]}, ${alpha})`;
}
