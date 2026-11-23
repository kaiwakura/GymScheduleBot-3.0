"""Turn a week's plan into what the page shows: a calendar grid, one column per day.

Like Google Calendar, each day is a column of hours. Commitments and workouts are
placed at their real times: ``top`` and ``height`` are percentages of the column, so
the CSS just positions them. Overlapping blocks share the width side by side
(``left`` / ``width``). Pure functions, no I/O.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.formatting import clock
from app.models import BusyBlock, Preferences, WeeklyPlan, Workout
from app.planning import PAST_WEEK, Inputs, busy_for, other_times
from app.scheduler.exercises import MUSCLES
from app.scheduler.sessions import EFFORT
from app.scheduler.volume import WEEKLY_SET_TARGET, weekly_sets


def short_clock(dt: datetime) -> str:
    """7 AM or 7:30 AM: compact, for suggested times."""
    return clock(dt).replace(":00", "")


def time_range(start: datetime, end: datetime) -> str:
    """5:00 – 6:00 PM, or 11:30 AM – 12:30 PM when it crosses noon."""
    first, last = clock(start), clock(end)
    return f"{first[:-3]} – {last}" if first[-2:] == last[-2:] else f"{first} – {last}"


def week_label(week: date) -> str:
    """Nov 23–29, 2026 · Nov 30 – Dec 6, 2026 · Dec 28, 2026 – Jan 3, 2027."""
    end = week + timedelta(days=6)
    if week.year != end.year:
        return f"{week:%b} {week.day}, {week.year} – {end:%b} {end.day}, {end.year}"
    if week.month != end.month:
        return f"{week:%b} {week.day} – {end:%b} {end.day}, {end.year}"
    return f"{week:%b} {week.day}–{end.day}, {end.year}"


def slug(name: str) -> str:
    """CSS class for a workout's color: "Full Body B" -> "full-body"."""
    return name.removesuffix(" A").removesuffix(" B").removesuffix(" C").lower().replace(" ", "-")


def grid_hours(prefs: Preferences) -> tuple[int, int]:
    """First and last hour shown in the grid: 6 AM to 10 PM, widened to fit the user's window."""
    first = min(6, prefs.earliest_start.hour)
    last = max(22, prefs.latest_end.hour + (1 if prefs.latest_end.minute else 0))
    return first, min(last, 24)


def _position(start: datetime, end: datetime, grid_start: datetime, grid_end: datetime) -> dict | None:
    """Top/height in % of the column, or None if the block is outside the visible hours."""
    start, end = max(start, grid_start), min(end, grid_end)
    if start >= end:
        return None
    total = (grid_end - grid_start).total_seconds()
    return {
        "top": round((start - grid_start).total_seconds() / total * 100, 3),
        "height": round((end - start).total_seconds() / total * 100, 3),
    }


def assign_lanes(items: list[dict]) -> None:
    """Give overlapping blocks side-by-side lanes (sets ``left`` and ``width`` in %).

    Items need ``_start``/``_end``. Walk through them in time order; a "cluster" is a
    run of blocks that overlap each other. Each block takes the first free lane, and
    every block in a cluster gets width = 100% / (lanes used by that cluster).
    """
    items.sort(key=lambda i: (i["_start"], -i["_end"].timestamp()))
    cluster: list[dict] = []
    lane_ends: list[datetime] = []
    cluster_end: datetime | None = None

    def close() -> None:
        for item in cluster:
            item["width"] = round(100 / len(lane_ends), 3)
            item["left"] = round(item["lane"] * item["width"], 3)

    for item in items:
        if cluster_end is not None and item["_start"] >= cluster_end:
            close()
            cluster, lane_ends, cluster_end = [], [], None
        lane = next((i for i, end in enumerate(lane_ends) if end <= item["_start"]), None)
        if lane is None:
            lane_ends.append(item["_end"])
            lane = len(lane_ends) - 1
        else:
            lane_ends[lane] = item["_end"]
        item["lane"] = lane
        cluster.append(item)
        cluster_end = item["_end"] if cluster_end is None else max(cluster_end, item["_end"])
    if cluster:
        close()


def without_duplicates(busy: list[BusyBlock]) -> list[BusyBlock]:
    """Hide a Google "Busy" block when the user's own commitment covers the same time.

    Both still count for availability (overlaps merge there); this only keeps the
    calendar from drawing the same class twice when it's in Google and added by hand.
    """
    own = [b for b in busy if b.event_id]
    return [b for b in busy if b.event_id or not any(o.start <= b.start and b.end <= o.end for o in own)]


def muscle_line(workout: Workout) -> str:
    return " · ".join(m.capitalize() for m in MUSCLES if m in workout.muscle_groups)


def workout_card(
    workout: Workout, inputs: Inputs, plan: WeeklyPlan, grid: tuple[datetime, datetime], index: int
) -> dict:
    """Everything the calendar block and its details popover show."""
    past = workout.start < inputs.now
    others = [] if past or workout.skipped else other_times(inputs, plan, workout)
    return {
        "id": f"w{index}",
        "name": workout.name,
        "slug": slug(workout.name),
        "start": workout.start.isoformat(),
        "range": time_range(workout.start, workout.end),
        "when": f"{workout.start:%a, %b} {workout.start.day} · {time_range(workout.start, workout.end)}",
        "muscles": muscle_line(workout),
        "exercises": [
            {
                "name": e.name,
                "dose": f"{e.sets} × {e.reps.replace('-', '–')}",
                "rest": e.rest.replace("-", "–"),
                "muscles": ", ".join(e.exercise.primary + e.exercise.secondary),
            }
            for e in workout.exercises
        ],
        "minutes": workout.minutes,
        "effort": EFFORT.split(". ")[0].replace("-", "–") + ".",
        "length": int((workout.end - workout.start).total_seconds() // 60),
        "past": past,
        "skipped": workout.skipped,
        "draggable": not past and plan.split_name != PAST_WEEK,
        "others": [
            {"start": o.start.isoformat(), "label": f"{o.start:%a} {short_clock(o.start)}", "note": o.note}
            for o in others
        ],
        "date": workout.day.isoformat(),
        **(_position(workout.start, workout.end, *grid) or {"top": 0, "height": 0}),
    }


def build_days(inputs: Inputs, plan: WeeklyPlan, proposal=None) -> list[dict]:
    prefs = inputs.settings.to_preferences(plan.week_start)
    tz = ZoneInfo(prefs.timezone)
    first_hour, last_hour = grid_hours(prefs)
    shown = without_duplicates(busy_for(inputs, plan.week_start))
    today, now = inputs.now.date(), inputs.now
    moved, added, removed = _proposal_changes(plan, proposal)
    days = []
    for offset in range(7):
        day = plan.week_start + timedelta(days=offset)
        day_start = datetime.combine(day, datetime.min.time(), tzinfo=tz)
        grid = (day_start + timedelta(hours=first_hour), day_start + timedelta(hours=last_hour))

        events = []
        for b in sorted(shown, key=lambda b: b.start):
            start, end = b.start.astimezone(tz), b.end.astimezone(tz)
            if end <= day_start or start >= day_start + timedelta(days=1):
                continue
            pos = _position(start, end, *grid)
            if pos is None:
                continue
            title = "Can't train" if b.kind == "unavailable" and b.title == "Busy" else b.title
            events.append(
                {
                    "title": title,
                    "id": b.event_id,
                    "kind": b.kind,
                    "range": time_range(start, end),
                    "_start": max(start, grid[0]),
                    "_end": min(end, grid[1]),
                    **pos,
                }
            )
        assign_lanes(events)
        for e in events:
            e.pop("_start")
            e.pop("_end")

        workouts = []
        for index, w in enumerate(plan.workouts):
            if w.day == day:
                card = workout_card(w, inputs, plan, grid, index)
                card["proposal"] = "moving" if any(a is w for a, _ in moved) or w in removed else None
                workouts.append(card)
        ghosts = [
            {
                "name": b.name,
                "slug": slug(b.name),
                "range": time_range(b.start, b.end),
                **_position(b.start, b.end, *grid),
            }
            for b in [b for _, b in moved] + added
            if b.day == day and _position(b.start, b.end, *grid)
        ]

        now_top = None
        if day == today:
            pos = _position(now, now + timedelta(minutes=1), *grid)
            now_top = pos["top"] if pos else None
        days.append(
            {
                "weekday": day.strftime("%a"),
                "weekday_index": day.weekday(),
                "weekday_long": day.strftime("%A"),
                "iso": day.isoformat(),
                "date_num": day.day,
                "date_label": f"{day:%b} {day.day}",
                "events": events,
                "workouts": workouts,
                "ghosts": ghosts,
                "is_today": day == today,
                "is_past": day < today,
                "now_top": now_top,
            }
        )
    return days


def _proposal_changes(plan: WeeklyPlan, proposal):
    if proposal is None or proposal.after is None:
        return [], [], []
    from app.proposals import pair  # proposals imports planning; importing here avoids a cycle at load time

    return pair(plan, proposal.after)


def hour_labels(prefs: Preferences) -> list[dict]:
    """Labels for the left gutter: '6 AM', '7 AM', ... with their position in %."""
    first, last = grid_hours(prefs)
    span = last - first
    labels = []
    for h in range(first, last):
        suffix = "AM" if h < 12 else "PM"
        labels.append({"label": f"{(h - 1) % 12 + 1} {suffix}", "top": round((h - first) / span * 100, 3)})
    return labels


def summary_line(plan: WeeklyPlan, prefs: Preferences) -> str:
    count = len([w for w in plan.workouts if not w.skipped])
    if plan.split_name == PAST_WEEK:
        return f"{count} workout{'s' if count != 1 else ''} planned"
    return f"{count} workout{'s' if count != 1 else ''} · {prefs.session_minutes} min"


def volume_line(plan: WeeklyPlan) -> str:
    """Weekly sets per muscle in one line, for "How this plan was decided"."""
    sets = weekly_sets([w for w in plan.workouts if not w.skipped])
    return " · ".join(f"{muscle} {count:g}" for muscle, count in sets.items())


def week_context(inputs: Inputs, plan: WeeklyPlan, proposal=None) -> dict:
    """Everything the week template needs."""
    week = plan.week_start
    prefs = inputs.settings.to_preferences(week)
    first_hour, last_hour = grid_hours(prefs)
    current = inputs.current_week
    return {
        "plan": plan,
        "days": build_days(inputs, plan, proposal),
        "hours": hour_labels(prefs),
        "grid_first_hour": first_hour,
        "grid_hours": last_hour - first_hour,
        "week_iso": week.isoformat(),
        "week_label": week_label(week),
        "prev_week": (week - timedelta(days=7)).isoformat(),
        "next_week": (week + timedelta(days=7)).isoformat(),
        "is_current_week": week == current,
        "is_past_week": week < current,
        "summary": summary_line(plan, prefs),
        "volume": volume_line(plan),
        "volume_target": WEEKLY_SET_TARGET,
        "buffer_minutes": prefs.buffer_minutes,
        "gym_hours": f"{short_clock(datetime.combine(week, prefs.earliest_start))} – "
        f"{short_clock(datetime.combine(week, prefs.latest_end))}",
        "proposal": proposal,
    }
