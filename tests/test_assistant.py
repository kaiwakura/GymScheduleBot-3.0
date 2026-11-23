import json
from datetime import time, timedelta
from types import SimpleNamespace

from app.ai.assistant import (
    coach_intro,
    coach_turn,
    explain_plan,
    fallback_reply,
    parse_with_rules,
    plan_facts,
    plan_summary,
)
from app.models import Commitment, Preferences, TimeOfDay
from app.scheduler.planner import build_plan
from tests.conftest import MONDAY


# ------------------------------------------------------------ rule parser ---
def test_rules_detect_time_of_day():
    assert parse_with_rules("I like to lift in the morning", MONDAY).preferred_time is TimeOfDay.MORNING
    assert parse_with_rules("after work works best", MONDAY).preferred_time is TimeOfDay.EVENING
    no_mornings = parse_with_rules("I hate mornings", MONDAY)
    assert no_mornings.preferred_time is TimeOfDay.EVENING
    assert no_mornings.earliest_start == time(10, 0)


def test_rules_detect_blocked_days():
    assert parse_with_rules("no gym on sundays", MONDAY).blocked_weekdays == {6}
    assert parse_with_rules("Saturdays off please", MONDAY).blocked_weekdays == {5}
    # "piano" ends in "no" — must not be read as "no ... monday"
    assert parse_with_rules("piano on monday 5-6pm", MONDAY).blocked_weekdays == set()


def test_rules_detect_recurring_commitment():
    notes = parse_with_rules("I have soccer on tuesdays 6-7:30pm", MONDAY)
    [r] = notes.recurring_busy
    assert (r.weekday, r.start, r.end, r.title) == (1, time(18, 0), time(19, 30), "Soccer")


def test_rules_detect_length_and_commitment():
    notes = parse_with_rules("super busy semester, 45 min sessions max", MONDAY)
    assert notes.commitment is Commitment.LIGHT
    assert notes.session_minutes == 45


# ------------------------------------------------- Gemini path (fake client) ---
class FakeGemini:
    """Stands in for google.genai.Client: client.models.generate_content(...) -> response.text"""

    def __init__(self, text=None, error=None):
        self.text, self.error, self.calls = text, error, []
        self.models = self

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(text=self.text)


def test_coach_turn_converts_gemini_json_into_changes():
    plan = build_plan([], MONDAY, Preferences())
    changes = {
        "preferred_time": "evening",
        "commitment": "dedicated",
        "blocked_weekdays": ["sunday"],
        "recurring_busy": [{"weekday": "tuesday", "start": "18:00", "end": "19:30", "title": "Soccer"}],
    }
    client = FakeGemini(text=json.dumps({"reply": "Done!", "changes": changes}))
    message = "evenings, very committed, no sundays, soccer tue 6-7:30pm"
    turn = coach_turn(message, plan, Preferences(), MONDAY, client=client)
    assert turn.changes.source == "ai"
    assert turn.changes.commitment is Commitment.DEDICATED
    assert turn.changes.blocked_weekdays == {6}
    assert turn.changes.recurring_busy[0].start == time(18, 0)
    config = client.calls[0]["config"]
    assert config.response_mime_type == "application/json"
    schema = config.response_json_schema["properties"]["changes"]["properties"]
    assert schema["commitment"]["enum"] == ["very_light", "light", "moderate", "high", "dedicated"]
    assert "experience" not in schema  # the coach never sets or guesses training experience


def test_gemini_can_ask_to_move_a_workout_and_change_programming():
    plan = build_plan([], MONDAY, Preferences())
    changes = {
        "move_workout": {"from_day": "thursday", "to_day": "friday", "part_of_day": "morning"},
        "simpler_exercises": True,
        "volume": "lower",
        "avoid_exercises": ["Back Squat", "Made-up Lift"],
    }
    client = FakeGemini(text=json.dumps({"reply": "Sure.", "changes": changes}))
    turn = coach_turn(
        "my lab runs late thursdays; easier, shorter workouts; no squats", plan, Preferences(), MONDAY, client=client
    )
    assert (turn.changes.move.from_weekday, turn.changes.move.to_weekday) == (3, 4)
    assert turn.changes.move.part_of_day is TimeOfDay.MORNING
    assert turn.changes.simpler_exercises is True and turn.changes.volume.value == "lower"
    assert turn.changes.avoid_exercises == ["Back Squat"]  # only real catalog exercises


def test_coach_turn_falls_back_to_rules_when_gemini_is_down():
    plan = build_plan([], MONDAY, Preferences())
    turn = coach_turn("mornings please", plan, Preferences(), MONDAY, client=FakeGemini(error=RuntimeError("API down")))
    assert turn.reply is None
    assert turn.changes.source == "rules"
    assert turn.changes.preferred_time is TimeOfDay.MORNING


def test_explanation_uses_ai_text():
    plan = build_plan([], MONDAY, Preferences())
    assert explain_plan(plan, client=FakeGemini(text="  Great week ahead!  ")) == "Great week ahead!"


def test_explanation_fallback_without_ai():
    plan = build_plan([], MONDAY, Preferences())
    client = FakeGemini(error=RuntimeError("API down"))
    assert plan.split_name in explain_plan(plan, client=client)


# ------------------------------------------------------------- AI Coach chat ---
def test_rules_unblock_days_and_light_week():
    notes = parse_with_rules("sundays are fine now, make it a light week", MONDAY)
    assert notes.allowed_weekdays == {6}
    assert notes.commitment is Commitment.LIGHT


def test_describe_lists_changes_in_plain_words():
    notes = parse_with_rules("mornings, 45 min, no gym on sundays, soccer on tuesdays 6-7:30pm", MONDAY)
    assert notes.describe() == ["mornings", "45-minute sessions", "no Sun", "Soccer Tue 18:00-19:30"]
    assert parse_with_rules("hello", MONDAY).is_empty()


def test_coach_turn_with_gemini_returns_reply_and_changes():
    plan = build_plan([], MONDAY, Preferences())
    answer = {"reply": "Mornings it is!", "changes": {"preferred_time": "morning"}}
    client = FakeGemini(text=json.dumps(answer))
    turn = coach_turn("I like mornings", plan, Preferences(), MONDAY, client=client)
    assert turn.reply == "Mornings it is!"
    assert turn.changes.preferred_time is TimeOfDay.MORNING
    prompt = json.loads(client.calls[0]["contents"])
    assert prompt["current_plan"]["split"] == plan.split_name  # the coach only sees plan facts
    assert prompt["user_message"] == "I like mornings"


def test_coach_turn_falls_back_to_rules_on_bad_ai_output():
    plan = build_plan([], MONDAY, Preferences())
    turn = coach_turn("evenings please", plan, Preferences(), MONDAY, client=FakeGemini(text="{oops"))
    assert turn.reply is None
    assert turn.changes.preferred_time is TimeOfDay.EVENING


def test_coach_text_written_by_code():
    plan = build_plan([], MONDAY, Preferences(commitment=Commitment.HIGH))
    assert plan_summary(plan).startswith("Upper/Lower x2: 4 sessions on")
    assert "What would you like to change?" in coach_intro(plan)
    assert "Got it: mornings" in fallback_reply(parse_with_rules("mornings", MONDAY), plan)


def test_yesterday_means_the_day_before_today_not_before_monday(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    plan = build_plan([], MONDAY, Preferences())
    thursday = MONDAY + timedelta(days=3)
    turn = coach_turn("I skipped my workout yesterday", plan, Preferences(), thursday)
    assert turn.changes.skipped_weekdays == {2}  # Wednesday
    assert plan_facts(plan, Preferences(), thursday)["today"] == "Thursday"
