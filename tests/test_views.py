from datetime import date, datetime, timedelta

from app.models import BusyBlock, Commitment, Preferences
from app.planning import Inputs, plan_for
from app.settings import Settings
from app.views import (
    build_days,
    hour_labels,
    summary_line,
    time_range,
    week_context,
    week_label,
    without_duplicates,
)
from tests.conftest import MONDAY, TZ, at


def inputs(events=(), now=None, **settings) -> Inputs:
    return Inputs(Settings(**{"commitment": "light", **settings}), list(events), [], [], now or at(0, 6))


def weekly(title, weekday, start, end, **extra):
    return {
        "id": f"e_{title}{weekday}",
        "title": title,
        "start": start,
        "end": end,
        "repeat": "weekly",
        "weekday": weekday,
        **extra,
    }


def test_week_labels_and_time_ranges():
    assert week_label(date(2026, 11, 23)) == "Nov 23–29, 2026"
    assert week_label(date(2026, 11, 30)) == "Nov 30 – Dec 6, 2026"
    assert week_label(date(2026, 12, 28)) == "Dec 28, 2026 – Jan 3, 2027"
    assert time_range(at(0, 17), at(0, 18)) == "5:00 – 6:00 PM"
    assert time_range(at(0, 11, 30), at(0, 12, 30)) == "11:30 AM – 12:30 PM"


def test_days_have_events_and_workout_details():
    data = inputs([weekly("Work", 0, "07:00", "18:00")], blocked_weekdays=[6])
    plan = plan_for(data, MONDAY)
    days = build_days(data, plan)
    assert len(days) == 7 and days[0]["weekday"] == "Mon" and days[0]["date_num"] == 16
    work = days[0]["events"][0]
    assert (work["title"], work["id"], work["range"]) == ("Work", "e_Work0", "7:00 AM – 6:00 PM")
    cards = [w for d in days for w in d["workouts"]]
    assert [c["name"] for c in cards] == ["Full Body A", "Full Body B"]
    first = cards[0]
    assert first["muscles"].startswith("Chest · Back") and first["exercises"][0]["dose"].count("×") == 1
    assert first["exercises"][0]["rest"] == "2–3 min" and first["draggable"] and first["others"]
    assert summary_line(plan, Preferences()) == "2 workouts · 60 min"


def test_past_days_and_now_line():
    data = inputs(now=at(2, 14))
    days = build_days(data, plan_for(data, MONDAY))
    assert days[0]["is_past"] and days[1]["is_past"] and not days[2]["is_past"]
    assert days[2]["now_top"] == round(8 / 16 * 100, 3)  # 2 PM is halfway through 6 AM-10 PM


def test_blocks_are_positioned_and_overlaps_share_the_width():
    events = [weekly("A", 0, "09:00", "11:00"), weekly("B", 0, "10:00", "12:00"), weekly("C", 0, "14:00", "15:00")]
    data = inputs(events)
    a, b, c = build_days(data, plan_for(data, MONDAY))[0]["events"]
    assert a["top"] == round(3 / 16 * 100, 3) and a["height"] == round(2 / 16 * 100, 3)
    assert (a["left"], a["width"]) == (0.0, 50.0)
    assert (b["left"], b["width"]) == (50.0, 50.0)
    assert (c["left"], c["width"]) == (0.0, 100.0)


def test_google_busy_time_already_added_by_hand_is_drawn_once():
    own = BusyBlock(at(0, 9), at(0, 10), "Calc lecture", "e_1")
    google = BusyBlock(at(0, 9), at(0, 10))
    other = BusyBlock(at(0, 13), at(0, 14))
    assert without_duplicates([own, google, other]) == [own, other]


def test_unavailable_time_is_its_own_kind():
    data = inputs([weekly("Can't train", 0, "06:00", "09:00", kind="unavailable")])
    event = build_days(data, plan_for(data, MONDAY))[0]["events"][0]
    assert event["kind"] == "unavailable"


def test_week_context_links_the_neighbouring_weeks():
    data = inputs()
    context = week_context(data, plan_for(data, MONDAY + timedelta(days=7)))
    assert context["week_label"] == "Nov 23–29, 2026"
    assert (context["prev_week"], context["next_week"]) == ("2026-11-16", "2026-11-30")
    assert not context["is_current_week"] and not context["is_past_week"]


def test_hour_labels_run_from_6_am_to_10_pm_by_default():
    labels = hour_labels(Preferences())
    assert labels[0] == {"label": "6 AM", "top": 0.0}
    assert labels[6]["label"] == "12 PM" and labels[-1]["label"] == "9 PM"


def test_moderate_is_the_default_for_new_visitors():
    assert Settings().commitment == Commitment.MODERATE.value
    assert datetime(2026, 11, 16, tzinfo=TZ).weekday() == 0
