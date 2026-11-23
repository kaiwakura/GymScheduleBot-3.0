"""FastAPI web app: the weekly calendar, the AI Coach chat, Preferences and Google Calendar.

Run locally with:  uvicorn app.main:app --reload

Flow: every request loads the visitor's saved Settings (session cookie) plus what the
browser keeps (their own commitments, recent workouts and moved workouts), reads busy
time from Google Calendar if connected, plans the requested calendar week and renders
it. The AI Coach suggests changes; anything that would move workouts is shown on the
calendar as a proposal first, and only applied when the user accepts it.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import secrets
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from fastapi import Body, Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app import google_auth, google_integration, planning, proposals, training_history, workout_moves
from app.ai.assistant import (
    ScreenshotUnavailable,
    coach_intro,
    coach_turn,
    explain_plan,
    fallback_reply,
    gemini_client,
    plan_summary,
    read_schedule_image,
    split_change_reply,
)
from app.formatting import clock
from app.ics_export import plan_to_ics
from app.models import Commitment, SplitStyle, TimeOfDay
from app.planning import Inputs, plan_for
from app.scheduler.planner import local_now
from app.scheduler.splits import STYLE_LABELS
from app.security import BodySizeLimit, ai_quota, client_ip, csrf_token, require_csrf
from app.settings import BUFFER_CHOICES, Settings, week_start
from app.sources import custom
from app.sources.sample_weeks import SAMPLE_WEEKS, sample_events
from app.views import week_context

load_dotenv()
log = logging.getLogger("gymschedulebot")
BASE_DIR = Path(__file__).parent
DEFAULT_TZ = os.getenv("DEFAULT_TIMEZONE", "America/Los_Angeles")
MAX_IMAGE_BYTES = 4 * 1024 * 1024  # the browser shrinks screenshots well below this
MAX_MESSAGE_CHARS = 2000  # room to explain a complicated week; app.js warns before sending more
RESET_WORDS = {"reset", "start over", "reset preferences", "clear preferences"}
WEEKDAY_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
WORKOUT_MINUTES = (45, 60, 75, 90)

if not google_auth.local_development():
    if not os.getenv("SESSION_SECRET"):
        log.warning("SESSION_SECRET isn't set: sessions will reset whenever the server restarts")
    elif len(os.environ["SESSION_SECRET"]) < 32:
        log.warning("SESSION_SECRET is short and guessable; generate a random 64-character one")

app = FastAPI(title="GymScheduleBot")
# The session cookie is signed (so it can't be edited), HttpOnly (JavaScript can't read it),
# SameSite=Lax (other sites can't post with it) and Secure outside local development.
# Google tokens inside it are encrypted separately (see google_auth.py).
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET") or secrets.token_hex(32),
    same_site="lax",
    https_only=not google_auth.local_development(),
)
app.add_middleware(BodySizeLimit)
CSRF = [Depends(require_csrf)]
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


# =============================================================== helpers ===
def ai_enabled() -> bool:
    return bool(os.getenv("GEMINI_API_KEY"))


def ai_client(request: Request):
    """Gemini for this request, or None: no API key, or this visitor (or the whole site) has
    reached the AI limits. None means the coach uses its keyword parser instead."""
    if not ai_enabled() or not ai_quota.allow(client_ip(request)):
        return None
    return gemini_client()


AI_RESTING = "(Lots of AI requests just now, so I used my simpler parser. Try again in a few minutes.)"


def load_settings(request: Request) -> Settings:
    settings = Settings.from_session(request.session.get("settings"))
    if "settings" not in request.session:
        settings.timezone = DEFAULT_TZ
    return settings


def save_settings(request: Request, settings: Settings) -> None:
    request.session["settings"] = settings.to_session()


def set_timezone(settings: Settings, timezone: str | None) -> None:
    if not timezone:
        return
    try:
        ZoneInfo(timezone)
        settings.timezone = timezone
    except Exception:
        pass


def parse_week(value: Any, current: date) -> date:
    """The Monday of the week asked for, or the current week."""
    try:
        return week_start(date.fromisoformat(str(value)))
    except ValueError:
        return current


def google_connected(request: Request) -> bool:
    return google_auth.is_configured() and google_auth.load_connection(request.session) is not None


@dataclass
class View:
    """One request's planning inputs and the week being shown."""

    inputs: Inputs
    week: date
    notice: str | None = None


def build_view(
    request: Request,
    settings: Settings,
    payload: dict,
    *,
    refresh: bool = False,
    events: list[dict] | None = None,
    sync_horizon: date | None = None,
) -> View:
    """Collect the inputs for a request: settings, the browser's data and Google availability."""
    set_timezone(settings, payload.get("timezone"))
    now = local_now(settings.to_preferences())
    current = week_start(now.date())
    week = parse_week(payload.get("week"), current)
    events = custom.clean_events(payload.get("events")) if events is None else events
    inputs = Inputs(
        settings,
        events,
        training_history.clean_history(payload.get("history")),
        workout_moves.clean_moves(payload.get("moves"), current),
        now,
    )
    notice = None
    if google_connected(request) and not inputs.demo:
        start, end = planning.google_window(current, week, settings.timezone)
        if sync_horizon is not None:  # syncing looks at every week from this one to the horizon
            start = min(start, datetime.combine(current, time.min, tzinfo=start.tzinfo))
            end = max(end, datetime.combine(sync_horizon, time.min, tzinfo=end.tzinfo))
        inputs.google, notice = google_integration.availability(
            request.session, settings.google_excluded, (start, end), now, settings.timezone, refresh
        )
    return View(inputs, week, notice)


def page_context(request: Request, view: View, proposal: proposals.Proposal | None = None) -> dict:
    inputs = view.inputs
    plan = plan_for(inputs, view.week)
    return {
        **week_context(inputs, plan, proposal),
        "settings": inputs.settings,
        "split_choice": split_choice(inputs.settings, view.week),
        "demo": inputs.demo,
        "google_connected": google_connected(request),
        "google_configured": google_auth.is_configured(),
        "google_last_sync": google_integration.last_sync_label(request.session, inputs.now.date()),
        "csrf": csrf_token(request.session),
        "ai_enabled": ai_enabled(),
        "max_message": MAX_MESSAGE_CHARS,
    }


def split_choice(settings: Settings, week: date) -> dict | None:
    """The split the user asked for, shown under the calendar with a reset button."""
    style = settings.active_split(week)
    if style is None:
        return None
    return {"label": STYLE_LABELS[style], "this_week_only": settings.split_until is not None}


def public_settings(settings: Settings) -> dict:
    """What the Preferences panel shows. Never includes anything secret."""
    data = settings.to_session()
    data["split"] = settings.split_style or "recommended"
    data["split_scope"] = "this_week" if settings.split_until else "ongoing"
    return data


def respond(
    request: Request,
    view: View,
    reply: str | None = None,
    proposal: proposals.Proposal | None = None,
    **extra: Any,
) -> JSONResponse:
    """The re-rendered week plus everything the browser keeps, cleaned by the server."""
    inputs = view.inputs
    plan = plan_for(inputs, view.week)
    current_plan = plan_for(inputs, inputs.current_week)
    history = training_history.merge_plan(inputs.history, current_plan, inputs.now)
    dropped = {(view.week.isoformat(), w.start.isoformat()) for w in plan.dropped}
    moves = [m for m in inputs.moves if (m["week"], m["start"]) not in dropped]
    save_settings(request, inputs.settings)
    context = page_context(request, view, proposal)
    return JSONResponse(
        {
            "reply": reply,
            "html": templates.get_template("_plan.html").render(context),
            "week": view.week.isoformat(),
            "week_label": context["week_label"],
            "events": inputs.events,
            "history": history,
            "moves": moves,
            "settings": public_settings(inputs.settings),
            "proposal": proposal.to_json() if proposal else None,
            **extra,
        }
    )


# ================================================================ routes ===
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    # The browser re-plans right away with its own data (it lives in localStorage).
    # A first-time visitor sees the busy student sample week until then.
    settings = load_settings(request)
    first_visit = "settings" not in request.session
    view = build_view(request, settings, {}, refresh=True, events=sample_events("busy") if first_visit else [])
    save_settings(request, settings)
    flash = request.session.pop("flash", None)
    messages = [m for m in (flash, view.notice) if m]
    messages.append(coach_intro(plan_for(view.inputs, view.week)))
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            **page_context(request, view),
            "coach_messages": messages,
            "sample_weeks": SAMPLE_WEEKS,
            "first_visit": first_visit,
            "prefs_json": public_settings(settings),
        },
    )


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/api/plan", dependencies=CSRF)
def api_plan(request: Request, payload: dict = Body(...)):
    """Re-plan the requested week with the browser's data.

    Called on page load, when changing weeks and after editing a commitment. Optional:
    `load_sample` ("busy" or "light") swaps in a sample week's events; `move`
    ({"name", "from", "start"}) moves one workout to where the user dropped it or picked.
    """
    settings = load_settings(request)
    events = None
    if payload.get("load_sample") in SAMPLE_WEEKS:
        events = sample_events(payload["load_sample"])
    view = build_view(request, settings, payload, events=events)
    extra: dict[str, Any] = {}
    if isinstance(move := payload.get("move"), dict):
        moved = apply_move(view, move)
        if moved is None:
            return JSONResponse({"error": "That workout couldn't be moved. Reload and try again."}, status_code=400)
        extra["undo"] = moved
    return respond(request, view, intro=coach_intro(plan_for(view.inputs, view.week)), **extra)


def apply_move(view: View, move: dict) -> str | None:
    """Move one workout (a drag or a picked time) and describe what changed, for the Undo bar."""
    inputs = view.inputs
    try:
        origin = datetime.fromisoformat(str(move["from"]))
        start = datetime.fromisoformat(str(move["start"]))
    except (KeyError, ValueError):
        return None
    if start.tzinfo is None:  # a drop on the calendar is in the user's own time zone
        start = start.replace(tzinfo=ZoneInfo(inputs.settings.timezone))
    before = plan_for(inputs, view.week)
    workout = planning.find_workout(before, origin)
    if workout is None or workout.start < inputs.now or start < inputs.now or week_start(start.date()) != view.week:
        return None
    inputs.moves = workout_moves.add(inputs.moves, view.week, workout.name, workout.start, start)
    inputs.memo.clear()
    after = plan_for(inputs, view.week)
    others = [line for line in proposals.describe(before, after) if not line.startswith(workout.name + ":")]
    text = f"Moved {workout.name} to {start:%a} {clock(start)}."
    return " ".join([text] + [f"Also: {line}." for line in others])


@app.post("/api/chat", dependencies=CSRF)
def chat(request: Request, payload: dict = Body(...)):
    """One AI Coach turn: a message, a schedule screenshot, or a button (re-plan, reset split, skip)."""
    settings = load_settings(request)
    view = build_view(request, settings, payload)
    inputs = view.inputs
    message = str(payload.get("message", "")).strip()
    if len(message) > MAX_MESSAGE_CHARS:  # refuse rather than cut: a cut message could mean something else
        return JSONResponse(
            {"error": f"Your message is {len(message):,} characters; the limit is {MAX_MESSAGE_CHARS:,}. "
             "Please shorten it and send it again."},
            status_code=400,
        )  # fmt: skip
    action = payload.get("action")
    before = plan_for(inputs, view.week)

    if action == "replan":
        return respond(request, view, explain_plan(before, ai_client(request)))
    if action == "reset_split":
        settings.set_split(None, False, inputs.now.date())
        inputs.memo.clear()
        return respond(request, view, f"Back to the recommended split. {plan_summary(plan_for(inputs, view.week))}")
    if action == "skip":
        try:
            day = date.fromisoformat(str(payload.get("date")))
        except ValueError:
            return JSONResponse({"error": "That workout couldn't be found."}, status_code=400)
        reply = mark_skipped(inputs, {day})
        return respond(request, view, reply)
    if payload.get("image"):
        return import_screenshot(request, view, payload["image"], message)
    if not message:
        return JSONResponse({"error": "Type a message first."}, status_code=400)
    if message.lower().strip(" .!") in RESET_WORDS:
        view.inputs.settings = Settings(timezone=settings.timezone)
        inputs.memo.clear()
        return respond(request, view, "Done, I reset your preferences. " + plan_summary(plan_for(inputs, view.week)))

    client = ai_client(request)
    turn = coach_turn(message, before, inputs.settings.to_preferences(view.week), inputs.now.date(), client)
    limited = client is None and ai_enabled()  # the keyword parser stood in; say so in the reply
    changes = turn.changes
    if changes.split_scope == "ongoing" and changes.split_style is None and settings.split_style is None:
        changes.split_scope = None  # "keep it" when there's no requested split to keep
    if changes.is_empty():
        if turn.reply:
            reply = turn.reply
        elif "why" in message.lower():
            reply = " ".join(before.notes)
        else:
            reply = fallback_reply(changes, before)
        return respond(request, view, f"{reply} {AI_RESTING}" if limited else reply)

    # Facts the user told us apply right away: new weekly commitments and skipped workouts.
    facts = [AI_RESTING] if limited else []
    for r in changes.recurring_busy:
        if event := custom.weekly_event(r.title, r.weekday, r.start, r.end):
            inputs.events = custom.clean_events(inputs.events + [event])
            facts.append(f"I added {r.title} to your commitments ({WEEKDAY_SHORT[r.weekday]}).")
    if changes.skipped_weekdays:
        days = {training_history.last_date_for(d, inputs.now.date()) for d in changes.skipped_weekdays}
        facts.append(mark_skipped(inputs, days))
    inputs.memo.clear()
    before = plan_for(inputs, view.week)

    # Everything else is a suggestion: if it would move workouts, show it first.
    proposed = deepcopy(inputs.settings).apply(changes, inputs.now.date())
    new_move = None
    if changes.move is not None:
        new_move, problem = planning.resolve_move(inputs, before, changes.move)
        if problem:
            return respond(request, view, " ".join(facts + [turn.reply or "", problem]).strip())
    proposal = proposals.make(inputs, view.week, inputs.settings, proposed, new_move)
    if proposal is not None:
        proposals.save(request.session, proposal)
        lead = turn.reply or f"Got it: {', '.join(changes.describe())}. Here's what that would change on your calendar."
        text = " ".join(facts + [lead, "Accept it to keep it, or reject it to leave your plan as it is."])
        return respond(request, view, text, proposal)

    # Nothing would move (say, fewer sets): apply it straight away.
    inputs.settings = proposed
    inputs.memo.clear()
    after = plan_for(inputs, view.week)
    split_reply = split_change_reply(changes, after)
    lead = turn.reply or (None if split_reply or facts else fallback_reply(changes, after))
    parts = facts + [lead, split_reply, plan_summary(after)]
    return respond(request, view, " ".join(p for p in parts if p))


def mark_skipped(inputs: Inputs, days: set[date]) -> str:
    """Stop counting skipped workouts toward recovery, and say what changed."""
    marked = training_history.mark_skipped(inputs.history, days, inputs.now)
    inputs.memo.clear()
    if not marked:
        return "I don't see a workout that already started on that day, so there was nothing to mark as skipped."
    return f"Got it, I marked {' and '.join(marked)} as skipped, so it no longer counts toward recovery."


@app.post("/api/proposal", dependencies=CSRF)
def decide_proposal(request: Request, payload: dict = Body(...)):
    """Accept or reject the coach's last suggestion."""
    settings = load_settings(request)
    view = build_view(request, settings, payload)
    proposal = proposals.load(request.session)
    if proposal is None or proposal.token != payload.get("token"):
        proposals.clear(request.session)
        return respond(request, view, "That suggestion is no longer current, so nothing changed.")
    proposals.clear(request.session)
    if not payload.get("accept"):
        return respond(request, view, "Okay, I left your plan as it was.")
    view.inputs.settings = proposal.apply_settings(view.inputs.settings)
    if proposal.move:
        m = proposal.move
        view.inputs.moves = workout_moves.add(
            view.inputs.moves,
            date.fromisoformat(m["week"]),
            m["name"],
            datetime.fromisoformat(m["from"]),
            datetime.fromisoformat(m["start"]),
        )
    view.inputs.memo.clear()
    return respond(request, view, f"Done. {plan_summary(plan_for(view.inputs, view.week))}")


@app.post("/api/preferences", dependencies=CSRF)
def update_preferences(request: Request, payload: dict = Body(...)):
    """Apply everything changed in the Preferences panel at once, or nothing if anything is invalid."""
    settings = load_settings(request)
    if payload.get("reset"):
        updated = Settings(timezone=settings.timezone, google_excluded=settings.google_excluded)
    else:
        try:
            updated = preferences_from(payload.get("preferences"), settings, request)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
    view = build_view(request, updated, payload)
    proposals.clear(request.session)  # a suggestion made under the old settings no longer applies
    return respond(request, view, None, saved=True)


def preferences_from(raw: Any, settings: Settings, request: Request) -> Settings:
    """A copy of ``settings`` with the panel's values, after checking every one of them."""
    if not isinstance(raw, dict):
        raise ValueError("Those preferences couldn't be read. Please try again.")
    updated = deepcopy(settings)
    commitment = raw.get("commitment", settings.commitment)
    if commitment not in {c.value for c in Commitment}:
        raise ValueError("Choose a commitment level.")
    minutes = raw.get("session_minutes", settings.session_minutes)
    if minutes not in WORKOUT_MINUTES and minutes != settings.session_minutes:
        raise ValueError("Workouts can be 45, 60, 75 or 90 minutes.")
    preferred = raw.get("preferred_time", settings.preferred_time)
    if preferred not in {t.value for t in TimeOfDay}:
        raise ValueError("Choose a preferred time.")
    buffer = raw.get("buffer_minutes", settings.buffer_minutes)
    if buffer not in BUFFER_CHOICES and buffer != settings.buffer_minutes:
        raise ValueError("The travel buffer can be none, 15, 30, 45 or 60 minutes.")
    split = raw.get("split", settings.split_style or "recommended")
    if split != "recommended" and split not in {s.value for s in SplitStyle}:
        raise ValueError("Choose a workout split.")
    scope = raw.get("split_scope", "ongoing")
    if scope not in ("this_week", "ongoing"):
        raise ValueError("Choose how long the split applies.")
    excluded = raw.get("google_excluded", settings.google_excluded)
    if not isinstance(excluded, list) or not all(isinstance(k, str) for k in excluded):
        raise ValueError("Those calendar choices couldn't be read.")

    updated.commitment, updated.session_minutes = commitment, minutes
    updated.preferred_time, updated.buffer_minutes = preferred, buffer
    current_split = settings.split_style or "recommended"
    current_scope = "this_week" if settings.split_until else "ongoing"
    if (split, scope) != (current_split, current_scope):
        style = None if split == "recommended" else SplitStyle(split)
        updated.set_split(style, scope == "ongoing", local_now(settings.to_preferences()).date())
    if google_connected(request):
        updated.google_excluded = [k[:16] for k in excluded][:30]
    return updated.validated()


def import_screenshot(request: Request, view: View, image: Any, note: str):
    """Read a schedule screenshot with Gemini and add what it finds as weekly commitments."""
    try:
        data_url = str(image.get("data", ""))
        mime_type = str(image.get("type", ""))
        raw = base64.b64decode(data_url.split(",", 1)[-1], validate=True)
    except (AttributeError, binascii.Error, ValueError):
        return JSONResponse({"error": "That image couldn't be read."}, status_code=400)
    if len(raw) > MAX_IMAGE_BYTES:
        return JSONResponse({"error": "That image is too large (4 MB max)."}, status_code=400)

    client = ai_client(request)
    if client is None and ai_enabled():
        return respond(request, view, "I've read a lot of screenshots just now. Try again in a few minutes.")
    try:
        found, ai_reply = read_schedule_image(raw, mime_type, note, client)
    except ScreenshotUnavailable as exc:
        return respond(request, view, f"{exc} You can add commitments with the Add commitment button.")
    except ValueError as exc:  # an unsupported image type; the message is ours
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:
        log.warning("Screenshot import failed (%s %s)", type(exc).__name__, getattr(exc, "code", ""))
        return respond(request, view, "Sorry, I couldn't read that screenshot. Try a clearer one, or add them by hand.")

    added = [e for f in found if (e := custom.weekly_event(f["title"], f["weekday"], f["start"], f["end"]))]
    if not added:
        return respond(request, view, ai_reply or "I couldn't find any events in that image.")
    view.inputs.events = custom.clean_events(view.inputs.events + added)
    view.inputs.memo.clear()
    listing = ", ".join(f"{e['title']} ({WEEKDAY_SHORT[e['weekday']]} {e['start']})" for e in added[:6])
    more = f" and {len(added) - 6} more" if len(added) > 6 else ""
    reply = (
        f"{ai_reply + ' ' if ai_reply else ''}I added {len(added)} weekly commitment{'s' if len(added) != 1 else ''}: "
        f"{listing}{more}. Click any of them to fix it. {plan_summary(plan_for(view.inputs, view.week))}"
    )
    return respond(request, view, reply)


@app.post("/export.ics")
def export_ics(request: Request, events: str = Form("[]"), moves: str = Form("[]"), week: str = Form("")):
    def parse(raw: str) -> Any:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return []

    view = build_view(request, load_settings(request), {"events": parse(events), "moves": parse(moves), "week": week})
    return Response(
        plan_to_ics(plan_for(view.inputs, view.week)),
        media_type="text/calendar",
        headers={"Content-Disposition": 'attachment; filename="gymschedulebot-workouts.ics"'},
    )


# ------------------------------------------------------- Google Calendar ---
@app.get("/auth/google/login")
def google_login(request: Request):
    if not google_auth.is_configured():
        request.session["flash"] = "Google Calendar isn't set up on this server yet."
        return RedirectResponse("/", status_code=303)
    url, state, verifier = google_auth.start_login()
    request.session["oauth_state"] = state
    request.session["oauth_verifier"] = verifier
    return RedirectResponse(url)


@app.get("/auth/google/callback")
def google_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    expected = request.session.pop("oauth_state", None)
    verifier = request.session.pop("oauth_verifier", None)
    if error == "access_denied":
        request.session["flash"] = "Google Calendar wasn't connected because access wasn't allowed."
    elif error or not code or not expected or not secrets.compare_digest(state, expected):
        request.session["flash"] = "Google sign-in was cancelled or didn't finish. Please try again."
    else:
        try:
            connection = google_auth.finish_login(code, state, verifier)
            google_auth.save_connection(request.session, connection)
            google_integration.forget(request.session)
            request.session["flash"] = (
                "Google Calendar connected. I'll plan around your busy times together with your own commitments, "
                "and keep workouts in a GymScheduleBot calendar when you sync."
            )
        except google_auth.GoogleAuthError as exc:
            request.session["flash"] = str(exc)
        except Exception as exc:
            log.warning("Google token exchange failed: %s", type(exc).__name__)  # never log the details
            request.session["flash"] = "Couldn't finish connecting to Google. Please try again."
    return RedirectResponse("/", status_code=303)


@app.post("/auth/google/logout", dependencies=CSRF)
def google_logout(request: Request):
    connection = google_auth.load_connection(request.session)
    if connection:
        google_auth.revoke(connection)  # best effort: the local connection is removed either way
    google_auth.clear_connection(request.session)
    google_integration.forget(request.session)
    request.session["flash"] = (
        "Disconnected from Google Calendar. Your GymScheduleBot calendar stays in Google until you delete it."
    )
    return RedirectResponse("/", status_code=303)


def _connected_or_error(request: Request) -> JSONResponse | None:
    if not google_connected(request):
        return JSONResponse({"error": "Connect Google Calendar first."}, status_code=409)
    return None


@app.get("/google/calendars")
def google_calendars(request: Request):
    if problem := _connected_or_error(request):
        return problem
    settings = load_settings(request)
    view = build_view(request, settings, {})
    if view.inputs.google is None:
        return JSONResponse({"error": view.notice or google_integration.UNREACHABLE}, status_code=503)
    return {"calendars": google_integration.checklist(view.inputs.google, settings.google_excluded)}


@app.post("/google/sync/preview", dependencies=CSRF)
def google_sync_preview(request: Request, payload: dict = Body(...)):
    """What syncing would change, week by week. Nothing is written to Google here."""
    if problem := _connected_or_error(request):
        return problem
    view = build_view(request, load_settings(request), payload, refresh=True, sync_horizon=sync_horizon(request))
    if view.inputs.demo:
        return JSONResponse(
            {"error": "Clear the sample week first: syncing would add its workouts to your Google Calendar."},
            status_code=409,
        )
    if view.inputs.google is None:
        return JSONResponse({"error": view.notice or google_integration.UNREACHABLE}, status_code=503)
    weeks = google_integration.preview_weeks(request.session, view.inputs, view.week)
    return JSONResponse({"weeks": [w.to_json() for w in weeks]})


def sync_horizon(request: Request) -> date:
    settings = load_settings(request)
    today = local_now(settings.to_preferences()).date()
    return week_start(today) + timedelta(weeks=google_integration.SYNC_WEEKS)


def _changes(count: int) -> str:
    return f"{count} change" if count == 1 else f"{count} changes"


@app.post("/google/sync/apply", dependencies=CSRF)
def google_sync_apply(request: Request, payload: dict = Body(...)):
    """Make the changes the user confirmed, week by week, if they're still the ones they saw."""
    if problem := _connected_or_error(request):
        return problem
    view = build_view(request, load_settings(request), payload, refresh=True, sync_horizon=sync_horizon(request))
    if view.inputs.demo or view.inputs.google is None:
        return JSONResponse({"error": view.notice or google_integration.UNREACHABLE}, status_code=503)
    chosen = {str(w.get("week")): w for w in payload.get("weeks", []) if isinstance(w, dict)}
    weeks = google_integration.preview_weeks(request.session, view.inputs, view.week)
    picked = [w for w in weeks if w.week.isoformat() in chosen]
    if not picked:
        return JSONResponse({"error": "Choose at least one week to sync."}, status_code=400)
    if any(w.preview.fingerprint != chosen[w.week.isoformat()].get("fingerprint") for w in picked):
        return JSONResponse({"status": "changed", "weeks": [w.to_json() for w in weeks]})
    try:
        done, failed = [], []
        for w in picked:
            restore = {str(k) for k in chosen[w.week.isoformat()].get("restore", [])}
            result = google_integration.apply(
                request.session,
                w.plan,
                view.inputs.google,
                w.preview,
                restore,
                view.inputs.now,
                view.inputs.settings.timezone,
            )
            done += result.done
            failed += result.failed
    except google_auth.GoogleAuthError:
        google_auth.clear_connection(request.session)
        return JSONResponse({"error": google_integration.EXPIRED}, status_code=401)
    except google_integration.GoogleUnavailable:
        return JSONResponse(
            {"error": "Couldn't reach Google Calendar. Nothing more was changed; try again."}, status_code=503
        )
    after = build_view(request, view.inputs.settings, payload, refresh=True)
    if failed:
        reply = (
            f"Synced {_changes(len(done))}, but {len(failed)} didn't go through: "
            f"{'; '.join(failed)}. Your plan is unchanged; press Sync again to retry."
        )
    elif done:
        reply = f"Synced {_changes(len(done))} to your GymScheduleBot calendar."
    else:
        reply = "Your GymScheduleBot calendar was already up to date."
    return respond(request, after, reply, status="partial" if failed else "done")
