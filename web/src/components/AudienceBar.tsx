import { useMemo } from "react";

import type {
  Audience,
  AudienceMode,
  AudienceOptions,
  RosterPlayer,
} from "../types";

/**
 * The audience bar: who the match is being explained to.
 *
 * The five readings are not five ways of saying the same sentence. The
 * analyst gets the deviation and the numbers behind it; the fan gets what
 * just happened; the player-focus reading counts one player's actions
 * inside the window; the my-team reading carries that side's numbers over
 * the same cited events; the metric lens shows one number, recomputed
 * from the same events, and nothing else.
 *
 * So the bar asks one more question depending on the reading, and that
 * question is the pickers underneath: whose side, which player, which
 * metric.
 */
export interface AudienceBarProps {
  audience: Audience;
  options: AudienceOptions | null;
  /** Whether the service is still rewriting the feed. */
  pending: boolean;
  /** Why the last rewrite did not finish, in one line, if it did not. */
  rewriteError: string | null;
  onChange: (audience: Audience) => void;
  /** Try the requested audience's rewrite again. */
  onRetry: () => void;
}

const MODE_HINT: Record<AudienceMode, string> = {
  analyst: "the deviation and the numbers behind it",
  fan: "what just happened",
  player: "one player's actions in the window",
  my_team: "your side's numbers, over the same events",
  metric_lens: "one number, recomputed from the same events",
};

export default function AudienceBar({
  audience,
  options,
  pending,
  rewriteError,
  onChange,
  onRetry,
}: AudienceBarProps) {
  const modes = options?.modes ?? [];

  const players = useMemo(
    () => (options?.players ?? []).slice().sort(byName),
    [options],
  );

  const update = (patch: Partial<Audience>) => {
    onChange({ ...audience, ...patch });
  };

  return (
    <div className="panel flex flex-col gap-2 px-3 py-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <span className="text-[10px] uppercase tracking-wider text-studio-500">
          Read it as
        </span>
        {modes.map((mode) => {
          const active = audience.mode === mode.id;
          return (
            <button
              key={mode.id}
              type="button"
              onClick={() => update({ mode: mode.id as AudienceMode })}
              data-active={active}
              title={MODE_HINT[mode.id as AudienceMode]}
              className={
                active
                  ? "rounded border border-amber-400/80 bg-amber-400/20 px-2 py-0.5 text-[11px] font-semibold text-amber-200"
                  : "rounded border border-studio-600 bg-studio-800 px-2 py-0.5 text-[11px] font-medium text-studio-300 transition-colors hover:border-studio-400 hover:text-white"
              }
            >
              {mode.label}
            </button>
          );
        })}
      </div>

      <p className="text-[11px] leading-snug text-studio-500">
        {MODE_HINT[audience.mode]}
        {pending ? " — rewriting the feed…" : ""}
      </p>

      {rewriteError && !pending && (
        <p className="flex flex-wrap items-center gap-2 text-[11px] leading-snug text-amber-300/90">
          <span>
            The rewrite did not finish — {rewriteError}
          </span>
          <button
            type="button"
            onClick={onRetry}
            className="rounded border border-amber-400/80 bg-amber-400/20 px-1.5 py-px text-[10px] font-semibold uppercase tracking-wider text-amber-200 transition-colors hover:bg-amber-400/30"
          >
            Retry
          </button>
        </p>
      )}

      <AudiencePicker audience={audience} options={options} players={players} onChange={update} />
    </div>
  );
}

/**
 * The question the reading asks.
 *
 * Only the reading that needs it shows its picker, so the bar does not
 * fill up with controls that do nothing.
 */
function AudiencePicker({
  audience,
  options,
  players,
  onChange,
}: {
  audience: Audience;
  options: AudienceOptions | null;
  players: RosterPlayer[];
  onChange: (patch: Partial<Audience>) => void;
}) {
  if (audience.mode === "my_team") {
    return (
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-[10px] uppercase tracking-wider text-studio-500">
          Your side
        </span>
        {(options?.teams ?? []).map((team) => {
          const active = audience.side === team.side;
          return (
            <button
              key={team.side}
              type="button"
              onClick={() => onChange({ side: team.side })}
              data-active={active}
              className={
                active
                  ? "rounded border border-amber-400/80 bg-amber-400/20 px-2 py-0.5 text-[11px] font-semibold text-amber-200"
                  : "rounded border border-studio-600 bg-studio-800 px-2 py-0.5 text-[11px] text-studio-300 transition-colors hover:border-studio-400 hover:text-white"
              }
            >
              {team.name}
            </button>
          );
        })}
      </div>
    );
  }

  if (audience.mode === "player") {
    return (
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-[10px] uppercase tracking-wider text-studio-500">
          Player
        </span>
        <select
          value={audience.player_id ?? ""}
          onChange={(event) => {
            const player = players.find(
              (item) => item.player_id === event.target.value,
            );
            onChange({
              player_id: player?.player_id ?? null,
              player_name: player?.name ?? null,
            });
          }}
          className="max-w-[16rem] rounded border border-studio-600 bg-studio-800 px-1.5 py-0.5 text-[11px] text-studio-200"
        >
          <option value="">Choose a player…</option>
          {players.map((player) => (
            <option key={player.player_id} value={player.player_id}>
              {player.name} · {player.club} · {player.position}
            </option>
          ))}
        </select>
      </div>
    );
  }

  if (audience.mode === "metric_lens") {
    return (
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-[10px] uppercase tracking-wider text-studio-500">
          Lens
        </span>
        <select
          value={audience.metric ?? ""}
          onChange={(event) => onChange({ metric: event.target.value || null })}
          className="max-w-[16rem] rounded border border-studio-600 bg-studio-800 px-1.5 py-0.5 text-[11px] text-studio-200"
        >
          <option value="">Choose a metric…</option>
          {(options?.metrics ?? []).map((metric) => (
            <option key={metric.name} value={metric.name}>
              {metric.label}
              {metric.unit ? ` (${metric.unit})` : ""}
            </option>
          ))}
        </select>
      </div>
    );
  }

  return null;
}

function byName(a: RosterPlayer, b: RosterPlayer): number {
  return a.name.localeCompare(b.name);
}
