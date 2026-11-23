"""Core data types shared across the app.

Everything time-related is a timezone-aware ``datetime``. Google Calendar and the
user's own events both become ``BusyBlock`` objects, so the scheduler never needs
to know where the data came from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from enum import StrEnum


class Commitment(StrEnum):
    """How often the user would like to train. Each level caps the workouts per week
    (1 to 5, see ``splits.MAX_DAYS``); availability and recovery decide the actual number."""

    VERY_LIGHT = "very_light"
    LIGHT = "light"
    MODERATE = "moderate"
    HIGH = "high"
    DEDICATED = "dedicated"


class Technique(StrEnum):
    """How much skill an exercise takes to do well. "Simpler exercises" avoids technical ones."""

    BASIC = "basic"
    TECHNICAL = "technical"  # free-weight squats, barbell rows, pull-ups...


class Volume(StrEnum):
    """Working sets per exercise the user asked the coach for."""

    LOWER = "lower"
    STANDARD = "standard"
    HIGHER = "higher"


class RepRange(StrEnum):
    """Rep ranges the user asked the coach for. All of them build muscle when sets go close to failure."""

    STANDARD = "standard"
    HEAVIER = "heavier"  # fewer reps, more weight
    LIGHTER = "lighter"  # more reps, less weight


class ExerciseKind(StrEnum):
    COMPOUND = "compound"  # several joints, heavier: 2-3 min rest
    ISOLATION = "isolation"  # one joint, lighter: 1-2 min rest


class SplitStyle(StrEnum):
    """A split the user asked for through the AI Coach, instead of the recommended one."""

    FULL_BODY = "full_body"
    UPPER_LOWER = "upper_lower"
    PUSH_PULL_LEGS = "push_pull_legs"
    PPL_UPPER_LOWER = "ppl_upper_lower"
    PPL_SIX_DAY = "ppl_six_day"


class TimeOfDay(StrEnum):
    MORNING = "morning"
    MIDDAY = "midday"
    EVENING = "evening"
    ANY = "any"


@dataclass(frozen=True)
class BusyBlock:
    """A span of time when the user is NOT available."""

    start: datetime
    end: datetime
    title: str = "Busy"
    event_id: str | None = None  # set for the user's own editable events
    kind: str = "commitment"  # or "unavailable": time the user marked as not for training


@dataclass(frozen=True)
class Exercise:
    """An exercise in the catalog: what it trains and who it suits."""

    name: str
    kind: ExerciseKind
    primary: tuple[str, ...]  # muscles it trains directly
    secondary: tuple[str, ...] = ()  # muscles that help (counted as half a set)
    reps: str = "8-12"
    equipment: str = "machine"
    technique: Technique = Technique.BASIC


@dataclass(frozen=True)
class PlannedExercise:
    """An exercise as prescribed in one workout, e.g. Bench Press 3×6-10, rest 2-3 min."""

    exercise: Exercise
    sets: int
    reps: str
    rest: str

    @property
    def name(self) -> str:
        return self.exercise.name

    def __str__(self) -> str:
        return f"{self.name} {self.sets}×{self.reps}"


@dataclass(frozen=True)
class Slot:
    """A free span of time long enough to hold a workout."""

    start: datetime
    end: datetime

    @property
    def minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)


@dataclass(frozen=True)
class Programming:
    """Changes to the default workouts the user asked the coach for. The defaults are the same for
    everyone: the app doesn't guess what someone needs from how long they've been lifting."""

    simpler_exercises: bool = False
    volume: Volume = Volume.STANDARD
    reps: RepRange = RepRange.STANDARD
    avoid: frozenset[str] = frozenset()  # exercise names to swap for something else


@dataclass
class Preferences:
    timezone: str = "America/Los_Angeles"
    commitment: Commitment = Commitment.MODERATE
    preferred_time: TimeOfDay = TimeOfDay.ANY
    session_minutes: int = 60
    # Time to get to/from the gym, shower, etc. Kept clear before and after.
    buffer_minutes: int = 15
    earliest_start: time = time(6, 0)
    latest_end: time = time(22, 0)
    # 0 = Monday ... 6 = Sunday. Days the user never wants to train.
    blocked_weekdays: set[int] = field(default_factory=set)
    programming: Programming = field(default_factory=Programming)
    requested_split: SplitStyle | None = None  # None = use the recommended split


@dataclass(frozen=True)
class Workout:
    """One scheduled training session."""

    name: str  # e.g. "Push"
    muscle_groups: frozenset[str]  # trained directly; used for recovery spacing
    exercises: tuple[PlannedExercise, ...]
    start: datetime
    end: datetime
    focus: str = ""  # e.g. "Chest · Shoulders · Triceps"
    minutes: int = 0  # estimated training time, warm-up included
    pinned: bool = False  # the user chose its time (or it already happened), so the planner keeps it
    skipped: bool = False  # the user said they didn't do it (past weeks only)

    @property
    def day(self) -> date:
        return self.start.date()


@dataclass(frozen=True)
class RecentWorkout:
    """A workout from before this plan starts that may still need recovery time."""

    name: str
    start: datetime
    muscle_groups: frozenset[str]


@dataclass(frozen=True)
class FixedWorkout:
    """A workout the planner must keep where it is.

    ``kind`` is "moved" when the user put it there (in the app or in Google Calendar) and
    "done" when it already started this week, so it still counts toward the week's plan.
    """

    name: str
    start: datetime
    end: datetime
    kind: str = "moved"


@dataclass
class WeeklyPlan:
    week_start: date
    split_name: str
    workouts: list[Workout]
    free_days: list[date]  # days that had at least one usable slot
    notes: list[str] = field(default_factory=list)  # human-readable reasons
    warnings: list[str] = field(default_factory=list)  # problems the user should see right away
    dropped: list[FixedWorkout] = field(default_factory=list)  # moved workouts the current settings can't keep
