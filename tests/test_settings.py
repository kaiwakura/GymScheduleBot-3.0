"""Saved settings: validation, the one-time migration, and applying chat changes."""

from datetime import date

from app.ai.rule_parser import parse_with_rules
from app.settings import Settings

TODAY = date(2026, 11, 16)


def test_old_moderate_settings_become_high_once():
    assert Settings.from_session({"commitment": "moderate"}).commitment == "high"  # saved before 5 levels
    migrated = Settings.from_session({"commitment": "moderate"}).to_session()
    assert migrated["version"] == 2
    chosen_later = {**migrated, "commitment": "moderate"}
    assert Settings.from_session(chosen_later).commitment == "moderate"  # never migrated twice


def test_tampered_cookie_settings_are_ignored():
    s = Settings.from_session({"source": "evil", "session_minutes": 99999, "blocked_weekdays": [9, 2], "volume": "x"})
    assert not hasattr(s, "source") and s.volume == "standard"
    assert s.session_minutes == 180
    assert s.blocked_weekdays == [2]
    assert Settings.from_session({"split_style": "nonsense"}).split_style is None


def test_chat_changes_layer_on_top_of_saved_settings():
    settings = Settings(blocked_weekdays=[0])
    settings.apply(parse_with_rules("mornings, no workouts on fridays", TODAY), TODAY)
    assert settings.preferred_time == "morning" and settings.blocked_weekdays == [0, 4]


def test_an_empty_message_changes_nothing():
    assert Settings().apply(parse_with_rules("   ", TODAY), TODAY) == Settings()


def test_a_this_week_split_ends_on_sunday():
    settings = Settings().apply(parse_with_rules("Can I do Upper/Lower this week?", TODAY), TODAY)
    assert settings.active_split(date(2026, 11, 22)) is not None
    assert settings.active_split(date(2026, 11, 23)) is None


def test_back_to_defaults_undoes_exercise_changes():
    settings = Settings().apply(parse_with_rules("simpler exercises, fewer sets, no barbell rows", TODAY), TODAY)
    assert settings.simpler_exercises and settings.volume == "lower" and settings.avoid_exercises == ["Barbell Row"]
    settings.apply(parse_with_rules("back to the defaults", TODAY), TODAY)
    assert (settings.simpler_exercises, settings.volume, settings.avoid_exercises) == (False, "standard", [])
