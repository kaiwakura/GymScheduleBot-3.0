"""Bringing Google Calendar into planning: availability, the GymScheduleBot calendar and sync.

This sits between the web routes and the Google API module, so main.py stays about HTTP
and the planner never knows Google exists. Google data for a session is cached for a
few minutes, so dragging an event or chatting with the coach doesn't call Google every
time; opening the page and syncing always read it fresh.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from google.auth.exceptions import TransportError

from app import calendar_sync, google_auth
from app.calendar_sync import CalendarState, SyncPreview, SyncResult
from app.formatting import clock
from app.google_auth import GoogleAuthError
from app.models import BusyBlock, WeeklyPlan
from app.planning import Inputs, plan_for
from app.sources import google_calendar
from app.sources.google_calendar import GoogleUnavailable
from app.training_history import RECOVERY_WINDOW

log = logging.getLogger(__name__)

CACHE_TTL = timedelta(minutes=5)
SYNC_WEEKS = 8  # syncing looks at most this many weeks ahead, so it never runs on forever
_cache: dict[tuple[str, tuple[datetime, datetime], tuple[str, ...]], GoogleData] = {}

EXPIRED = "Your Google Calendar connection expired or was removed. Connect again to plan around it."
UNREACHABLE = "Couldn't reach Google Calendar right now, so this plan doesn't include your Google events."


@dataclass
class GoogleData:
    busy: list[BusyBlock]
    state: CalendarState
    calendars: list[dict]  # the user's calendars, for the checklist
    calendar_id: str  # the GymScheduleBot calendar
    window: tuple[datetime, datetime]
    fetched_at: datetime
    unreadable: list[str] = field(default_factory=list)


def calendar_key(calendar_id: str) -> str:
    """A short stand-in for a calendar id, so settings don't store ids (often email addresses)."""
    return hashlib.sha256(calendar_id.encode()).hexdigest()[:12]


def make_service(session: dict):
    """An authorized Calendar API client for this session's connection."""
    connection = google_auth.load_connection(session)
    if connection is None:
        raise GoogleAuthError("Not connected.")
    try:
        creds, refreshed = google_auth.credentials(connection)
    except TransportError as exc:
        raise GoogleUnavailable("Couldn't refresh the Google token") from exc
    if refreshed:
        google_auth.save_connection(session, connection)  # keep the new access token
    return google_calendar.build_service(creds), connection


def _cache_key(session: dict) -> str:
    if "google_cache" not in session:
        session["google_cache"] = secrets.token_hex(8)  # not a secret: only finds this session's cache
    return session["google_cache"]


def forget(session: dict) -> None:
    key = session.get("google_cache", "")
    for cached in [k for k in _cache if k[0] == key]:
        _cache.pop(cached, None)


def availability(
    session: dict,
    excluded: list[str],
    window: tuple[datetime, datetime],
    now: datetime,
    timezone: str,
    refresh: bool = False,
) -> tuple[GoogleData | None, str | None]:
    """Busy times and the GymScheduleBot calendar's state, plus a message for the user if needed.

    Returns (None, message) when there's nothing to plan around. A lost connection is
    cleared; a temporary problem keeps it and, if possible, reuses the last good data.
    """
    key = (_cache_key(session), window, tuple(sorted(excluded)))
    cached = _cache.get(key)
    if cached and now - cached.fetched_at < CACHE_TTL and not refresh:
        return cached, None
    try:
        service, connection = make_service(session)
        calendars = google_calendar.list_calendars(service)
        calendar_id = google_calendar.ensure_app_calendar(service, connection.calendar_id, calendars, timezone)
        if calendar_id != connection.calendar_id:
            connection.calendar_id = calendar_id
            google_auth.save_connection(session, connection)
        busy_ids = [
            c["id"]
            for c in calendars
            if c["selected"] and c["id"] != calendar_id and not c["app"] and calendar_key(c["id"]) not in excluded
        ]
        busy, unreadable = google_calendar.fetch_busy(service, busy_ids, *window, timezone) if busy_ids else ([], [])
        events_from, events_to = min(window[0], now - RECOVERY_WINDOW), max(window[1], now)
        items = google_calendar.list_app_events(service, calendar_id, events_from, events_to)
        data = GoogleData(busy, calendar_sync.read_state(items, now), calendars, calendar_id, window, now, unreadable)
    except GoogleAuthError:
        google_auth.clear_connection(session)
        forget(session)
        return None, EXPIRED
    except GoogleUnavailable:
        log.warning("Google Calendar unavailable")
        if cached:
            return (
                cached,
                f"Couldn't reach Google Calendar, so this plan uses availability from {clock(cached.fetched_at)}.",
            )
        return None, UNREACHABLE

    _cache[key] = data
    _prune(now)
    names = [c["name"] for c in calendars if c["id"] in unreadable]
    note = f"Couldn't read {', '.join(names)}, so it isn't included." if names else None
    return data, note


def _prune(now: datetime) -> None:
    for key in [k for k, v in _cache.items() if now - v.fetched_at > CACHE_TTL * 6]:
        _cache.pop(key, None)


def checklist(data: GoogleData, excluded: list[str]) -> list[dict]:
    """The calendars that can count toward availability, for the checklist."""
    return [
        {
            "key": calendar_key(c["id"]),
            "name": c["name"],
            "primary": c["primary"],
            "included": calendar_key(c["id"]) not in excluded,
        }
        for c in data.calendars
        if c["selected"] and not c["app"] and c["id"] != data.calendar_id
    ]


# ------------------------------------------------------------------ sync ---
@dataclass
class WeekSync:
    """One week of a sync preview."""

    week: date
    plan: WeeklyPlan
    preview: SyncPreview
    selected: bool  # the week on screen; other weeks are listed only when they have changes

    def to_json(self) -> dict:
        end = self.week + timedelta(days=6)
        label = f"{self.week:%b} {self.week.day}–{'' if end.month == self.week.month else f'{end:%b} '}{end.day}"
        return {"week": self.week.isoformat(), "label": label, "selected": self.selected, **self.preview.to_json()}


def preview(session: dict, plan: WeeklyPlan, data: GoogleData, now: datetime) -> SyncPreview:
    """What syncing one week's plan would change. Nothing is written."""
    connection = google_auth.load_connection(session)
    removed = set(connection.removed_days) if connection else set()
    week = plan.week_start
    return calendar_sync.plan_sync(plan, data.state.within(week, week + timedelta(days=6)), removed, now)


def preview_weeks(session: dict, inputs: Inputs, selected: date) -> list[WeekSync]:
    """The week on screen, plus every other week with changes waiting to sync.

    "Other weeks" are the current week, the ones the user already synced (they have
    GymScheduleBot events) and the ones they moved workouts in, up to ``SYNC_WEEKS``
    ahead. Weeks nobody touched aren't generated, so changing a preference never fills
    months of the calendar by itself.
    """
    data = inputs.google
    current = inputs.current_week
    horizon = current + timedelta(weeks=SYNC_WEEKS)
    moved = {date.fromisoformat(m["week"]) for m in inputs.moves}
    pending = {w for w in data.state.weeks() | moved if current <= w < horizon} | {current}
    candidates = sorted(pending | ({selected} if selected >= current else set()))
    weeks = []
    for week in candidates:
        plan = plan_for(inputs, week)
        changes = preview(session, plan, data, inputs.now)
        if week == selected or changes.changes:
            weeks.append(WeekSync(week, plan, changes, week == selected))
    return weeks


def apply(
    session: dict,
    plan: WeeklyPlan,
    data: GoogleData,
    changes: SyncPreview,
    restore: set[str],
    now: datetime,
    timezone: str,
) -> SyncResult:
    """Write the confirmed changes and remember when it happened."""
    service, connection = make_service(session)
    result = calendar_sync.apply_sync(service, data.calendar_id, plan, changes, restore, timezone)
    today = now.date().isoformat()
    connection.removed_days = sorted({d for d in connection.removed_days + result.removed_days if d >= today})
    if result.done or not result.failed:
        connection.last_sync = now.isoformat()
    google_auth.save_connection(session, connection)
    forget(session)  # the calendar just changed
    return result


def last_sync_label(session: dict, today: date) -> str | None:
    connection = google_auth.load_connection(session)
    if connection is None or not connection.last_sync:
        return None
    when = datetime.fromisoformat(connection.last_sync)
    day = "today" if when.date() == today else f"{when:%a %b} {when.day}"
    return f"Last synced {day} at {clock(when)}"
