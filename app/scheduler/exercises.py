"""The exercise catalog.

Each exercise lists the muscles it trains directly (primary) and the ones that help
(secondary). Weekly volume counts a set as 1 for primary muscles and 0.5 for
secondary ones. Equipment is recorded for a future "dumbbells only" or home-gym
option; for now every workout assumes a full gym.

Exercises marked technical take more skill to do well (free-weight squats, pull-ups,
barbell rows). Everyone gets the same default workouts; when someone asks the coach
for simpler exercises, a basic option for the same muscles is used instead.
"""

from __future__ import annotations

from app.models import Exercise, ExerciseKind, Technique

CHEST, BACK, SHOULDERS, BICEPS, TRICEPS = "chest", "back", "shoulders", "biceps", "triceps"
QUADS, HAMSTRINGS, GLUTES, CALVES, CORE = "quads", "hamstrings", "glutes", "calves", "core"

# Every muscle group the app programs and tracks, in display order.
MUSCLES = (CHEST, BACK, SHOULDERS, BICEPS, TRICEPS, QUADS, HAMSTRINGS, GLUTES, CALVES, CORE)

COMPOUND, ISOLATION = ExerciseKind.COMPOUND, ExerciseKind.ISOLATION
TECHNICAL = Technique.TECHNICAL

# ------------------------------------------------------------------ legs ---
BACK_SQUAT = Exercise("Back Squat", COMPOUND, (QUADS,), (GLUTES,), "6-10", "barbell", TECHNICAL)
SPLIT_SQUAT = Exercise("Bulgarian Split Squat", COMPOUND, (QUADS,), (GLUTES,), "8-12", "dumbbell", TECHNICAL)
LEG_PRESS = Exercise("Leg Press", COMPOUND, (QUADS,), (GLUTES,), "8-12", "machine")
HACK_SQUAT = Exercise("Hack Squat", COMPOUND, (QUADS,), (GLUTES,), "8-12", "machine")
ROMANIAN_DEADLIFT = Exercise("Romanian Deadlift", COMPOUND, (HAMSTRINGS, GLUTES), (), "6-10", "barbell", TECHNICAL)
DUMBBELL_RDL = Exercise("Dumbbell Romanian Deadlift", COMPOUND, (HAMSTRINGS, GLUTES), (), "8-12", "dumbbell")
HIP_THRUST = Exercise("Hip Thrust", COMPOUND, (GLUTES,), (HAMSTRINGS,), "8-12", "barbell")
LEG_EXTENSION = Exercise("Leg Extension", ISOLATION, (QUADS,), (), "10-15")
SEATED_LEG_CURL = Exercise("Seated Leg Curl", ISOLATION, (HAMSTRINGS,), (), "10-15")
LYING_LEG_CURL = Exercise("Lying Leg Curl", ISOLATION, (HAMSTRINGS,), (), "10-15")
STANDING_CALF_RAISE = Exercise("Standing Calf Raise", ISOLATION, (CALVES,), (), "10-15")
SEATED_CALF_RAISE = Exercise("Seated Calf Raise", ISOLATION, (CALVES,), (), "12-20")

# ----------------------------------------------------------------- push ---
BENCH_PRESS = Exercise("Bench Press", COMPOUND, (CHEST,), (TRICEPS, SHOULDERS), "6-10", "barbell", TECHNICAL)
MACHINE_CHEST_PRESS = Exercise("Machine Chest Press", COMPOUND, (CHEST,), (TRICEPS, SHOULDERS), "8-12")
INCLINE_DB_PRESS = Exercise("Incline Dumbbell Press", COMPOUND, (CHEST,), (SHOULDERS, TRICEPS), "8-12", "dumbbell")
OVERHEAD_PRESS = Exercise("Overhead Press", COMPOUND, (SHOULDERS,), (TRICEPS,), "6-10", "barbell", TECHNICAL)
DB_SHOULDER_PRESS = Exercise("Seated Dumbbell Shoulder Press", COMPOUND, (SHOULDERS,), (TRICEPS,), "8-12", "dumbbell")
CABLE_FLY = Exercise("Cable Fly", ISOLATION, (CHEST,), (), "12-15", "cable")
LATERAL_RAISE = Exercise("Lateral Raise", ISOLATION, (SHOULDERS,), (), "12-20", "dumbbell")
CABLE_LATERAL_RAISE = Exercise("Cable Lateral Raise", ISOLATION, (SHOULDERS,), (), "12-20", "cable")
TRICEPS_PUSHDOWN = Exercise("Triceps Pushdown", ISOLATION, (TRICEPS,), (), "10-15", "cable")
OVERHEAD_TRICEPS = Exercise("Overhead Cable Triceps Extension", ISOLATION, (TRICEPS,), (), "10-15", "cable")

# ----------------------------------------------------------------- pull ---
PULL_UP = Exercise("Pull-up", COMPOUND, (BACK,), (BICEPS,), "6-10", "bodyweight", TECHNICAL)
LAT_PULLDOWN = Exercise("Lat Pulldown", COMPOUND, (BACK,), (BICEPS,), "8-12", "cable")
BARBELL_ROW = Exercise("Barbell Row", COMPOUND, (BACK,), (BICEPS,), "6-10", "barbell", TECHNICAL)
CHEST_SUPPORTED_ROW = Exercise("Chest-Supported Row", COMPOUND, (BACK,), (BICEPS,), "8-12")
SEATED_CABLE_ROW = Exercise("Seated Cable Row", COMPOUND, (BACK,), (BICEPS,), "10-12", "cable")
STRAIGHT_ARM_PULLDOWN = Exercise("Straight-Arm Pulldown", ISOLATION, (BACK,), (), "12-15", "cable")
EZ_BAR_CURL = Exercise("EZ-Bar Curl", ISOLATION, (BICEPS,), (), "10-15", "barbell")
INCLINE_DB_CURL = Exercise("Incline Dumbbell Curl", ISOLATION, (BICEPS,), (), "10-15", "dumbbell")
HAMMER_CURL = Exercise("Hammer Curl", ISOLATION, (BICEPS,), (), "10-15", "dumbbell")

# ----------------------------------------------------------------- core ---
# Loaded movements, so core work can progress like any other muscle.
CABLE_CRUNCH = Exercise("Cable Crunch", ISOLATION, (CORE,), (), "10-15", "cable")
MACHINE_CRUNCH = Exercise("Machine Crunch", ISOLATION, (CORE,), (), "10-15")
HANGING_LEG_RAISE = Exercise("Hanging Leg Raise", ISOLATION, (CORE,), (), "10-15", "bodyweight", TECHNICAL)

# Every exercise above, for finding a substitute when the user asks to swap one.
CATALOG: tuple[Exercise, ...] = tuple(v for v in list(globals().values()) if isinstance(v, Exercise))
