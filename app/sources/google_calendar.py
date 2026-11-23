"""Google Calendar API calls.

Privacy: other calendars are only ever asked for free/busy times, never event titles
or details. Events are read and written only on the GymScheduleBot calendar, which is
all the app's permission allows.

Every function takes a `service` object, so tests can pass a fake instead of Google.
Google errors are turned into two kinds: GoogleAuthError (the user must reconnect)
and GoogleUnavailable (try again later; the connection is kept).
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterable
from datetime import datetime
from typing import Any

import httplib2
from google.auth.exceptions import RefreshError, TransportError
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.google_auth import GoogleAuthError
from app.models import BusyBlock

APP_CALENDAR_NAME = "GymScheduleBot"
APP_CALENDAR_DESCRIPTION = "Workouts planned by GymScheduleBot. Safe to hide, recolor or delete."
FREEBUSY_LIMIT = 50  # calendars per free/busy request


class GoogleUnavailable(Exception):
    """Google or the network had a temporary problem."""


class NotFound(Exception):
    pass


class Conflict(Exception):
    """An event with that id already exists (possibly deleted)."""


def build_service(credentials):
    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


def run(request) -> Any:
    """Execute one API request, translating errors."""
    try:
        return request.execute(num_retries=1)
    except RefreshError as exc:
        raise GoogleAuthError("Google access expired or was revoked.") from exc
    except HttpError as exc:
        status = exc.resp.status
        if status == 401 or (status == 403 and "insufficientPermissions" in str(exc.content)):
            raise GoogleAuthError("Google access expired or is missing a permission.") from exc
        if status in (404, 410):
            raise NotFound from exc
        if status == 409:
            raise Conflict from exc
        raise GoogleUnavailable(f"Google Calendar error {status}") from exc
    except (TimeoutError, TransportError, httplib2.HttpLib2Error, OSError) as exc:
        raise GoogleUnavailable("Couldn't reach Google Calendar") from exc


def _pages(make_request) -> Iterable[dict]:
    token = None
    while True:
        page = run(make_request(token))
        yield from page.get("items", [])
        token = page.get("nextPageToken")
        if not token:
            return


# ------------------------------------------------------------ calendars ---
def list_calendars(service) -> list[dict[str, Any]]:
    """The user's calendars: id, name, whether it's shown in Google, and whether it's primary."""
    entries = _pages(lambda token: service.calendarList().list(pageToken=token, minAccessRole="freeBusyReader"))
    return [
        {
            "id": e["id"],
            "name": e.get("summaryOverride") or e.get("summary") or e["id"],
            "selected": bool(e.get("selected")),
            "primary": bool(e.get("primary")),
            "app": e.get("summary") == APP_CALENDAR_NAME and e.get("description") == APP_CALENDAR_DESCRIPTION,
        }
        for e in entries
    ]


def ensure_app_calendar(service, known_id: str | None, calendars: list[dict], timezone: str) -> str:
    """The GymScheduleBot calendar's id, reusing an existing one and creating it only once."""
    if known_id and any(c["id"] == known_id for c in calendars):
        return known_id
    existing = next((c["id"] for c in calendars if c["app"]), None)
    if existing:
        return existing
    created = run(
        service.calendars().insert(
            body={"summary": APP_CALENDAR_NAME, "description": APP_CALENDAR_DESCRIPTION, "timeZone": timezone}
        )
    )
    return created["id"]


# ------------------------------------------------------------- free/busy ---
def fetch_busy(
    service, calendar_ids: list[str], start: datetime, end: datetime, timezone: str
) -> tuple[list[BusyBlock], list[str]]:
    """Busy blocks across these calendars, plus the ids of any that couldn't be read."""
    blocks, unreadable = [], []
    for i in range(0, len(calendar_ids), FREEBUSY_LIMIT):
        chunk = calendar_ids[i : i + FREEBUSY_LIMIT]
        body = {
            "timeMin": start.isoformat(),
            "timeMax": end.isoformat(),
            "timeZone": timezone,
            "items": [{"id": cid} for cid in chunk],
        }
        response = run(service.freebusy().query(body=body))
        for cid, info in response.get("calendars", {}).items():
            if info.get("errors"):
                unreadable.append(cid)
            blocks += [
                BusyBlock(datetime.fromisoformat(p["start"]), datetime.fromisoformat(p["end"]))
                for p in info.get("busy", [])
            ]
    return sorted(blocks, key=lambda b: b.start), unreadable


# ------------------------------------------------- GymScheduleBot events ---
def list_app_events(service, calendar_id: str, start: datetime, end: datetime) -> list[dict]:
    """Every event on the GymScheduleBot calendar in this window, deleted ones included."""
    return list(
        _pages(
            lambda token: service.events().list(
                calendarId=calendar_id,
                timeMin=start.isoformat(),
                timeMax=end.isoformat(),
                showDeleted=True,
                singleEvents=True,
                maxResults=250,
                pageToken=token,
            )
        )
    )


def insert_event(service, calendar_id: str, body: dict) -> dict:
    return run(service.events().insert(calendarId=calendar_id, body=body))


def update_event(service, calendar_id: str, event_id: str, body: dict) -> dict:
    return run(service.events().update(calendarId=calendar_id, eventId=event_id, body=body))


def delete_event(service, calendar_id: str, event_id: str) -> None:
    with contextlib.suppress(NotFound):  # already gone, which is what we wanted
        run(service.events().delete(calendarId=calendar_id, eventId=event_id))
