import { useRef, useState } from "react";

import { formatClock, formatDuration } from "../types";
import { SPEED_CHOICES } from "../useLiveMatch";

/**
 * The transport: the bar under the pitch.
 *
 * It is always visible, because a viewer who cannot find the controls
 * cannot explore the match.
 *
 * The scrubber is the fiddly one. The server pushes a clock frame ten
 * times a second, and a scrubber that followed every one of those frames
 * would fight the pointer: the thumb would be dragged back to wherever the
 * clock had got to. So while the pointer is down the local value is held,
 * the seek is sent on release, and the clock is allowed to catch up.
 */

export interface TransportProps {
  playing: boolean;
  speed: number;
  clockMs: number;
  durationMs: number;
  /** The match clock of the next insight, or null when there is none. */
  nextMomentMs: number | null;
  onTogglePlay: () => void;
  onReplay: () => void;
  onSpeed: (speed: number) => void;
  onSeek: (clockMs: number) => void;
  onNextMoment: () => void;
}

export default function Transport({
  playing,
  speed,
  clockMs,
  durationMs,
  nextMomentMs,
  onTogglePlay,
  onReplay,
  onSpeed,
  onSeek,
  onNextMoment,
}: TransportProps) {
  // The value the scrubber shows while the pointer is down. Null means
  // "follow the clock".
  const [scrubValue, setScrubValue] = useState<number | null>(null);
  const pendingRef = useRef<number | null>(null);
  const draggingRef = useRef(false);

  const shown = scrubValue ?? clockMs;
  const progress = durationMs > 0 ? (shown / durationMs) * 100 : 0;

  const beginDrag = () => {
    draggingRef.current = true;
    pendingRef.current = clockMs;
    setScrubValue(clockMs);
  };

  const handleChange = (value: number) => {
    pendingRef.current = value;
    setScrubValue(value);
  };

  const commit = () => {
    const value = pendingRef.current;
    draggingRef.current = false;
    pendingRef.current = null;
    setScrubValue(null);
    if (value !== null) {
      onSeek(value);
    }
  };

  return (
    <div className="panel px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={onTogglePlay}
          className="transport-button"
          data-active={playing}
          title={playing ? "Pause the match" : "Play the match"}
        >
          {playing ? (
            <>
              <span aria-hidden="true">❚❚</span>
              Pause
            </>
          ) : (
            <>
              <span aria-hidden="true">▶</span>
              Play
            </>
          )}
        </button>

        <button
          type="button"
          onClick={onReplay}
          className="transport-button"
          title="Start the match again from kick-off"
        >
          <span aria-hidden="true">↺</span>
          Replay
        </button>

        <button
          type="button"
          onClick={onNextMoment}
          className="transport-button"
          disabled={nextMomentMs === null}
          title={
            nextMomentMs === null
              ? "There is no further insight"
              : `Jump to the next key moment at ${formatClock(nextMomentMs)}`
          }
        >
          <span aria-hidden="true">⏭</span>
          Next key moment
        </button>

        <div
          className="flex items-center gap-1 rounded-md border border-studio-600 bg-studio-850 p-0.5"
          role="group"
          aria-label="Playback speed, in match-seconds per second"
        >
          {SPEED_CHOICES.map((choice) => (
            <button
              key={choice}
              type="button"
              onClick={() => onSpeed(choice)}
              data-active={speed === choice}
              className="transport-button !border-transparent !bg-transparent !px-2 !py-1 hover:!bg-studio-700 data-[active=true]:!bg-studio-600 data-[active=true]:!text-white"
              title={`${choice} match-seconds a second`}
            >
              {choice}×
            </button>
          ))}
        </div>

        <div className="ml-auto flex items-baseline gap-2 font-mono text-xs tabular-nums">
          <span className="text-studio-100">{formatClock(shown)}</span>
          <span className="text-studio-500">/ {formatDuration(durationMs)}</span>
        </div>
      </div>

      <div className="mt-2.5 flex items-center gap-3">
        <input
          type="range"
          className="scrubber"
          min={0}
          max={Math.max(1, durationMs)}
          step={1000}
          value={Math.round(shown)}
          onPointerDown={beginDrag}
          onChange={(event) => handleChange(Number(event.target.value))}
          onPointerUp={commit}
          onPointerCancel={commit}
          onKeyUp={commit}
          onBlur={() => {
            if (draggingRef.current) commit();
          }}
          aria-label="The match clock"
          aria-valuetext={formatClock(shown)}
        />
        <span className="w-12 shrink-0 text-right font-mono text-[10px] tabular-nums text-studio-500">
          {progress.toFixed(1)}%
        </span>
      </div>
    </div>
  );
}
