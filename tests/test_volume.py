from datetime import datetime

from app.models import Commitment, PlannedExercise, Workout
from app.scheduler.exercises import BENCH_PRESS, CABLE_CRUNCH, LEG_PRESS, MUSCLES, STANDING_CALF_RAISE
from app.scheduler.planner import build_plan
from app.scheduler.volume import WEEKLY_SET_TARGET, weekly_sets
from app.views import volume_line
from tests.conftest import MONDAY


def workout(*planned):
    start = datetime(2026, 11, 16, 18)
    return Workout("Test", frozenset(), tuple(planned), start, start)


def sets(exercise, n):
    return PlannedExercise(exercise, n, exercise.reps, "2-3 min")


def test_helper_muscles_count_half_a_set():
    totals = weekly_sets([workout(sets(BENCH_PRESS, 3), sets(LEG_PRESS, 4))])
    assert totals["chest"] == 3 and totals["triceps"] == 1.5 and totals["shoulders"] == 1.5
    assert totals["quads"] == 4 and totals["glutes"] == 2
    assert totals["back"] == 0


def test_every_muscle_group_is_tracked_including_calves_and_core():
    totals = weekly_sets([workout(sets(STANDING_CALF_RAISE, 3), sets(CABLE_CRUNCH, 3))])
    assert tuple(totals) == MUSCLES
    assert totals["calves"] == 3 and totals["core"] == 3


def test_sets_add_up_across_the_week(prefs):
    plan = build_plan([], MONDAY, prefs)
    per_workout = [weekly_sets([w]) for w in plan.workouts]
    week = weekly_sets(plan.workouts)
    assert all(week[m] == sum(t[m] for t in per_workout) for m in MUSCLES)


def test_volume_line_lists_every_muscle_with_its_weekly_sets(prefs):
    plan = build_plan([], MONDAY, prefs)
    week = weekly_sets(plan.workouts)
    line = volume_line(plan)
    assert line == " · ".join(f"{m} {week[m]:g}" for m in MUSCLES)
    assert WEEKLY_SET_TARGET == (10, 20)


def test_main_splits_train_every_muscle_with_real_volume(prefs):
    prefs.session_minutes = 75
    for commitment in (Commitment.HIGH, Commitment.DEDICATED):
        prefs.commitment = commitment
        week = weekly_sets(build_plan([], MONDAY, prefs).workouts)
        assert all(week[m] >= 6 for m in MUSCLES), week  # nothing is neglected, calves and core included


def test_low_volume_is_explained_not_hidden(prefs):
    prefs.commitment, prefs.session_minutes = Commitment.VERY_LIGHT, 45
    plan = build_plan([], MONDAY, prefs)
    assert any("fewer than 10 weekly sets" in n for n in plan.notes)
    prefs.commitment, prefs.session_minutes = Commitment.HIGH, 75
    plan = build_plan([], MONDAY, prefs)
    assert not any("weekly sets (" in n for n in plan.notes)
