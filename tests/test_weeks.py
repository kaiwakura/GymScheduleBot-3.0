"""Calendar weeks, moved workouts, other available times and coach proposals."""

from datetime import date, datetime, timedelta

from app import proposals, workout_moves
from app.ai.rule_parser import parse_with_rules
from app.models import FixedWorkout, Preferences
from app.planning import Inputs, google_window, other_times, plan_for, resolve_move
from app.scheduler.availability import free_slots_for_day
from app.scheduler.planner import build_plan, rest_hours
from app.settings import Settings
from tests.conftest import MONDAY, TZ, at, busy

NEXT_MONDAY = MONDAY + timedelta(days=7)


def inputs(events=(), history=(), moves=(), now=None, **settings) -> Inputs:
    return Inputs(
        Settings(**{"commitment": "high", **settings}), list(events), list(history), list(moves), now or at(0, 6)
    )


def weekly(title, weekday, start, end):
    return {
        "id": f"e_{title}{weekday}",
        "title": title,
        "start": start,
        "end": end,
        "repeat": "weekly",
        "weekday": weekday,
    }


# ------------------------------------------------------------------ weeks ---
def test_recovery_carries_across_the_week_boundary():
    """Sunday's workout still counts when next week is planned."""
    data = inputs([weekly("Busy", d, "06:00", "22:00") for d in range(6)])  # only Sundays are free
    this_week, next_week = plan_for(data, MONDAY), plan_for(data, NEXT_MONDAY)
    sunday = [w for w in this_week.workouts if w.start.weekday() == 6]
    assert sunday and next_week.workouts
    for later in next_week.workouts:
        for earlier in sunday:
            if later.muscle_groups & earlier.muscle_groups:
                assert rest_hours(earlier.start, later.start) >= 48


def test_far_future_weeks_plan_quickly_and_completely():
    data = inputs()
    far = MONDAY + timedelta(weeks=60)
    plan = plan_for(data, far)
    assert len(plan.workouts) == 4 and plan.week_start == far
    assert len(data.memo) <= 5  # only a few weeks are chained, never all 60
    start, end = google_window(MONDAY, far, "America/Los_Angeles")
    assert (end - start).days <= 7 * 4


def test_workouts_that_started_this_week_count_toward_it():
    monday_plan = plan_for(inputs(), MONDAY)
    history = [
        {"name": w.name, "start": w.start.isoformat(), "muscles": sorted(w.muscle_groups), "status": "planned"}
        for w in monday_plan.workouts
    ]
    thursday = inputs(history=history, now=at(3, 8))
    plan = plan_for(thursday, MONDAY)
    done = [w for w in plan.workouts if w.start < thursday.now]
    assert done and all(w.pinned for w in done)
    assert len(plan.workouts) <= 4  # still within the commitment, done workouts included
    skipped = [{**h, "status": "skipped"} if h["start"] == history[0]["start"] else h for h in history]
    assert history[0]["start"] not in [
        w.start.isoformat() for w in plan_for(inputs(history=skipped, now=at(3, 8)), MONDAY).workouts
    ]


def test_past_weeks_show_history_and_skipped_workouts():
    history = [
        {"name": "Upper", "start": at(-6, 18).isoformat(), "muscles": ["chest"], "status": "planned"},
        {"name": "Lower", "start": at(-4, 18).isoformat(), "muscles": ["quads"], "status": "skipped"},
    ]
    plan = plan_for(inputs(history=history), MONDAY - timedelta(days=7))
    assert [(w.name, w.skipped) for w in plan.workouts] == [("Upper", False), ("Lower", True)]


# ------------------------------------------------------------ moved workouts ---
def move(week, name, origin, start):
    return {"week": week.isoformat(), "name": name, "from": origin.isoformat(), "start": start.isoformat()}


def test_moves_are_cleaned_and_one_per_workout():
    good = move(NEXT_MONDAY, "Upper", at(8, 18), at(9, 7))
    assert workout_moves.clean_moves([good, {**good, "name": "Nope"}, "x"], MONDAY) == [good]
    assert workout_moves.clean_moves([{**good, "start": at(20, 7).isoformat()}], MONDAY) == []  # outside its week
    assert workout_moves.clean_moves([good], NEXT_MONDAY + timedelta(days=7)) == []  # weeks that have passed
    later = {**good, "start": at(10, 7).isoformat()}
    assert workout_moves.clean_moves([good, later], MONDAY) == [later]


def test_moving_twice_remembers_the_first_spot_and_moving_back_removes_the_move():
    moves = workout_moves.add([], NEXT_MONDAY, "Upper", at(8, 18), at(9, 7))
    moves = workout_moves.add(moves, NEXT_MONDAY, "Upper", at(9, 7), at(10, 7))
    assert len(moves) == 1 and moves[0]["from"] == at(8, 18).isoformat()
    assert workout_moves.add(moves, NEXT_MONDAY, "Upper", at(10, 7), at(8, 18)) == []


def test_the_planner_keeps_a_moved_workout_and_plans_around_it():
    origin = plan_for(inputs(), NEXT_MONDAY).workouts[0]
    target = origin.start.replace(hour=7) + timedelta(days=1)
    plan = plan_for(inputs(moves=[move(NEXT_MONDAY, origin.name, origin.start, target)]), NEXT_MONDAY)
    kept = [w for w in plan.workouts if w.start == target]
    assert kept and kept[0].pinned and len(plan.workouts) == 4
    assert any("where you put it" in n for n in plan.notes)


def test_a_move_that_no_longer_fits_the_split_is_explained_and_dropped(prefs):
    fixed = [FixedWorkout("Upper", at(1, 7), at(1, 8))]
    prefs.commitment = prefs.commitment.VERY_LIGHT  # one workout: Full Body, which has no Upper
    plan = build_plan([], MONDAY, prefs, fixed=fixed)
    assert [w.name for w in plan.workouts] == ["Full Body A"]
    assert plan.dropped == fixed and any("doesn't fit your current settings" in w for w in plan.warnings)


def test_a_moved_workout_that_now_clashes_gets_a_warning_and_stays(prefs):
    prefs.buffer_minutes = 30
    fixed = [FixedWorkout("Upper", at(1, 7), at(1, 8))]
    plan = build_plan([busy(1, 8, 9, "Lab")], MONDAY, prefs, fixed=fixed)
    assert any(w.start == at(1, 7) for w in plan.workouts)
    assert any("30-minute travel buffer around Lab" in w for w in plan.warnings)


def test_the_buffer_only_pads_around_workouts_not_between_commitments(prefs):
    prefs.buffer_minutes = 60
    back_to_back = [busy(0, 9, 10, "Class"), busy(0, 10, 11, "Class")]
    slots = free_slots_for_day(MONDAY, back_to_back, prefs)
    assert [s.start.hour for s in slots] == [6, 12]  # an hour before 9 and after 11, nothing in between
    assert back_to_back[0].end == at(0, 10)  # the commitments themselves never change


# ------------------------------------------------------------ other times ---
def test_other_times_are_free_with_the_buffer_and_keep_48_hours():
    data = inputs([weekly("Shift", 2, "06:00", "17:00")], buffer_minutes=30)
    plan = plan_for(data, NEXT_MONDAY)
    for workout in plan.workouts:
        for option in other_times(data, plan, workout):
            assert not (option.start.weekday() == 2 and option.start.hour < 17)  # Wednesday shift + 30 min
            same = [w for w in plan.workouts if w is not workout and w.muscle_groups & workout.muscle_groups]
            assert all(rest_hours(option.start, w.start) >= 48 for w in same)
            assert option.start.date() not in {w.day for w in plan.workouts if w is not workout}


def test_no_other_times_for_workouts_that_already_happened():
    data = inputs(now=at(6, 23))
    plan = plan_for(data, MONDAY)
    assert all(other_times(data, plan, w) == [] for w in plan.workouts if w.start < data.now)


# --------------------------------------------------------- coach requests ---
def test_rule_parser_reads_moves_and_programming_but_never_experience():
    m = parse_with_rules("move thursday's workout to friday morning", MONDAY).move
    assert (m.from_weekday, m.to_weekday, m.part_of_day.value) == (3, 4, "morning")
    m = parse_with_rules("put my upper on saturday at 7am", MONDAY).move
    assert (m.workout, m.to_weekday, m.at.hour) == ("Upper", 5, 7)
    assert parse_with_rules("simpler exercises please", MONDAY).simpler_exercises is True
    assert parse_with_rules("I can't do pull-ups", MONDAY).avoid_exercises == ["Pull-up"]
    assert parse_with_rules("more sets", MONDAY).volume.value == "higher"
    assert parse_with_rules("lighter weights, higher reps", MONDAY).reps.value == "lighter"
    assert parse_with_rules("I've been lifting for 5 years, I'm advanced", MONDAY).is_empty()


def test_rule_parser_reads_exercise_swaps_and_resets():
    assert parse_with_rules("no barbell rows please", MONDAY).avoid_exercises == ["Barbell Row"]
    assert parse_with_rules("no sundays", MONDAY).avoid_exercises == []  # a day off, not an exercise
    assert parse_with_rules("back to the defaults", MONDAY).reset_programming is True
    split = parse_with_rules("switch me back to the default split", MONDAY)
    assert split.reset_programming is False and split.reset_split is True


def test_resolving_a_move_checks_free_time_and_recovery():
    data = inputs()
    plan = plan_for(data, NEXT_MONDAY)
    first, *others = plan.workouts
    free_day = next(d for d in range(7) if all(w.start.weekday() != d for w in plan.workouts))
    request = parse_with_rules(f"move {first.start:%A}'s workout to {date(2026, 11, 23 + free_day):%A}", MONDAY).move
    result, problem = resolve_move(data, plan, request)
    if problem is None:
        assert result["name"] == first.name and result["from"] == first.start.isoformat()
    else:
        assert "48-hour minimum" in problem
    taken = parse_with_rules(f"move {first.start:%A}'s workout to {others[0].start:%A}", MONDAY).move
    assert "already has a workout" in resolve_move(data, plan, taken)[1]


# --------------------------------------------------------------- proposals ---
def test_proposals_describe_what_would_move():
    data = inputs()
    proposal = proposals.make(data, MONDAY, data.settings, Settings(commitment="high", preferred_time="morning"), None)
    assert proposal and proposal.settings == {"preferred_time": "morning"}  # only what changes is kept
    assert all("→" in line for line in proposal.lines)
    same = proposals.make(data, MONDAY, data.settings, Settings(commitment="high", volume="lower"), None)
    assert same is None  # nothing moves, so there's nothing to propose


def test_proposals_round_trip_through_the_session():
    data = inputs()
    proposal = proposals.make(data, MONDAY, data.settings, Settings(commitment="high", preferred_time="morning"), None)
    session = {}
    proposals.save(session, proposal)
    loaded = proposals.load(session)
    assert loaded.token == proposal.token and loaded.lines == proposal.lines
    assert loaded.apply_settings(data.settings).preferred_time == "morning"
    proposals.clear(session)
    assert proposals.load(session) is None


def test_preferences_model_has_no_experience():
    assert not hasattr(Preferences(), "experience")
    assert datetime(2026, 11, 16, tzinfo=TZ).date() == MONDAY


def test_today_and_tomorrow_follow_the_users_date():
    thursday = MONDAY + timedelta(days=3)
    move = parse_with_rules("move today's workout to tomorrow morning", thursday).move
    assert (move.from_weekday, move.to_weekday) == (3, 4)
    assert parse_with_rules("I skipped today", thursday).skipped_weekdays == {3}
