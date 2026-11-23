from datetime import time
from itertools import pairwise

from icalendar import Calendar

from app.ics_export import plan_to_ics
from app.models import Commitment, TimeOfDay
from app.scheduler.planner import build_plan
from app.sources.custom import busy_blocks, clean_events
from app.sources.sample_weeks import SAMPLE_WEEKS, sample_events
from tests.conftest import MONDAY


def test_plan_round_trips_through_ics(prefs):
    plan = build_plan([], MONDAY, prefs)
    cal = Calendar.from_ical(plan_to_ics(plan))
    events = list(cal.walk("VEVENT"))
    assert len(events) == len(plan.workouts)
    assert str(events[0]["SUMMARY"]).startswith("Workout:")


def test_busy_sample_fits_workouts_into_gaps_between_classes(prefs):
    """The busy sample has no empty days, but the gaps between classes and shifts are enough
    to train every muscle twice."""
    prefs.preferred_time = TimeOfDay.EVENING  # the app's default
    plan = build_plan(busy_blocks(sample_events("busy"), MONDAY, prefs.timezone), MONDAY, prefs)
    assert plan.split_name == "Upper/Lower x2"
    busy = busy_blocks(sample_events("busy"), MONDAY, prefs.timezone)
    for w in plan.workouts:
        assert not any(b.start < w.end and w.start < b.end for b in busy)
    # Thursday's session sits between CS lab (ends 5:20 PM) and the club meeting (7:00 PM).
    thursday = next(w for w in plan.workouts if w.day.weekday() == 3)
    assert time(17, 20) < thursday.start.time() and thursday.end.time() <= time(19, 0)

    prefs.commitment = Commitment.LIGHT
    plan = build_plan(busy_blocks(sample_events("busy"), MONDAY, prefs.timezone), MONDAY, prefs)
    assert plan.split_name == "Full Body A/B"


def test_samples_are_valid_editable_events():
    for name in SAMPLE_WEEKS:
        events = sample_events(name)
        assert clean_events(events) == events  # same shape as events the user adds
        assert all(e["id"].startswith(f"sample-{name}-") for e in events)
        by_day = {}
        for e in events:
            by_day.setdefault(e["weekday"], []).append((e["start"], e["end"]))
        for rows in by_day.values():  # sorted, no overlaps
            rows.sort()
            assert all(a[1] <= b[0] for a, b in pairwise(rows))
    # The busy sample has free time between classes (e.g. lunch on Monday).
    monday = sorted((e["start"], e["end"]) for e in sample_events("busy") if e["weekday"] == 0)
    assert any(b[0] > a[1] for a, b in pairwise(monday))
