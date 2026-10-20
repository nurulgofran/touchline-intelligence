"""Every tunable number the deterministic analyst uses.

The thresholds live in one dataclass rather than as literals inside the
rules, because the rules are the part of this project most likely to be
re-tuned once the numbers are seen on a real feed. Changing a threshold
changes what the analyst says; it never changes how it computes, and it never
changes what the grounding step verifies.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Thresholds:
    """The analyst's tuning knobs.

    Attributes:
        window_ms: Length of the "recent" window an insight is about.
        baseline_ms: Length of the earlier window the recent one is compared
            against. The baseline ends where the recent window starts, so the
            two never overlap.
        cooldown_ms: Minimum gap between two insights of the same kind and
            side. Without it, one busy five minutes would produce dozens of
            near-identical claims.
        eval_interval_ms: How often the analyst looks at the match. A window
            is five minutes long, so a small gap here costs nothing in
            detection and saves a great deal of repeated work.
        feed_interval_ms: How much match time one on-screen insight is
            allowed to occupy. The analyst finds more moments than a
            broadcast can say, so the feed picks one per interval.
        min_window_events: A window with fewer events than this is too small
            to say anything about.
        momentum_shift: How far the momentum index has to move, in index
            points, before it counts as a swing.
        pass_accuracy_shift: How far the pass accuracy has to move, in
            percentage points, before it counts as a change of shape.
        pass_accuracy_min_passes: Passes needed in the window before the
            pass accuracy is worth comparing.
        control_shift: How far the control share has to move, in percentage
            points, before it counts as a change.
        control_min_events: Classified actions needed before the control
            share is worth comparing.
        press_min_run: Length of the spell of possession that counts as
            playing through a press.
        press_min_passes: Completed passes needed in that spell.
        press_min_control: Control share the side must hold during that
            spell for it to read as a press break rather than a scramble.
        chance_min_shots: Shots in the window that count as a chance being
            created.
        chance_min_xg: Total expected goals that count as a chance being
            created.
        threat_speed_mps: Ball speed, in metres per second, that counts as
            a threat. Only shots reach this -- a pass tops out well below it.
        tempo_min_breaks: Turnovers won inside the window that count as a
            change of tempo.
        set_piece_window_ms: How long after a set piece a shot still counts
            as the set piece being worked. A restart is followed by the
            next possession within about fifteen seconds, and a shot from
            that possession lands twenty to twenty-five seconds after the
            set piece -- so the window has to be wider than the first
            possession's start or the rule can never fire.
        set_piece_range_m: How close to the goal a free kick has to be
            won for it to count as dangerous.
        discipline_min_fouls: Fouls in the window that count as a discipline
            problem.
        milestone_distances_m: Ground-covered milestones, in metres. A
            professional outfielder covers 8-12 km a match, so these are
            the points at which a player has run a quarter, a half and
            most of the way through that.
        milestone_goals: The goal that counts as a milestone.
    """

    window_ms: int = 300_000
    baseline_ms: int = 900_000
    cooldown_ms: int = 180_000
    eval_interval_ms: int = 15_000
    feed_interval_ms: int = 90_000
    min_window_events: int = 10

    momentum_shift: float = 0.45
    pass_accuracy_shift: float = 18.0
    pass_accuracy_min_passes: int = 5
    control_shift: float = 14.0
    control_min_events: int = 8
    press_min_run: int = 8
    press_min_passes: int = 8
    press_min_control: float = 80.0
    chance_min_shots: int = 2
    chance_min_xg: float = 0.25
    threat_speed_mps: float = 32.0
    tempo_min_breaks: int = 7
    set_piece_window_ms: int = 30_000
    set_piece_range_m: float = 30.0
    discipline_min_fouls: int = 3

    milestone_distances_m: tuple[float, ...] = (3000.0, 5000.0, 7000.0)
    milestone_goals: int = 5

    #: The metric a baseline is compared against, per rule. Kept beside the
    #: thresholds so a rule and its baseline metric can never drift apart.
    baseline_metrics: dict[str, str] = field(
        default_factory=lambda: {
            "momentum": "momentum_index",
            "passing": "pass_accuracy",
            "control": "control_pct",
        }
    )
