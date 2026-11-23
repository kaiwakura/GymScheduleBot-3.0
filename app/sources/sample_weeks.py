"""Sample weeks so anyone (including recruiters) can try the app without logging in.

A sample isn't a fixed calendar: picking one copies its events into "your events", so
every class, shift and gap can be moved, resized or deleted like an event you added.
"""

from __future__ import annotations

# (weekday, start, end, title) — weekday 0 = Monday. Gaps between rows are free time.
_BUSY_WEEK = [
    # Monday: lunch gap between lectures and lab, then an evening shift
    (0, "09:00", "10:15", "Calc II lecture"),
    (0, "10:30", "11:45", "Chemistry lecture"),
    (0, "13:00", "15:50", "Chemistry lab"),
    (0, "17:00", "21:00", "Part-time job"),
    # Tuesday: a long free stretch around lunch
    (1, "09:30", "10:45", "English seminar"),
    (1, "11:00", "12:00", "Study group"),
    (1, "14:00", "15:15", "Intro to CS"),
    (1, "18:00", "19:30", "Intramural soccer"),
    # Wednesday
    (2, "09:00", "10:15", "Calc II lecture"),
    (2, "10:30", "11:45", "Chemistry lecture"),
    (2, "13:00", "14:00", "Office hours"),
    (2, "17:00", "21:00", "Part-time job"),
    # Thursday
    (3, "09:30", "10:45", "English seminar"),
    (3, "14:00", "15:15", "Intro to CS"),
    (3, "15:30", "17:20", "CS lab"),
    (3, "19:00", "20:30", "Club meeting"),
    # Friday: free evening
    (4, "09:00", "10:15", "Calc II lecture"),
    (4, "10:30", "11:45", "Chemistry lecture"),
    (4, "13:00", "18:00", "Part-time job"),
    # Weekend
    (5, "10:00", "16:00", "Part-time job"),
    (5, "19:00", "22:00", "Friend's birthday dinner"),
    (6, "10:00", "11:30", "Groceries + laundry"),
    (6, "13:00", "17:00", "Homework"),
]

_LIGHT_WEEK = [
    *[row for day in range(5) for row in ((day, "09:00", "12:00", "Work"), (day, "13:00", "17:30", "Work"))],
    (0, "18:00", "18:30", "Call with family"),
    (2, "18:30", "20:00", "Dinner with friends"),
    (3, "07:30", "08:15", "Dentist"),
    (5, "10:00", "12:00", "Brunch"),
    (6, "11:00", "12:00", "Grocery run"),
]

SAMPLE_WEEKS = {
    "busy": ("Busy student week", _BUSY_WEEK),
    "light": ("Normal 9-to-5 week", _LIGHT_WEEK),
}


def sample_events(name: str) -> list[dict]:
    """The sample week as the user's own weekly events (same shape as events they add)."""
    _, rows = SAMPLE_WEEKS[name if name in SAMPLE_WEEKS else "busy"]
    return [
        {"id": f"sample-{name}-{i}", "title": title, "start": start, "end": end, "repeat": "weekly", "weekday": weekday}
        for i, (weekday, start, end, title) in enumerate(rows)
    ]
