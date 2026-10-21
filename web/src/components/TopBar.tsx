import { formatClock, rgbToDisplayCss, type ConnectionStatus, type ProviderState, type Teams } from "../types";
import ProviderBadge from "./ProviderBadge";

/**
 * The scorebar: the product, the two clubs, the score, the clock, and the
 * provider badge.
 *
 * It is laid out the way a broadcast lays out a scoreline - the two
 * clubs' colour tabs, the score between them, the clock in its own box -
 * because that is the one piece of chrome every viewer already knows how
 * to read.
 *
 * The club names and colours come from the service. Nothing about a club
 * is hard-coded here - the clubs are fictional and they change with the
 * seed, so the studio reads them rather than knows them.
 *
 * The club colour is a *tab*, not the name's colour: the colours the
 * service sends are muted, and a muted navy as 20px body type on a dark
 * ground is unreadable. The tab keeps the identity; the name stays legible.
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
  shortName,
  colour,
}: {
  name: string;
  shortName: string | undefined;
  colour: string | undefined;
}) {
  return (
    <span className="flex items-center gap-2" title={shortName ? `${name} (${shortName})` : name}>
      <span
        className="h-4 w-[3px] shrink-0 rounded-[1px]"
        style={{ backgroundColor: colour ?? "transparent" }}
        aria-hidden="true"
      />
      <span className="font-display text-[19px] font-semibold uppercase leading-none tracking-[0.04em] text-ink-50">
        {name}
      </span>
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
    <header className="flex flex-wrap items-center gap-x-6 gap-y-2 border-b border-ink-800 bg-ink-900 px-4 py-2.5">
      <div className="min-w-0">
        <h1 className="flex items-center gap-2 font-display text-[15px] font-semibold uppercase leading-none tracking-[0.2em] text-ink-50">
          <span
            className="h-3.5 w-[3px] shrink-0 rounded-[1px] bg-pitch-400"
            aria-hidden="true"
          />
          Touchline Intelligence
        </h1>
        {/*
          The synthetic-data disclosure. It is a compliance requirement,
          so it is a first-class line here, not a footnote.
        */}
        <p className="mt-1 truncate text-[11px] text-ink-300">
          Synthetic data · fictional clubs
          {matchId ? (
            <span className="text-ink-500"> · {matchId}</span>
          ) : null}
        </p>
      </div>

      <div className="flex items-center gap-3 sm:gap-5">
        <div className="flex items-center gap-3 sm:gap-4">
          <ClubName
            name={home?.name ?? "—"}
            shortName={home?.short_name}
            colour={home ? rgbToDisplayCss(home.primary_colour, 0.1) : undefined}
          />
          <span
            className="font-display text-score font-bold tabular-nums leading-none text-ink-50"
            title="The score"
          >
            {scoreHome}
            <span className="px-1.5 text-ink-600">–</span>
            {scoreAway}
          </span>
          <ClubName
            name={away?.name ?? "—"}
            shortName={away?.short_name}
            colour={away ? rgbToDisplayCss(away.primary_colour, 0.1) : undefined}
          />
        </div>

        {/*
          The clock, in its own box. The figures are tabular, so the
          minutes do not shuffle sideways as the seconds tick.
        */}
        <div
          className="rounded-sm border border-ink-700 bg-ink-950 px-2.5 py-1 font-display text-[20px] font-semibold leading-none tabular-nums text-ink-50"
          title="The match clock"
        >
          {formatClock(clockMs)}
        </div>
      </div>

      <div className="ml-auto flex items-center gap-3">
        {reconnecting && (
          <span className="inline-flex items-center gap-1.5 text-[11px] font-medium text-signal-amber">
            <span className="h-1.5 w-1.5 animate-pulse-soft rounded-full bg-signal-amber" />
            {status === "connecting" ? "connecting…" : "reconnecting…"}
          </span>
        )}
        <ProviderBadge provider={provider} />
      </div>
    </header>
  );
}
