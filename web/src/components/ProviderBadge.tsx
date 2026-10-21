import type { ProviderState } from "../types";

/**
 * The provider badge.
 *
 * The badge answers one question: **who actually served the last call?**
 *
 * The rules are exact, and they exist for one reason: the badge must never
 * claim a provider that did not answer.
 *
 * - With no model configured, the label is the *configured* provider -
 *   which reads "Template engine (model not configured)" - and there is no
 *   chip at all. A "fallback" chip next to "model not configured" would say
 *   two contradictory things at once.
 * - With a model configured, the label is the provider that served the
 *   **last** call, or the configured provider if no call has been made yet,
 *   and the chip says whether that call was live or a fallback. The
 *   configured provider is shown separately, as small static text, so the
 *   two facts never contradict each other: the big name is what answered,
 *   the small name is what the service intends to use.
 */

export interface ProviderBadgeProps {
  provider: ProviderState | null;
}

type Chip = "live" | "fallback";

interface BadgeView {
  label: string;
  chip: Chip | null;
}

export function badgeFor(provider: ProviderState): BadgeView {
  const {
    configured_provider,
    model_configured,
    last_call_provider,
    last_call_state,
  } = provider;

  if (!model_configured) {
    // No model is configured, so there is nothing that could have been
    // live and nothing that could have fallen back. The label says what
    // the service is using, and no chip is shown.
    return { label: configured_provider, chip: null };
  }

  // A chip describes a call that was actually made. A state with no
  // provider to name is not one the service produces, and the guard keeps
  // the badge from ever pairing a name with a call it did not serve.
  const chip: Chip | null =
    last_call_provider !== null &&
    (last_call_state === "live" || last_call_state === "fallback")
      ? last_call_state
      : null;

  // The label is the provider that served the last call. If no call has
  // been made yet there is nothing to name, so the configured provider is
  // shown instead.
  const label = last_call_provider ?? configured_provider;

  return { label, chip };
}

export default function ProviderBadge({ provider }: ProviderBadgeProps) {
  if (!provider) {
    return (
      <div
        className="flex items-center gap-2 rounded-md border border-studio-700 bg-studio-850 px-2.5 py-1.5"
        title="Which provider served the last call."
      >
        <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-studio-500">
          Provider
        </span>
        <span className="text-xs text-studio-400">connecting…</span>
      </div>
    );
  }

  const { label, chip } = badgeFor(provider);

  return (
    <div
      className="flex items-center gap-2 rounded-md border border-studio-700 bg-studio-850 px-2.5 py-1.5"
      title="Which provider served the last call."
    >
      <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-studio-500">
        Provider
      </span>
      <span className="max-w-[16rem] truncate text-xs font-medium text-studio-100">
        {label}
      </span>
      {chip === "live" && (
        <span className="inline-flex items-center gap-1 rounded-full border border-emerald-500/60 bg-emerald-500/15 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider text-emerald-300">
          <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
          live
        </span>
      )}
      {chip === "fallback" && (
        <span className="inline-flex items-center gap-1 rounded-full border border-amber-500/60 bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider text-amber-300">
          <span className="h-1.5 w-1.5 rounded-full bg-amber-400" />
          fallback
        </span>
      )}
      {provider.model_configured && (
        <span
          className="max-w-[12rem] truncate text-[10px] text-studio-500"
          title="The provider the service is configured to use."
        >
          configured: {provider.configured_provider}
        </span>
      )}
    </div>
  );
}
