"""Export a week's workouts as an .ics file that any calendar app can import."""

from __future__ import annotations

from datetime import datetime

from icalendar import Calendar, Event

from app.models import WeeklyPlan


def plan_to_ics(plan: WeeklyPlan) -> bytes:
    cal = Calendar()
    cal.add("prodid", "-//GymScheduleBot//Workout Planner//EN")
    cal.add("version", "2.0")
    for w in plan.workouts:
        event = Event()
        event.add("uid", f"{w.start.isoformat()}-{w.name}@gymschedulebot")
        event.add("summary", f"Workout: {w.name}")
        event.add("dtstart", w.start)
        event.add("dtend", w.end)
        event.add("dtstamp", datetime.now(tz=w.start.tzinfo))
        event.add("description", f"{plan.split_name}\n\n" + "\n".join(str(e) for e in w.exercises))
        cal.add_component(event)
    return cal.to_ical()
