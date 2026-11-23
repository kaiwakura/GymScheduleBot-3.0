"""A visitor's saved planner settings.

Settings live in the signed session cookie, so the AI Coach "remembers" how
someone likes to train across visits in the same browser. Everything here is
small, JSON-safe data; the scheduler only ever sees a ``Preferences`` object.
(The user's own commitments and moved workouts are bigger, so the browser stores
those; see ``app.sources.custom`` and ``app.workout_moves``.)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, time, timedelta
from typing import Any

from app.ai.chat_changes import ChatChanges
from app.models import Commitment, Preferences, Programming, RepRange, SplitStyle, TimeOfDay, Volume

# Version 2 added five commitment levels. Version 1 had three, and its "moderate" (up to 4
# workouts) is now called "high"; see ``Settings.from_session``.
SETTINGS_VERSION = 2
BUFFER_CHOICES = (0, 15, 30, 45, 60)
MAX_AVOIDED = 20


def week_start(day: date) -> date:
    """The Monday of ``day``'s week. Weeks run Monday to Sunday."""
    return day - timedelta(days=day.weekday())


@dataclass
class Settings:
    version: int = SETTINGS_VERSION
    timezone: str = "America/Los_Angeles"
    commitment: str = Commitment.MODERATE.value
    preferred_time: str = TimeOfDay.EVENING.value  # most people train after work or class
    session_minutes: int = 60
    buffer_minutes: int = 15  # travel to and from the gym
    earliest_start: str = "06:00"
    latest_end: str = "22:00"
    blocked_weekdays: list[int] = field(default_factory=list)
    split_style: str | None = None  # a split the user asked for; None = recommended
    split_until: str | None = None  # ISO date the requested split ends; None = until changed
    google_excluded: list[str] = field(default_factory=list)  # calendars (by short key) that don't count as busy
    # Changes to the default workouts, asked for through the coach.
    simpler_exercises: bool = False
    volume: str = Volume.STANDARD.value
    reps: str = RepRange.STANDARD.value
    avoid_exercises: list[str] = field(default_factory=list)

    # ------------------------------------------------------------- storage ---
    @classmethod
    def from_session(cls, data: dict[str, Any] | None) -> Settings:
        """Rebuild settings from the cookie, ignoring anything unknown or malformed.

        Cookies saved before five commitment levels existed get one migration: their
        "moderate" (up to 4 workouts) becomes "high", which still means 4. Saving the new
        version number makes sure it never happens twice.
        """
        settings = cls()
        if not isinstance(data, dict):
            return settings
        for key, value in data.items():
            if hasattr(settings, key):
                setattr(settings, key, value)
        saved_before_v2 = not isinstance(data.get("version"), int) or data["version"] < 2
        if saved_before_v2 and settings.commitment == Commitment.MODERATE.value:
            settings.commitment = Commitment.HIGH.value
        settings.version = SETTINGS_VERSION
        return settings.validated()

    def to_session(self) -> dict[str, Any]:
        return asdict(self)

    def validated(self) -> Settings:
        """Clamp every field to a safe value (the cookie could be old or edited)."""
        defaults = Settings()
        if self.commitment not in {c.value for c in Commitment}:
            self.commitment = defaults.commitment
        if self.preferred_time not in {t.value for t in TimeOfDay}:
            self.preferred_time = defaults.preferred_time
        if self.split_style not in {s.value for s in SplitStyle}:
            self.split_style = self.split_until = None
        try:
            if self.split_until is not None:
                date.fromisoformat(self.split_until)
        except (TypeError, ValueError):
            self.split_style = self.split_until = None
        if self.volume not in {v.value for v in Volume}:
            self.volume = defaults.volume
        if self.reps not in {r.value for r in RepRange}:
            self.reps = defaults.reps
        self.simpler_exercises = self.simpler_exercises is True
        if not isinstance(self.avoid_exercises, list):
            self.avoid_exercises = []
        self.avoid_exercises = list(dict.fromkeys(str(n)[:60] for n in self.avoid_exercises))[:MAX_AVOIDED]
        try:
            self.session_minutes = max(20, min(180, int(self.session_minutes)))
            self.buffer_minutes = max(0, min(60, int(self.buffer_minutes)))
            time.fromisoformat(self.earliest_start)
            time.fromisoformat(self.latest_end)
            self.blocked_weekdays = sorted({int(d) for d in self.blocked_weekdays if 0 <= int(d) <= 6})
        except (TypeError, ValueError):
            return defaults
        if not isinstance(self.google_excluded, list):
            self.google_excluded = []
        self.google_excluded = [str(k)[:16] for k in self.google_excluded][:30]
        return self

    # ------------------------------------------------------------ planning ---
    def active_split(self, week: date) -> SplitStyle | None:
        """The requested split for the week starting ``week``, unless it was only for an earlier week."""
        if self.split_style is None:
            return None
        if self.split_until is not None and date.fromisoformat(self.split_until) < week:
            return None
        return SplitStyle(self.split_style)

    def programming(self) -> Programming:
        return Programming(
            simpler_exercises=self.simpler_exercises,
            volume=Volume(self.volume),
            reps=RepRange(self.reps),
            avoid=frozenset(self.avoid_exercises),
        )

    def to_preferences(self, week: date | None = None) -> Preferences:
        """What the scheduler needs. ``week`` decides whether a requested split applies; leave it
        out when only the time zone or the exercises matter."""
        return Preferences(
            timezone=self.timezone,
            commitment=Commitment(self.commitment),
            preferred_time=TimeOfDay(self.preferred_time),
            session_minutes=self.session_minutes,
            buffer_minutes=self.buffer_minutes,
            earliest_start=time.fromisoformat(self.earliest_start),
            latest_end=time.fromisoformat(self.latest_end),
            blocked_weekdays=set(self.blocked_weekdays),
            programming=self.programming(),
            requested_split=self.active_split(week) if week else None,
        )

    def set_split(self, style: SplitStyle | None, ongoing: bool, today: date) -> None:
        """Ask for a split from now on, or for the rest of this week (it ends on Sunday)."""
        if style is None:
            self.split_style = self.split_until = None
            return
        self.split_style = style.value
        self.split_until = None if ongoing else (week_start(today) + timedelta(days=6)).isoformat()

    def apply(self, changes: ChatChanges, today: date) -> Settings:
        """Layer changes from a chat message on top of these settings. ``today`` is the user's
        local date (a "this week" split ends on its Sunday).

        Weekly commitments in the message ("soccer Tuesdays 6-7:30pm") aren't settings:
        the chat endpoint turns those into the user's own calendar events instead.
        """
        if changes.reset_split:
            self.set_split(None, False, today)
        elif changes.split_style is not None:
            # Anything not clearly "from now on" applies to this week only; the coach asks to confirm.
            self.set_split(changes.split_style, changes.split_scope == "ongoing", today)
        elif changes.split_scope == "ongoing" and self.split_style is not None:
            self.split_until = None
        if changes.preferred_time is not None:
            self.preferred_time = changes.preferred_time.value
        if changes.commitment is not None:
            self.commitment = changes.commitment.value
        if changes.session_minutes is not None:
            self.session_minutes = changes.session_minutes
        if changes.earliest_start is not None:
            self.earliest_start = changes.earliest_start.strftime("%H:%M")
        if changes.latest_end is not None:
            self.latest_end = changes.latest_end.strftime("%H:%M")
        blocked = (set(self.blocked_weekdays) | changes.blocked_weekdays) - changes.allowed_weekdays
        self.blocked_weekdays = sorted(blocked)
        if changes.reset_programming:
            defaults = Settings()
            self.simpler_exercises, self.volume, self.reps = False, defaults.volume, defaults.reps
            self.avoid_exercises = []
        if changes.simpler_exercises is not None:
            self.simpler_exercises = changes.simpler_exercises
        if changes.volume is not None:
            self.volume = changes.volume.value
        if changes.reps is not None:
            self.reps = changes.reps.value
        self.avoid_exercises = [n for n in self.avoid_exercises if n not in changes.allow_exercises]
        self.avoid_exercises += [n for n in changes.avoid_exercises if n not in self.avoid_exercises]
        return self.validated()
