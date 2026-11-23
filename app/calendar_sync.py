"""Keeping the GymScheduleBot calendar in step with the plan.

Each workout event has an id made from its date ("gsb20261127"), so syncing twice
can't create two events for the same day: Google rejects a second event with the same
id. The event also remembers the start and end GymScheduleBot gave it. If the event's
real time differs, the user moved it in Google Calendar, and the app keeps their time.

Syncing is two steps:
  1. `plan_sync` compares the plan with the calendar and lists every change
     (add, move, update, remove, restore). Nothing is written.
  2. `apply_sync` makes those changes after the user confirms. It only ever touches
     events on the GymScheduleBot calendar, and never ones that have already started.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.formatting import clock
from app.google_auth import GoogleAuthError
from app.models import WeeklyPlan, Workout
from app.sources.google_calendar import Conflict, delete_event, insert_event, update_event

APP_TAG = "gymschedulebot"
_ID_RE = re.compile(r"^gsb(\d{8})[a-v]?$")


def event_id_for(day: date, taken: set[str] | frozenset[str] = frozenset()) -> str:
    """ "gsb20261127", or "gsb20261127a", "...b" if a workout the user moved still holds that id.

    Google event ids may only use the letters a-v and digits.
    """
    base = f"gsb{day:%Y%m%d}"
    return next(i for i in [base] + [base + c for c in "abcdefghijklmnopqrstuv"] if i not in taken)


def day_from_event_id(event_id: str) -> date | None:
    match = _ID_RE.match(event_id or "")
    return datetime.strptime(match.group(1), "%Y%m%d").date() if match else None


# ------------------------------------------------------------ event body ---
def describe(workout: Workout, split_name: str) -> str:
    lines = [f"{split_name} · about {workout.minutes} min"]
    lines += [f"{e.name} — {e.sets} × {e.reps.replace('-', '–')}" for e in workout.exercises]
    lines.append("Created by GymScheduleBot")
    return "\n".join(lines)


def content_hash(summary: str, description: str) -> str:
    return hashlib.sha256(f"{summary}\n{description}".encode()).hexdigest()[:16]


def event_body(workout: Workout, split_name: str, timezone: str, event_id: str | None = None) -> dict:
    summary = f"Workout: {workout.name}"
    description = describe(workout, split_name)
    return {
        "id": event_id or event_id_for(workout.day),
        "summary": summary,
        "description": description,
        "start": {"dateTime": workout.start.isoformat(), "timeZone": timezone},
        "end": {"dateTime": workout.end.isoformat(), "timeZone": timezone},
        "status": "confirmed",  # also restores an event with this id that was deleted earlier
        "extendedProperties": {
            "private": {
                "app": APP_TAG,
                "workout": workout.name,
                "planned_start": workout.start.isoformat(),
                "planned_end": workout.end.isoformat(),
                "content": content_hash(summary, description),
            }
        },
    }


# ------------------------------------------------- reading the calendar ---
@dataclass(frozen=True)
class CalendarWorkout:
    """A workout event found on the GymScheduleBot calendar."""

    event_id: str
    day: date  # the day GymScheduleBot created it for
    name: str
    start: datetime
    end: datetime
    moved: bool = False  # the user changed its time in Google Calendar
    content: str = ""


@dataclass
class CalendarState:
    upcoming: dict[date, CalendarWorkout] = field(default_factory=dict)  # managed by the plan
    pinned: list[CalendarWorkout] = field(default_factory=list)  # moved by the user: kept as is
    recent: list[CalendarWorkout] = field(default_factory=list)  # already started
    deleted_days: set[date] = field(default_factory=set)  # deleted in Google Calendar

    def within(self, first: date, last: date) -> CalendarState:
        """Only the events for days ``first`` to ``last``, so syncing one week never touches another."""
        return CalendarState(
            {d: w for d, w in self.upcoming.items() if first <= d <= last},
            [w for w in self.pinned if first <= w.start.date() <= last],
            self.recent,
            {d for d in self.deleted_days if first <= d <= last},
        )

    def weeks(self) -> set[date]:
        """Mondays of the weeks that have GymScheduleBot events (or deleted ones) still to come."""
        days = set(self.upcoming) | {w.start.date() for w in self.pinned} | self.deleted_days
        return {d - timedelta(days=d.weekday()) for d in days}


def _when(value: dict | None, tz: ZoneInfo) -> datetime | None:
    if not value or "dateTime" not in value:
        return None  # all-day events aren't workouts
    return datetime.fromisoformat(value["dateTime"]).astimezone(tz)


def read_state(items: list[dict], now: datetime) -> CalendarState:
    """Sort the calendar's events into upcoming, moved by the user, recent and deleted."""
    state = CalendarState()
    tz = now.tzinfo
    for item in items:
        day = day_from_event_id(item.get("id", ""))
        if day is None:
            continue  # not one of ours (the user may add their own notes to this calendar)
        if item.get("status") == "cancelled":
            state.deleted_days.add(day)
            continue
        private = item.get("extendedProperties", {}).get("private", {})
        start, end = _when(item.get("start"), tz), _when(item.get("end"), tz)
        if start is None or end is None:
            continue
        planned = (private.get("planned_start"), private.get("planned_end"))
        moved = bool(planned[0]) and (
            datetime.fromisoformat(planned[0]) != start or datetime.fromisoformat(planned[1]) != end
        )
        workout = CalendarWorkout(
            item["id"], day, private.get("workout", "Workout"), start, end, moved, private.get("content", "")
        )
        if start < now:
            state.recent.append(workout)
        elif moved:
            state.pinned.append(workout)
        else:
            state.upcoming[day] = workout
    return state


# ------------------------------------------------------------- the diff ---
@dataclass(frozen=True)
class SyncChange:
    kind: str  # "add", "move", "update", "remove" or "restore"
    label: str  # e.g. "Move Upper — Friday, 3:00 PM → 5:00 PM"
    workout: Workout | None = None
    event: CalendarWorkout | None = None
    new_id: str | None = None  # id for a new event

    @property
    def key(self) -> str:
        day = self.workout.day if self.workout else self.event.day
        return f"{self.kind}:{day.isoformat()}"


@dataclass
class SyncPreview:
    changes: list[SyncChange]
    unchanged: int
    kept: list[str]  # workouts the user moved in Google Calendar, left alone

    @property
    def fingerprint(self) -> str:
        """Identifies this exact set of changes, so confirming applies what was previewed."""
        return hashlib.sha256("\n".join(f"{c.key}|{c.label}" for c in self.changes).encode()).hexdigest()[:16]

    def to_json(self) -> dict:
        return {
            "changes": [{"key": c.key, "kind": c.kind, "label": c.label} for c in self.changes],
            "unchanged": self.unchanged,
            "kept": self.kept,
            "fingerprint": self.fingerprint,
        }


def _when_label(moment: datetime) -> str:
    return f"{moment.strftime('%A')}, {clock(moment)}"


def plan_sync(plan: WeeklyPlan, state: CalendarState, removed_days: set[str], now: datetime) -> SyncPreview:
    """Everything a sync would change. Nothing is written to Google here."""
    # Workouts moved in Google Calendar are already there as the user left them. Workouts
    # moved in the app are pinned too, but Google doesn't know about them yet, so they sync.
    in_google = {(w.name, w.start) for w in state.pinned}
    planned = {w.day: w for w in plan.workouts if (w.name, w.start) not in in_google and w.start >= now}
    adds, removes, changes, unchanged = [], [], [], 0
    taken = {w.event_id for w in state.pinned + state.recent}  # ids that must not be reused

    for day, workout in sorted(planned.items()):
        event = state.upcoming.get(day)
        body = event_body(workout, plan.split_name, "UTC")
        expected = body["extendedProperties"]["private"]["content"]
        if event is None:
            if day in state.deleted_days and day.isoformat() not in removed_days:
                label = f"Restore {workout.name} — {_when_label(workout.start)} (you deleted it in Google Calendar)"
                changes.append(SyncChange("restore", label, workout, new_id=event_id_for(day, taken)))
            else:
                adds.append(workout)
        elif event.start != workout.start:
            label = f"Move {workout.name} — {_when_label(event.start)} → {clock(workout.start)}"
            changes.append(SyncChange("move", label, workout, event))
        elif event.end != workout.end:
            minutes = int((workout.end - workout.start).total_seconds() // 60)
            label = f"Update {workout.name} — {_when_label(workout.start)} (now {minutes} min)"
            changes.append(SyncChange("update", label, workout, event))
        elif event.name != workout.name or event.content != expected:
            label = f"Update {workout.name} — {_when_label(workout.start)} (exercises changed)"
            changes.append(SyncChange("update", label, workout, event))
        else:
            unchanged += 1
    removes = [event for day, event in sorted(state.upcoming.items()) if day not in planned]

    # A workout that moved to another day shows as one "move", not a remove plus an add.
    for workout in adds:
        event = next((e for e in removes if e.name == workout.name), None)
        if event:
            removes.remove(event)
            label = f"Move {workout.name} — {_when_label(event.start)} → {_when_label(workout.start)}"
            changes.append(SyncChange("move", label, workout, event, event_id_for(workout.day, taken)))
        else:
            label = f"Add {workout.name} — {_when_label(workout.start)}"
            changes.append(SyncChange("add", label, workout, new_id=event_id_for(workout.day, taken)))
    for event in removes:
        changes.append(SyncChange("remove", f"Remove {event.name} — {event.start.strftime('%A')}", event=event))

    order = {"add": 0, "move": 1, "update": 2, "restore": 3, "remove": 4}
    changes.sort(key=lambda c: (order[c.kind], c.key))
    kept = [f"{w.name} — {_when_label(w.start)} (you moved it)" for w in state.pinned]
    return SyncPreview(changes, unchanged, kept)


# ------------------------------------------------------------- applying ---
@dataclass
class SyncResult:
    done: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    removed_days: list[str] = field(default_factory=list)


def apply_sync(
    service, calendar_id: str, plan: WeeklyPlan, preview: SyncPreview, restore: set[str], timezone: str
) -> SyncResult:
    """Make the previewed changes. Restores happen only if the user ticked them.

    A failed change doesn't stop the others; the caller reports what didn't work so the
    user can retry. Losing Google access does stop the sync, since nothing else would work.
    """
    result = SyncResult()
    for change in preview.changes:
        if change.kind == "restore" and change.key not in restore:
            continue
        try:
            _apply_one(service, calendar_id, change, plan.split_name, timezone, result)
            result.done.append(change.label)
        except GoogleAuthError:
            raise
        except Exception:
            result.failed.append(change.label)
    return result


def _apply_one(service, calendar_id, change: SyncChange, split_name: str, timezone: str, result: SyncResult):
    if change.kind == "remove":
        delete_event(service, calendar_id, change.event.event_id)
        result.removed_days.append(change.event.day.isoformat())
        return
    body = event_body(change.workout, split_name, timezone, change.new_id)
    if change.event and change.event.day != change.workout.day:  # moved to another day
        delete_event(service, calendar_id, change.event.event_id)
        result.removed_days.append(change.event.day.isoformat())
    if change.event and change.event.day == change.workout.day:
        body["id"] = change.event.event_id
        update_event(service, calendar_id, change.event.event_id, body)
        return
    try:
        insert_event(service, calendar_id, body)
    except Conflict:
        # The id was used before (an event deleted earlier keeps its id): bring it back.
        update_event(service, calendar_id, body["id"], body)
