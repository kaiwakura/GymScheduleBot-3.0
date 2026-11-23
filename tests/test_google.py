"""Google Calendar: connection, token security, availability and duplicate-free syncing.

Every test runs against tests/google_fake.py, so no real Google account is needed.
"""

import re
from datetime import UTC, datetime, timedelta
from html import unescape
from zoneinfo import ZoneInfo

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from google.auth.exceptions import RefreshError

import app.main as main
from app import google_auth, google_integration
from app.google_auth import GoogleConnection
from app.main import app
from app.scheduler.planner import rest_hours
from app.sources import google_calendar
from tests.google_fake import FakeGoogle, http_error

LA = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 11, 16, 8, 0, tzinfo=LA)  # Monday 8 AM


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    google_integration._cache.clear()


@pytest.fixture
def fake(env, monkeypatch):
    fake = FakeGoogle()
    monkeypatch.setattr(google_calendar, "build_service", lambda creds: fake)
    monkeypatch.setattr(google_auth, "credentials", lambda connection: (object(), False))
    monkeypatch.setattr(main, "local_now", lambda prefs: NOW.astimezone(ZoneInfo(prefs.timezone)))
    return fake


def connect(client, monkeypatch):
    """Go through the real login and callback routes with Google's side faked."""
    monkeypatch.setattr(
        google_auth, "start_login", lambda: ("https://accounts.google.com/o/oauth2/auth?x", "st8", "ver")
    )
    monkeypatch.setattr(
        google_auth, "finish_login", lambda code, state, verifier: GoogleConnection("access", "refresh", None)
    )
    r = client.get("/auth/google/login", follow_redirects=False)
    assert r.headers["location"].startswith("https://accounts.google.com/")
    return client.get("/auth/google/callback?code=abc&state=st8")


@pytest.fixture
def client(fake, monkeypatch):
    client = TestClient(app, base_url="https://testserver")
    page = connect(client, monkeypatch).text
    client.headers["X-CSRF-Token"] = re.search(r'name="csrf-token" content="([^"]+)"', page).group(1)
    set_prefs(client)
    return client


def refresh(client, events=()):
    """Re-plan with fresh Google data, like reloading the page."""
    google_integration._cache.clear()
    return client.post("/api/plan", json={"events": list(events)}).json()["html"]


def set_prefs(client, **values):
    prefs = {
        "commitment": "high", "session_minutes": 60, "preferred_time": "evening",
        "buffer_minutes": 15, "split": "recommended", "split_scope": "ongoing", **values,
    }  # fmt: skip
    return client.post("/api/preferences", json={"events": [], "preferences": prefs}).json()


def preview_weeks(client, **body):
    return client.post("/google/sync/preview", json={"events": [], "history": [], **body}).json()["weeks"]


def preview(client, **body):
    """The week on screen."""
    return next(w for w in preview_weeks(client, **body) if w["selected"])


def sync(client, restore=(), only_selected=False, **body):
    """Sync like the dialog's defaults: every week with changes, or only the week on screen."""
    weeks = [w for w in preview_weeks(client, **body) if w["selected"] or (w["changes"] and not only_selected)]
    picked = [{"week": w["week"], "fingerprint": w["fingerprint"], "restore": list(restore)} for w in weeks]
    return client.post("/google/sync/apply", json={"weeks": picked, "events": [], **body}).json()


# ---------------------------------------------------------- connection ---
def test_connecting_plans_around_google(client, fake):
    page = client.get("/").text
    assert 'class="google-chip" data-action="sync-preview"' in page and "Not synced yet" in page
    assert "Counts as busy" in page and "Disconnect Google Calendar" in page


def test_cancelled_or_forged_sign_in_does_not_connect(env, fake, monkeypatch):
    client = TestClient(app, base_url="https://testserver")
    monkeypatch.setattr(google_auth, "start_login", lambda: ("https://accounts.google.com/x", "st8", "ver"))
    client.get("/auth/google/login", follow_redirects=False)
    assert "access wasn't allowed" in unescape(client.get("/auth/google/callback?error=access_denied").text)
    client.get("/auth/google/login", follow_redirects=False)
    assert "cancelled or didn't finish" in unescape(client.get("/auth/google/callback?code=abc&state=forged").text)
    assert "Connect Google Calendar" in client.get("/").text


def test_missing_permission_is_explained(env, fake, monkeypatch):
    class Creds:
        token, refresh_token, expiry = "a", "r", None
        granted_scopes = google_auth.SCOPES[:1]

    class Flow:
        credentials, code_verifier = Creds(), None

        def fetch_token(self, code):
            pass

    monkeypatch.setattr(google_auth, "_flow", lambda state=None: Flow())
    with pytest.raises(google_auth.GoogleAuthError, match="needs all three"):
        google_auth.finish_login("code", "state", "verifier")


def test_tokens_are_encrypted_and_the_cookie_stays_small(client):
    session_cookie = client.cookies.get("session")
    assert "access" not in session_cookie and "refresh" not in session_cookie
    assert len(session_cookie) < 4000
    session = {}
    google_auth.save_connection(session, GoogleConnection("secret-access", "secret-refresh", None))
    assert "secret" not in session["google"]
    assert google_auth.load_connection(session).refresh_token == "secret-refresh"


def test_a_changed_key_disconnects_instead_of_failing(env, monkeypatch):
    session = {}
    google_auth.save_connection(session, GoogleConnection("a", "r", None))
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    assert google_auth.load_connection(session) is None and "google" not in session


def test_google_stays_off_without_its_secrets(env, monkeypatch):
    assert google_auth.is_configured()
    monkeypatch.delenv("TOKEN_ENCRYPTION_KEY")
    assert not google_auth.is_configured()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "not-a-key")
    assert not google_auth.is_configured()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("SESSION_SECRET")
    monkeypatch.delenv("OAUTHLIB_INSECURE_TRANSPORT", raising=False)
    assert not google_auth.is_configured()  # sessions would reset on every Render restart


def test_expired_access_token_is_refreshed_and_saved(env, monkeypatch):
    def refresh(self, request):
        self.token, self.expiry = "new-access", datetime(2099, 1, 1)

    monkeypatch.setattr(google_auth.Credentials, "refresh", refresh)
    connection = GoogleConnection("old", "refresh", datetime(2020, 1, 1, tzinfo=UTC).isoformat())
    creds, changed = google_auth.credentials(connection)
    assert changed and connection.token == "new-access" and connection.expiry.startswith("2099")
    _, changed_again = google_auth.credentials(connection)
    assert not changed_again


def test_revoked_refresh_token_means_reconnect(env, monkeypatch):
    def refresh(self, request):
        raise RefreshError("invalid_grant")

    monkeypatch.setattr(google_auth.Credentials, "refresh", refresh)
    with pytest.raises(google_auth.GoogleAuthError):
        google_auth.credentials(GoogleConnection("old", "refresh", datetime(2020, 1, 1, tzinfo=UTC).isoformat()))


def test_disconnect_needs_csrf_and_revokes(client, monkeypatch):
    revoked = []
    monkeypatch.setattr(google_auth, "revoke", lambda connection: revoked.append(connection.refresh_token))
    no_token = TestClient(app, base_url="https://testserver", cookies=client.cookies)
    assert no_token.post("/auth/google/logout").status_code == 403
    page = client.post("/auth/google/logout").text
    assert revoked == ["refresh"]
    assert "Disconnected from Google Calendar" in page and "Connect Google Calendar" in page


def test_requests_without_the_csrf_token_are_refused(client):
    stranger = TestClient(app, base_url="https://testserver", cookies=client.cookies)
    assert stranger.post("/google/sync/preview", json={}).status_code == 403
    assert stranger.post("/api/plan", json={}).status_code == 403
    assert stranger.post("/api/preferences", json={"preferences": {}}).status_code == 403
    assert stranger.post("/api/proposal", json={}).status_code == 403


# --------------------------------------------------------- availability ---
def test_busy_times_come_from_selected_calendars_only(client, fake):
    fake.busy["school@group.calendar.google.com"] = [
        {"start": "2026-11-16T17:00:00-08:00", "end": "2026-11-16T20:00:00-08:00"}
    ]
    refresh(client)
    assert set(fake.freebusy_ids) == {"me@example.com", "school@group.calendar.google.com"}  # holidays hidden
    assert fake.app_calendar not in fake.freebusy_ids  # never its own workouts
    workouts = [w for w in client.post("/api/plan", json={}).json()["history"] if w["start"].startswith("2026-11-16")]
    assert all(not ("17:00" <= w["start"][11:16] < "20:00") for w in workouts)


def test_calendar_checklist_changes_availability(client, fake):
    calendars = client.get("/google/calendars").json()["calendars"]
    assert [c["name"] for c in calendars] == ["Personal", "School"]
    assert all("@" not in c["key"] for c in calendars)  # ids (emails) never go into settings
    school = next(c["key"] for c in calendars if c["name"] == "School")
    set_prefs(client, google_excluded=[school])  # applied together with the other preferences
    assert fake.freebusy_ids == ["me@example.com"]
    assert (
        next(c for c in client.get("/google/calendars").json()["calendars"] if c["name"] == "School")["included"]
        is False
    )


def test_the_gymschedulebot_calendar_is_created_once(client, fake):
    sync(client)
    first = fake.app_calendar
    assert fake.calls.count("calendars.insert") == 1
    google_auth_conn = GoogleConnection("access", "refresh", None)  # reconnecting forgets the cached id
    client.cookies.clear()
    fresh = TestClient(app, base_url="https://testserver")
    google_integration._cache.clear()
    from unittest import mock

    with (
        mock.patch.object(google_auth, "finish_login", lambda *a: google_auth_conn),
        mock.patch.object(google_auth, "start_login", lambda: ("https://accounts.google.com/x", "s", "v")),
    ):
        fresh.get("/auth/google/login", follow_redirects=False)
        fresh.get("/auth/google/callback?code=c&state=s")
    assert fake.calls.count("calendars.insert") == 1 and fake.app_calendar == first


def test_google_outage_keeps_the_connection(client, fake):
    fake.fail["freebusy.query"] = http_error(503)
    page = client.get("/").text
    assert "Couldn&#39;t reach Google Calendar" in page
    assert 'data-action="sync-preview"' in page  # still connected


def test_revoked_access_disconnects_with_a_clear_message(client, fake):
    fake.fail["calendarList.list"] = http_error(401)
    assert "expired or was removed" in client.get("/").text
    del fake.fail["calendarList.list"]
    assert "Connect Google Calendar" in client.get("/").text


# ------------------------------------------------------------------ sync ---
def test_first_sync_previews_then_creates_readable_events(client, fake):
    p = preview(client)
    assert [c["kind"] for c in p["changes"]] == ["add"] * 4
    assert p["changes"][0]["label"].startswith("Add Upper — ")
    assert fake.live_events() == {}  # previewing writes nothing
    result = sync(client)
    assert result["status"] == "done" and "Synced 4 changes" in result["reply"]
    event = fake.store[sorted(fake.store)[0]]
    assert re.fullmatch(r"gsb\d{8}", event["id"])
    assert event["summary"] == "Workout: Upper"
    assert "Incline Dumbbell Press — 3 × 8–12" in event["description"]
    assert event["description"].endswith("Created by GymScheduleBot")
    assert fake.written_calendars == {fake.app_calendar}  # nothing written anywhere else


def test_syncing_again_creates_no_duplicates_and_no_reshuffling(client, fake):
    sync(client)
    before = dict(fake.live_events())
    again = preview(client)
    assert again["changes"] == [] and again["unchanged"] == 4  # its own events aren't busy time
    assert sync(client)["reply"] == "Your GymScheduleBot calendar was already up to date."
    assert fake.live_events() == before


def test_new_busy_time_moves_the_workout_instead_of_duplicating(client, fake):
    sync(client)
    first = fake.store[sorted(fake.live_events())[0]]
    start = datetime.fromisoformat(first["start"]["dateTime"])
    fake.busy["me@example.com"] = [{"start": start.isoformat(), "end": (start + timedelta(hours=1)).isoformat()}]
    p = preview(client)
    assert any(c["kind"] == "move" for c in p["changes"])
    sync(client)
    assert len(fake.live_events()) == 4


def test_new_exercises_update_the_event(client, fake):
    sync(client)
    set_prefs(client, session_minutes=90)
    p = preview(client)
    assert {c["kind"] for c in p["changes"]} == {"update"}
    sync(client)
    assert len(fake.live_events()) == 4


def test_fewer_workouts_remove_events_only_after_confirming(client, fake):
    sync(client)
    set_prefs(client, commitment="light")
    p = preview(client)
    assert any(c["kind"] == "remove" for c in p["changes"])
    assert len(fake.live_events()) == 4  # nothing removed yet
    sync(client)
    assert len(fake.live_events()) == 2


def test_deleted_in_google_is_not_recreated_unless_chosen(client, fake):
    sync(client)
    gone = sorted(fake.live_events())[1]
    fake.user_deletes(gone)
    p = preview(client)
    restore = [c for c in p["changes"] if c["kind"] == "restore"]
    assert len(restore) == 1 and "you deleted it in Google Calendar" in restore[0]["label"]
    sync(client)  # restore left unticked
    assert gone not in fake.live_events()
    sync(client, restore=[restore[0]["key"]])
    assert gone in fake.live_events()  # the same event id came back, no duplicate


def test_a_workout_moved_in_google_is_kept_and_planned_around(client, fake):
    sync(client)
    events = sorted(fake.live_events())
    upper = fake.store[events[0]]
    start = datetime.fromisoformat(upper["start"]["dateTime"]) + timedelta(hours=1)
    fake.user_moves(events[0], start, start + timedelta(hours=1))
    p = preview(client)
    assert any("you moved it" in k for k in p["kept"])
    sync(client)
    assert fake.store[events[0]]["start"]["dateTime"] == start.isoformat()  # never overwritten
    html = refresh(client)
    assert "where you put it" in html


def test_a_conflict_the_planner_can_fix_is_fixed_quietly(client, fake):
    sync(client)
    upper_ids = [i for i, e in sorted(fake.live_events().items()) if e["summary"] == "Workout: Upper"]
    first = datetime.fromisoformat(fake.store[upper_ids[0]]["start"]["dateTime"])
    fake.user_moves(upper_ids[1], first + timedelta(hours=24), first + timedelta(hours=25))
    html = refresh(client)
    assert "Heads up" not in html  # the other Upper isn't fixed, so the planner moves it instead
    assert any(c["kind"] == "move" and "Upper" in c["label"] for c in preview(client)["changes"])


def test_a_moved_workout_that_breaks_recovery_gets_a_warning(client, fake):
    sync(client)
    upper_ids = [i for i, e in sorted(fake.live_events().items()) if e["summary"] == "Workout: Upper"]
    first = datetime.fromisoformat(fake.store[upper_ids[0]]["start"]["dateTime"])
    fake.user_moves(upper_ids[0], first + timedelta(hours=1), first + timedelta(hours=2))  # both moved by the user
    moved = first + timedelta(hours=24)
    fake.user_moves(upper_ids[1], moved, moved + timedelta(hours=1))
    html = refresh(client)  # edits show up after a refresh
    warning = re.search(r'<p class="plan-warning" role="alert">([^<]+)</p>', html)
    assert warning and "Heads up: Upper on" in warning.group(1) and "under the 48-hour minimum" in html
    changes = preview(client)["changes"]
    assert not any("Upper" in c["label"] for c in changes)  # syncing won't move either one back
    assert fake.store[upper_ids[1]]["start"]["dateTime"] == moved.isoformat()


def test_partial_sync_failure_reports_and_retries(client, fake):
    fake.fail["events.insert"] = [http_error(500)]
    result = sync(client)
    assert result["status"] == "partial" and "didn't go through" in result["reply"]
    assert len(fake.live_events()) == 3
    sync(client)
    assert len(fake.live_events()) == 4


def test_confirming_a_stale_preview_shows_the_new_changes(client, fake):
    p = preview(client)
    set_prefs(client, commitment="light")
    picked = [{"week": p["week"], "fingerprint": p["fingerprint"]}]
    data = client.post("/google/sync/apply", json={"weeks": picked, "events": []}).json()
    assert data["status"] == "changed" and fake.live_events() == {}


def test_past_workouts_are_never_touched(client, fake, monkeypatch):
    sync(client)
    later = NOW + timedelta(days=3)
    monkeypatch.setattr(main, "local_now", lambda prefs: later)
    google_integration._cache.clear()
    before = {k: v for k, v in fake.live_events().items() if datetime.fromisoformat(v["start"]["dateTime"]) < later}
    sync(client)
    for event_id, event in before.items():
        assert fake.store[event_id] == event


# -------------------------------------------------------------- time zones ---
def test_rest_is_counted_in_real_hours_across_daylight_saving():
    # US clocks fall back on Sunday Nov 1, 2026: Saturday 6 PM to Monday 6 PM is 49 hours.
    saturday = datetime(2026, 10, 31, 18, 0, tzinfo=LA)
    monday = datetime(2026, 11, 2, 18, 0, tzinfo=LA)
    assert rest_hours(saturday, monday) == 49


def test_busy_times_keep_their_offsets_across_daylight_saving():
    fake = FakeGoogle(
        busy={"me@example.com": [{"start": "2026-11-01T01:30:00-07:00", "end": "2026-11-01T01:30:00-08:00"}]}
    )
    blocks, unreadable = google_calendar.fetch_busy(
        fake,
        ["me@example.com"],
        datetime(2026, 11, 1, tzinfo=LA),
        datetime(2026, 11, 2, tzinfo=LA),
        "America/Los_Angeles",
    )
    assert unreadable == [] and blocks[0].end - blocks[0].start == timedelta(hours=1)  # the repeated 1 AM hour


# ------------------------------------------------------------- many weeks ---
NEXT_WEEK = "2026-11-23"


def test_any_future_week_can_be_synced_on_its_own(client, fake):
    p = preview(client, week=NEXT_WEEK)
    assert p["label"] == "Nov 23–29" and [c["kind"] for c in p["changes"]] == ["add"] * 4
    # This week has workouts waiting too, so it's offered alongside; only the chosen week is written.
    assert [w["week"] for w in preview_weeks(client, week=NEXT_WEEK)] == ["2026-11-16", NEXT_WEEK]
    sync(client, only_selected=True, week=NEXT_WEEK)
    assert len(fake.live_events()) == 4
    assert all(e["start"]["dateTime"] >= "2026-11-23" for e in fake.live_events().values())


def test_preview_lists_the_week_on_screen_and_other_weeks_with_changes(client, fake):
    sync(client)  # this week
    sync(client, week=NEXT_WEEK)  # next week
    assert len(fake.live_events()) == 8
    set_prefs(client, session_minutes=75)  # changes every planned week...
    assert len(fake.live_events()) == 8 and all(
        e["end"]["dateTime"][11:16]
        == (datetime.fromisoformat(e["start"]["dateTime"]) + timedelta(hours=1)).strftime("%H:%M")
        for e in fake.live_events().values()
    )  # ...but applying preferences never writes to Google
    weeks = preview_weeks(client)
    assert [(w["week"], w["selected"]) for w in weeks] == [("2026-11-16", True), (NEXT_WEEK, False)]
    assert all({c["kind"] for c in w["changes"]} == {"update"} for w in weeks)
    only_this = [{"week": weeks[0]["week"], "fingerprint": weeks[0]["fingerprint"]}]
    client.post("/google/sync/apply", json={"weeks": only_this, "events": []})
    assert [w["week"] for w in preview_weeks(client)] == ["2026-11-16", NEXT_WEEK]  # the unticked week still waits
    assert preview(client)["changes"] == []


def test_weeks_with_moved_workouts_are_offered_for_sync(client, fake):
    week = client.post("/api/plan", json={"events": [], "week": NEXT_WEEK}).json()
    origin = re.search(r'data-start="([^"]+)"', week["html"]).group(1)
    name = re.search(r'data-name="([^"]+)"', week["html"]).group(1)
    moved = client.post(
        "/api/plan",
        json={"events": [], "week": NEXT_WEEK, "move": {"name": name, "from": origin, "start": f"{origin[:10]}T07:00"}},
    ).json()["moves"]
    weeks = preview_weeks(client, moves=moved)  # looking at this week
    assert [w["week"] for w in weeks] == ["2026-11-16", NEXT_WEEK]


def test_a_workout_moved_in_the_app_is_synced_where_you_put_it(client, fake):
    sync(client)
    html = client.post("/api/plan", json={"events": []}).json()["html"]
    origin = re.search(r'data-start="(2026-11-1[89][^"]+)"', html).group(1)  # a workout still ahead
    name = re.search(rf'data-name="([^"]+)" data-start="{re.escape(origin)}"', html).group(1)
    target = f"{origin[:10]}T07:00"
    move = {"name": name, "from": origin, "start": target}
    moved = client.post("/api/plan", json={"events": [], "move": move}).json()
    changes = preview(client, moves=moved["moves"])["changes"]
    assert any(c["kind"] == "move" and name in c["label"] and "7:00 AM" in c["label"] for c in changes)
    sync(client, moves=moved["moves"])
    starts = [e["start"]["dateTime"] for e in fake.live_events().values()]
    assert any(s.startswith(target) for s in starts)


def test_weeks_far_ahead_are_never_generated_on_their_own(client, fake):
    sync(client)
    far = "2027-03-01"
    assert [w["week"] for w in preview_weeks(client)] == ["2026-11-16"]
    assert [w["week"] for w in preview_weeks(client, week=far)][-1] == far  # only because it's on screen


# ----------------------------------------------------- calendars together ---
def test_google_and_your_own_commitments_count_together(client, fake):
    fake.busy["me@example.com"] = [{"start": "2026-11-17T17:00:00-08:00", "end": "2026-11-17T20:00:00-08:00"}]
    own = [{"id": "e_1", "title": "Club", "start": "17:00", "end": "20:00", "repeat": "weekly", "weekday": 3}]
    starts = re.findall(r'class="block workout[^"]*"[^>]*data-start="([^"]+)"', refresh(client, own))
    for day in ("2026-11-17", "2026-11-19"):  # Google busy on Tuesday, your own event on Thursday
        assert not any(s.startswith(day) and "17:00" <= s[11:16] < "20:15" for s in starts)
    duplicate = [{"id": "e_2", "title": "Lecture", "start": "17:00", "end": "20:00", "repeat": "weekly", "weekday": 1}]
    html = refresh(client, duplicate)
    assert (
        html.count("Lecture") >= 1
        and "(from Google Calendar)" not in html.split('data-date="2026-11-17"')[1].split("</section>")[0]
    )


def test_sample_weeks_never_use_or_change_google(client, fake):
    fake.busy["me@example.com"] = [{"start": "2026-11-17T06:00:00-08:00", "end": "2026-11-17T22:00:00-08:00"}]
    data = client.post("/api/plan", json={"load_sample": "busy"}).json()
    assert "exploring a sample week" in data["html"] and "(from Google Calendar)" not in data["html"]
    r = client.post("/google/sync/preview", json={"events": data["events"]})
    assert r.status_code == 409 and "sample week" in r.json()["error"]
    assert fake.live_events() == {}
