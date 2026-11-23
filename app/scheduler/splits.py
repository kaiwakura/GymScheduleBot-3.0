"""Training split definitions and the rules for choosing one.

A *split* is an ordered list of session templates for one week. The recommended
split depends on how many workouts fit this week: the user's commitment caps the
count, and free time never adds more. The user can also ask the AI Coach for a
specific split style, which the planner tries first.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models import Commitment, SplitStyle
from app.scheduler.sessions import (
    FULL_BODY_A,
    FULL_BODY_B,
    FULL_BODY_C,
    LEGS,
    LOWER,
    PULL,
    PUSH,
    UPPER,
    SessionTemplate,
)


@dataclass(frozen=True)
class Split:
    name: str
    sessions: tuple[SessionTemplate, ...]

    @property
    def days(self) -> int:
        return len(self.sessions)


# ------------------------------------------------------------------ splits ---
SPLITS: dict[str, Split] = {
    s.name: s
    for s in (
        Split("Full Body", (FULL_BODY_A,)),
        Split("Full Body A/B", (FULL_BODY_A, FULL_BODY_B)),
        Split("Upper/Lower x1", (UPPER, LOWER)),
        Split("Full Body A/B/C", (FULL_BODY_A, FULL_BODY_B, FULL_BODY_C)),
        Split("Push/Pull/Legs", (PUSH, PULL, LEGS)),
        Split("Upper/Lower x2", (UPPER, LOWER, UPPER, LOWER)),
        Split("PPL + Upper/Lower", (PUSH, PULL, LEGS, UPPER, LOWER)),
        Split("Push/Pull/Legs x2", (PUSH, PULL, LEGS, PUSH, PULL, LEGS)),
    )
}

# Most workouts per week for each commitment level (never shown in the interface).
# Availability and recovery can mean fewer; six only happens when the user asks for it.
MAX_DAYS: dict[Commitment, int] = {
    Commitment.VERY_LIGHT: 1,
    Commitment.LIGHT: 2,
    Commitment.MODERATE: 3,
    Commitment.HIGH: 4,
    Commitment.DEDICATED: 5,
}

# The recommended split for each number of workouts, best first. Splits that train
# every muscle once (Upper/Lower x1, Push/Pull/Legs) are fallbacks for when the
# free days are too close together for the first choice.
RECOMMENDED: dict[int, list[str]] = {
    1: ["Full Body"],
    2: ["Full Body A/B", "Upper/Lower x1"],
    3: ["Full Body A/B/C", "Push/Pull/Legs"],
    4: ["Upper/Lower x2"],
    5: ["PPL + Upper/Lower"],
}

# Splits for each style the user can ask for, most workouts first.
BY_STYLE: dict[SplitStyle, list[str]] = {
    SplitStyle.FULL_BODY: ["Full Body A/B/C", "Full Body A/B", "Full Body"],
    SplitStyle.UPPER_LOWER: ["Upper/Lower x2", "Upper/Lower x1"],
    SplitStyle.PUSH_PULL_LEGS: ["Push/Pull/Legs"],
    SplitStyle.PPL_UPPER_LOWER: ["PPL + Upper/Lower"],
    SplitStyle.PPL_SIX_DAY: ["Push/Pull/Legs x2"],
}

STYLE_LABELS: dict[SplitStyle, str] = {
    SplitStyle.FULL_BODY: "Full Body",
    SplitStyle.UPPER_LOWER: "Upper/Lower",
    SplitStyle.PUSH_PULL_LEGS: "Push/Pull/Legs",
    SplitStyle.PPL_UPPER_LOWER: "PPL + Upper/Lower",
    SplitStyle.PPL_SIX_DAY: "Push/Pull/Legs, six days",
}


def recommended_splits(days: int) -> list[Split]:
    """Recommended splits that use exactly ``days`` workouts, best first."""
    return [SPLITS[name] for name in RECOMMENDED.get(days, [])]


def style_splits(style: SplitStyle) -> list[Split]:
    return [SPLITS[name] for name in BY_STYLE[style]]


def style_cap(style: SplitStyle, commitment: Commitment) -> int:
    """How many workouts a requested style may use.

    Styles with a fixed length (PPL + Upper/Lower is five days, PPL six-day is six)
    use their own length: asking for them is asking for that many days. Flexible
    styles (Full Body, Upper/Lower) stay within the commitment cap.
    """
    lengths = {split.days for split in style_splits(style)}
    return lengths.pop() if len(lengths) == 1 else MAX_DAYS[commitment]
