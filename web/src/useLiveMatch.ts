import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type {
  Audience,
  AudienceOptions,
  ConnectionStatus,
  ControlMessage,
  Handoff,
  InsightsPayload,
  MatchEvent,
  MatchSummary,
  MomentumPayload,
  MomentumPoint,
  Overlay,
  ProviderState,
  ServerFrame,
  Teams,
  TracePayload,
} from "./types";

/**
 * The live match, over one WebSocket.
 *
 * The socket is the live path: it pushes the clock, the events, the
 * momentum, the insights and the hand-off trace. The REST endpoints are
 * the first paint (the club names are needed before anything can be
 * drawn) and the fallback (if the socket never opens, the feed is still
 * worth showing).
 *
 * Three things in here are not obvious, and all three are load-bearing:
 *
 * 1. **The connection is expected to drop.** The ingress in front of the
 *    service has a request timeout that cannot be changed, so the socket
 *    will be cut at some point in a long demo. The studio reopens it and
 *    asks the server to resume from the clock it last saw, so a drop
 *    never looks like a stalled match.
 *
 * 2. **The frames are not idempotent.** A fresh session walks the event
 *    log from the beginning, so after a reconnect the same events, the
 *    same momentum points and the same hand-offs arrive again. Everything
 *    is therefore keyed by its own id, and re-delivery is a no-op.
 *
 * 3. **The clock is never held up for the model.** The server pushes the
 *    clock on every tick whatever else is happening, so an insight that
 *    arrives late is shown late rather than not at all.
 */

const DEFAULT_SEED = 42;

/**
 * The language the studio is written in, and the only one the reader can
 * be asked for.
 *
 * The service still writes German and Bengali when it is asked to - that
 * capability is tested and it is not going away - but the studio is for an
 * international room, and a language switcher on the reader bar is
 * clutter in front of the story the studio is telling. So the locale is
 * fixed here, at the one place an audience request is made.
 */
const READER_LOCALE = "en";

/** How long a match event stays bright on the pitch, in match time. */
export const EVENT_FADE_MS = 8_000;

/** How many events the pitch keeps around. */
export const PITCH_EVENT_LIMIT = 120;

/** How many events the studio keeps in memory, for the citation chips. */
const EVENT_LOG_LIMIT = 3_000;

/** The speeds on offer, in match-seconds per wall-clock second. */
export const SPEED_CHOICES = [10, 20, 30, 45, 60] as const;

/** The speed the studio starts at: a ninety-minute match in three minutes. */
export const DEFAULT_SPEED = 30;

/** How often the REST fallback is refreshed. */
const REST_POLL_MS = 3_000;

/**
 * How many key moments the feed panel holds.
 *
 * The feed is a live list, not an archive: the newest card is at the top
 * and the panel scrolls to it as each one arrives. The rest of the match
 * is still there - scrub the clock back and the earlier moments return.
 */
const FEED_LIMIT = 8;

export interface LiveMatchState {
  /** How the socket is doing. */
  status: ConnectionStatus;
  /** The match's id, once the hello frame has arrived. */
  matchId: string | null;
  /** Which match is being watched. */
  seed: number;
  /** The two fictional clubs, with their colours. */
  teams: Teams | null;
  /** The length of the match, in match milliseconds. */
  durationMs: number;
  /** Where the clock is. */
  clockMs: number;
  scoreHome: number;
  scoreAway: number;
  period: number;
  /** How far the analysis has got, in match time. */
  computedMs: number;
  /** Whether the final whistle has gone. */
  complete: boolean;
  /** The event log, oldest first. */
  events: MatchEvent[];
  /** The momentum curve, oldest first. */
  momentum: MomentumPoint[];
  /** The insights, newest first. */
  overlays: Overlay[];
  /** The hand-off trace, newest first. */
  trace: Handoff[];
  /** Who actually answered the last call. */
  provider: ProviderState | null;
  /** Whether the clock is running. */
  playing: boolean;
  /** Match-seconds per wall-clock second. */
  speed: number;
  /** The match clocks of every insight the service knows about. */
  insightClocks: number[];
  /** The match clock of the next insight, or null when there is none. */
  nextMomentMs: number | null;
  /** How many key moments the service has computed in total. */
  knownInsightCount: number;
  /**
   * Whether the socket has ever delivered a frame.
   *
   * This is what tells the studio whether it has a clock at all. The
   * clock comes from the socket, so a studio whose socket has never
   * spoken cannot say which moments the match has reached.
   */
  everConnected: boolean;
  /** Whether the studio started at the first key moment. */
  startedAtFirstMoment: boolean;
  /** Whether the viewer has moved the clock themselves. */
  userMovedClock: boolean;
  /** Whether the live socket is down and the feed is coming from REST. */
  usingFallback: boolean;
  /** Who the overlays are written for. */
  audience: Audience;
  /** The audiences, the languages, the squad and the metrics on offer. */
  audienceOptions: AudienceOptions | null;
  /** Whether the audience has been asked for but not applied yet. */
  audiencePending: boolean;
  /**
   * Why the last rewrite did not finish, in one line. Null when the last
   * rewrite finished or none was asked for - the studio shows it next to
   * the retry control.
   */
  audienceRewriteError: string | null;
}

export interface LiveMatchApi extends LiveMatchState {
  play: () => void;
  pause: () => void;
  togglePlay: () => void;
  replay: () => void;
  changeSpeed: (speed: number) => void;
  seek: (clockMs: number) => void;
  nextMoment: () => void;
  /** Ask the service to write the overlays for a different audience. */
  setAudience: (audience: Audience) => void;
  /**
   * Try the requested audience's rewrite again, after it failed or was
   * abandoned. A no-op when the rewrite is running or has finished.
   */
  retryAudience: () => void;
  /** The event with the given id, if the studio has it. */
  eventById: (eventId: string) => MatchEvent | undefined;
}

const INITIAL_STATE: LiveMatchState = {
  status: "connecting",
  matchId: null,
  seed: DEFAULT_SEED,
  teams: null,
  durationMs: 90 * 60 * 1000,
  clockMs: 0,
  scoreHome: 0,
  scoreAway: 0,
  period: 1,
  computedMs: 0,
  complete: false,
  events: [],
  momentum: [],
  overlays: [],
  trace: [],
  provider: null,
  playing: true,
  speed: DEFAULT_SPEED,
  insightClocks: [],
  nextMomentMs: null,
  knownInsightCount: 0,
  everConnected: false,
  startedAtFirstMoment: false,
  userMovedClock: false,
  usingFallback: false,
  audience: {
    mode: "analyst",
    locale: "en",
    side: null,
    player_id: null,
    player_name: null,
    metric: null,
  },
  audienceOptions: null,
  audiencePending: false,
  audienceRewriteError: null,
};

function socketUrl(seed: number): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/ws/match?seed=${seed}`;
}

/** Turn a REST insight into the overlay shape the feed renders. */
function insightToOverlay(
  insight: InsightsPayload["insights"][number],
): Overlay {
  return {
    schema_version: "1.0",
    overlay_id: insight.overlay_id,
    candidate_id: insight.candidate_id,
    type: insight.type,
    start_ms: insight.match_clock_ms,
    end_ms: insight.match_clock_ms,
    match_clock_ms: insight.match_clock_ms,
    title: insight.title,
    body: insight.body,
    metrics: insight.metrics,
    cited_event_ids: insight.cited_event_ids,
    locale: insight.locale ?? "en",
    confidence: insight.confidence,
    revision: insight.revision ?? 0,
    provenance_provider: insight.provenance_provider ?? null,
    prose_substituted: insight.prose_substituted ?? false,
    deterministic_prose: insight.deterministic_prose ?? false,
  };
}

export function useLiveMatch(seed: number = DEFAULT_SEED): LiveMatchApi {
  const [state, setState] = useState<LiveMatchState>(INITIAL_STATE);

  // The socket, and the small number of facts the frame handlers need to
  // read while they are running. They live in refs because a frame handler
  // is registered once and must still see the latest value.
  const socketRef = useRef<WebSocket | null>(null);
  const helloCountRef = useRef(0);
  const lastClockRef = useRef(0);
  const userMovedRef = useRef(false);
  const openingTargetRef = useRef<number | null>(null);
  const playingRef = useRef(true);
  const speedRef = useRef(DEFAULT_SPEED);
  const reconnectAttemptRef = useRef(0);
  const reconnectTimerRef = useRef<number | null>(null);
  const disposedRef = useRef(false);
  /**
   * The reader change that has been asked for and not yet answered.
   *
   * A reader change is a message on the socket, and the socket in front
   * of the service is cut every few minutes. A change that was in flight
   * when it was cut would be lost, and the studio would sit there
   * showing a language the service is not using. So the change is kept
   * until the service confirms it, and sent again when the socket opens.
   */
  const pendingAudienceRef = useRef<Audience | null>(null);

  const send = useCallback((message: ControlMessage): boolean => {
    const socket = socketRef.current;
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify(message));
      return true;
    }
    return false;
  }, []);

  /* ---------------------------------------------------------------- */
  /* The opening move                                                  */
  /* ---------------------------------------------------------------- */

  /**
   * Open the studio at the first key moment.
   *
   * The first insight in a match needs a baseline window, so it does not
   * arrive until the twelfth minute of match time - twenty-four seconds
   * of waiting at 30x, which is a long time for a judge who has just
   * opened the link. So the studio jumps to it instead.
   *
   * It jumps as soon as it *knows* the moment. The socket only releases
   * an insight once the clock has reached it, so waiting for the socket
   * would mean waiting for the clock to walk the whole way; the poll
   * knows about every insight the service has already computed, and that
   * is usually the same moment a second or two sooner. Either way the
   * target is the same, and the first one to arrive decides it.
   *
   * Moving the clock changes the pacing and nothing else. The analyst
   * only ever looks backwards from the moment it reports on, so the
   * insight for 12:00 is the same insight whether the clock reaches it
   * now or in a minute.
   */
  const openAtFirstMoment = useCallback(
    (clockMs: number) => {
      // The viewer has taken the clock into their own hands. The studio
      // does not move it again.
      if (userMovedRef.current) return;
      // The target is decided **once**. Deciding it again would send the
      // seek on every poll, and the clock would sit pinned to the first
      // moment instead of playing the match.
      if (openingTargetRef.current !== null) return;

      const target = Math.max(0, Math.round(clockMs));
      openingTargetRef.current = target;
      // The studio only claims to have started at the first moment if the
      // clock was still before it. If the socket got here first the
      // viewer has already watched the opening of the match, and the
      // claim would not be true.
      const jumped = lastClockRef.current < target;
      setState((prev) => ({ ...prev, startedAtFirstMoment: jumped }));
      send({ type: "control", action: "seek", value: target });
    },
    [send],
  );

  /* ---------------------------------------------------------------- */
  /* The socket                                                       */
  /* ---------------------------------------------------------------- */

  useEffect(() => {
    disposedRef.current = false;

    const connect = () => {
      if (disposedRef.current) return;

      let socket: WebSocket;
      try {
        socket = new WebSocket(socketUrl(seed));
      } catch {
        scheduleReconnect();
        return;
      }
      socketRef.current = socket;

      socket.onopen = () => {
        reconnectAttemptRef.current = 0;
        setState((prev) => ({ ...prev, status: "open" }));
        // The opening move may have been decided before the socket was
        // ready - the poll does not wait for the connection. On a first
        // connection there is no resume to do, so the seek goes out now.
        const target = openingTargetRef.current;
        if (target !== null && !userMovedRef.current && helloCountRef.current === 0) {
          send({ type: "control", action: "seek", value: target });
        }
        // A reader change that was in flight when the last socket was cut
        // never reached the service. It is asked for again, so the reader
        // gets the language they asked for rather than the one that
        // happened to be in force when the socket came back.
        const pending = pendingAudienceRef.current;
        if (pending) {
          send({ type: "control", action: "audience", value: pending });
        }
      };

      socket.onmessage = (message) => {
        let frame: ServerFrame;
        try {
          frame = JSON.parse(String(message.data)) as ServerFrame;
        } catch {
          return;
        }
        handleFrame(frame);
      };

      socket.onerror = () => {
        // `onclose` always follows; the reconnect is scheduled there.
      };

      socket.onclose = () => {
        socketRef.current = null;
        if (disposedRef.current) {
          setState((prev) => ({ ...prev, status: "closed" }));
          return;
        }
        scheduleReconnect();
      };
    };

    const scheduleReconnect = () => {
      if (disposedRef.current) return;
      setState((prev) => ({ ...prev, status: "reconnecting" }));
      const attempt = reconnectAttemptRef.current;
      reconnectAttemptRef.current += 1;
      const delay = Math.min(1_000 * 2 ** Math.min(attempt, 3), 6_000);
      if (reconnectTimerRef.current !== null) {
        window.clearTimeout(reconnectTimerRef.current);
      }
      reconnectTimerRef.current = window.setTimeout(connect, delay);
    };

    connect();

    return () => {
      disposedRef.current = true;
      if (reconnectTimerRef.current !== null) {
        window.clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      const socket = socketRef.current;
      socketRef.current = null;
      if (socket) {
        socket.onclose = null;
        socket.onmessage = null;
        socket.onerror = null;
        socket.onopen = null;
        if (
          socket.readyState === WebSocket.OPEN ||
          socket.readyState === WebSocket.CONNECTING
        ) {
          socket.close();
        }
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seed]);

  /* ---------------------------------------------------------------- */
  /* The frames                                                       */
  /* ---------------------------------------------------------------- */

  const handleFrame = useCallback(
    (frame: ServerFrame) => {
      switch (frame.type) {
        case "hello": {
          const isResume = helloCountRef.current > 0;
          helloCountRef.current += 1;
          lastClockRef.current = frame.clock_ms;
          // The reader is service state, so the service has the last word
          // about it: a judge who opens the studio after somebody else
          // changed the reader sees that reader's choice.
          //
          // But a change this studio asked for and has not had answered
          // is not the service's to overwrite -- the request is still in
          // flight, or was lost with the last socket, and it is sent
          // again when the socket opens. The studio keeps showing what
          // the reader asked for until the service says otherwise.
          const pending = pendingAudienceRef.current;
          setState((prev) => ({
            ...prev,
            matchId: frame.match_id,
            teams: frame.teams,
            durationMs: frame.duration_ms,
            clockMs: frame.clock_ms,
            computedMs: Math.max(prev.computedMs, frame.clock_ms),
            provider: frame.provider,
            speed: frame.speed,
            everConnected: true,
            audience: pending ?? frame.audience ?? prev.audience,
            // The service says whether the feed is still being rewritten,
            // so the studio is not left saying it is working when the
            // work is done -- or the other way about. A failed rewrite
            // carries one line saying why, next to the retry control.
            audiencePending: pending
              ? true
              : (frame.rewriting ?? prev.audiencePending),
            audienceRewriteError: frame.rewrite_error ?? null,
          }));
          if (isResume) {
            // The connection dropped. Resume from the clock we last saw,
            // so the match does not appear to have stalled or restarted.
            send({ type: "control", action: "seek", value: lastClockRef.current });
            if (!playingRef.current) {
              send({ type: "control", action: "pause" });
            }
          }
          break;
        }

        case "clock": {
          lastClockRef.current = frame.clock_ms;
          setState((prev) => ({
            ...prev,
            clockMs: frame.clock_ms,
            scoreHome: frame.score_home,
            scoreAway: frame.score_away,
            period: frame.period,
            computedMs: frame.computed_ms,
            complete: frame.complete || prev.complete,
            playing: frame.complete ? false : prev.playing,
          }));
          if (frame.complete) {
            playingRef.current = false;
          }
          break;
        }

        case "events": {
          setState((prev) => {
            const known = new Set(prev.events.map((event) => event.event_id));
            const fresh = frame.events.filter((event) => !known.has(event.event_id));
            if (fresh.length === 0) return prev;
            const next = [...prev.events, ...fresh];
            return {
              ...prev,
              events:
                next.length > EVENT_LOG_LIMIT
                  ? next.slice(next.length - EVENT_LOG_LIMIT)
                  : next,
            };
          });
          break;
        }

        case "momentum": {
          setState((prev) => {
            const known = new Set(
              prev.momentum.map((point) => point.match_clock_ms),
            );
            const fresh = frame.points.filter(
              (point) => !known.has(point.match_clock_ms),
            );
            if (fresh.length === 0) return prev;
            const next = [...prev.momentum, ...fresh].sort(
              (a, b) => a.match_clock_ms - b.match_clock_ms,
            );
            return { ...prev, momentum: next };
          });
          break;
        }

        case "overlay": {
          const overlay: Overlay = {
            ...frame.overlay,
            candidate_id: frame.candidate_id,
          };
          setState((prev) => {
            // An overlay keeps its id when the audience changes, so the
            // revision is what says whether this card is newer than the
            // one already on screen.
            const existing = prev.overlays.find(
              (item) => item.overlay_id === overlay.overlay_id,
            );
            if (
              existing &&
              (existing.revision ?? 0) >= (overlay.revision ?? 0)
            ) {
              return prev;
            }
            const overlays = prev.overlays
              .filter((item) => item.overlay_id !== overlay.overlay_id)
              .concat(overlay)
              .sort((a, b) => b.match_clock_ms - a.match_clock_ms);
            return {
              ...prev,
              overlays,
              // Whether the feed is still being rewritten is the
              // service's to say -- it knows when the last rewrite
              // landed -- so the studio does not guess it from the
              // arrival of one card.
              insightClocks: prev.insightClocks.includes(overlay.match_clock_ms)
                ? prev.insightClocks
                : [...prev.insightClocks, overlay.match_clock_ms].sort(
                    (a, b) => a - b,
                  ),
            };
          });

          // The opening move. The socket only releases an insight once the
          // clock has reached it, so this is usually the second thing to
          // know the first moment; the poll is usually the first.
          openAtFirstMoment(frame.clock_ms);
          break;
        }

        case "trace": {
          setState((prev) => {
            const known = new Set(prev.trace.map((entry) => entry.index));
            const fresh = frame.entries.filter(
              (entry) => !known.has(entry.index),
            );
            if (fresh.length === 0) return prev;
            const trace = [...prev.trace, ...fresh].sort(
              (a, b) => b.index - a.index,
            );
            return { ...prev, trace };
          });
          break;
        }

        case "provider": {
          setState((prev) => ({ ...prev, provider: frame.provider }));
          break;
        }

        case "audience": {
          // The service has applied the reader change and says what it
          // applied. The studio follows it: a language the service does
          // not write is refused, and the picker must show the language
          // that is really in use rather than the one that was asked for.
          //
          // The service also says whether the feed is still being
          // rewritten. A rewrite is one call per insight and takes
          // minutes at the deployment's rate, so the hint is the
          // service's to give: a feed that is still being rewritten is
          // not a finished one, and the studio says so until it is.
          const applied = frame.audience;
          // The change has been answered, so it no longer has to be
          // re-sent when the socket comes back.
          pendingAudienceRef.current = null;
          setState((prev) => ({
            ...prev,
            audience: applied,
            audiencePending: frame.rewriting,
            audienceRewriteError: frame.rewrite_error ?? null,
          }));
          break;
        }

        case "end": {
          playingRef.current = false;
          setState((prev) => ({
            ...prev,
            complete: true,
            playing: false,
            clockMs: frame.clock_ms,
            scoreHome: frame.score_home,
            scoreAway: frame.score_away,
          }));
          break;
        }
      }
    },
    [openAtFirstMoment, send],
  );

  /* ---------------------------------------------------------------- */
  /* The REST fallback                                                */
  /* ---------------------------------------------------------------- */

  const [restFallback, setRestFallback] = useState<{
    overlays: Overlay[];
    trace: Handoff[];
    momentum: MomentumPoint[];
    insightClocks: number[];
  }>({ overlays: [], trace: [], momentum: [], insightClocks: [] });

  const refreshRest = useCallback(async () => {
    try {
      const [insights, trace, momentum] = await Promise.all([
        fetchJson<InsightsPayload>(`/api/match/insights?seed=${seed}`),
        fetchJson<TracePayload>(`/api/match/trace?seed=${seed}`),
        fetchJson<MomentumPayload>(`/api/match/momentum?seed=${seed}`),
      ]);
      if (!insights || !trace || !momentum) return;
      const clocks = insights.insights
        .map((insight) => insight.match_clock_ms)
        .sort((a, b) => a - b);
      setRestFallback({
        overlays: insights.insights.map(insightToOverlay),
        trace: [...trace.entries].sort((a, b) => b.index - a.index),
        momentum: [...momentum.points].sort(
          (a, b) => a.match_clock_ms - b.match_clock_ms,
        ),
        insightClocks: clocks,
      });

      // The poll knows about every insight the service has computed,
      // including the ones the clock has not reached yet. That is how the
      // studio finds the first key moment without waiting for the clock
      // to walk there - and how the "next key moment" button knows there
      // is another one coming.
      if (clocks.length > 0) {
        openAtFirstMoment(clocks[0]);
      }
    } catch {
      // The service is unreachable. The socket will keep trying; there is
      // nothing useful to do about a failed poll.
    }
  }, [openAtFirstMoment, seed]);

  // The first paint: the club names, the length of the match and the
  // provider state, all of which are needed before anything can be drawn.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const summary = await fetchJson<MatchSummary>(`/api/match?seed=${seed}`);
        if (cancelled || !summary) return;
        setState((prev) => ({
          ...prev,
          matchId: summary.match_id,
          teams: summary.teams,
          durationMs: summary.duration_ms,
          computedMs: summary.computed_ms,
          provider: summary.provider,
        }));
      } catch {
        // The socket will deliver the same facts as soon as it opens.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [seed]);

  // The insight list is polled, because the "next key moment" button has
  // to know about the insights that have been computed but not yet reached
  // - and the socket only ever reveals the ones the clock has passed.
  useEffect(() => {
    void refreshRest();
    const timer = window.setInterval(() => {
      void refreshRest();
    }, REST_POLL_MS);
    return () => window.clearInterval(timer);
  }, [refreshRest]);

  // The audience's options: the five readings, the three languages, the
  // two squads and the metrics a lens can look through. They are fetched
  // once, because the squad does not change during a match.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const options = await fetchJson<AudienceOptions>(
        `/api/match/audience?seed=${seed}`,
      );
      if (cancelled || !options) return;
      setState((prev) => ({
        ...prev,
        audienceOptions: options,
        audience: options.audience ?? prev.audience,
        audienceRewriteError: options.rewrite_error ?? prev.audienceRewriteError,
      }));
    })();
    return () => {
      cancelled = true;
    };
  }, [seed]);

  /* ---------------------------------------------------------------- */
  /* The merged view                                                  */
  /* ---------------------------------------------------------------- */

  /**
   * The socket and the poll are two readings of the same truth, not two
   * different truths.
   *
   * So they are **unioned**, never chosen between: taking whichever list
   * is non-empty makes the studio flash the whole match and then collapse
   * to one card, or show a hundred hand-offs and then cut them to three.
   * A union only ever grows.
   */
  const allOverlays = useMemo(() => {
    const merged = new Map<string, Overlay>();
    const put = (overlay: Overlay) => {
      const existing = merged.get(overlay.overlay_id);
      // The audience can rewrite a card without changing its id, so the
      // newer revision wins whichever reading it arrived in.
      if (existing && (existing.revision ?? 0) > (overlay.revision ?? 0)) return;
      merged.set(overlay.overlay_id, overlay);
    };
    for (const overlay of restFallback.overlays) put(overlay);
    for (const overlay of state.overlays) put(overlay);
    return Array.from(merged.values());
  }, [state.overlays, restFallback.overlays]);

  /**
   * The clock the feed is filtered against.
   *
   * An insight is shown when the clock has reached its moment, so the
   * future is never on screen - the same rule the pitch and the momentum
   * chart follow. The clock comes from the socket, so a studio whose
   * socket has never spoken has no clock to filter by, and an empty
   * panel would say nothing at all when the service has already analysed
   * the match. In that one case the filter falls back to the analysis
   * frontier: the furthest point the service has analysed, which is the
   * latest insight it actually has.
   *
   * Once the socket has spoken the clock rules, even if the socket then
   * drops: a studio that has lost its connection must not start showing
   * the match ahead of where it was.
   */
  const feedClockMs = useMemo(() => {
    if (state.everConnected || allOverlays.length === 0) return state.clockMs;
    return allOverlays.reduce(
      (furthest, overlay) => Math.max(furthest, overlay.match_clock_ms),
      0,
    );
  }, [allOverlays, state.clockMs, state.everConnected]);

  /**
   * The feed reveals itself.
   *
   * The panel is a live list rather than an archive, so only the newest
   * few are held; scrubbing the clock back brings the earlier ones back.
   */
  const overlays = useMemo(
    () =>
      allOverlays
        .filter((overlay) => overlay.match_clock_ms <= feedClockMs)
        .sort((a, b) => b.match_clock_ms - a.match_clock_ms)
        .slice(0, FEED_LIMIT),
    [allOverlays, feedClockMs],
  );

  const trace = useMemo(() => {
    const merged = new Map<number, Handoff>();
    for (const entry of restFallback.trace) {
      merged.set(entry.index, entry);
    }
    for (const entry of state.trace) {
      merged.set(entry.index, entry);
    }
    return Array.from(merged.values()).sort((a, b) => b.index - a.index);
  }, [state.trace, restFallback.trace]);

  const momentum = useMemo(() => {
    const merged = new Map<number, MomentumPoint>();
    for (const point of restFallback.momentum) {
      merged.set(point.match_clock_ms, point);
    }
    for (const point of state.momentum) {
      merged.set(point.match_clock_ms, point);
    }
    return Array.from(merged.values()).sort(
      (a, b) => a.match_clock_ms - b.match_clock_ms,
    );
  }, [state.momentum, restFallback.momentum]);

  const insightClocks = useMemo(() => {
    const merged = new Set<number>([
      ...state.insightClocks,
      ...restFallback.insightClocks,
    ]);
    return Array.from(merged).sort((a, b) => a - b);
  }, [state.insightClocks, restFallback.insightClocks]);

  const nextMomentMs = useMemo(() => {
    for (const clock of insightClocks) {
      if (clock > state.clockMs) return clock;
    }
    return null;
  }, [insightClocks, state.clockMs]);

  /* ---------------------------------------------------------------- */
  /* The controls                                                     */
  /* ---------------------------------------------------------------- */

  const play = useCallback(() => {
    playingRef.current = true;
    setState((prev) => ({ ...prev, playing: true }));
    send({ type: "control", action: "play" });
  }, [send]);

  const pause = useCallback(() => {
    playingRef.current = false;
    setState((prev) => ({ ...prev, playing: false }));
    send({ type: "control", action: "pause" });
  }, [send]);

  const togglePlay = useCallback(() => {
    if (playingRef.current) {
      pause();
    } else {
      play();
    }
  }, [pause, play]);

  const replay = useCallback(() => {
    userMovedRef.current = true;
    playingRef.current = true;
    lastClockRef.current = 0;
    setState((prev) => ({
      ...prev,
      playing: true,
      userMovedClock: true,
      clockMs: 0,
      startedAtFirstMoment: false,
    }));
    send({ type: "control", action: "replay" });
  }, [send]);

  const changeSpeed = useCallback(
    (speed: number) => {
      speedRef.current = speed;
      setState((prev) => ({ ...prev, speed }));
      send({ type: "control", action: "speed", value: speed });
    },
    [send],
  );

  const seek = useCallback(
    (clockMs: number) => {
      userMovedRef.current = true;
      const target = Math.max(0, Math.round(clockMs));
      lastClockRef.current = target;
      setState((prev) => ({ ...prev, clockMs: target, userMovedClock: true }));
      send({ type: "control", action: "seek", value: target });
    },
    [send],
  );

  const nextMoment = useCallback(() => {
    userMovedRef.current = true;
    setState((prev) => ({ ...prev, userMovedClock: true }));
    send({ type: "control", action: "next" });
  }, [send]);

  /**
   * Ask the service to write the overlays for a different audience.
   *
   * The service rewrites the overlays it has already computed and sends
   * them again with a higher revision, so the studio shows the same
   * moments in a different voice. Until they arrive the studio says it is
   * working, because a card that has not been rewritten yet is still the
   * old one.
   *
   * The change is kept until the service confirms it, so it survives a
   * socket that is cut before the message gets there.
   */
  const setAudience = useCallback(
    (audience: Audience) => {
      // The studio is English only. The reader's locale is fixed, so the
      // request says so whatever the caller passed: the mode is the only
      // thing the reader chooses.
      const requested: Audience = { ...audience, locale: READER_LOCALE };
      pendingAudienceRef.current = requested;
      setState((prev) => ({
        ...prev,
        audience: requested,
        audiencePending: true,
        audienceRewriteError: null,
      }));
      send({ type: "control", action: "audience", value: requested });
    },
    [send],
  );

  /**
   * Try the requested audience's rewrite again.
   *
   * The service only restarts a rewrite that failed or was abandoned;
   * asking when the rewrite is running or finished is a no-op. The
   * error clears optimistically -- the service sends the truth in its
   * next audience frame either way.
   */
  const retryAudience = useCallback(() => {
    setState((prev) => ({ ...prev, audienceRewriteError: null }));
    send({ type: "control", action: "audience_retry" });
  }, [send]);

  const eventById = useCallback(
    (eventId: string) => state.events.find((event) => event.event_id === eventId),
    [state.events],
  );

  return {
    ...state,
    overlays,
    trace,
    momentum,
    insightClocks,
    nextMomentMs,
    knownInsightCount: allOverlays.length,
    // The socket is the live path. When it has dropped, whatever is on
    // screen came from the poll, and the studio says so rather than
    // pretending the match is still being pushed. A socket that is
    // merely still connecting is not a failure and is not announced.
    usingFallback:
      state.status !== "open" &&
      state.status !== "connecting" &&
      overlays.length > 0,
    play,
    pause,
    togglePlay,
    replay,
    changeSpeed,
    seek,
    nextMoment,
    setAudience,
    retryAudience,
    eventById,
  };
}

async function fetchJson<T>(url: string): Promise<T | null> {
  try {
    const response = await fetch(url, { headers: { accept: "application/json" } });
    if (!response.ok) return null;
    return (await response.json()) as T;
  } catch {
    return null;
  }
}
