"""The AI Coach: Gemini reads chat messages and screenshots; code writes the facts.

Design rule: the language model only *translates*. It turns a message into a
``ChatChanges`` (checked against a JSON schema) and writes a short friendly reply.
Every scheduling decision is made by the deterministic code in ``app.scheduler``, and
replies that state facts about the plan are written by code too. Without an API key,
``rule_parser`` reads messages instead, so the app works fully without AI.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import date, time

from app.ai.chat_changes import EXERCISE_NAMES, WEEKDAYS, ChatChanges, MoveRequest, RecurringBusy
from app.ai.rule_parser import parse_with_rules
from app.formatting import join_words
from app.models import Commitment, Preferences, RepRange, SplitStyle, TimeOfDay, Volume, WeeklyPlan
from app.scheduler.splits import BY_STYLE, STYLE_LABELS

log = logging.getLogger(__name__)

# ================================================================== Gemini ===
# JSON Schema for Gemini's structured output: the model must answer with exactly this shape.
CHANGES_SCHEMA = {
    "type": "object",
    "properties": {
        "preferred_time": {"type": "string", "enum": [t.value for t in TimeOfDay]},
        "commitment": {
            "type": "string",
            "enum": [c.value for c in Commitment],
            "description": "light = busy / just staying consistent; dedicated = training is a top priority",
        },
        "session_minutes": {"type": "integer", "minimum": 20, "maximum": 180},
        "earliest_start": {"type": "string", "description": "HH:MM, 24h. Earliest they'd start a workout."},
        "latest_end": {"type": "string", "description": "HH:MM, 24h. Latest a workout may end."},
        "blocked_weekdays": {
            "type": "array",
            "items": {"type": "string", "enum": WEEKDAYS},
            "description": "Days they never want to train.",
        },
        "allowed_weekdays": {
            "type": "array",
            "items": {"type": "string", "enum": WEEKDAYS},
            "description": "Days they previously blocked but now say are fine for training.",
        },
        "simpler_exercises": {
            "type": "boolean",
            "description": "true if they ask for easier or less technical exercises, false to go back to standard.",
        },
        "volume": {
            "type": "string",
            "enum": [v.value for v in Volume],
            "description": "Only if they ask for fewer or more sets (training volume).",
        },
        "reps": {
            "type": "string",
            "enum": [r.value for r in RepRange],
            "description": "heavier = fewer reps with more weight; lighter = more reps with less weight.",
        },
        "avoid_exercises": {
            "type": "array",
            "items": {"type": "string", "enum": EXERCISE_NAMES},
            "description": "Exercises they want swapped out (don't like, can't do, no equipment).",
        },
        "allow_exercises": {
            "type": "array",
            "items": {"type": "string", "enum": EXERCISE_NAMES},
            "description": "Exercises they swapped out before and now want back.",
        },
        "reset_programming": {"type": "boolean", "description": "true to go back to the default workouts."},
        "move_workout": {
            "type": "object",
            "description": "When they ask to move one workout, or describe a conflict with one (e.g. 'my lab "
            "runs late on Thursdays'): the day it's on now, the day it should go to, and a time if they say. "
            "Use the 'today' fact to turn 'today' or 'tomorrow' into a weekday.",
            "properties": {
                "from_day": {"type": "string", "enum": WEEKDAYS},
                "workout": {"type": "string", "description": "The workout's name, e.g. Upper, if they say it."},
                "to_day": {"type": "string", "enum": WEEKDAYS},
                "time": {"type": "string", "description": "HH:MM, 24h, only if they give one."},
                "part_of_day": {"type": "string", "enum": [t.value for t in TimeOfDay]},
            },
            "required": ["to_day"],
        },
        "split_style": {
            "type": "string",
            "enum": [s.value for s in SplitStyle] + ["recommended"],
            "description": "Only if they ask for a training split. 'recommended' = go back to the app's choice. "
            "ppl_six_day = Push/Pull/Legs twice (six days).",
        },
        "split_scope": {
            "type": "string",
            "enum": ["this_week", "ongoing", "unclear"],
            "description": "With split_style: 'this_week' for one week ('can I do Upper/Lower this week?'), "
            "'ongoing' for 'from now on', 'unclear' if they don't say. Use 'ongoing' alone when they "
            "say to keep their current split.",
        },
        "skipped_days": {
            "type": "array",
            "items": {"type": "string", "enum": WEEKDAYS},
            "description": "Days in the past week they say they skipped or missed a planned workout. "
            "Use the 'today' fact to turn 'yesterday' into a weekday.",
        },
        "recurring_busy": {
            "type": "array",
            "description": "Weekly commitments mentioned in the note that may not be on their calendar.",
            "items": {
                "type": "object",
                "properties": {
                    "weekday": {"type": "string", "enum": WEEKDAYS},
                    "start": {"type": "string", "description": "HH:MM, 24h"},
                    "end": {"type": "string", "description": "HH:MM, 24h"},
                    "title": {"type": "string"},
                },
                "required": ["weekday", "start", "end", "title"],
            },
        },
    },
}

EXPLAIN_INSTRUCTIONS = (
    "You are a friendly strength coach. In 2-3 short sentences, explain this week's workout "
    "plan to the user using ONLY the facts given. Mention why this split was chosen. "
    "No lists, no markdown, no made-up details."
)


def gemini_client():
    """A Gemini client, or None when no API key is set. The key is read here, on the server only,
    and the SDK sends it in a request header; it never reaches the page."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None
    from google import genai

    return genai.Client(api_key=api_key)


def _model() -> str:
    return os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")


def _log_failure(what: str, exc: Exception) -> None:
    # Only the error type and status code: messages can echo the user's text or Google's details.
    log.warning("%s failed (%s %s); using the fallback", what, type(exc).__name__, getattr(exc, "code", ""))


def _notes_from_ai_json(data: dict) -> ChatChanges:
    out = ChatChanges(source="ai")
    if v := data.get("preferred_time"):
        out.preferred_time = TimeOfDay(v)
    if v := data.get("commitment"):
        out.commitment = Commitment(v)
    if v := data.get("session_minutes"):
        out.session_minutes = max(20, min(180, int(v)))
    if v := data.get("earliest_start"):
        out.earliest_start = time.fromisoformat(v)
    if v := data.get("latest_end"):
        out.latest_end = time.fromisoformat(v)
    out.blocked_weekdays = {WEEKDAYS.index(d) for d in data.get("blocked_weekdays", [])}
    out.allowed_weekdays = {WEEKDAYS.index(d) for d in data.get("allowed_weekdays", [])}
    out.blocked_weekdays -= out.allowed_weekdays
    if (v := data.get("simpler_exercises")) is not None:
        out.simpler_exercises = bool(v)
    if v := data.get("volume"):
        out.volume = Volume(v)
    if v := data.get("reps"):
        out.reps = RepRange(v)
    out.avoid_exercises = [n for n in data.get("avoid_exercises", []) if n in EXERCISE_NAMES]
    out.allow_exercises = [n for n in data.get("allow_exercises", []) if n in EXERCISE_NAMES]
    out.reset_programming = bool(data.get("reset_programming"))
    if (move := data.get("move_workout")) and move.get("to_day") in WEEKDAYS:
        out.move = MoveRequest(
            WEEKDAYS.index(move["to_day"]),
            WEEKDAYS.index(move["from_day"]) if move.get("from_day") in WEEKDAYS else None,
            str(move.get("workout") or "").strip().title() or None,
            time.fromisoformat(move["time"]) if move.get("time") else None,
            TimeOfDay(move["part_of_day"]) if move.get("part_of_day") in {t.value for t in TimeOfDay} else None,
        )
    if (v := data.get("split_style")) == "recommended":
        out.reset_split = True
    elif v:
        out.split_style = SplitStyle(v)
    if (v := data.get("split_scope")) in ("this_week", "ongoing"):
        out.split_scope = v
    out.skipped_weekdays = {WEEKDAYS.index(d) for d in data.get("skipped_days", [])}
    for r in data.get("recurring_busy", []):
        start, end = time.fromisoformat(r["start"]), time.fromisoformat(r["end"])
        if start < end:
            out.recurring_busy.append(RecurringBusy(WEEKDAYS.index(r["weekday"]), start, end, r["title"]))
    return out


def fallback_explanation(plan: WeeklyPlan) -> str:
    if not plan.workouts:
        return "Your calendar is packed this week, so this is a rest week. " + " ".join(plan.notes)
    days = ", ".join(w.start.strftime("%A") for w in plan.workouts)
    return f"This week you're doing {plan.split_name} on {days}. " + " ".join(plan.notes)


def explain_plan(plan: WeeklyPlan, client=None) -> str:
    """A short, friendly summary of why the plan looks the way it does. No client: written by code."""
    if client is None:
        return fallback_explanation(plan)
    facts = {
        "split": plan.split_name,
        "sessions": [{"name": w.name, "when": w.start.strftime("%A %I:%M %p")} for w in plan.workouts],
        "planner_reasons": plan.notes,
    }
    try:
        from google.genai import types

        response = client.models.generate_content(
            model=_model(),
            contents=json.dumps(facts),
            config=types.GenerateContentConfig(system_instruction=EXPLAIN_INSTRUCTIONS),
        )
        text = (response.text or "").strip()
        return text or fallback_explanation(plan)
    except Exception as exc:
        _log_failure("Gemini explanation", exc)
        return fallback_explanation(plan)


# ============================================================ AI Coach chat ===
COACH_INSTRUCTIONS = (
    "You are the AI Coach inside GymScheduleBot, a muscle-building workout planner. The app's own scheduling code "
    "decides when workouts happen; your job is to (1) turn the user's message into setting changes "
    "and (2) write a short, friendly reply (1-2 sentences, no markdown). "
    "Only put a field in `changes` if the user asked for it. If they ask a question, answer it using "
    "ONLY the plan facts given, and leave `changes` empty. Never invent workouts, times or numbers. "
    "When they ask for a split, just acknowledge it: the app adds the details and any tradeoffs. "
    "When they ask to move a workout or describe a conflict with one, fill in `move_workout`: the app checks "
    "free time and recovery, then shows the change on their calendar to accept or reject. "
    "Never assume their training experience; only change exercises, sets or reps when they ask. "
    "Use 24-hour HH:MM times."
)

COACH_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string", "description": "1-2 friendly sentences to the user."},
        "changes": CHANGES_SCHEMA,
    },
    "required": ["reply"],
}


@dataclass
class CoachTurn:
    changes: ChatChanges
    reply: str | None  # None means "let the app write the reply"


def plan_facts(plan: WeeklyPlan, prefs: Preferences, today: date) -> dict:
    """The only things the coach is allowed to talk about."""
    return {
        "today": today.strftime("%A"),
        "split": plan.split_name,
        "sessions": [
            {
                "workout": w.name,
                "day": w.start.strftime("%A"),
                "time": w.start.strftime("%H:%M"),
                "exercises": [str(e) for e in w.exercises],
            }
            for w in plan.workouts
        ],
        "open_days": len(plan.free_days),
        "settings": {
            "commitment": prefs.commitment.value,
            "preferred_time": prefs.preferred_time.value,
            "session_minutes": prefs.session_minutes,
            "blocked_days": [WEEKDAYS[d] for d in sorted(prefs.blocked_weekdays)],
            "simpler_exercises": prefs.programming.simpler_exercises,
            "volume": prefs.programming.volume.value,
            "reps": prefs.programming.reps.value,
            "swapped_out": sorted(prefs.programming.avoid),
            "requested_split": prefs.requested_split.value if prefs.requested_split else "recommended",
        },
        "planner_reasons": plan.notes,
    }


def coach_turn(message: str, plan: WeeklyPlan, prefs: Preferences, today: date, client=None) -> CoachTurn:
    """Read one chat message: what should change, and what should the coach say?
    Without a Gemini client, the keyword parser reads it and the app writes the reply."""
    if client is None:
        return CoachTurn(parse_with_rules(message, today), None)
    try:
        from google.genai import types

        prompt = json.dumps({"current_plan": plan_facts(plan, prefs, today), "user_message": message})
        response = client.models.generate_content(
            model=_model(),
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=COACH_INSTRUCTIONS,
                response_mime_type="application/json",
                response_json_schema=COACH_SCHEMA,
            ),
        )
        data = json.loads(response.text)
        reply = str(data.get("reply", "")).strip() or None
        return CoachTurn(_notes_from_ai_json(data.get("changes") or {}), reply)
    except Exception as exc:
        _log_failure("Gemini coach", exc)
        return CoachTurn(parse_with_rules(message, today), None)


def plan_summary(plan: WeeklyPlan) -> str:
    """One factual sentence about the plan, written by code (never by the AI)."""
    if not plan.workouts:
        return "Your week is packed, so this is a rest week."
    days = join_words([w.start.strftime("%a") for w in plan.workouts])
    count = len(plan.workouts)
    return f"{plan.split_name}: {count} session{'s' if count != 1 else ''} on {days}."


def split_change_reply(changes: ChatChanges, plan: WeeklyPlan) -> str | None:
    """What the coach says after a split request, written by code so the facts are right."""
    if changes.reset_split:
        return "Back to the recommended split."
    if changes.split_style is None:
        return "Got it, I'll keep this split from now on." if changes.split_scope == "ongoing" else None

    label = STYLE_LABELS[changes.split_style]
    if plan.split_name not in BY_STYLE[changes.split_style]:
        return (
            f"{label} doesn't fit your free days this week with at least 48 hours of rest between sessions "
            "for the same muscles, so I kept the recommended split for now."
        )
    when = "from now on" if changes.split_scope == "ongoing" else "for this week"
    parts = [f"Switched to {label} {when}."]
    if plan.split_name in ("Upper/Lower x1", "Push/Pull/Legs", "Full Body"):
        parts.append("With this split, each muscle is trained once this week instead of twice.")
    if changes.split_style is SplitStyle.PPL_SIX_DAY:
        parts.append("Six workouts leave one rest day, so keep an eye on sleep and how recovered you feel.")
    if changes.split_scope is None:
        parts.append('Want to keep it after this week? Just say "from now on".')
    return " ".join(parts)


def coach_intro(plan: WeeklyPlan) -> str:
    """The coach's first message when the page opens (no AI call, so page loads stay fast and free)."""
    if not plan.workouts:
        return "Your calendar is packed this week, so I've planned a rest week. Want me to try shorter sessions?"
    count = len(plan.workouts)
    days = join_words([w.start.strftime("%a") for w in plan.workouts])
    intro = (
        f"I've planned {count} session{'s' if count != 1 else ''} ({plan.split_name}) around your calendar on {days}."
    )
    if plan.warnings:
        return f"{intro} Two of your workouts are too close together for recovery; see the note above the calendar."
    return f"{intro} What would you like to change?"


def fallback_reply(changes: ChatChanges, plan: WeeklyPlan) -> str:
    if changes.is_empty():
        return (
            "I can move a workout, change your times, days off or split, or adjust the exercises, sets and reps. "
            'Try "move Thursday\'s workout to Friday morning" or "simpler exercises, please".'
        )
    return f"Got it: {join_words(changes.describe())}. I've re-planned your week."


# ======================================================= schedule screenshots ===
SCREENSHOT_INSTRUCTIONS = (
    "You read screenshots of schedules (class timetables, calendar week views, work rosters) and list "
    "every event as a weekly event: its title, weekday, start and end time (24-hour HH:MM). "
    "Only list events you can actually see; skip anything unreadable. Ignore all-day items. "
    "Also write a one-sentence friendly `reply` about what you found. If the image is not a schedule, "
    "return no events and say so in the reply."
)

SCREENSHOT_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "weekday": {"type": "string", "enum": WEEKDAYS},
                    "start": {"type": "string", "description": "HH:MM, 24h"},
                    "end": {"type": "string", "description": "HH:MM, 24h"},
                },
                "required": ["title", "weekday", "start", "end"],
            },
        },
    },
    "required": ["reply", "events"],
}

SUPPORTED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}


class ScreenshotUnavailable(RuntimeError):
    """Reading screenshots needs Gemini; raised when no API key is configured."""


def read_schedule_image(image: bytes, mime_type: str, note: str = "", client=None) -> tuple[list[dict], str]:
    """Ask Gemini to turn a schedule screenshot into weekly events.

    Returns (events, reply). Each event is {"title", "weekday" (0=Mon), "start", "end"} with
    `start`/`end` as datetime.time; the caller validates and stores them.
    """
    if mime_type not in SUPPORTED_IMAGE_TYPES:
        raise ValueError("Please send a PNG, JPG or WebP image.")
    if client is None:
        raise ScreenshotUnavailable("Reading screenshots needs Gemini, which isn't set up on this server.")
    from google.genai import types

    response = client.models.generate_content(
        model=_model(),
        contents=[types.Part.from_bytes(data=image, mime_type=mime_type), note or "Read this schedule."],
        config=types.GenerateContentConfig(
            system_instruction=SCREENSHOT_INSTRUCTIONS,
            response_mime_type="application/json",
            response_json_schema=SCREENSHOT_SCHEMA,
        ),
    )
    try:
        data = json.loads(response.text)
    except (TypeError, ValueError):
        return [], ""
    events = []
    for e in data.get("events", [])[:60]:
        try:
            start, end = time.fromisoformat(e["start"]), time.fromisoformat(e["end"])
            weekday = WEEKDAYS.index(e["weekday"])
        except (KeyError, ValueError):
            continue
        if start < end:
            events.append({"title": str(e.get("title") or "Busy"), "weekday": weekday, "start": start, "end": end})
    return events, str(data.get("reply") or "").strip()
