from app.models import Commitment, SplitStyle, TimeOfDay
from app.scheduler.planner import build_plan, rest_penalty, spacing_penalty, trains_every_muscle_twice
from app.scheduler.sessions import build_session
from app.scheduler.splits import FULL_BODY_A, LEGS, PULL, PUSH, recommended_splits
from tests.conftest import MONDAY, at, block_whole_day, busy

FB, PUSH_DAY, PULL_DAY, LEG_DAY = (build_session(t, 60) for t in (FULL_BODY_A, PUSH, PULL, LEGS))


def days(*offsets):
    return tuple(at(o, 0).date() for o in offsets)


# ------------------------------------------------------------------ helpers ---
def test_rest_under_48_hours_is_not_allowed():
    assert rest_penalty((FB, FB), (at(0, 18), at(1, 18))) is None  # 24h
    assert rest_penalty((FB, FB), (at(0, 21), at(2, 6))) is None  # 33h: two days apart isn't enough
    assert rest_penalty((FB, FB), (at(0, 18), at(2, 18))) is not None  # exactly 48h


def test_rest_penalty_shrinks_toward_72_hours():
    at_48 = rest_penalty((FB, FB), (at(0, 18), at(2, 18)))
    at_60 = rest_penalty((FB, FB), (at(0, 18), at(3, 6)))
    at_72 = rest_penalty((FB, FB), (at(0, 18), at(3, 18)))
    assert at_48 > at_60 > at_72 == 0


def test_ppl_on_consecutive_days_is_fine():
    assert rest_penalty((PUSH_DAY, PULL_DAY, LEG_DAY), (at(0, 18), at(1, 18), at(2, 18))) == 0


def test_spacing_prefers_even_gaps():
    assert spacing_penalty(days(0, 3)) < spacing_penalty(days(0, 1))
    assert spacing_penalty(days(0, 2, 4)) < spacing_penalty(days(0, 1, 2))


def test_recommended_split_order():
    assert [s.name for s in recommended_splits(2)] == ["Full Body A/B", "Upper/Lower x1"]
    assert [s.name for s in recommended_splits(3)] == ["Full Body A/B/C", "Push/Pull/Legs"]
    assert recommended_splits(6) == []  # six days only when the user asks for it
    assert recommended_splits(0) == []


# ------------------------------------------------------- split selection rules ---
def free_only(*weekdays):
    """A calendar where only these days (0 = Monday) are free."""
    return [block_whole_day(d) for d in range(7) if d not in weekdays]


def test_one_free_day_gets_full_body(prefs):
    plan = build_plan(free_only(2), MONDAY, prefs)
    assert plan.split_name == "Full Body"
    assert any("each muscle once" in n for n in plan.notes)


def test_two_spaced_days_get_full_body_a_and_b(prefs):
    plan = build_plan(free_only(1, 4), MONDAY, prefs)  # Tuesday and Friday
    assert [w.name for w in plan.workouts] == ["Full Body A", "Full Body B"]
    assert any("two full-body workouts" in n for n in plan.notes)


def test_light_commitment_open_week_gets_full_body_twice(prefs):
    prefs.commitment = Commitment.LIGHT
    plan = build_plan([], MONDAY, prefs)
    assert plan.split_name == "Full Body A/B"
    assert (plan.workouts[1].day - plan.workouts[0].day).days >= 3  # spread out, e.g. Mon/Thu


def test_two_consecutive_days_fall_back_to_upper_lower(prefs):
    plan = build_plan(free_only(0, 1), MONDAY, prefs)
    assert [w.name for w in plan.workouts] == ["Upper", "Lower"]
    assert not plan.workouts[0].muscle_groups & plan.workouts[1].muscle_groups  # no muscle trained two days running
    assert any("back-to-back" in n for n in plan.notes)


def test_three_spaced_days_get_full_body_a_b_c(prefs):
    plan = build_plan(free_only(0, 2, 4), MONDAY, prefs)
    assert [w.name for w in plan.workouts] == ["Full Body A", "Full Body B", "Full Body C"]


def test_three_consecutive_days_keep_every_muscle_twice(prefs):
    # Full Body A/B/C can't get 48h of rest on Mon/Tue/Wed, and PPL would train each muscle
    # once, so the planner picks Full Body A/B on Monday and Wednesday.
    plan = build_plan(free_only(0, 1, 2), MONDAY, prefs)
    assert plan.split_name == "Full Body A/B"
    assert [w.day.weekday() for w in plan.workouts] == [0, 2]
    assert any("2 workouts instead of 3" in n for n in plan.notes)


def test_four_spaced_days_get_upper_lower_twice(prefs):
    plan = build_plan(free_only(0, 1, 3, 4), MONDAY, prefs)
    assert [w.name for w in plan.workouts] == ["Upper", "Lower", "Upper", "Lower"]


def test_five_free_days_get_ppl_plus_upper_lower(prefs):
    prefs.commitment = Commitment.DEDICATED
    plan = build_plan(free_only(0, 1, 2, 4, 5), MONDAY, prefs)
    assert [w.name for w in plan.workouts] == ["Push", "Pull", "Legs", "Upper", "Lower"]


def test_six_or_seven_free_days_still_get_five_workouts(prefs):
    prefs.commitment = Commitment.DEDICATED
    for calendar in ([], free_only(0, 1, 2, 3, 4, 5)):
        plan = build_plan(calendar, MONDAY, prefs)
        assert plan.split_name == "PPL + Upper/Lower"
        assert len(plan.workouts) == 5
        assert any("two rest days" in n for n in plan.notes)


def test_commitment_caps_workouts_however_much_free_time(prefs):
    plan = build_plan([], MONDAY, prefs)  # high, 7 free days
    assert len(plan.workouts) == 4
    assert any("allows up to 4 workouts" in n for n in plan.notes)


def test_five_commitment_levels_cap_one_to_five_workouts(prefs):
    caps = dict(zip(Commitment, range(1, 6), strict=True))  # Very Light 1 ... Dedicated 5
    for commitment, cap in caps.items():
        prefs.commitment = commitment
        assert len(build_plan([], MONDAY, prefs).workouts) == cap, commitment


def test_commitment_is_a_ceiling_not_a_promise(prefs):
    prefs.commitment = Commitment.DEDICATED
    plan = build_plan(free_only(1, 4), MONDAY, prefs)  # only two free days
    assert len(plan.workouts) == 2


def test_requested_upper_lower_with_two_days_is_honored(prefs):
    prefs.requested_split = SplitStyle.UPPER_LOWER
    plan = build_plan(free_only(1, 4), MONDAY, prefs)
    assert plan.split_name == "Upper/Lower x1"
    assert any("split you asked for" in n for n in plan.notes)


def test_requested_six_day_ppl_is_honored_when_it_fits(prefs):
    prefs.requested_split = SplitStyle.PPL_SIX_DAY  # works even though high caps at 4
    plan = build_plan([], MONDAY, prefs)
    assert plan.split_name == "Push/Pull/Legs x2"
    assert min(every_pair_rest(plan)) >= 48


def test_requested_split_that_does_not_fit_falls_back_safely(prefs):
    prefs.requested_split = SplitStyle.PPL_SIX_DAY
    plan = build_plan(free_only(0, 2, 4), MONDAY, prefs)  # only three free days
    assert plan.split_name == "Full Body A/B/C"
    assert any("doesn't fit" in n for n in plan.notes)


def test_no_free_time_means_rest_week(prefs):
    plan = build_plan([block_whole_day(d) for d in range(7)], MONDAY, prefs)
    assert plan.split_name == "Rest week"
    assert plan.workouts == []


def test_workouts_never_overlap_busy_time_or_buffer(prefs):
    prefs.commitment = Commitment.DEDICATED
    calendar = [busy(d, 8, 17, "Work") for d in range(5)] + [busy(1, 18, 19.5, "Soccer")]
    plan = build_plan(calendar, MONDAY, prefs)
    for w in plan.workouts:
        for b in calendar:
            padded_start = b.start.timestamp() - prefs.buffer_minutes * 60
            padded_end = b.end.timestamp() + prefs.buffer_minutes * 60
            assert w.end.timestamp() <= padded_start or w.start.timestamp() >= padded_end


def test_evening_preference_is_respected(prefs):
    prefs.preferred_time = TimeOfDay.EVENING
    plan = build_plan([], MONDAY, prefs)
    assert all(w.start.hour == 18 for w in plan.workouts)


def test_morning_preference_moves_around_early_class(prefs):
    prefs.preferred_time = TimeOfDay.MORNING
    plan = build_plan([busy(d, 6, 8) for d in range(7)], MONDAY, prefs)
    # 6-8 is busy, +15 min buffer -> earliest start 8:15
    assert all(w.start == at((w.day - MONDAY).days, 8, 15) for w in plan.workouts)


# ------------------------------------------------------- muscle-building rules ---
def every_pair_rest(plan):
    return [
        (b.start - a.start).total_seconds() / 3600
        for i, a in enumerate(plan.workouts)
        for b in plan.workouts[i + 1 :]
        if a.muscle_groups & b.muscle_groups
    ]


def test_rest_is_at_least_48h_and_ideally_72h(prefs):
    for commitment in [c for c in Commitment if c is not Commitment.VERY_LIGHT]:  # one workout has no pairs
        prefs.commitment = commitment
        plan = build_plan([], MONDAY, prefs)
        assert min(every_pair_rest(plan)) >= 72  # an open week always gets the ideal rest


def test_planner_moves_a_session_later_to_reach_48h(prefs):
    # Mon is only free late, Wed only early, Fri open. Mon 9pm -> Wed 6am is 33h,
    # so Full Body can't go on Mon+Wed; the planner must find another valid pair.
    prefs.commitment = Commitment.LIGHT
    calendar = [busy(0, 6, 21), busy(2, 8, 22)] + [block_whole_day(d) for d in (1, 3, 5, 6)]
    plan = build_plan(calendar, MONDAY, prefs)
    assert plan.split_name == "Full Body A/B"
    assert min(every_pair_rest(plan)) >= 48


def test_every_muscle_twice_beats_more_sessions(prefs):
    prefs.commitment = Commitment.DEDICATED
    plan = build_plan([], MONDAY, prefs)
    assert trains_every_muscle_twice(plan.workouts)


def test_session_length_reshapes_the_workout(prefs):
    prefs.session_minutes = 45
    short = build_plan([], MONDAY, prefs).workouts[0]
    prefs.session_minutes = 90
    long = build_plan([], MONDAY, prefs).workouts[0]
    total = lambda w: sum(e.sets for e in w.exercises)  # noqa: E731
    assert total(short) < total(long)
    assert short.minutes <= 45 and long.minutes <= 90  # the exercises really fit the time
    assert (short.end - short.start).total_seconds() == 45 * 60
    assert (long.end - long.start).total_seconds() == 90 * 60
