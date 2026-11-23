import pytest

from app.models import Programming, RepRange, Technique, Volume
from app.scheduler.exercises import BACK_SQUAT, CALVES, CATALOG, CORE, DUMBBELL_RDL, LEG_PRESS
from app.scheduler.sessions import (
    FULL_BODY_A,
    FULL_BODY_B,
    FULL_BODY_C,
    LEGS,
    LOWER,
    PULL,
    PUSH,
    UPPER,
    Slot,
    build_session,
    choose,
)

TEMPLATES = [FULL_BODY_A, FULL_BODY_B, FULL_BODY_C, PUSH, PULL, LEGS, UPPER, LOWER]
names = lambda session: [e.name for e in session.exercises]  # noqa: E731
total_sets = lambda session: sum(e.sets for e in session.exercises)  # noqa: E731
CUSTOM = [
    Programming(),
    Programming(simpler_exercises=True),
    Programming(volume=Volume.LOWER),
    Programming(volume=Volume.HIGHER),
    Programming(reps=RepRange.HEAVIER),
]


@pytest.mark.parametrize("minutes", [45, 60, 75, 90])
@pytest.mark.parametrize("programming", CUSTOM)
def test_every_workout_fits_its_length_and_covers_its_muscles(minutes, programming):
    for template in TEMPLATES:
        session = build_session(template, minutes, programming)
        assert session.minutes <= minutes, (template.name, session.minutes)
        assert session.left_out == ()


def test_everyone_gets_the_same_default_workouts():
    """No experience profiles: the same evidence-informed defaults for everyone."""
    session = build_session(FULL_BODY_A, 60)
    assert names(session)[:3] == ["Back Squat", "Bench Press", "Barbell Row"]
    assert all(e.sets <= 3 for e in session.exercises)
    assert build_session(LEGS, 90) == build_session(LEGS, 90, Programming())


def test_longer_workouts_get_more_sets():
    for template in TEMPLATES:
        assert total_sets(build_session(template, 90)) > total_sets(build_session(template, 45))


def test_short_workouts_use_fewer_sets_before_dropping_muscles():
    session = build_session(FULL_BODY_A, 30)
    assert session.minutes <= 30
    assert {CALVES, CORE} <= session.muscle_groups  # still in, with fewer sets
    tiny = build_session(FULL_BODY_A, 20)
    assert tiny.left_out == (CALVES, CORE)  # reported, never silently dropped


def test_simpler_exercises_swap_technical_lifts_for_basic_ones():
    simpler = build_session(FULL_BODY_A, 60, Programming(simpler_exercises=True))
    assert "Leg Press" in names(simpler) and "Back Squat" not in names(simpler)
    assert all(e.exercise.technique is Technique.BASIC for e in simpler.exercises)
    assert simpler.muscle_groups == build_session(FULL_BODY_A, 60).muscle_groups  # same muscles, easier movements


def test_swapping_out_an_exercise_uses_another_for_the_same_muscles():
    swapped = build_session(FULL_BODY_A, 60, Programming(avoid=frozenset({"Back Squat"})))
    assert "Back Squat" not in names(swapped) and "Leg Press" in names(swapped)
    # A slot with one option still gets a stand-in from the catalog.
    no_curl = build_session(UPPER, 90, Programming(avoid=frozenset({"EZ-Bar Curl"})))
    assert "EZ-Bar Curl" not in names(no_curl) and any("Curl" in n for n in names(no_curl))


def test_volume_and_rep_ranges_follow_what_the_user_asked_for():
    lower = build_session(LEGS, 90, Programming(volume=Volume.LOWER))
    higher = build_session(LEGS, 90, Programming(volume=Volume.HIGHER))
    assert max(e.sets for e in lower.exercises) == 2
    assert max(e.sets for e in higher.exercises) == 4
    heavier = build_session(PUSH, 60, Programming(reps=RepRange.HEAVIER))
    assert {e.reps for e in heavier.exercises if e.exercise.kind == "compound"} == {"5-8"}


def test_rest_matches_the_kind_of_exercise():
    for e in build_session(PUSH, 60).exercises:
        assert e.rest == ("2-3 min" if e.exercise.kind == "compound" else "1-2 min")


def test_full_body_a_b_c_vary_exercises_but_cover_the_same_muscles():
    sessions = [build_session(t, 60) for t in (FULL_BODY_A, FULL_BODY_B, FULL_BODY_C)]
    assert len({tuple(names(s)) for s in sessions}) == 3
    major = {"chest", "back", "shoulders", "quads", "glutes", CALVES, CORE}
    assert all(major <= s.muscle_groups for s in sessions)


def test_calves_and_core_are_programmed_where_they_belong():
    built = {t.name: build_session(t, 45) for t in TEMPLATES}
    for name in ("Full Body A", "Full Body B", "Full Body C", "Legs", "Lower"):
        assert CALVES in built[name].muscle_groups, name
    for name in ("Full Body A", "Full Body B", "Full Body C", "Push", "Upper"):
        assert CORE in built[name].muscle_groups, name


def test_simpler_exercises_prefer_basic_options_in_the_same_slot():
    slot = Slot((BACK_SQUAT, LEG_PRESS, DUMBBELL_RDL))
    assert choose(slot) is BACK_SQUAT
    assert choose(slot, Programming(simpler_exercises=True)) is LEG_PRESS


def test_the_catalog_records_technique_for_every_exercise():
    assert BACK_SQUAT.technique is Technique.TECHNICAL and LEG_PRESS.technique is Technique.BASIC
    assert len(CATALOG) == len({e.name for e in CATALOG})
