"""Weekly training volume: hard sets per muscle.

A set counts as 1 for each muscle the exercise trains directly and 0.5 for each
muscle that helps, so a set of bench press is 1 set for chest and half a set for
triceps and shoulders. This "fractional" counting fit the research data best in a
2026 meta-regression (Pelland et al., see README).

The target is a starting range, not a hard threshold: growth keeps rising with volume
but with diminishing returns, so the right amount is what you can recover from. About
10 or more weekly sets per muscle is where the ACSM's 2026 position stand found
hypertrophy enhanced (Currier et al., see README).
"""

from __future__ import annotations

from collections.abc import Iterable

from app.scheduler.exercises import MUSCLES

SECONDARY_WEIGHT = 0.5

WEEKLY_SET_TARGET: tuple[int, int] = (10, 20)


def weekly_sets(sessions: Iterable) -> dict[str, float]:
    """Fractional sets per muscle across a week's sessions (anything with `.exercises`)."""
    totals = dict.fromkeys(MUSCLES, 0.0)
    for session in sessions:
        for planned in session.exercises:
            for muscle in planned.exercise.primary:
                totals[muscle] += planned.sets
            for muscle in planned.exercise.secondary:
                totals[muscle] += planned.sets * SECONDARY_WEIGHT
    return totals
