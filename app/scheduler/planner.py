"""Build a 7-day muscle-building plan from free slots.

Training rules:
  * every major muscle is trained twice a week (splits that can't are fallbacks)
  * at least 48 hours between sessions that train the same muscle (hard rule),
    ideally 72 hours (the planner prefers it but will accept 48)
  * never more workouts than the commitment allows (1 to 5), however much
    free time there is; six only when the user asks for it

Steps:
  1. Find free slots for each day (``availability.find_free_slots``).
  2. If the user asked for a split style, try it first. Otherwise aim for
     min(commitment cap, free days) sessions with the recommended split.
  3. For each candidate split, search every way of placing its sessions on the
     free days and, on each day, a few possible start times (preferred, earliest,
     latest). That's at most C(7, k) * 3^k options (~5,000 for 6 sessions), small
     enough to check them all. Keep the best *valid* placement:
       - valid  = sessions sharing a muscle start at least 48 hours apart
       - best   = lowest score, adding up: uneven spacing across the week,
                  hours of rest short of the ideal 72, and hours away from the
                  preferred time of day
  4. If nothing fits, try fewer sessions. Splits that train every muscle twice
     are always tried before the ones that don't (e.g. 3 free days in a row:
     Full Body x3 can't get 48h of rest, so Full Body x2 on the 1st and 3rd day
     beats Push/Pull/Legs, which would train each muscle only once).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from itertools import combinations, pairwise, product
from statistics import pvariance
from zoneinfo import ZoneInfo

from app.formatting import clock, join_words
from app.models import (
    BusyBlock,
    FixedWorkout,
    Preferences,
    RecentWorkout,
    Slot,
    TimeOfDay,
    WeeklyPlan,
    Workout,
)
from app.scheduler.availability import find_free_slots, round_up
from app.scheduler.exercises import BACK, CALVES, CHEST, CORE, GLUTES, HAMSTRINGS, QUADS, SHOULDERS
from app.scheduler.sessions import TEMPLATES, Session, build_session
from app.scheduler.splits import MAX_DAYS, STYLE_LABELS, Split, recommended_splits, style_cap, style_splits
from app.scheduler.volume import WEEKLY_SET_TARGET, weekly_sets

PREFERRED_TARGET: dict[TimeOfDay, time | None] = {
    TimeOfDay.MORNING: time(7, 0),
    TimeOfDay.MIDDAY: time(12, 0),
    TimeOfDay.EVENING: time(18, 0),
    TimeOfDay.ANY: None,
}

# How much one hour away from the preferred time "costs" compared to uneven spacing.
TIME_PENALTY_PER_HOUR = 0.25

ONLY_FIXED = "Your workouts"  # the plan when nothing fits around the workouts the user placed
MIN_REST_HOURS = 48  # never train the same muscle again sooner than this
IDEAL_REST_HOURS = 72  # aim for this much rest
# Cost of each hour of rest short of the ideal (48h of rest costs 24 * 0.075 = 1.8), enough to
# lean clearly toward 72 hours, a conservative default for everyone.
REST_PENALTY_PER_HOUR = 0.075

# Muscles the "twice a week" preference checks. Biceps and triceps also get trained by
# every press and row, so they aren't required to have their own exercise twice.
TWICE_A_WEEK = (CHEST, BACK, SHOULDERS, QUADS, HAMSTRINGS, GLUTES, CALVES, CORE)


def start_options(day: date, slots: list[Slot], prefs: Preferences) -> list[tuple[datetime, float]]:
    """A few possible start times on ``day``: closest to the preferred time, earliest, latest.

    Returns (start, hours_away_from_preference) pairs, best first. Offering more than one
    time lets the planner shift a session (say, Wednesday evening instead of morning)
    to keep 48+ hours of rest between sessions for the same muscles.
    """
    duration = timedelta(minutes=prefs.session_minutes)
    target_time = PREFERRED_TARGET[prefs.preferred_time]
    options: dict[datetime, float] = {}

    for slot in slots:
        latest = slot.end - duration
        earliest = round_up(slot.start) if round_up(slot.start) <= latest else slot.start
        latest_rounded = latest.replace(minute=latest.minute - latest.minute % 15, second=0, microsecond=0)
        if latest_rounded < earliest:
            latest_rounded = latest
        if target_time is None:  # no preference: earliest is best
            target = slot.start
            preferred = earliest
        else:
            target = datetime.combine(day, target_time, tzinfo=slot.start.tzinfo)
            preferred = min(max(target, slot.start), latest)  # clamp into the slot
            rounded = round_up(preferred)
            preferred = rounded if rounded <= latest else preferred
        for candidate in (preferred, earliest, latest_rounded):
            options[candidate] = abs((candidate - target).total_seconds()) / 3600

    closest = min(options.items(), key=lambda kv: (kv[1], kv[0]))
    by_time = sorted(options.items())
    return list(dict.fromkeys([closest, by_time[0], by_time[-1]]))  # without repeats, best first


def rest_hours(first: datetime, second: datetime) -> float:
    """Real hours between the starts of two sessions.

    Converting to UTC matters: Python subtracts two times in the same zone by wall clock,
    so across a daylight-saving change it would be an hour off.
    """
    return abs((second.astimezone(UTC) - first.astimezone(UTC)).total_seconds()) / 3600


def trains_every_muscle_twice(sessions: tuple[Session, ...]) -> bool:
    return all(sum(muscle in s.muscle_groups for s in sessions) >= 2 for muscle in TWICE_A_WEEK)


def rest_penalty(
    sessions: tuple[Session, ...],
    starts: tuple[datetime, ...],
    recent: tuple[RecentWorkout, ...] = (),
) -> float | None:
    """None if two sessions training the same muscle are under 48 hours apart, else the cost of
    every hour of rest short of the ideal 72. ``recent`` workouts from before the plan count too."""
    pairs = [
        (a.muscle_groups, starts[i], b.muscle_groups, starts[j])
        for i, a in enumerate(sessions)
        for j, b in enumerate(sessions)
        if i < j
    ]
    pairs += [
        (r.muscle_groups, r.start, s.muscle_groups, start)
        for r in recent
        for s, start in zip(sessions, starts, strict=True)
    ]
    cost = 0.0
    for first_muscles, first_start, second_muscles, second_start in pairs:
        if first_muscles & second_muscles:
            gap = rest_hours(first_start, second_start)
            if gap < MIN_REST_HOURS:
                return None
            cost += max(0.0, IDEAL_REST_HOURS - gap) * REST_PENALTY_PER_HOUR
    return cost


def spacing_penalty(days: tuple[date, ...]) -> float:
    """Variance of the gaps between sessions, including the wrap to the next week.

    Mon/Thu -> gaps [3, 4] -> low penalty. Mon/Tue -> gaps [1, 6] -> high penalty.
    """
    if len(days) < 2:
        return 0.0
    gaps = [(b - a).days for a, b in pairwise(days)]
    gaps.append(7 - (days[-1] - days[0]).days)
    return pvariance(gaps)


def place_split(
    sessions: tuple[Session, ...],
    free_days: list[date],
    slots_by_day: dict[date, list[Slot]],
    prefs: Preferences,
    recent: tuple[RecentWorkout, ...] = (),
) -> list[tuple[Session, datetime]] | None:
    """Best valid placement of ``sessions`` onto ``free_days``, or None if impossible."""
    best_score: float | None = None
    best_starts: tuple[datetime, ...] | None = None
    options = {d: start_options(d, slots_by_day[d], prefs) for d in free_days}

    for days in combinations(free_days, len(sessions)):  # already in date order
        spacing = spacing_penalty(days)
        for choice in product(*(options[d] for d in days)):
            starts = tuple(start for start, _ in choice)
            rest = rest_penalty(sessions, starts, recent)
            if rest is None:
                continue
            time_cost = sum(hours_away for _, hours_away in choice) * TIME_PENALTY_PER_HOUR
            score = spacing + rest + time_cost
            if best_score is None or score < best_score:
                best_score, best_starts = score, starts

    if best_starts is None:
        return None
    return list(zip(sessions, best_starts, strict=True))


def _make_plan(
    week_start: date,
    split: Split,
    fitted: Fitted,
    free_days: list[date],
    prefs: Preferences,
    notes: list[str],
) -> WeeklyPlan:
    duration = timedelta(minutes=prefs.session_minutes)
    pinned, placement = fitted
    workouts = [
        Workout(s.name, s.muscle_groups, s.exercises, start, start + duration, s.focus, s.minutes)
        for s, start in placement
    ] + [Workout(s.name, s.muscle_groups, s.exercises, f.start, f.end, s.focus, s.minutes, True) for s, f in pinned]
    workouts.sort(key=lambda w: w.start)
    left_out = list(dict.fromkeys(m for s, _ in placement for m in s.left_out))
    if left_out:
        notes.append(
            f"{prefs.session_minutes}-minute workouts are too short to fit everything, so {join_words(left_out)} "
            "didn't make it in. Longer workouts would cover every muscle."
        )
    rests = [
        rest_hours(a.start, b.start)
        for i, a in enumerate(workouts)
        for b in workouts[i + 1 :]
        if a.muscle_groups & b.muscle_groups
    ]
    if rests:
        notes.append(f"Shortest rest for any muscle: {min(rests):.0f} hours (at least 48, ideally 72).")
    notes += _volume_notes(workouts, prefs)
    return WeeklyPlan(week_start, split.name, workouts, free_days, notes)


def _volume_notes(workouts: list[Workout], prefs: Preferences) -> list[str]:
    """Say so when the week can't reach the starting volume target, instead of hiding it."""
    low, _ = WEEKLY_SET_TARGET
    below = [muscle for muscle, sets in weekly_sets(workouts).items() if sets < low]
    if not below:
        return []
    which = "most muscles" if len(below) > 5 else join_words(below)
    return [
        f"With {len(workouts)} workout(s) of {prefs.session_minutes} minutes, {which} get fewer than {low} "
        "weekly sets (about 10 or more is associated with more growth). "
        "Longer workouts or another training day would add more."
    ]


def _back_to_back(starts: list[datetime]) -> bool:
    return any((b.date() - a.date()).days == 1 for a, b in pairwise(sorted(starts)))


def _why_this_split(
    split: Split, twice: bool, free_count: int, prefs: Preferences, target: int, starts: list[datetime]
) -> list[str]:
    """Plain-language reasons for a recommended split (shown in the plan and used by the coach)."""
    cap = MAX_DAYS[prefs.commitment]
    reasons = []
    if not twice:
        if _back_to_back(starts):
            reasons.append(
                f"Since your available days are back-to-back, I chose {split.name} instead of full-body workouts. "
                "This avoids training the same muscles on consecutive days."
            )
        else:
            reasons.append(
                f"Your free time only fits training each muscle once this week, so {split.name} is the best fit."
            )
    elif split.name == "Full Body A/B":
        reasons.append(
            "You have two workout days, so I chose two full-body workouts. This trains your major muscles twice "
            "this week instead of only once with an Upper/Lower split."
        )
    elif split.name == "PPL + Upper/Lower" and free_count > split.days:
        reasons.append(
            f"You have {free_count} days available, but I scheduled 5 workouts using Push/Pull/Legs + Upper/Lower. "
            "This gives you two rest days while still training your major muscles twice."
        )
    elif free_count > cap:
        reasons.append(
            f"Your {prefs.commitment.value} setting allows up to {cap} workouts a week, "
            "so your other free days stay rest days."
        )
    if split.days < target and twice:
        reasons.append(
            f"I planned {split.days} workouts instead of {target} so every muscle gets at least 48 hours of rest."
        )
    return reasons


def _build(split: Split, prefs: Preferences) -> tuple[Session, ...]:
    return tuple(build_session(t, prefs.session_minutes, prefs.programming) for t in split.sessions)


def sessions_by_name(prefs: Preferences) -> dict[str, Session]:
    """Every workout template built for this person, by name."""
    return {t.name: build_session(t, prefs.session_minutes, prefs.programming) for t in TEMPLATES.values()}


# Workouts the user fixed (with their sessions), and where the planner placed the rest.
Fitted = tuple[list[tuple[Session, FixedWorkout]], list[tuple[Session, datetime]]]


def _fit(
    split: Split,
    prefs: Preferences,
    fixed: tuple[FixedWorkout, ...],
    free_days: list[date],
    slots_by_day: dict[date, list[Slot]],
    recent: tuple[RecentWorkout, ...],
) -> Fitted | None:
    """Place a split around the workouts the user fixed, or None if it doesn't fit.

    Each fixed workout stands in for the split's session of the same name (a moved Legs
    is this week's Legs). The other sessions are placed with 48+ hours of rest from it.
    A workout that already happened stays even if this split has no session by its name.
    """
    sessions = list(_build(split, prefs))
    pinned = []
    for workout in fixed:
        match = next((s for s in sessions if s.name == workout.name), None)
        if match is not None:
            sessions.remove(match)
        elif workout.kind == "done":
            match = sessions_by_name(prefs)[workout.name]
        else:
            return None
        pinned.append((match, workout))
    around = recent + tuple(RecentWorkout(s.name, w.start, s.muscle_groups) for s, w in pinned)
    placement = place_split(tuple(sessions), free_days, slots_by_day, prefs, around)
    return None if placement is None else (pinned, placement)


def _fixed_conflicts(
    fixed: tuple[FixedWorkout, ...], recent: tuple[RecentWorkout, ...], prefs: Preferences
) -> list[str]:
    """Warnings when a workout the user moved breaks the 48-hour rule. The app never undoes it."""
    sessions = sessions_by_name(prefs)
    known = [(w, sessions[w.name].muscle_groups) for w in fixed if w.name in sessions]
    others = [(w.name, w.start, m) for w, m in known] + [(r.name, r.start, r.muscle_groups) for r in recent]
    warnings = []
    for workout, muscles in known:
        if workout.kind != "moved":
            continue
        for other, other_start, other_muscles in others:
            shared = muscles & other_muscles
            gap = rest_hours(workout.start, other_start)
            if other_start < workout.start and shared and gap < MIN_REST_HOURS:
                warnings.append(
                    f"Heads up: {workout.name} on {_when(workout.start)} is only {gap:.0f} hours after "
                    f"{other} ({other_start.strftime('%A')}). That's under the 48-hour minimum for "
                    f"{join_words(sorted(shared))}. Move one of them to give those muscles time to recover."
                )
    return warnings


def _when(moment: datetime) -> str:
    return f"{moment:%A} at {clock(moment)}"


def _availability_conflicts(
    fixed: tuple[FixedWorkout, ...], busy: list[BusyBlock], prefs: Preferences, now: datetime | None
) -> list[str]:
    """Warnings when a workout the user placed no longer fits their calendar or settings.

    It stays where they put it (their choice wins); the warning says what's wrong so they
    can decide. This happens after a commitment is added or the travel buffer grows.
    """
    buffer = timedelta(minutes=prefs.buffer_minutes)
    tz = ZoneInfo(prefs.timezone)
    warnings = []
    for w in fixed:
        if w.kind != "moved" or (now is not None and w.end <= now):
            continue
        start, end = w.start.astimezone(tz), w.end.astimezone(tz)
        overlap = next((b for b in busy if start < b.end and end > b.start), None)
        near = next((b for b in busy if start < b.end + buffer and end > b.start - buffer), None)
        if overlap:
            what = "something on your calendar" if overlap.title == "Busy" else overlap.title
            warnings.append(f"Heads up: {w.name} on {_when(start)} overlaps {what}. Drag it to a free time.")
        elif near:
            what = "something on your calendar" if near.title == "Busy" else near.title
            warnings.append(
                f"Heads up: {w.name} on {_when(start)} leaves less than your {prefs.buffer_minutes}-minute travel "
                f"buffer around {what}. Drag it if you need the time to get there."
            )
        elif start.weekday() in prefs.blocked_weekdays:
            warnings.append(f"Heads up: {w.name} is on {start.strftime('%A')}, a day you keep free from training.")
        elif start.time() < prefs.earliest_start or end.time() > prefs.latest_end or end.date() != start.date():
            warnings.append(f"Heads up: {w.name} on {_when(start)} is outside the hours you said you can train.")
    return warnings


def build_plan(
    busy: list[BusyBlock],
    week_start: date,
    prefs: Preferences,
    now: datetime | None = None,
    recent: list[RecentWorkout] | tuple[RecentWorkout, ...] = (),
    fixed: list[FixedWorkout] | tuple[FixedWorkout, ...] = (),
) -> WeeklyPlan:
    """Main entry point: busy calendar + preferences -> weekly workout plan.

    ``recent`` holds workouts from just before the plan (say, Sunday's Legs when the new
    week starts Monday) so the 48-hour rule still holds across the boundary. ``fixed``
    holds workouts that can't move: ones the user moved (in the app or in Google
    Calendar) and ones that already started. Moved workouts stay where the user put
    them, and the plan warns if that breaks recovery instead of moving them back.
    """
    recent = tuple(recent)
    fixed = tuple(w for w in fixed if w.name in TEMPLATES)
    plan = _plan_week(busy, week_start, prefs, now, recent, fixed)
    moved = [w for w in fixed if w.kind == "moved"]
    if plan.split_name == ONLY_FIXED and moved:
        # No split fits around the moved workouts (say the split or commitment changed): plan the
        # week without those moves rather than keep a week of only them, and say so.
        retry = _plan_week(busy, week_start, prefs, now, recent, tuple(w for w in fixed if w.kind == "done"))
        if retry.workouts:
            names = join_words([f"{w.name} on {_when(w.start)}" for w in moved])
            retry.warnings.append(
                f"You'd moved {names}, but that doesn't fit your current settings, so this week was planned "
                "without the move. Drag a workout again to place it yourself."
            )
            retry.dropped = moved
            plan, fixed = retry, tuple(w for w in fixed if w.kind == "done")
    plan.warnings = (
        _fixed_conflicts(fixed, recent, prefs) + _availability_conflicts(fixed, busy, prefs, now) + plan.warnings
    )
    return plan


def _plan_week(
    busy: list[BusyBlock],
    week_start: date,
    prefs: Preferences,
    now: datetime | None,
    recent: tuple[RecentWorkout, ...],
    fixed: tuple[FixedWorkout, ...],
) -> WeeklyPlan:
    fixed_days = {w.start.date() for w in fixed}
    slots_by_day = find_free_slots(busy, week_start, prefs, now)
    free_days = [d for d in sorted(slots_by_day) if slots_by_day[d] and d not in fixed_days]
    notes = [
        f"You have {len(free_days)} day(s) this week with room for a {prefs.session_minutes}-minute workout "
        f"(plus {prefs.buffer_minutes} min travel buffer)."
    ]
    if recent:
        names = join_words([f"{r.name} ({r.start.strftime('%a')})" for r in recent])
        notes.append(f"Your recent {names} still count toward recovery, so those muscles get 48+ hours first.")
    for w in fixed:
        if w.kind == "done":
            notes.append(
                f"{w.name} on {w.start.strftime('%A')} already started, so the rest of the week plans around it."
            )
        else:
            notes.append(f"Kept {w.name} on {_when(w.start)}, where you put it.")

    def fit(split: Split) -> Fitted | None:
        return _fit(split, prefs, fixed, free_days, slots_by_day, recent)

    # A split the user asked for comes first, as long as it fits with 48+ hours of rest.
    if prefs.requested_split is not None:
        label = STYLE_LABELS[prefs.requested_split]
        limit = min(style_cap(prefs.requested_split, prefs.commitment), len(free_days) + len(fixed))
        for split in style_splits(prefs.requested_split):
            if split.days > limit or (fitted := fit(split)) is None:
                continue
            notes.append(f"Using the split you asked for: {label}.")
            return _make_plan(week_start, split, fitted, free_days, prefs, notes)
        notes.append(
            f"{label} doesn't fit your free days this week with 48+ hours of rest between sessions "
            "for the same muscles, so I used the recommended split."
        )

    target = min(MAX_DAYS[prefs.commitment], len(free_days) + len(fixed))
    # Pass 1: only splits that train every muscle twice. Pass 2 (fallback): anything that fits.
    for twice_only in (True, False):
        for days in range(target, max(len(fixed), 1) - 1, -1):
            for split in recommended_splits(days):
                if trains_every_muscle_twice(_build(split, prefs)) != twice_only or (fitted := fit(split)) is None:
                    continue
                starts = [start for _, start in fitted[1]] + [w.start for _, w in fitted[0]]
                notes += _why_this_split(split, twice_only, len(free_days) + len(fixed), prefs, target, starts)
                return _make_plan(week_start, split, fitted, free_days, prefs, notes)

    if fixed:  # no split matches the moved workouts: keep them and plan nothing else around them
        sessions = sessions_by_name(prefs)
        pinned = [(sessions[w.name], w) for w in fixed]
        return _make_plan(week_start, Split(ONLY_FIXED, ()), (pinned, []), free_days, prefs, notes)
    notes.append("There isn't a free window long enough for a workout this week.")
    return WeeklyPlan(week_start, "Rest week", [], free_days, notes)


def local_now(prefs: Preferences) -> datetime:
    return datetime.now(ZoneInfo(prefs.timezone))
