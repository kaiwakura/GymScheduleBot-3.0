"""A keyword parser for chat messages, used when Gemini isn't set up (or fails).

It understands common phrasings only ("I prefer mornings", "no gym on Sundays",
"move Thursday's workout to Friday", "fewer sets"); anything else comes back empty
and the coach suggests what it can do.
"""

from __future__ import annotations

import re
from datetime import date, time

from app.ai.chat_changes import EXERCISE_NAMES, WEEKDAYS, ChatChanges, MoveRequest, RecurringBusy
from app.models import Commitment, RepRange, SplitStyle, TimeOfDay, Volume

_DAY_RE = r"(mon|tues?|wed(?:nes)?|thu(?:rs)?|fri|sat(?:ur)?|sun)(?:day)?s?"
_TIME_RE = r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?"


def _weekday(token: str) -> int:
    return next(i for i, name in enumerate(WEEKDAYS) if name.startswith(token[:3].lower()))


def _to_time(hour: str, minute: str | None, ampm: str | None, fallback_ampm: str | None = None) -> time:
    h, m = int(hour), int(minute or 0)
    ampm = ampm or fallback_ampm
    if ampm == "pm" and h < 12:
        h += 12
    if ampm == "am" and h == 12:
        h = 0
    return time(min(h, 23), min(m, 59))


def parse_with_rules(text: str, today: date) -> ChatChanges:
    """Read a chat message with keywords. ``today`` is the user's local date, for "yesterday"."""
    t = text.lower()
    out = ChatChanges(source="rules")

    if re.search(r"(no|hate|not a|avoid)\s+(early\s+)?morning", t):
        out.earliest_start = time(10, 0)
        out.preferred_time = TimeOfDay.EVENING
    elif re.search(r"morning|before (work|class)|early", t):
        out.preferred_time = TimeOfDay.MORNING
    elif re.search(r"lunch|midday|noon", t):
        out.preferred_time = TimeOfDay.MIDDAY
    elif re.search(r"evening|after (work|class)|night", t):
        out.preferred_time = TimeOfDay.EVENING

    if re.search(r"dedicated|serious|committed|bodybuild|compet", t):
        out.commitment = Commitment.DEDICATED
    elif re.search(r"busy|consistent|maintain|light (week|load|schedule)|make it light", t):
        out.commitment = Commitment.LIGHT
    elif re.search(r"moderate|normal week", t):
        out.commitment = Commitment.MODERATE

    _parse_split_request(t, out)
    _parse_programming(t, out)
    out.move = _parse_move(_name_relative_days(t, today))

    if m := re.search(r"(\d{2,3})\s*(?:min|minute)", t):
        out.session_minutes = max(20, min(180, int(m.group(1))))
    elif m := re.search(r"(\d(?:\.\d)?)\s*(?:hour|hr)", t):
        out.session_minutes = max(20, min(180, int(float(m.group(1)) * 60)))

    # "no gym on sundays", "never on saturday", "sundays off", "rest on friday"
    for m in re.finditer(rf"\b(?:no|never|not|rest|off)\b[^.,;]{{0,20}}?\b{_DAY_RE}", t):
        out.blocked_weekdays.add(_weekday(m.group(1)))
    for m in re.finditer(rf"\b{_DAY_RE}\s+(?:off|are off|is off|rest)", t):
        out.blocked_weekdays.add(_weekday(m.group(1)))
    # "sundays are fine", "I can train on saturdays"
    for m in re.finditer(rf"\b{_DAY_RE}\s+(?:are|is)\s+(?:fine|ok|okay|free|open)", t):
        out.allowed_weekdays.add(_weekday(m.group(1)))
    for m in re.finditer(rf"\bcan\s+(?:train|lift|go|do)\b[^.,;]{{0,15}}?\b{_DAY_RE}", t):
        out.allowed_weekdays.add(_weekday(m.group(1)))
    out.blocked_weekdays -= out.allowed_weekdays

    # "soccer on tuesdays 6-7:30pm", "work saturday 9am to 5pm"
    pattern = rf"([a-z][a-z ]{{1,30}}?)\s+(?:on\s+)?{_DAY_RE}\s+(?:from\s+)?{_TIME_RE}\s*(?:-|to|until)\s*{_TIME_RE}"
    for m in re.finditer(pattern, t):
        title, day, h1, m1, ap1, h2, m2, ap2 = m.groups()
        end = _to_time(h2, m2, ap2)
        start = _to_time(h1, m1, ap1, fallback_ampm=ap2)
        if start < end:
            title = re.sub(r"^(i have|have|i've got|got|i do|and|also)\s+", "", title.strip())
            out.recurring_busy.append(RecurringBusy(_weekday(day), start, end, title.capitalize() or "Busy"))
    _parse_skipped(t, today, out)
    if out.move is not None:
        out.blocked_weekdays.discard(out.move.to_weekday)  # "move it to Friday" isn't "no gym on Friday"
        if out.move.from_weekday is not None:
            out.blocked_weekdays.discard(out.move.from_weekday)
        out.preferred_time = None  # "...to Friday morning" picks a time for one workout, not every workout
    return out


def _exercise_pattern(name: str) -> str:
    """ "Pull-up" matches "pull-ups", "pull ups" and "pullups"."""
    words = re.sub(r"[^a-z0-9]+", " ", name.lower()).split()
    return r"\b" + r"[\s-]?".join(map(re.escape, words)) + r"s?\b"


def _named_exercises(t: str) -> list[str]:
    """Catalog exercises mentioned in the text, longest names first so "Seated Leg Curl" beats "Leg Curl"."""
    found, taken = [], t
    for name in sorted(EXERCISE_NAMES, key=len, reverse=True):
        if re.search(_exercise_pattern(name), taken):
            found.append(name)
            taken = re.sub(_exercise_pattern(name), " ", taken)
    return found


_SWAP_WORDS = ["swap", "replace", "switch out", "change", "don'?t like", "dislike", "hate", "can'?t do", "cannot do"]
_SWAP_RE = r"\b(" + "|".join(_SWAP_WORDS + ["no more", "no", "avoid", "instead of", "without"]) + r")\b"


def _parse_programming(t: str, out: ChatChanges) -> None:
    """Simpler exercises, swaps, fewer or more sets, heavier or lighter reps, or back to the defaults."""
    if re.search(
        r"(default|normal|standard|original) (workouts?|exercises|programming)|reset (my )?(workouts?|exercises)"
        r"|back to (the )?defaults?\b(?! (split|schedule|times?|plan))",
        t,
    ):
        out.reset_programming = True
        return
    if re.search(
        r"(simpler|easier|less technical|beginner.?friendly) (exercises?|movements?|lifts?|alternatives?|options?)", t
    ):
        out.simpler_exercises = True
    elif re.search(r"(harder|more challenging|regular|free.?weight) (exercises|movements|lifts)", t):
        out.simpler_exercises = False
    if re.search(r"(fewer|less) (sets|volume)|lower volume|(cut|reduce|drop) (the |my )?(sets|volume)", t):
        out.volume = Volume.LOWER
    elif re.search(r"(more|extra) (sets|volume)|higher volume|(increase|add) (the |my |more )?(sets|volume)", t):
        out.volume = Volume.HIGHER
    elif re.search(r"(normal|standard|default) (sets|volume)", t):
        out.volume = Volume.STANDARD
    if re.search(r"heav(y|ier) (weights?|sets|lifting|loads?)|lower reps|fewer reps|low.?rep", t):
        out.reps = RepRange.HEAVIER
    elif re.search(r"light(er)? (weights?|loads?)|higher reps|more reps|high.?rep", t):
        out.reps = RepRange.LIGHTER
    elif re.search(r"(normal|standard|default) reps|normal rep range", t):
        out.reps = RepRange.STANDARD
    named = _named_exercises(t)
    if named and re.search(_SWAP_RE, t):
        out.avoid_exercises = named
    elif named and re.search(r"\b(bring back|add back|put back|i like|i want)\b", t):
        out.allow_exercises = named


_MOVE_VERB = r"\b(?:move|shift|push|put|switch|reschedule|bump|change)\b(?P<what>[^.;]{0,40}?)"
# "to Friday" wins over "on Friday", so "move my workout on Thursday to Friday" goes to Friday.
_MOVE_RES = [
    rf"{_MOVE_VERB}\bto\s+(?:a\s+)?(?:on\s+)?(?P<to>{_DAY_RE})(?P<rest>[^.;]{{0,30}})",
    rf"{_MOVE_VERB}\bon\s+(?P<to>{_DAY_RE})(?P<rest>[^.;]{{0,30}})",
]


def _name_relative_days(t: str, today: date) -> str:
    """ "Move today's workout to tomorrow" -> "move monday's workout to tuesday", using the user's date."""
    t = re.sub(r"\btomorrow\b", WEEKDAYS[(today.weekday() + 1) % 7], t)
    return re.sub(r"\btoday\b", WEEKDAYS[today.weekday()], t)


def _parse_move(t: str) -> MoveRequest | None:
    """ "Move Thursday's workout to Friday", "put my upper on saturday at 7am", "push legs to sunday evening"."""
    m = next((m for pattern in _MOVE_RES if (m := re.search(pattern, t))), None)
    if not m:
        return None
    what, rest = m.group("what"), m.group("rest")
    days = list(re.finditer(rf"\b{_DAY_RE}", what))
    named = re.search(r"\b(upper|lower|push|pull|legs|full body ?[abc]?)\b", what)
    move = MoveRequest(_weekday(m.group("to")))
    if days:
        move.from_weekday = _weekday(days[-1].group(1))
    if named:
        move.workout = named.group(1).title().replace("Body ", "Body ").strip()
    if at := re.search(rf"\bat\s+{_TIME_RE}", rest):
        hour, minute, ampm = at.groups()
        move.at = _to_time(hour, minute, ampm, fallback_ampm=None if ampm else ("am" if 6 <= int(hour) <= 11 else "pm"))
    elif re.search(r"morning|before (work|class)|early", rest):
        move.part_of_day = TimeOfDay.MORNING
    elif re.search(r"lunch|midday|noon|afternoon", rest):
        move.part_of_day = TimeOfDay.MIDDAY
    elif re.search(r"evening|night|after (work|class)", rest):
        move.part_of_day = TimeOfDay.EVENING
    return move


# Requests need an action word, so a question like "why full body?" doesn't switch splits.
_REQUEST_RE = r"\b(want|wanna|do|switch|try|use|give me|prefer|can i|could i|let'?s|rather|change|go with|put me on)\b"
_THIS_WEEK_RE = r"this week|for now|just (this|for the) week|temporar|one week|only this"
_ONGOING_RE = r"from now on|going forward|always|permanent|every week|by default|keep (it|this|doing|that)|stick with"
_SPLIT_PATTERNS = [  # most specific first
    (
        SplitStyle.PPL_SIX_DAY,
        r"(ppl|push.?pull.?legs)\s*(x\s*2|twice|two times)|(6|six).?days?\b.{0,25}(ppl|push)|"
        r"(ppl|push.?pull.?legs).{0,25}\b(6|six).?days?",
    ),
    (SplitStyle.PPL_UPPER_LOWER, r"(ppl|push.?pull.?legs)\s*(\+|and|plus|with)\s*upper"),
    (SplitStyle.PUSH_PULL_LEGS, r"\bppl\b|push.?pull.?legs"),
    (SplitStyle.UPPER_LOWER, r"upper.?(/|and|-)?.?lower"),
    (SplitStyle.FULL_BODY, r"full.?body"),
]


_SKIPPED_RE = r"\b(skipped|missed|didn'?t (go|train|lift|work ?out|make it|do it)|couldn'?t (go|train|make it))\b"


def _parse_skipped(t: str, today: date, out: ChatChanges) -> None:
    """ "I skipped my workout on Sunday", "missed yesterday's session"."""
    for clause in re.split(r"[,.;]| and | but ", t):
        if not re.search(_SKIPPED_RE, clause):
            continue
        out.skipped_weekdays |= {_weekday(m.group(1)) for m in re.finditer(rf"\b{_DAY_RE}", clause)}
        if "yesterday" in clause:
            out.skipped_weekdays.add((today.weekday() - 1) % 7)
        if re.search(r"\btoday\b", clause):
            out.skipped_weekdays.add(today.weekday())
    out.blocked_weekdays -= out.skipped_weekdays  # "didn't go on Sunday" isn't "no gym on Sundays"


def _parse_split_request(t: str, out: ChatChanges) -> None:
    """Split requests: "Upper/Lower from now on", "PPL this week?", "back to the recommended split"."""
    reset = r"(recommended|default|original) (split|plan)|switch (me )?back|go back to (the )?(recommended|default)"
    if re.search(reset + r"|reset (my |the )?split", t):
        out.reset_split = True
        return
    if re.search(_THIS_WEEK_RE, t):
        out.split_scope = "this_week"
    elif re.search(_ONGOING_RE, t):
        out.split_scope = "ongoing"
    if re.search(_REQUEST_RE, t) or out.split_scope:
        out.split_style = next((style for style, pattern in _SPLIT_PATTERNS if re.search(pattern, t)), None)
    if out.split_style is None and (out.split_scope == "this_week" or "split" not in t and len(t) > 40):
        out.split_scope = None  # "this week" or "from now on" inside a longer message isn't about the split
