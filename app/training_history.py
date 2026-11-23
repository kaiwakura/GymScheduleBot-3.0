"""Recent workouts, so recovery carries over from one planning week to the next.

The browser keeps the last few weeks of planned workouts (like the user's own events)
and sends them with every request. Past weeks on the calendar show them. A workout that has already started is treated
as possibly done: the planner keeps 48+ hours before training those muscles again.
It isn't recorded as completed, and if the user tells the coach they skipped it, it
stops counting.

Entry shape (JSON):
    {"name": "Legs", "start": "2026-11-22T18:00:00-08:00", "muscles": ["quads", ...],
     "status": "planned"}                 # or "skipped"
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from app.models import RecentWorkout, WeeklyPlan
from app.scheduler.exercises import MUSCLES

KEEP_DAYS = 56  # how far back the browser keeps entries (past weeks show them)
RECOVERY_WINDOW = timedelta(hours=96)  # older sessions no longer affect the next one
MAX_ENTRIES = 80
STATUSES = ("planned", "skipped")


def clean_entry(raw: Any) -> dict | None:
    if not isinstance(raw, dict):
        return None
    try:
        start = datetime.fromisoformat(str(raw["start"]))
    except (KeyError, ValueError):
        return None
    if start.tzinfo is None:
        return None
    muscles = raw.get("muscles")
    if not isinstance(muscles, list):
        return None
    return {
        "name": str(raw.get("name") or "Workout")[:40],
        "start": start.isoformat(),
        "muscles": [m for m in MUSCLES if m in muscles],
        "status": raw.get("status") if raw.get("status") in STATUSES else "planned",
    }


def clean_history(raw: Any) -> list[dict]:
    if not isinstance(raw, list):
        return []
    return [e for e in (clean_entry(r) for r in raw[:MAX_ENTRIES]) if e]


def _start(entry: dict) -> datetime:
    return datetime.fromisoformat(entry["start"])


def merge_plan(history: list[dict], plan: WeeklyPlan, now: datetime) -> list[dict]:
    """Keep what already started, replace everything upcoming with the new plan's workouts."""
    past = [e for e in history if now - timedelta(days=KEEP_DAYS) <= _start(e) < now]
    upcoming = [
        {"name": w.name, "start": w.start.isoformat(), "muscles": sorted(w.muscle_groups), "status": "planned"}
        for w in plan.workouts
        if w.start >= now
    ]
    return sorted(past + upcoming, key=_start)[-MAX_ENTRIES:]


def recent_workouts(history: list[dict], now: datetime) -> list[RecentWorkout]:
    """Workouts that started in the last 96 hours and weren't skipped."""
    return [
        RecentWorkout(e["name"], _start(e), frozenset(e["muscles"]))
        for e in history
        if e["status"] != "skipped" and now - RECOVERY_WINDOW <= _start(e) <= now
    ]


def mark_skipped(history: list[dict], days: set[date], now: datetime) -> list[str]:
    """Mark started workouts on these dates as skipped. Returns what was marked, e.g. ["Sunday's Legs"]."""
    marked = []
    for entry in history:
        start = _start(entry).astimezone(now.tzinfo)
        if start.date() in days and start <= now and entry["status"] != "skipped":
            entry["status"] = "skipped"
            marked.append(f"{start.strftime('%A')}'s {entry['name']}")
    return marked


def last_date_for(weekday: int, today: date) -> date:
    """The most recent date (today included) that falls on ``weekday``."""
    return today - timedelta(days=(today.weekday() - weekday) % 7)
