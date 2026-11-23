"""Integration tests: the page, Preferences, weeks, moved workouts, the AI Coach and exports."""

import base64
import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from icalendar import Calendar

import app.main as main
from app.main import app

LA = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 11, 16, 8, 0, tzinfo=LA)  # Monday 8 AM: the current week is Nov 16-22
EVENT = {"id": "e_test1", "title": "Calc lecture", "start": "09:00", "end": "10:30", "repeat": "weekly"}
DEFAULTS = {
    "commitment": "high", "session_minutes": 60, "preferred_time": "evening",
    "buffer_minutes": 15, "split": "recommended", "split_scope": "ongoing",
}  # fmt: skip


@pytest.fixture
def client(monkeypatch):
    # Freeze "now" so plans don't depend on when the tests run.
    monkeypatch.setattr(main, "local_now", lambda prefs: NOW.astimezone(ZoneInfo(prefs.timezone)))
    # Rule-based coach (no API key) and no Google, so tests never touch the network.
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    # https so the Secure session cookie is sent back, like on the real site.
    client = TestClient(app, base_url="https://testserver")
    return with_csrf(client)


def with_csrf(client):
    """Load the page once and send its CSRF token with every later request, like app.js does."""
    page = client.get("/").text
    client.headers["X-CSRF-Token"] = re.search(r'name="csrf-token" content="([^"]+)"', page).group(1)
    return client


def plan(client, **body):
    return client.post("/api/plan", json={"timezone": "America/Los_Angeles", "events": [], **body}).json()


def chat(client, message=None, **body):
    payload = {"timezone": "America/Los_Angeles", "events": [], **body}
    if message is not None:
        payload["message"] = message
    return client.post("/api/chat", json=payload)


def set_prefs(client, **values):
    return client.post("/api/preferences", json={"events": [], "preferences": {**DEFAULTS, **values}})


def decide(client, data, accept=True, **body):
    token = data["proposal"]["token"]
    return client.post("/api/proposal", json={"events": [], "token": token, "accept": accept, **body}).json()


def every_day(event):
    return [{**event, "id": f"{event['id']}_{d}", "weekday": d} for d in range(7)]


def workout_ranges(html):
    return re.findall(r'class="w-range">([^<]+)<', html)


def workout_starts(html):
    return re.findall(r'class="block workout[^"]*"[^>]*data-start="([^"]+)"', html)


def workout_names(html):
    return re.findall(r'class="block workout[^"]*"[^>]*data-name="([^"]+)"', html)


# ------------------------------------------------------------------- page ---
def test_home_page_is_the_a_plus_studio_layout(client):
    html = TestClient(app, base_url="https://testserver").get("/").text  # a brand-new visitor
    assert "Nov 16–22, 2026" in html  # the current week's date range
    assert 'aria-label="Previous week"' in html and 'aria-label="Next week"' in html and ">Today<" in html
    assert "Add commitment" in html and "Edit availability" in html and "Preferences" in html
    assert "Calendars &amp; commitments" in html and "Travel buffer" in html
    assert "Very Light" in html and "Dedicated" in html and "Beginner" not in html  # no experience setting
    assert "Calc II lecture" in html  # a first visit shows the sample week
    assert "planned" in html  # coach intro


def test_first_visit_sample_is_a_demo_that_can_be_cleared(client):
    data = plan(client, load_sample="busy")
    assert data["events"] and all(e["id"].startswith("sample-busy-") for e in data["events"])
    assert "You're exploring a sample week" in data["html"]
    own = plan(client, events=[{**EVENT, "weekday": 2}])
    assert "exploring a sample week" not in own["html"]


# ----------------------------------------------------------- preferences ---
def test_preferences_apply_together_and_are_remembered(client):
    data = set_prefs(client, commitment="dedicated", session_minutes=75, buffer_minutes=30).json()
    assert data["saved"] is True
    assert data["settings"]["commitment"] == "dedicated" and data["settings"]["buffer_minutes"] == 30
    assert "PPL + Upper/Lower" in data["html"]  # dedicated: up to 5 workouts
    assert all("–" in r for r in workout_ranges(data["html"]))
    assert "PPL + Upper/Lower" in plan(client)["html"]  # remembered on the next request


def test_invalid_preferences_change_nothing(client):
    set_prefs(client, commitment="light")
    r = set_prefs(client, commitment="light", buffer_minutes=20)
    assert r.status_code == 400 and "travel buffer" in r.json()["error"]
    r = set_prefs(client, commitment="huge")
    assert r.status_code == 400
    assert plan(client)["settings"]["buffer_minutes"] == 15  # the earlier, valid settings stay


def test_five_commitment_levels_cap_the_week_without_showing_numbers(client):
    html = client.get("/").text
    assert re.search(r"Very Light</span>", html) and "up to" not in html.lower().split("prefs-hint")[0][-200:]
    for level, count in [("very_light", 1), ("light", 2), ("moderate", 3), ("high", 4), ("dedicated", 5)]:
        data = set_prefs(client, commitment=level).json()
        assert len(workout_starts(data["html"])) == count, level


def test_travel_buffer_keeps_workouts_clear_of_commitments(client):
    events = every_day({**EVENT, "start": "06:00", "end": "17:30"})
    starts = workout_starts(set_prefs(client, buffer_minutes=0).json()["html"])
    assert starts and all(s[11:16] >= "17:30" for s in starts)
    data = client.post(
        "/api/preferences", json={"events": events, "preferences": {**DEFAULTS, "buffer_minutes": 60}}
    ).json()
    starts = workout_starts(data["html"])
    assert starts and all(s[11:16] >= "18:30" for s in starts)  # an hour to get to the gym
    assert next(e for e in data["events"] if e["id"] == "e_test1_0")["start"] == "06:00"  # events never change


def test_reset_preferences(client):
    set_prefs(client, commitment="dedicated")
    data = client.post("/api/preferences", json={"events": [], "reset": True}).json()
    assert data["settings"]["commitment"] == "moderate"


# ------------------------------------------------------------------ weeks ---
def test_weeks_navigate_freely_forward_and_back(client):
    assert plan(client, week="2026-11-23")["week_label"] == "Nov 23–29, 2026"
    assert plan(client, week="2026-11-30")["week_label"] == "Nov 30 – Dec 6, 2026"
    assert plan(client, week="2027-03-03")["week_label"] == "Mar 1–7, 2027"  # any day snaps to its Monday
    future = plan(client, week="2027-11-15")
    assert future["week_label"] == "Nov 15–21, 2027" and workout_starts(future["html"])
    today = plan(client, week="")
    assert today["week_label"] == "Nov 16–22, 2026"


def test_past_weeks_are_read_only(client):
    history = [{"name": "Upper", "start": "2026-11-10T18:00:00-08:00", "muscles": ["chest"], "status": "planned"}]
    data = plan(client, week="2026-11-09", history=history)
    assert "Past week" in data["html"]
    assert workout_starts(data["html"]) == ["2026-11-10T18:00:00-08:00"]
    assert " draggable" not in data["html"] and "Other available times" not in data["html"]


def test_each_week_plans_around_that_weeks_events(client):
    dentist = {
        "id": "e_d",
        "title": "Dentist",
        "start": "06:00",
        "end": "21:00",
        "repeat": "once",
        "date": "2026-11-25",
    }
    this_week = plan(client, events=[dentist])
    next_week = plan(client, events=[dentist], week="2026-11-23")
    assert "Dentist" in next_week["html"] and "Dentist" not in this_week["html"]
    assert not any(s.startswith("2026-11-25") for s in workout_starts(next_week["html"]))


# ---------------------------------------------------------- moving workouts ---
def test_moving_a_workout_keeps_it_there_across_weeks(client):
    week = plan(client, week="2026-11-23")
    origin, name = workout_starts(week["html"])[0], workout_names(week["html"])[0]
    target = "2026-11-24T07:00"  # Tuesday 7 AM, in local time
    moved = plan(client, week="2026-11-23", move={"name": name, "from": origin, "start": target})
    assert moved["undo"].startswith(f"Moved {name} to Tue 7:00 AM")
    assert any(s.startswith("2026-11-24T07:00") for s in workout_starts(moved["html"]))
    assert moved["moves"] and moved["moves"][0]["week"] == "2026-11-23"
    plan(client, moves=moved["moves"])  # look at another week...
    again = plan(client, week="2026-11-23", moves=moved["moves"])  # ...and come back
    assert any(s.startswith("2026-11-24T07:00") for s in workout_starts(again["html"]))
    assert "where you put it" in again["html"]


def test_moves_into_the_past_are_refused(client):
    origin = workout_starts(plan(client)["html"])[0]
    r = client.post(
        "/api/plan", json={"events": [], "move": {"name": "Upper", "from": origin, "start": "2026-11-16T07:00"}}
    )
    assert r.status_code == 400


def test_a_moved_workout_that_now_clashes_is_explained_not_moved(client):
    week = plan(client, week="2026-11-23")
    upper = workout_starts(week["html"])[0]
    moved = plan(client, week="2026-11-23", move={"name": "Upper", "from": upper, "start": upper[:16]})["moves"]
    assert moved == []  # moving a workout to where it already is isn't a move
    target = f"{upper[:10]}T07:00"
    moves = plan(client, week="2026-11-23", move={"name": "Upper", "from": upper, "start": target})["moves"]
    clash = {"id": "e_c", "title": "Early lab", "start": "06:30", "end": "07:30", "repeat": "once", "date": upper[:10]}
    data = plan(client, week="2026-11-23", moves=moves, events=[clash])
    assert any(s.startswith(target) for s in workout_starts(data["html"]))  # still where the user put it
    assert "overlaps Early lab" in data["html"]


def test_other_available_times_are_offered_for_upcoming_workouts(client):
    html = plan(client, week="2026-11-23")["html"]
    assert "Other available times" in html
    notes = re.findall(r"<b>[A-Z][a-z]{2} [^<]+</b><span>([^<]+)</span>", html)
    assert notes and all(re.match(r"(\d+ h (after|before) \w{3} \w+|plenty of recovery)", n) for n in notes)


# --------------------------------------------------------------- AI Coach ---
def test_coach_changes_that_move_workouts_are_proposed_first(client):
    before = plan(client)["html"]
    data = chat(client, "I prefer mornings").json()
    assert data["proposal"] and data["settings"]["preferred_time"] == "evening"  # not applied yet
    assert "Proposed" in data["html"] and "Accept" in data["html"]
    assert workout_starts(data["html"]) == workout_starts(before)  # the real plan is unchanged
    accepted = decide(client, data)
    assert accepted["settings"]["preferred_time"] == "morning"
    assert all(s[11:13] < "12" for s in workout_starts(accepted["html"]))


def test_rejecting_a_proposal_changes_nothing(client):
    data = chat(client, "I prefer mornings").json()
    rejected = decide(client, data, accept=False)
    assert rejected["settings"]["preferred_time"] == "evening"
    assert "left your plan as it was" in rejected["reply"]
    stale = decide(client, data)  # the same suggestion can't be accepted later
    assert "no longer current" in stale["reply"] and stale["settings"]["preferred_time"] == "evening"


def test_coach_move_request_is_checked_and_proposed(client):
    week = plan(client)["html"]
    tuesday = [s for s in workout_starts(week) if s.startswith("2026-11-17")]
    data = chat(client, "move tuesday's workout to wednesday").json()
    if tuesday:
        assert data["proposal"] and "→ Wed" in data["proposal"]["lines"][0]
        moved = decide(client, data)
        assert moved["moves"][0]["from"] == tuesday[0]
    else:
        assert "I don't see an upcoming workout" in data["reply"]


def test_coach_explains_a_move_that_would_break_recovery(client):
    starts = workout_starts(plan(client)["html"])
    first, second = datetime.fromisoformat(starts[0]), datetime.fromisoformat(starts[2])  # Upper and Upper
    day = second.strftime("%A").lower()
    target = (first.replace(day=first.day + 1)).strftime("%A").lower()
    data = chat(client, f"move {day}'s workout to {target}").json()
    assert data["proposal"] is None and "48-hour minimum" in data["reply"]


def test_changes_that_dont_move_workouts_apply_right_away(client):
    data = chat(client, "fewer sets please").json()
    assert data["proposal"] is None and data["settings"]["volume"] == "lower"
    assert "× " in data["html"] and "3 × " not in data["html"]


def test_coach_swaps_exercises_and_never_guesses_experience(client):
    data = chat(client, "I can't do back squats, swap them").json()
    assert data["settings"]["avoid_exercises"] == ["Back Squat"]
    assert "Back Squat" not in data["html"]
    data = chat(client, "I've been lifting for three years").json()
    assert "experience" not in data["settings"]
    assert data["settings"]["volume"] == "standard" and data["settings"]["simpler_exercises"] is False


def test_weekly_commitment_from_chat_is_added_right_away(client):
    data = chat(client, "I have soccer on tuesdays 6-7:30pm").json()
    assert [(e["title"], e["weekday"], e["start"]) for e in data["events"]] == [("Soccer", 1, "18:00")]
    assert "Soccer" in data["html"]


def test_chat_question_without_changes_explains_the_plan(client):
    set_prefs(client)
    data = chat(client, "Why this split?").json()
    assert "allows up to 4 workouts" in data["reply"] and data["proposal"] is None


def test_chat_with_unknown_request_suggests_examples(client):
    assert "move Thursday's workout to Friday morning" in chat(client, "hello there").json()["reply"]


def test_chat_reset_and_empty_message(client):
    chat(client, "fewer sets")
    assert chat(client, "reset").json()["settings"]["volume"] == "standard"
    assert chat(client, "   ").status_code == 400


def test_replan_button_explains_the_plan(client):
    assert chat(client, action="replan").json()["reply"]


# ------------------------------------------------------------ split requests ---
def test_split_request_for_this_week_only(client):
    data = chat(client, "Can I do Upper/Lower this week?").json()
    accepted = decide(client, data) if data["proposal"] else data
    assert accepted["settings"]["split_style"] == "upper_lower"
    assert accepted["settings"]["split_until"] == "2026-11-22"  # the end of this calendar week
    assert "Your split: <b>Upper/Lower</b> (this week)" in accepted["html"]
    next_week = plan(client, week="2026-11-23")
    assert "Your split" not in next_week["html"]  # next week is back to the recommended split


def test_split_from_preferences_for_this_week_or_until_changed(client):
    data = set_prefs(client, split="full_body", split_scope="this_week").json()
    assert data["settings"]["split_until"] == "2026-11-22"
    data = set_prefs(client, split="full_body", split_scope="ongoing").json()
    assert data["settings"]["split_style"] == "full_body" and data["settings"]["split_until"] is None
    data = set_prefs(client, split="recommended").json()
    assert data["settings"]["split_style"] is None


def test_six_day_ppl_only_when_asked(client):
    set_prefs(client, commitment="dedicated")
    data = chat(client, "I want push pull legs six days a week from now on").json()
    accepted = decide(client, data)
    assert "Push/Pull/Legs x2" in accepted["html"]


def test_back_to_the_recommended_split(client):
    set_prefs(client, split="upper_lower")
    button = chat(client, action="reset_split").json()  # the "Use recommended" button
    assert button["settings"]["split_style"] is None


# ------------------------------------------------------------- screenshots ---
PNG = "data:image/png;base64," + base64.b64encode(b"\x89PNG fake image bytes").decode()


def test_screenshot_without_gemini_explains_how_else_to_add_events(client):
    data = chat(client, image={"data": PNG, "type": "image/png"}).json()
    assert "needs Gemini" in data["reply"] and data["events"] == []


def test_screenshot_events_are_added_as_weekly_commitments(client, monkeypatch):
    from datetime import time

    def fake_reader(image, mime_type, note="", client=None):
        assert image == b"\x89PNG fake image bytes" and mime_type == "image/png"
        return [
            {"title": "Chem lab", "weekday": 2, "start": time(13, 0), "end": time(16, 0)},
            {"title": "Work shift", "weekday": 5, "start": time(9, 0), "end": time(17, 0)},
        ], "Found your class schedule."

    monkeypatch.setattr(main, "read_schedule_image", fake_reader)
    data = chat(client, image={"data": PNG, "type": "image/png"}).json()
    assert "I added 2 weekly commitments: Chem lab (Wed 13:00), Work shift (Sat 09:00)" in data["reply"]
    assert {e["title"] for e in data["events"]} == {"Chem lab", "Work shift"}


def test_screenshot_rejects_bad_images(client, monkeypatch):
    r = chat(client, image={"data": "data:text/plain;base64,aGk=", "type": "text/plain"})
    assert r.status_code == 400 and "PNG, JPG or WebP" in r.json()["error"]
    monkeypatch.setattr(main, "MAX_IMAGE_BYTES", 5)
    r = chat(client, image={"data": PNG, "type": "image/png"})
    assert r.status_code == 400 and "too large" in r.json()["error"]


# ------------------------------------------------------- your commitments ---
def test_your_events_block_time_and_are_editable(client):
    events = every_day({**EVENT, "start": "06:00", "end": "21:00"})  # busy all day, every day
    data = plan(client, events=events)
    assert "Rest week" in data["html"] and 'data-event-id="e_test1_0"' in data["html"]
    assert "rest week" in data["intro"]


def test_time_marked_unavailable_blocks_workouts(client):
    blocked = [{**EVENT, "id": f"u{d}", "title": "Can't train", "kind": "unavailable", "start": "06:00", "end": "22:00",
                "weekday": d} for d in range(7)]  # fmt: skip
    data = plan(client, events=blocked)
    assert data["events"][0]["kind"] == "unavailable" and "unavailable" in data["html"]
    assert workout_starts(data["html"]) == []


def test_bad_events_and_moves_are_dropped(client):
    junk = [{"title": "x", "start": "10:00", "end": "09:00", "weekday": 1}, {"nope": 1}, "string"]
    bad_moves = [{"week": "2026-11-16", "name": "Nope"}, {"week": "2020-01-06", "name": "Upper", "from": "x"}]
    data = plan(client, events=junk + [{**EVENT, "weekday": 2}], moves=bad_moves)
    assert [e["id"] for e in data["events"]] == ["e_test1"] and data["moves"] == []


def test_export_ics_uses_the_chosen_week(client):
    set_prefs(client, commitment="dedicated")
    r = client.post("/export.ics", data={"events": "[]", "moves": "[]", "week": "2026-11-23"})
    events = list(Calendar.from_ical(r.content).walk("VEVENT"))
    assert r.headers["content-type"].startswith("text/calendar") and len(events) == 5
    assert all(str(e.decoded("dtstart").date()) >= "2026-11-23" for e in events)
    busy = json.dumps(every_day({**EVENT, "start": "06:00", "end": "21:00"}))
    r = client.post("/export.ics", data={"events": busy})
    assert len(list(Calendar.from_ical(r.content).walk("VEVENT"))) == 0


# ------------------------------------------------------- recovery history ---
SUNDAY_LEGS = {"name": "Legs", "start": "2026-11-15T18:00:00-08:00", "muscles": ["quads", "hamstrings"]}


def test_plan_returns_history_with_upcoming_workouts(client):
    data = plan(client, history=[SUNDAY_LEGS])
    assert data["history"][0]["name"] == "Legs" and data["history"][0]["status"] == "planned"
    assert len(data["history"]) == 1 + len(workout_starts(data["html"]))


def test_workouts_that_already_happened_this_week_stay_and_count(client, monkeypatch):
    thursday = datetime(2026, 11, 19, 8, 0, tzinfo=LA)
    first = plan(client)  # planned on Monday
    monkeypatch.setattr(main, "local_now", lambda prefs: thursday)
    data = plan(client, history=first["history"])
    past = [s for s in workout_starts(data["html"]) if s < "2026-11-19"]
    assert past and "is-past" in data["html"]
    assert len(workout_starts(data["html"])) <= 4  # the week still respects the commitment


def test_skipping_a_workout_through_the_coach_and_the_details(client):
    data = chat(client, "I skipped my workout on Sunday", history=[SUNDAY_LEGS]).json()
    assert "marked Sunday's Legs as skipped" in data["reply"] and data["history"][0]["status"] == "skipped"
    button = chat(client, action="skip", date="2026-11-15", history=[SUNDAY_LEGS]).json()
    assert "skipped" in button["reply"]
    nothing = chat(client, "I missed my workout on Friday", history=[SUNDAY_LEGS]).json()
    assert "nothing to mark as skipped" in nothing["reply"]
