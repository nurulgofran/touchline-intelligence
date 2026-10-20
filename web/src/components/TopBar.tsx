import { formatClock, rgbToCss, type ConnectionStatus, type ProviderState, type Teams } from "../types";
import ProviderBadge from "./ProviderBadge";

/**
 * The top bar: the product, the two clubs, the score, the clock, and the
 * provider badge.
 *
 * The club names and colours come from the service. Nothing about a club
 * is hard-coded here - the clubs are fictional and they change with the
 * seed, so the studio reads them rather than knows them.
 */

export interface TopBarProps {
  teams: Teams | null;
  scoreHome: number;
  scoreAway: number;
  clockMs: number;
  provider: ProviderState | null;
  status: ConnectionStatus;
  matchId: string | null;
}

function ClubName({
  name,
  colour,
}: {
  name: string;
  colour: string | undefined;
}) {
  return (
    <span
      className="text-base font-bold tracking-wide sm:text-lg"
      style={{ color: colour }}
      title={name}
    >
      {name}
    </span>
  );
}

export default function TopBar({
  teams,
  scoreHome,
  scoreAway,
  clockMs,
  provider,
  status,
  matchId,
}: TopBarProps) {
  const home = teams?.home;
  const away = teams?.away;
  const reconnecting = status === "reconnecting" || status === "connecting";

  return (
    <header className="flex flex-wrap items-center gap-x-6 gap-y-2 border-b border-studio-700/70 bg-studio-900/95 px-4 py-2.5">
      <div className="min-w-0">
        <h1 className="truncate text-sm font-bold tracking-tight text-white sm:text-base">
          Touchline Intelligence
        </h1>
        <p className="truncate text-[11px] text-studio-400">
          Synthetic data · fictional clubs
          {matchId ? (
            <span className="text-studio-600"> · {matchId}</span>
          ) : null}
        </p>
      </div>

      <div className="flex items-center gap-3 sm:gap-4">
        <div className="flex items-center gap-2">
          <ClubName
            name={home?.short_name ?? "—"}
            colour={home ? rgbToCss(home.primary_colour) : undefined}
          />
          <span
            className="rounded-md bg-studio-800 px-2.5 py-1 font-mono text-sm font-bold tabular-nums text-white"
            title="The score"
          >
            {scoreHome} – {scoreAway}
          </span>
          <ClubName
            name={away?.short_name ?? "—"}
            colour={away ? rgbToCss(away.primary_colour) : undefined}
          />
        </div>
        <div
          className="rounded-md border border-studio-700 bg-studio-850 px-2.5 py-1 font-mono text-sm font-semibold tabular-nums text-studio-100"
          title="The match clock"
        >
          {formatClock(clockMs)}
        </div>
      </div>

      <div className="ml-auto flex items-center gap-2">
        {reconnecting && (
          <span className="inline-flex items-center gap-1.5 rounded-md border border-amber-500/60 bg-amber-500/10 px-2 py-1 text-[11px] font-semibold text-amber-300">
            <span className="h-1.5 w-1.5 animate-pulse-soft rounded-full bg-amber-400" />
            {status === "connecting" ? "connecting…" : "reconnecting…"}
          </span>
        )}
        <ProviderBadge provider={provider} />
      </div>
    </header>
  );
}
