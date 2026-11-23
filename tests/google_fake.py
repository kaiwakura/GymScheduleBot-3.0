"""An in-memory stand-in for the Google Calendar API client.

It behaves like Google where it matters for syncing: inserting an id that already
exists (even a deleted one) fails with 409, deleting twice fails with 410, deleted
events keep only their id and status, and free/busy never includes event details.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import httplib2
from googleapiclient.errors import HttpError

APP_DESCRIPTION = "Workouts planned by GymScheduleBot. Safe to hide, recolor or delete."


def http_error(status: int, reason: str = "") -> HttpError:
    return HttpError(httplib2.Response({"status": status}), reason.encode() or b"error")


class Request:
    def __init__(self, fake, op, fn):
        self.fake, self.op, self.fn = fake, op, fn

    def execute(self, num_retries=0):
        self.fake.calls.append(self.op)
        if self.op in self.fake.fail:
            error = self.fake.fail[self.op]
            if not isinstance(error, list):
                raise error
            if error:
                raise error.pop(0)
        return self.fn()


class FakeGoogle:
    def __init__(self, calendars=None, busy=None):
        self.calendar_list = calendars or [
            {"id": "me@example.com", "summary": "Personal", "selected": True, "primary": True},
            {"id": "school@group.calendar.google.com", "summary": "School", "selected": True},
            {"id": "en.usa#holiday@group.v.calendar.google.com", "summary": "Holidays", "selected": False},
        ]
        self.busy = busy or {}  # calendar id -> [{"start": ..., "end": ...}]
        self.store: dict[str, dict] = {}  # events on the GymScheduleBot calendar, by id
        self.app_calendar: str | None = None
        self.calls: list[str] = []
        self.fail: dict = {}  # op name -> exception (or list of exceptions, used one by one)
        self.freebusy_ids: list[str] = []
        self.written_calendars: set[str] = set()

    # ---------------------------------------------------- calendar list
    def calendarList(self):  # noqa: N802 (Google's name)
        def items():
            extra = []
            if self.app_calendar:
                extra.append(
                    {
                        "id": self.app_calendar,
                        "summary": "GymScheduleBot",
                        "description": APP_DESCRIPTION,
                        "selected": True,
                    }
                )
            return {"items": self.calendar_list + extra}

        return SimpleNamespace(list=lambda **kw: Request(self, "calendarList.list", items))

    def calendars(self):
        def create(body):
            self.app_calendar = f"app{len(self.calls)}@group.calendar.google.com"
            return {"id": self.app_calendar, **body}

        return SimpleNamespace(insert=lambda body: Request(self, "calendars.insert", lambda: create(body)))

    def freebusy(self):
        def query(body):
            self.freebusy_ids = [i["id"] for i in body["items"]]
            out = {}
            for cid in self.freebusy_ids:
                if cid == "broken@example.com":
                    out[cid] = {"errors": [{"reason": "notFound"}], "busy": []}
                else:
                    out[cid] = {"busy": self.busy.get(cid, [])}
            return {"calendars": out}

        return SimpleNamespace(query=lambda body: Request(self, "freebusy.query", lambda: query(body)))

    # ------------------------------------------------------------ events
    def events(self):
        return SimpleNamespace(
            list=lambda **kw: Request(self, "events.list", lambda: self._list(**kw)),
            insert=lambda calendarId, body: Request(self, "events.insert", lambda: self._insert(calendarId, body)),
            update=lambda calendarId, eventId, body: Request(
                self, "events.update", lambda: self._update(calendarId, eventId, body)
            ),
            delete=lambda calendarId, eventId: Request(
                self, "events.delete", lambda: self._delete(calendarId, eventId)
            ),
        )

    def _list(self, calendarId, timeMin, timeMax, **kw):
        start, end = datetime.fromisoformat(timeMin), datetime.fromisoformat(timeMax)
        items = []
        for event in self.store.values():
            if event["status"] == "cancelled":
                items.append({"id": event["id"], "status": "cancelled"})
            elif start <= datetime.fromisoformat(event["start"]["dateTime"]) < end:
                items.append(event)
        return {"items": items}

    def _insert(self, calendar_id, body):
        self.written_calendars.add(calendar_id)
        if body["id"] in self.store:
            raise http_error(409, "duplicate")
        self.store[body["id"]] = {**body, "status": "confirmed"}
        return self.store[body["id"]]

    def _update(self, calendar_id, event_id, body):
        self.written_calendars.add(calendar_id)
        if event_id not in self.store:
            raise http_error(404)
        self.store[event_id] = {**body, "id": event_id, "status": body.get("status", "confirmed")}
        return self.store[event_id]

    def _delete(self, calendar_id, event_id):
        self.written_calendars.add(calendar_id)
        event = self.store.get(event_id)
        if event is None or event["status"] == "cancelled":
            raise http_error(410)
        self.store[event_id] = {"id": event_id, "status": "cancelled"}
        return {}

    # ------------------------------------------- what a user does in Google
    def user_moves(self, event_id, start: datetime, end: datetime):
        event = self.store[event_id]
        event["start"] = {"dateTime": start.isoformat()}
        event["end"] = {"dateTime": end.isoformat()}

    def user_deletes(self, event_id):
        self.store[event_id] = {"id": event_id, "status": "cancelled"}

    def live_events(self):
        return {k: v for k, v in self.store.items() if v["status"] != "cancelled"}
