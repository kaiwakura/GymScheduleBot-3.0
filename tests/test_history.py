from app.models import Commitment, RecentWorkout
from app.scheduler.planner import build_plan
from app.training_history import clean_history, last_date_for, mark_skipped, merge_plan, recent_workouts
from tests.conftest import MONDAY, at, block_whole_day

LEGS = ["calves", "glutes", "hamstrings", "quads"]


def entry(day, hour, name="Legs", muscles=LEGS, status="planned"):
    return {"name": name, "start": at(day, hour).isoformat(), "muscles": muscles, "status": status}


def shortest_rest_after(recent, plan):
    return min(
        (w.start - recent.start).total_seconds() / 3600 for w in plan.workouts if w.muscle_groups & recent.muscle_groups
    )


# ------------------------------------------------------------- the history ---
def test_bad_entries_are_dropped_and_fields_cleaned():
    good = entry(-1, 18, muscles=["quads", "wings"], status="weird")
    naive = {**entry(-1, 18), "start": "2026-11-15T18:00:00"}
    history = clean_history([good, naive, {"start": "nope"}, "x"])
    assert history == [{**good, "muscles": ["quads"], "status": "planned"}]
    assert clean_history("not a list") == []


def test_merge_keeps_what_started_and_replaces_what_is_upcoming(prefs):
    now = at(0, 8)
    old_future = entry(3, 18, name="Old plan")
    history = merge_plan([entry(-1, 18), entry(-9, 18), old_future], build_plan([], MONDAY, prefs, now=now), now)
    names = [e["name"] for e in history]
    assert names[0] == "Legs"  # yesterday stays
    assert "Old plan" not in names  # superseded by the new plan
    assert len(history) == 2 + 4  # yesterday and 9 days ago stay (past weeks show them); 4 new planned workouts


def test_only_recent_unskipped_workouts_count_for_recovery():
    now = at(0, 8)
    history = clean_history([entry(-1, 18), entry(-2, 18, status="skipped"), entry(-5, 18), entry(1, 18)])
    assert [r.start for r in recent_workouts(history, now)] == [at(-1, 18)]


def test_marking_a_day_as_skipped():
    now = at(0, 8)
    history = clean_history([entry(-1, 18), entry(1, 18)])
    assert mark_skipped(history, {at(-1, 0).date()}, now) == ["Sunday's Legs"]
    assert history[0]["status"] == "skipped"
    assert mark_skipped(history, {at(1, 0).date()}, now) == []  # hasn't happened yet
    assert last_date_for(6, MONDAY) == at(-1, 0).date() and last_date_for(0, MONDAY) == MONDAY


# ---------------------------------------------------- recovery across weeks ---
def test_sunday_legs_count_when_the_new_week_starts(prefs):
    prefs.commitment = Commitment.LIGHT  # Full Body A/B, which trains legs every session
    sunday_legs = RecentWorkout("Legs", at(-1, 18), frozenset(LEGS))
    plan = build_plan([], MONDAY, prefs, now=at(0, 6), recent=[sunday_legs])
    assert shortest_rest_after(sunday_legs, plan) >= 48
    assert any("still count toward recovery" in n for n in plan.notes)
    without = build_plan([], MONDAY, prefs, now=at(0, 6))
    assert shortest_rest_after(sunday_legs, without) < 48  # it would have clashed


def test_late_night_workouts_use_real_hours(prefs):
    # Sunday 9 PM legs. Tuesday 6 AM is two calendar days later but only 33 hours.
    prefs.commitment = Commitment.LIGHT
    late = RecentWorkout("Lower", at(-1, 21), frozenset(LEGS))
    calendar = [block_whole_day(d) for d in (0, 2, 4, 5, 6)]  # free: Tue, Thu
    plan = build_plan(calendar, MONDAY, prefs, now=at(0, 6), recent=[late])
    assert shortest_rest_after(late, plan) >= 48


def test_skipped_workouts_stop_counting(prefs):
    prefs.commitment = Commitment.DEDICATED
    history = clean_history([entry(-1, 18)])
    mark_skipped(history, {at(-1, 0).date()}, at(0, 6))
    assert recent_workouts(history, at(0, 6)) == []
