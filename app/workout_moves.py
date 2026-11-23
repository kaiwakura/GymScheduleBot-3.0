"""Workouts the user moved in the app (by dragging, picking another time, or accepting a coach proposal).

Like the user's own commitments, moves live in the browser (localStorage) and come with
every request. The planner keeps a moved workout where the user put it and plans the rest
of that week around it.

Move shape (JSON):
    {"week": "2026-11-23", "name": "Upper",
     "from": "2026-11-26T18:00:00-08:00",    # where the plan had it
     "start": "2026-11-27T07:00:00-08:00"}   # where the user put it

Only the start is stored: the workout's length always follows the current settings.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from app.models import FixedWorkout
from app.scheduler.sessions import TEMPLATES

MAX_MOVES = 80


def _parse(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


def clean_move(raw: Any) -> dict | None:
    if not isinstance(raw, dict) or raw.get("name") not in TEMPLATES:
        return None
    try:
        week = date.fromisoformat(str(raw.get("week")))
    except ValueError:
        return None
    start, origin = _parse(raw.get("start")), _parse(raw.get("from"))
    if start is None or origin is None or week.weekday() != 0:
        return None
    if not week <= start.date() < week + timedelta(days=7):
        return None  # a move stays inside its week
    return {"week": week.isoformat(), "name": raw["name"], "from": origin.isoformat(), "start": start.isoformat()}


def clean_moves(raw: Any, current_week: date) -> list[dict]:
    """Valid moves for this week and later. Older ones no longer change anything."""
    if not isinstance(raw, list):
        return []
    moves = [m for m in (clean_move(r) for r in raw[:MAX_MOVES]) if m and m["week"] >= current_week.isoformat()]
    unique: dict[tuple[str, str], dict] = {}
    for move in moves:  # one entry per workout: a later move of the same workout replaces the earlier one
        unique[(move["week"], move["from"])] = move
    return list(unique.values())


def for_week(moves: list[dict], week: date, minutes: int) -> list[FixedWorkout]:
    """The week's moved workouts, as fixed workouts the planner keeps."""
    fixed = []
    for move in moves:
        if move["week"] == week.isoformat():
            start = datetime.fromisoformat(move["start"])
            fixed.append(FixedWorkout(move["name"], start, start + timedelta(minutes=minutes), "moved"))
    return fixed


def add(moves: list[dict], week: date, name: str, origin: datetime, start: datetime) -> list[dict]:
    """``moves`` with one more. Moving a workout that was already moved keeps where the plan first had it."""
    week_iso = week.isoformat()
    previous = next((m for m in moves if m["week"] == week_iso and m["start"] == origin.isoformat()), None)
    first_origin = previous["from"] if previous else origin.isoformat()
    kept = [m for m in moves if m is not previous]
    if start.isoformat() == first_origin:
        return kept  # moved back to where the plan had it: no move needed
    return kept + [{"week": week_iso, "name": name, "from": first_origin, "start": start.isoformat()}]
