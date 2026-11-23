"""Workout templates, and how a template becomes a timed session for one person.

A template is a list of slots. Each slot is one job in the workout ("a quad
exercise", "a calf exercise") with a few exercises that can do it, best first.
Required slots cover every muscle the workout is responsible for, calves and core
included. Optional slots add volume when there's time.

Everyone gets the same evidence-informed defaults. The user can change them through
the AI Coach (``Programming``): simpler exercises, swapping one they don't like, fewer
or more sets, and heavier or lighter rep ranges.

Building a session:
  1. Pick each slot's exercise: the first option, unless the user avoids it or asked
     for simpler exercises.
  2. Give every required exercise 2 working sets (fewer only if the workout is very short).
  3. Add sets, required exercises first, then optional ones, until the next set
     would no longer fit the workout length or each exercise reaches its goal.
Time per exercise counts setup, the sets themselves and the rest between them, so
a 45-minute workout really fits in 45 minutes.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models import Exercise, ExerciseKind, PlannedExercise, Programming, RepRange, Technique, Volume
from app.scheduler.exercises import (
    BACK_SQUAT,
    BARBELL_ROW,
    BENCH_PRESS,
    CABLE_CRUNCH,
    CABLE_FLY,
    CABLE_LATERAL_RAISE,
    CATALOG,
    CHEST_SUPPORTED_ROW,
    DB_SHOULDER_PRESS,
    DUMBBELL_RDL,
    EZ_BAR_CURL,
    HACK_SQUAT,
    HAMMER_CURL,
    HANGING_LEG_RAISE,
    HIP_THRUST,
    INCLINE_DB_CURL,
    INCLINE_DB_PRESS,
    LAT_PULLDOWN,
    LATERAL_RAISE,
    LEG_EXTENSION,
    LEG_PRESS,
    LYING_LEG_CURL,
    MACHINE_CHEST_PRESS,
    MACHINE_CRUNCH,
    OVERHEAD_PRESS,
    OVERHEAD_TRICEPS,
    PULL_UP,
    ROMANIAN_DEADLIFT,
    SEATED_CABLE_ROW,
    SEATED_CALF_RAISE,
    SEATED_LEG_CURL,
    SPLIT_SQUAT,
    STANDING_CALF_RAISE,
    STRAIGHT_ARM_PULLDOWN,
    TRICEPS_PUSHDOWN,
)

COMPOUND, ISOLATION = ExerciseKind.COMPOUND, ExerciseKind.ISOLATION
DEFAULT_PROGRAMMING = Programming()


@dataclass(frozen=True)
class Slot:
    options: tuple[Exercise, ...]  # best first
    required: bool = True


def need(*options: Exercise) -> Slot:
    return Slot(options)


def extra(*options: Exercise) -> Slot:
    return Slot(options, required=False)


@dataclass(frozen=True)
class SessionTemplate:
    name: str
    focus: str  # short label for the workout card
    slots: tuple[Slot, ...]


# ------------------------------------------------------------- templates ---
# Pulldowns and loaded crunches come before pull-ups and hanging leg raises: their load
# can be set to anyone's strength, so they work as everyone's default.
# The three full-body workouts use different exercises for the same muscles, so
# two or three of them in a week add variety without leaving anything out.
FULL_BODY_A = SessionTemplate(
    "Full Body A",
    "Squat · Bench · Row",
    (
        need(BACK_SQUAT, LEG_PRESS),
        need(BENCH_PRESS, MACHINE_CHEST_PRESS),
        need(BARBELL_ROW, CHEST_SUPPORTED_ROW),
        need(ROMANIAN_DEADLIFT, DUMBBELL_RDL),
        need(LATERAL_RAISE),
        need(STANDING_CALF_RAISE),
        need(CABLE_CRUNCH),
        extra(EZ_BAR_CURL),
        extra(TRICEPS_PUSHDOWN),
    ),
)
FULL_BODY_B = SessionTemplate(
    "Full Body B",
    "Squat · Incline · Pulldown",
    (
        need(HACK_SQUAT, LEG_PRESS),
        need(INCLINE_DB_PRESS),
        need(LAT_PULLDOWN, PULL_UP),
        need(DUMBBELL_RDL),
        need(CABLE_LATERAL_RAISE, LATERAL_RAISE),
        need(SEATED_CALF_RAISE),
        need(MACHINE_CRUNCH, HANGING_LEG_RAISE),
        extra(OVERHEAD_TRICEPS),
        extra(HAMMER_CURL),
        extra(SEATED_LEG_CURL),
    ),
)
FULL_BODY_C = SessionTemplate(
    "Full Body C",
    "Lunge · Press · Hip thrust",
    (
        need(SPLIT_SQUAT, LEG_PRESS),
        need(MACHINE_CHEST_PRESS),
        need(SEATED_CABLE_ROW),
        need(HIP_THRUST),
        need(LATERAL_RAISE),
        need(STANDING_CALF_RAISE),
        need(CABLE_CRUNCH),
        extra(SEATED_LEG_CURL),
        extra(INCLINE_DB_CURL),
        extra(TRICEPS_PUSHDOWN),
    ),
)
PUSH = SessionTemplate(
    "Push",
    "Chest · Shoulders · Triceps · Core",
    (
        need(BENCH_PRESS, MACHINE_CHEST_PRESS),
        need(OVERHEAD_PRESS, DB_SHOULDER_PRESS),
        need(LATERAL_RAISE),
        need(TRICEPS_PUSHDOWN),
        need(CABLE_CRUNCH),
        extra(INCLINE_DB_PRESS),
        extra(CABLE_FLY),
        extra(OVERHEAD_TRICEPS),
        extra(MACHINE_CRUNCH, HANGING_LEG_RAISE),
    ),
)
PULL = SessionTemplate(
    "Pull",
    "Back · Biceps",
    (
        need(LAT_PULLDOWN, PULL_UP),
        need(BARBELL_ROW, CHEST_SUPPORTED_ROW),
        need(INCLINE_DB_CURL),
        extra(SEATED_CABLE_ROW),
        extra(HAMMER_CURL),
        extra(STRAIGHT_ARM_PULLDOWN),
    ),
)
LEGS = SessionTemplate(
    "Legs",
    "Quads · Hamstrings · Glutes · Calves",
    (
        need(BACK_SQUAT, LEG_PRESS),
        need(ROMANIAN_DEADLIFT, DUMBBELL_RDL),
        need(SEATED_LEG_CURL),
        need(STANDING_CALF_RAISE),
        extra(LEG_EXTENSION),
        extra(HIP_THRUST),
        extra(SEATED_CALF_RAISE),
    ),
)
UPPER = SessionTemplate(
    "Upper",
    "Chest · Back · Shoulders · Core",
    (
        need(INCLINE_DB_PRESS),
        need(CHEST_SUPPORTED_ROW),
        need(MACHINE_CHEST_PRESS),
        need(LAT_PULLDOWN),
        need(CABLE_LATERAL_RAISE, LATERAL_RAISE),
        need(MACHINE_CRUNCH, HANGING_LEG_RAISE),
        # Presses and rows already work the arms; direct arm work comes in when there's time.
        extra(EZ_BAR_CURL),
        extra(OVERHEAD_TRICEPS),
        extra(CABLE_CRUNCH),
    ),
)
LOWER = SessionTemplate(
    "Lower",
    "Quads · Hamstrings · Glutes · Calves",
    (
        need(HACK_SQUAT, LEG_PRESS),
        need(ROMANIAN_DEADLIFT, DUMBBELL_RDL),
        need(LYING_LEG_CURL),
        need(LEG_EXTENSION),
        need(SEATED_CALF_RAISE),
        extra(SPLIT_SQUAT),
        extra(HIP_THRUST),
        extra(STANDING_CALF_RAISE),
    ),
)

TEMPLATES = {t.name: t for t in (FULL_BODY_A, FULL_BODY_B, FULL_BODY_C, PUSH, PULL, LEGS, UPPER, LOWER)}

# -------------------------------------------------------------- timing ---
# Estimates in minutes. Rest uses the middle of the usual ranges: 2-3 min after
# compound sets, 1-2 min after isolation sets.
WARMUP_MINUTES = 8  # general warm-up plus a couple of lighter ramp-up sets
SETUP_MINUTES = 1.5  # loading a bar or adjusting a machine, moving between exercises
SET_MINUTES = 0.75  # about 40-50 seconds of lifting
REST_MINUTES = {COMPOUND: 2.5, ISOLATION: 1.5}
REST_LABELS = {COMPOUND: "2-3 min", ISOLATION: "1-2 min"}

MIN_SETS = 2
# Working sets per exercise, time allowing. Standard is 3, which puts most muscles
# around 10 or more sets a week with two sessions each.
SETS_GOAL = {Volume.LOWER: 2, Volume.STANDARD: 3, Volume.HIGHER: 4}
# Rep ranges when the user asks for heavier or lighter work; standard uses each exercise's own.
REPS = {
    RepRange.HEAVIER: {COMPOUND: "5-8", ISOLATION: "8-12"},
    RepRange.LIGHTER: {COMPOUND: "10-15", ISOLATION: "15-20"},
}
# How far from failure to stop: close enough to grow, without needing to reach failure.
EFFORT = (
    "Finish most sets 1-3 reps short of failure. When you reach the top of the rep range "
    "on every set, add a little weight next time."
)


def exercise_minutes(kind: ExerciseKind, sets: int) -> float:
    if sets <= 0:
        return 0.0
    return SETUP_MINUTES + sets * SET_MINUTES + (sets - 1) * REST_MINUTES[kind]


@dataclass(frozen=True)
class Session:
    """A template made concrete for one person: exercises, sets and estimated time."""

    name: str
    focus: str
    exercises: tuple[PlannedExercise, ...]
    minutes: int  # estimated, warm-up included
    muscle_groups: frozenset[str]  # trained directly
    left_out: tuple[str, ...] = ()  # muscles cut because the workout was too short


def choose(slot: Slot, programming: Programming = DEFAULT_PROGRAMMING) -> Exercise | None:
    """The exercise for this slot.

    The first option, skipping exercises the user swapped out and, when they asked for
    simpler exercises, preferring basic ones. If every option was swapped out, a similar
    exercise from the catalog stands in.
    """
    options = [e for e in slot.options if e.name not in programming.avoid]
    if programming.simpler_exercises:
        options.sort(key=lambda e: e.technique is Technique.TECHNICAL)  # stable: keeps the order otherwise
    if options:
        return options[0]
    return substitute(slot.options[0], programming)


def substitute(exercise: Exercise, programming: Programming) -> Exercise | None:
    """A different exercise for the same main muscles, as close in type as possible."""
    candidates = [
        e
        for e in CATALOG
        if e.primary == exercise.primary
        and e is not exercise
        and e.name not in programming.avoid
        and not (programming.simpler_exercises and e.technique is Technique.TECHNICAL)
    ]
    candidates.sort(key=lambda e: e.kind is not exercise.kind)
    return candidates[0] if candidates else None


def build_session(template: SessionTemplate, minutes: int, programming: Programming = DEFAULT_PROGRAMMING) -> Session:
    picked: list[tuple[Exercise, bool]] = []
    for slot in template.slots:
        exercise = choose(slot, programming)
        if exercise and all(exercise is not e for e, _ in picked):  # a stand-in may repeat another slot's
            picked.append((exercise, slot.required))
    goal = SETS_GOAL[programming.volume]
    sets = [MIN_SETS if required else 0 for _, required in picked]
    budget = minutes - WARMUP_MINUTES

    def used() -> float:
        return sum(exercise_minutes(ex.kind, n) for (ex, _), n in zip(picked, sets, strict=True))

    # Very short workouts: drop to one set per exercise (isolation work first) so every muscle
    # still gets trained. Exercises are only cut, from the end, if even that doesn't fit.
    for kind in (ISOLATION, COMPOUND):
        for i in reversed(range(len(sets))):
            if used() > budget and sets[i] and picked[i][0].kind is kind:
                sets[i] = 1
    for i in reversed(range(len(sets))):
        if used() > budget:
            sets[i] = 0

    def grow(indices: list[int]) -> None:
        added = True
        while added:
            added = False
            for i in indices:
                before = sets[i]
                if before >= goal:
                    continue
                sets[i] = before + 1 if before else min(MIN_SETS, goal)
                if used() > budget:
                    sets[i] = before
                else:
                    added = True

    grow([i for i, (_, required) in enumerate(picked) if required and sets[i]])
    grow([i for i, (_, required) in enumerate(picked) if not required])

    exercises = tuple(
        PlannedExercise(ex, n, reps_for(ex, programming), REST_LABELS[ex.kind])
        for (ex, _), n in zip(picked, sets, strict=True)
        if n
    )
    trained = frozenset(m for p in exercises for m in p.exercise.primary)
    left_out = tuple(dict.fromkeys(m for ex, req in picked if req for m in ex.primary if m not in trained))
    return Session(template.name, template.focus, exercises, round(WARMUP_MINUTES + used()), trained, left_out)


def reps_for(exercise: Exercise, programming: Programming = DEFAULT_PROGRAMMING) -> str:
    if programming.reps is RepRange.STANDARD:
        return exercise.reps
    return REPS[programming.reps][exercise.kind]
