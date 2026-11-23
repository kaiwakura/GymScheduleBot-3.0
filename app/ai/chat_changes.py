"""What a chat message asks to change, as plain data.

Both readers of chat messages (Gemini and the keyword parser in ``rule_parser``) produce
a ``ChatChanges``; ``Settings.apply`` and the chat route decide what to do with it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time

from app.models import Commitment, RepRange, SplitStyle, TimeOfDay, Volume
from app.scheduler.exercises import CATALOG
from app.scheduler.splits import STYLE_LABELS

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
EXERCISE_NAMES = [e.name for e in CATALOG]


@dataclass
class RecurringBusy:
    """A weekly commitment mentioned in the notes, e.g. "soccer Tuesdays 6-7:30pm"."""

    weekday: int
    start: time
    end: time
    title: str


@dataclass
class MoveRequest:
    """ "Move Thursday's workout to Friday morning." Any part may be missing; the app fills it in."""

    to_weekday: int
    from_weekday: int | None = None
    workout: str | None = None  # e.g. "Upper", when they name the workout instead of the day
    at: time | None = None
    part_of_day: TimeOfDay | None = None


@dataclass
class ChatChanges:
    """What one chat message asks to change. Empty fields mean "not mentioned"."""

    preferred_time: TimeOfDay | None = None
    commitment: Commitment | None = None
    session_minutes: int | None = None
    earliest_start: time | None = None
    latest_end: time | None = None
    blocked_weekdays: set[int] = field(default_factory=set)
    allowed_weekdays: set[int] = field(default_factory=set)  # days to un-block ("Sundays are fine now")
    recurring_busy: list[RecurringBusy] = field(default_factory=list)
    split_style: SplitStyle | None = None
    split_scope: str | None = None  # "this_week", "ongoing", or None when the message doesn't say
    reset_split: bool = False  # "switch me back to the recommended split"
    skipped_weekdays: set[int] = field(default_factory=set)  # "I skipped my workout on Sunday"
    # Workout content, which is the same for everyone until they ask for something different.
    simpler_exercises: bool | None = None
    volume: Volume | None = None
    reps: RepRange | None = None
    avoid_exercises: list[str] = field(default_factory=list)  # catalog names to swap out
    allow_exercises: list[str] = field(default_factory=list)  # swapped-out names to bring back
    reset_programming: bool = False
    move: MoveRequest | None = None
    source: str = "rules"  # "ai" or "rules", handy when debugging a reply

    def is_empty(self) -> bool:
        return not self.describe()

    def describe(self) -> list[str]:
        """Short human-readable list of what these notes change, e.g. ["mornings", "no Sundays"]."""
        short = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        parts: list[str] = []
        if self.preferred_time is not None:
            parts.append("any time of day" if self.preferred_time is TimeOfDay.ANY else f"{self.preferred_time.value}s")
        if self.commitment is not None:
            parts.append(f"{self.commitment.value} commitment")
        if self.session_minutes is not None:
            parts.append(f"{self.session_minutes}-minute sessions")
        if self.earliest_start is not None:
            parts.append(f"nothing before {self.earliest_start.strftime('%H:%M')}")
        if self.latest_end is not None:
            parts.append(f"done by {self.latest_end.strftime('%H:%M')}")
        parts += [f"no {short[d]}" for d in sorted(self.blocked_weekdays)]
        parts += [f"{short[d]} is open again" for d in sorted(self.allowed_weekdays)]
        parts += [
            f"{r.title} {short[r.weekday]} {r.start.strftime('%H:%M')}-{r.end.strftime('%H:%M')}"
            for r in self.recurring_busy
        ]
        parts += [f"skipped {short[d]}" for d in sorted(self.skipped_weekdays)]
        if self.reset_programming:
            parts.append("default workouts")
        if self.simpler_exercises is not None:
            parts.append("simpler exercises" if self.simpler_exercises else "standard exercises")
        if self.volume is not None:
            parts.append({"lower": "fewer sets", "standard": "standard sets", "higher": "more sets"}[self.volume.value])
        if self.reps is not None:
            parts.append({"standard": "standard reps", "heavier": "heavier sets", "lighter": "lighter sets"}[self.reps])
        parts += [f"swap out {n}" for n in self.avoid_exercises]
        parts += [f"bring back {n}" for n in self.allow_exercises]
        if self.move is not None:
            parts.append(f"move a workout to {short[self.move.to_weekday]}")
        if self.reset_split:
            parts.append("recommended split")
        elif self.split_style is not None:
            when = "from now on" if self.split_scope == "ongoing" else "this week"
            parts.append(f"{STYLE_LABELS[self.split_style]} {when}")
        elif self.split_scope == "ongoing":
            parts.append("keep this split")
        return parts
