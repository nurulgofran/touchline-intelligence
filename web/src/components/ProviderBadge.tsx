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
 *
 * The chip is a quiet marker, not a pill: a dot and two letters. The badge
 * is a trust signal, and a trust signal does not need to shout - but it
 * does need to be findable, which is what the data attributes are for.
 * The demo recorder reads the badge out of the page, and it looks these
 * up by name rather than by the class it happens to wear this week.
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
        className="flex items-center gap-2"
        title="Which provider served the last call."
      >
        <span className="micro-label">Provider</span>
        <span className="text-[12px] text-ink-400">connecting…</span>
      </div>
    );
  }

  const { label, chip } = badgeFor(provider);

  return (
    <div
      className="flex items-center gap-2"
      title="Which provider served the last call."
    >
      <span className="micro-label">Provider</span>
      <span
        data-provider-name
        className="max-w-[16rem] truncate text-[12px] font-medium text-ink-100"
      >
        {label}
      </span>
      {chip === "live" && (
        <span
          data-provider-chip
          className="inline-flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-pitch-300"
        >
          <span className="h-1.5 w-1.5 rounded-full bg-pitch-400" />
          live
        </span>
      )}
      {chip === "fallback" && (
        <span
          data-provider-chip
          className="inline-flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-signal-amber"
        >
          <span className="h-1.5 w-1.5 rounded-full bg-signal-amber" />
          fallback
        </span>
      )}
      {provider.model_configured && (
        <span
          className="max-w-[12rem] truncate text-[10px] text-ink-500"
          title="The provider the service is configured to use."
        >
          configured: {provider.configured_provider}
        </span>
      )}
    </div>
  );
}
