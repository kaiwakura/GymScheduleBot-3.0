"""Planning any calendar week from everything the user gave us.

The web routes collect the inputs (settings, the browser's commitments, history and moved
workouts, Google availability); this module turns them into a plan. Weeks run Monday to Sunday.

  * Past weeks are read-only: they show what was planned, from the browser's history.
  * The current week keeps workouts that already started and plans the rest around them.
  * Later weeks are planned in order from the current one, so recovery carries across
    each week boundary (Sunday's Legs still counts on Monday). For weeks far ahead, the
    chain starts a few weeks earlier instead of at today, which keeps the work bounded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app import training_history, workout_moves
from app.ai.chat_changes import MoveRequest
from app.formatting import clock
from app.models import BusyBlock, FixedWorkout, RecentWorkout, WeeklyPlan, Workout
from app.scheduler.availability import find_free_slots
from app.scheduler.planner import (
    IDEAL_REST_HOURS,
    MIN_REST_HOURS,
    build_plan,
    rest_hours,
    sessions_by_name,
    start_options,
)
from app.settings import Settings, week_start
from app.sources import custom

MAX_CHAIN = 3  # weeks planned in order before the one asked for
PAST_WEEK = "Past week"
SAME_TIME = timedelta(minutes=5)


@dataclass
class Inputs:
    """Everything a plan depends on, for one request."""

    settings: Settings
    events: list[dict]
    history: list[dict]
    moves: list[dict]
    now: datetime
    google: object | None = None  # google_integration.GoogleData; kept loose so this module never imports Google
    memo: dict[date, WeeklyPlan] = field(default_factory=dict)

    @property
    def current_week(self) -> date:
        return week_start(self.now.date())

    @property
    def demo(self) -> bool:
        return custom.is_sample(self.events)


def google_window(current: date, week: date, timezone: str) -> tuple[datetime, datetime]:
    """The span of Google availability needed to plan ``week`` (and the weeks chained before it)."""
    first = max(current, week - timedelta(weeks=MAX_CHAIN)) if week >= current else week
    tz = ZoneInfo(timezone)
    return datetime.combine(first, time.min, tzinfo=tz), datetime.combine(week + timedelta(days=7), time.min, tzinfo=tz)


def busy_for(inputs: Inputs, week: date) -> list[BusyBlock]:
    """The week's busy time: the user's own commitments plus Google Calendar (unless exploring a sample)."""
    tz = ZoneInfo(inputs.settings.timezone)
    start = datetime.combine(week, time.min, tzinfo=tz)
    end = start + timedelta(days=7)
    busy = custom.busy_blocks(inputs.events, week, inputs.settings.timezone)
    if inputs.google is not None and not inputs.demo:
        busy += [b for b in inputs.google.busy if b.end > start and b.start < end]
    return busy


def plan_for(inputs: Inputs, week: date, depth: int = 0) -> WeeklyPlan:
    """The plan for the week starting on Monday ``week``."""
    if week in inputs.memo:
        return inputs.memo[week]
    current = inputs.current_week
    if week < current:
        plan = _past_plan(inputs, week)
    elif week == current:
        recent = training_history.recent_workouts(inputs.history, inputs.now) + _google_recent(inputs)
        plan = _plan(inputs, week, _one_per_start(recent), _done_this_week(inputs, week))
    else:
        previous = week - timedelta(days=7)
        chained = depth < MAX_CHAIN and previous >= current
        before = plan_for(inputs, previous, depth + 1) if chained else None
        recent = _tail(before, week) if before else []
        plan = _plan(inputs, week, recent, [])
    inputs.memo[week] = plan
    return plan


def _plan(inputs: Inputs, week: date, recent: list[RecentWorkout], done: list[FixedWorkout]) -> WeeklyPlan:
    prefs = inputs.settings.to_preferences(week)
    fixed = done + _google_moved(inputs, week) + workout_moves.for_week(inputs.moves, week, prefs.session_minutes)
    now = inputs.now if week == inputs.current_week else None
    return build_plan(busy_for(inputs, week), week, prefs, now=now, recent=recent, fixed=_one_per_start(fixed))


def _tail(plan: WeeklyPlan, week: date) -> list[RecentWorkout]:
    """The end of the previous week's plan that still needs recovery when ``week`` starts."""
    start = datetime.combine(week, time.min, tzinfo=plan.workouts[0].start.tzinfo) if plan.workouts else None
    return [
        RecentWorkout(w.name, w.start, w.muscle_groups)
        for w in plan.workouts
        if start and start - w.start <= training_history.RECOVERY_WINDOW
    ]


def _one_per_start(workouts: list) -> list:
    """The same workout can come from two places (browser history and Google): keep it once."""
    unique = []
    for workout in workouts:
        if not any(abs(workout.start - other.start) < SAME_TIME for other in unique):
            unique.append(workout)
    return unique


def _in_week(moment: datetime, week: date) -> bool:
    return week <= moment.date() < week + timedelta(days=7)


def _done_this_week(inputs: Inputs, week: date) -> list[FixedWorkout]:
    """Workouts this week that already started: they count toward the week and stay put."""
    minutes = timedelta(minutes=inputs.settings.session_minutes)
    done = [
        FixedWorkout(e["name"], start, start + minutes, "done")
        for e in inputs.history
        if e["status"] != "skipped"
        and (start := datetime.fromisoformat(e["start"]).astimezone(inputs.now.tzinfo)) < inputs.now
        and _in_week(start, week)
    ]
    if inputs.google is not None and not inputs.demo:
        done += [
            FixedWorkout(w.name, w.start, w.end, "done")
            for w in inputs.google.state.recent
            if _in_week(w.start, week) and not _skipped_at(inputs, w.start)
        ]
    return done


def _skipped_at(inputs: Inputs, moment: datetime) -> bool:
    return any(
        e["status"] == "skipped" and abs(datetime.fromisoformat(e["start"]) - moment) < SAME_TIME
        for e in inputs.history
    )


def _google_recent(inputs: Inputs) -> list[RecentWorkout]:
    if inputs.google is None or inputs.demo:
        return []
    sessions = sessions_by_name(inputs.settings.to_preferences())
    return [
        RecentWorkout(w.name, w.start, sessions[w.name].muscle_groups)
        for w in inputs.google.state.recent
        if w.name in sessions
        and inputs.now - training_history.RECOVERY_WINDOW <= w.start <= inputs.now
        and not _skipped_at(inputs, w.start)
    ]


def _google_moved(inputs: Inputs, week: date) -> list[FixedWorkout]:
    """Workouts the user moved in Google Calendar: their time wins, like a move in the app."""
    if inputs.google is None or inputs.demo:
        return []
    return [
        FixedWorkout(w.name, w.start, w.end, "moved") for w in inputs.google.state.pinned if _in_week(w.start, week)
    ]


def _past_plan(inputs: Inputs, week: date) -> WeeklyPlan:
    """A past week, read-only: the workouts that were planned, with skipped ones marked."""
    prefs = inputs.settings.to_preferences(week)
    sessions = sessions_by_name(prefs)
    minutes = timedelta(minutes=prefs.session_minutes)
    workouts: list[Workout] = []
    for entry in inputs.history:
        start = datetime.fromisoformat(entry["start"]).astimezone(ZoneInfo(prefs.timezone))
        session = sessions.get(entry["name"])
        if session is None or not _in_week(start, week) or any(abs(w.start - start) < SAME_TIME for w in workouts):
            continue
        workouts.append(
            Workout(
                session.name, session.muscle_groups, session.exercises, start, start + minutes,
                session.focus, session.minutes, pinned=True, skipped=entry["status"] == "skipped",
            )
        )  # fmt: skip
    workouts.sort(key=lambda w: w.start)
    note = (
        "This week has passed. Workouts are shown as they were planned."
        if workouts
        else ("This week has passed. GymScheduleBot keeps about 8 weeks of planned workouts in this browser.")
    )
    return WeeklyPlan(week, PAST_WEEK, workouts, [], [note])


# ------------------------------------------------------------ other times ---
@dataclass(frozen=True)
class OtherTime:
    start: datetime
    note: str  # e.g. "72 h after Mon Upper"


def other_times(inputs: Inputs, plan: WeeklyPlan, workout: Workout, limit: int = 3) -> list[OtherTime]:
    """Better or equally good times for ``workout`` this week, best first, one per day.

    A time qualifies when it's free in the user's calendar (travel buffer included), on a day
    with no other workout, and leaves at least 48 hours before and after every workout that
    trains the same muscles, including the end of last week and the start of next week.
    """
    week = plan.week_start
    if plan.split_name == PAST_WEEK or workout.start < inputs.now:
        return []
    prefs = inputs.settings.to_preferences(week)
    slots = _free_slots(inputs, week)
    same = _same_muscles(inputs, plan, workout)
    busy_days = {w.day for w in plan.workouts if w is not workout and not w.skipped}

    found = []
    for day, day_slots in slots.items():
        if day in busy_days or not day_slots:
            continue
        best = _best_start(day, day_slots, prefs, workout, same)
        if best:
            found.append(best)
    found.sort(key=lambda f: (f[0], abs((f[1].date() - workout.day).days)))
    return [OtherTime(start, _rest_note(start, nearest)) for _, start, nearest in found[:limit]]


def _free_slots(inputs: Inputs, week: date) -> dict:
    prefs = inputs.settings.to_preferences(week)
    now = inputs.now if week == inputs.current_week else None
    return find_free_slots(busy_for(inputs, week), week, prefs, now)


def _same_muscles(inputs: Inputs, plan: WeeklyPlan, workout: Workout) -> list[Workout | RecentWorkout]:
    """Every other workout near this week that trains any of the same muscles."""
    week = plan.week_start
    others: list[Workout | RecentWorkout] = [w for w in plan.workouts if w is not workout and not w.skipped]
    if week > inputs.current_week:
        others += plan_for(inputs, week - timedelta(days=7)).workouts
    else:
        others += training_history.recent_workouts(inputs.history, inputs.now) + _google_recent(inputs)
    others += plan_for(inputs, week + timedelta(days=7)).workouts
    return [w for w in others if w.muscle_groups & workout.muscle_groups and abs(w.start - workout.start) >= SAME_TIME]


def _gaps(start: datetime, same: list) -> list[tuple[float, Workout | RecentWorkout]]:
    return sorted(((rest_hours(start, w.start), w) for w in same), key=lambda g: g[0])


def _best_start(day: date, day_slots: list, prefs, workout: Workout, same: list):
    """(score, start, nearest same-muscle workout) for the best start on ``day``, or None."""
    best = None
    for start, hours_away in start_options(day, day_slots, prefs):
        if abs(start - workout.start) < SAME_TIME:
            continue
        gaps = _gaps(start, same)
        if gaps and gaps[0][0] < MIN_REST_HOURS:
            continue
        score = (gaps[0][0] < IDEAL_REST_HOURS if gaps else False, hours_away)
        if best is None or score < best[0]:
            best = (score, start, gaps[0] if gaps else None)
    return best


def resolve_move(inputs: Inputs, plan: WeeklyPlan, request: MoveRequest) -> tuple[dict | None, str | None]:
    """Turn "move Thursday's workout to Friday morning" into a move, or explain why it can't happen.

    Returns (move, None) or (None, reason). The target has to be free (travel buffer
    included) and leave 48 hours between sessions for the same muscles.
    """
    week = plan.week_start
    upcoming = [w for w in plan.workouts if w.start >= inputs.now and not w.skipped]
    if request.from_weekday is not None:
        matches = [w for w in upcoming if w.start.weekday() == request.from_weekday]
    elif request.workout:
        matches = [w for w in upcoming if w.name.lower() == request.workout.lower()]
    else:
        matches = upcoming if len(upcoming) == 1 else []
    if not matches:
        if request.from_weekday is None and not request.workout:
            return None, "Which workout should I move? Say the day it's on, like \"move Thursday's workout to Friday\"."
        return None, "I don't see an upcoming workout like that this week."
    workout = matches[0]
    day = week + timedelta(days=request.to_weekday)
    day_name = day.strftime("%A")
    if day < inputs.now.date():
        return None, f"{day_name} has already passed this week."
    if any(w.day == day and w is not workout for w in upcoming):
        return None, f"{day_name} already has a workout. I keep it to one a day so each one gets your full effort."
    prefs = inputs.settings.to_preferences(week)
    if request.part_of_day is not None:
        prefs.preferred_time = request.part_of_day
    day_slots = _free_slots(inputs, week).get(day, [])
    if not day_slots:
        return None, f"{day_name} doesn't have a free {prefs.session_minutes}-minute window with your travel buffer."
    same = _same_muscles(inputs, plan, workout)
    if request.at is not None:
        start = datetime.combine(day, request.at, tzinfo=ZoneInfo(prefs.timezone))
        end = start + timedelta(minutes=prefs.session_minutes)
        if not any(slot.start <= start and end <= slot.end for slot in day_slots):
            return None, f"{day_name} at {clock(start)} isn't free once your travel buffer is counted."
        nearest = _gaps(start, same)[:1]
    else:
        best = _best_start(day, day_slots, prefs, workout, same)
        if best is None:
            nearest = _gaps(day_slots[0].start, same)[:1]
            start = None
        else:
            _, start, near = best
            nearest = [near] if near else []
    if start is None or (nearest and nearest[0][0] < MIN_REST_HOURS):
        hours, other = nearest[0] if nearest else (0, workout)
        shared = ", ".join(sorted(workout.muscle_groups & other.muscle_groups)) or "the same muscles"
        return None, (
            f"{day_name} would leave only {hours:.0f} hours after {other.start.strftime('%A')}'s {other.name}, "
            f"under the 48-hour minimum for {shared}. Open the workout to see times that work."
        )
    move = {
        "week": week.isoformat(),
        "name": workout.name,
        "from": workout.start.isoformat(),
        "start": start.isoformat(),
    }
    return move, None


def _rest_note(start: datetime, nearest: tuple[float, Workout | RecentWorkout] | None) -> str:
    if nearest is None:
        return "plenty of recovery"
    hours, other = nearest
    side = "after" if other.start < start else "before"
    return f"{hours:.0f} h {side} {other.start.strftime('%a')} {other.name}"


def find_workout(plan: WeeklyPlan, start: datetime) -> Workout | None:
    return next((w for w in plan.workouts if abs(w.start - start) < SAME_TIME), None)
