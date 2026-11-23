from datetime import time, timedelta

from app.sources.custom import busy_blocks, clean_event, clean_events, weekly_event
from tests.conftest import MONDAY, at


def test_weekly_and_one_time_events_expand_into_the_window():
    events = clean_events(
        [
            {"id": "a", "title": "Lecture", "start": "09:00", "end": "10:15", "repeat": "weekly", "weekday": 0},
            {"id": "b", "title": "Dentist", "start": "16:30", "end": "17:00", "repeat": "once", "date": "2026-11-18"},
            {"id": "c", "title": "Old", "start": "08:00", "end": "09:00", "repeat": "once", "date": "2020-01-01"},
        ]
    )
    blocks = busy_blocks(events, MONDAY, "America/Los_Angeles")
    assert [(b.title, b.start, b.event_id) for b in blocks] == [
        ("Lecture", at(0, 9), "a"),
        ("Dentist", at(2, 16, 30), "b"),
    ]


def test_window_can_start_mid_week():
    events = clean_events([{"id": "a", "title": "Lecture", "start": "09:00", "end": "10:00", "weekday": 0}])
    blocks = busy_blocks(events, MONDAY + timedelta(days=3), "America/Los_Angeles")
    assert [b.start for b in blocks] == [at(7, 9)]  # next Monday


def test_clean_event_rejects_bad_input_and_trims_titles():
    assert clean_event({"start": "10:00", "end": "09:00", "weekday": 1}) is None
    assert clean_event({"start": "x", "end": "09:00", "weekday": 1}) is None
    assert clean_event({"start": "09:00", "end": "10:00", "weekday": 9}) is None
    assert clean_event({"start": "09:00", "end": "10:00", "repeat": "once", "date": "nope"}) is None
    e = clean_event({"title": "  a" * 100, "start": "09:00", "end": "10:00", "weekday": 1})
    assert len(e["title"]) <= 60 and e["repeat"] == "weekly"


def test_duplicate_ids_are_dropped_and_list_is_capped():
    one = {"id": "same", "title": "x", "start": "09:00", "end": "10:00", "weekday": 1}
    assert len(clean_events([one, one])) == 1
    many = [{**one, "id": f"e{i}"} for i in range(500)]
    assert len(clean_events(many)) == 150


def test_weekly_event_from_the_coach_gets_an_id_and_times():
    e = weekly_event("Soccer", 1, time(18), time(19, 30))
    assert e["repeat"] == "weekly" and e["start"] == "18:00" and e["id"].startswith("e_")
