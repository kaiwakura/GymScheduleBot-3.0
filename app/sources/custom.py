"""The user's own schedule: events they add by hand or import from a screenshot.

The browser stores these events (localStorage) and sends them with every request,
so they survive server restarts without a database. The server never trusts them
blindly: every event is validated here before it reaches the scheduler.

Event shape (JSON):
    {"id": "e_ab12", "title": "Calc lecture", "start": "09:30", "end": "10:45",
     "repeat": "weekly", "weekday": 0}            # every Monday
    {"id": "e_cd34", "title": "Dentist", "start": "16:30", "end": "17:15",
     "repeat": "once", "date": "2026-11-18"}      # one day only
    {..., "kind": "unavailable"}                  # time marked "can't train" in Edit availability

Sample weeks are loaded as events whose ids start with "sample-". While any are loaded
the app is in demo mode: Google Calendar isn't used for planning or syncing.
"""

from __future__ import annotations

import secrets
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.models import BusyBlock

MAX_EVENTS = 150
MAX_TITLE = 60
KINDS = ("commitment", "unavailable")
SAMPLE_PREFIX = "sample-"


def new_id() -> str:
    return "e_" + secrets.token_hex(4)


def clean_event(raw: Any) -> dict | None:
    """A safe copy of one event, or None if it's malformed."""
    if not isinstance(raw, dict):
        return None
    try:
        start = time.fromisoformat(str(raw["start"]))
        end = time.fromisoformat(str(raw["end"]))
    except (KeyError, ValueError):
        return None
    if not start < end:
        return None
    title = " ".join(str(raw.get("title") or "Busy").split())[:MAX_TITLE] or "Busy"
    event_id = str(raw.get("id") or new_id())[:40]
    event = {"id": event_id, "title": title, "start": start.strftime("%H:%M"), "end": end.strftime("%H:%M")}
    if raw.get("kind") in KINDS[1:]:
        event["kind"] = raw["kind"]

    if raw.get("repeat") == "once":
        try:
            event["date"] = date.fromisoformat(str(raw["date"])).isoformat()
        except (KeyError, ValueError):
            return None
        event["repeat"] = "once"
    else:
        try:
            weekday = int(raw["weekday"])
        except (KeyError, TypeError, ValueError):
            return None
        if not 0 <= weekday <= 6:
            return None
        event["repeat"] = "weekly"
        event["weekday"] = weekday
    return event


def clean_events(raw: Any) -> list[dict]:
    if not isinstance(raw, list):
        return []
    cleaned = [e for e in (clean_event(r) for r in raw[:MAX_EVENTS]) if e]
    seen, unique = set(), []
    for e in cleaned:  # drop duplicate ids
        if e["id"] not in seen:
            seen.add(e["id"])
            unique.append(e)
    return unique


def busy_blocks(events: list[dict], start: date, timezone: str, days: int = 7) -> list[BusyBlock]:
    """Expand the events into busy blocks for the `days` dates beginning at `start`."""
    tz = ZoneInfo(timezone)
    blocks = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        for e in events:
            if e["repeat"] == "weekly" and e["weekday"] != day.weekday():
                continue
            if e["repeat"] == "once" and e["date"] != day.isoformat():
                continue
            blocks.append(
                BusyBlock(
                    datetime.combine(day, time.fromisoformat(e["start"]), tzinfo=tz),
                    datetime.combine(day, time.fromisoformat(e["end"]), tzinfo=tz),
                    e["title"],
                    e["id"],
                    e.get("kind", "commitment"),
                )
            )
    return blocks


def is_sample(events: list[dict]) -> bool:
    """True while a sample week is loaded (demo mode)."""
    return any(str(e["id"]).startswith(SAMPLE_PREFIX) for e in events)


def weekly_event(title: str, weekday: int, start: time, end: time) -> dict | None:
    return clean_event(
        {"id": new_id(), "title": title, "start": start.strftime("%H:%M"), "end": end.strftime("%H:%M"),
         "repeat": "weekly", "weekday": weekday}
    )  # fmt: skip
