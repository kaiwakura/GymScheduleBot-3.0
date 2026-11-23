"""Coach suggestions that would move workouts, shown on the calendar before anything changes.

When a chat message would change where workouts go (a different preferred time, a
split, or "move Thursday's workout to Friday"), the coach doesn't apply it. It saves
a proposal in the session, the calendar shows the proposed workouts as dashed blocks
next to the ones they'd replace, and the user accepts or rejects it.

Only what changes is stored (a few settings and at most one moved workout), so the
session cookie stays small.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import date, datetime

from app import workout_moves
from app.formatting import clock
from app.models import WeeklyPlan, Workout
from app.planning import Inputs, plan_for
from app.settings import Settings

SESSION_KEY = "proposal"


@dataclass
class Proposal:
    token: str
    week: str
    settings: dict = field(default_factory=dict)  # only the fields that change
    move: dict | None = None  # one workout_moves entry
    lines: list[str] = field(default_factory=list)  # e.g. "Upper: Thu 6:00 PM → Fri 7:00 AM"
    after: WeeklyPlan | None = None  # the proposed plan, for drawing it (not stored)

    def to_json(self) -> dict:
        return {"token": self.token, "lines": self.lines}

    def to_session(self) -> dict:
        return {
            "token": self.token,
            "week": self.week,
            "settings": self.settings,
            "move": self.move,
            "lines": self.lines,
        }

    def apply_settings(self, settings: Settings) -> Settings:
        data = settings.to_session()
        data.update(self.settings)
        return Settings.from_session(data)


def _label(workout: Workout) -> str:
    return f"{workout.start:%a} {clock(workout.start)}"


def pair(before: WeeklyPlan, after: WeeklyPlan) -> tuple[list[tuple[Workout, Workout]], list[Workout], list[Workout]]:
    """Match each workout to its counterpart by name and order: (moved pairs, added, removed)."""
    moved, added, removed = [], [], []
    for name in sorted({w.name for w in before.workouts + after.workouts}):
        old = [w for w in before.workouts if w.name == name and not w.skipped]
        new = [w for w in after.workouts if w.name == name and not w.skipped]
        for a, b in zip(old, new, strict=False):
            if a.start != b.start:
                moved.append((a, b))
        added += new[len(old) :]
        removed += old[len(new) :]
    return moved, added, removed


def describe(before: WeeklyPlan, after: WeeklyPlan) -> list[str]:
    """What changes between two plans, in plain words."""
    moved, added, removed = pair(before, after)
    lines = [f"{a.name}: {_label(a)} → {_label(b)}" for a, b in sorted(moved, key=lambda p: p[1].start)]
    lines += [f"Add {w.name}: {_label(w)}" for w in added]
    lines += [f"Remove {w.name}: {_label(w)}" for w in removed]
    return lines


def make(inputs: Inputs, week: date, current: Settings, proposed: Settings, move: dict | None) -> Proposal | None:
    """A proposal if the suggested settings or move would change where workouts go, else None."""
    changed = {k: v for k, v in proposed.to_session().items() if current.to_session().get(k) != v}
    if not changed and move is None:
        return None
    before = plan_for(inputs, week)
    moves = inputs.moves
    if move is not None:
        origin, start = datetime.fromisoformat(move["from"]), datetime.fromisoformat(move["start"])
        moves = workout_moves.add(moves, week, move["name"], origin, start)
    after = plan_for(Inputs(proposed, inputs.events, inputs.history, moves, inputs.now, inputs.google), week)
    lines = describe(before, after)
    if not lines:
        return None
    return Proposal(secrets.token_urlsafe(8), week.isoformat(), changed, move, lines, after)


def save(session: dict, proposal: Proposal) -> None:
    session[SESSION_KEY] = proposal.to_session()


def load(session: dict) -> Proposal | None:
    data = session.get(SESSION_KEY)
    if not isinstance(data, dict) or not isinstance(data.get("token"), str):
        return None
    move = None
    if data.get("move") is not None:
        move = workout_moves.clean_move(data["move"])
        if move is None:
            return None  # a damaged cookie: drop the proposal rather than guess
    return Proposal(
        data["token"], str(data.get("week")), dict(data.get("settings") or {}), move, list(data.get("lines") or [])
    )


def clear(session: dict) -> None:
    session.pop(SESSION_KEY, None)
