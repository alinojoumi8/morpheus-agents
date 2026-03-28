"""Tests for the personalization engine."""

import pytest

from intelligence.personalization import (
    PersonaManager,
    WorkflowTracker,
    analyze_user_style,
    generate_style_directive,
    get_time_context,
    infer_priorities,
)


class TestAnalyzeUserStyle:
    def test_terse_style(self):
        messages = [
            {"role": "user", "content": "fix it"},
            {"role": "user", "content": "done?"},
            {"role": "user", "content": "ok"},
        ]
        style = analyze_user_style(messages)
        assert style["verbosity"] == "terse"

    def test_verbose_style(self):
        messages = [
            {"role": "user", "content": "I would like you to please investigate the authentication " * 5},
        ]
        style = analyze_user_style(messages)
        assert style["verbosity"] == "verbose"

    def test_casual_formality(self):
        messages = [
            {"role": "user", "content": "lol yeah btw can u fix it pls"},
            {"role": "user", "content": "haha omg thx"},
        ]
        style = analyze_user_style(messages)
        assert style["formality"] == "casual"

    def test_formal_formality(self):
        messages = [
            {"role": "user", "content": "Could you please investigate this issue? Thank you for your assistance."},
            {"role": "user", "content": "I would appreciate if you could kindly review the changes."},
        ]
        style = analyze_user_style(messages)
        assert style["formality"] == "formal"

    def test_empty_messages(self):
        assert analyze_user_style([]) == {}

    def test_ignores_non_user_messages(self):
        messages = [
            {"role": "assistant", "content": "Here is a very long response " * 20},
            {"role": "user", "content": "ok"},
        ]
        style = analyze_user_style(messages)
        assert style["verbosity"] == "terse"


class TestGenerateStyleDirective:
    def test_terse_directive(self):
        prefs = {
            "user_preferred_verbosity": {"value": "terse", "confidence": 0.8},
        }
        directive = generate_style_directive(prefs)
        assert "concise" in directive.lower() or "brief" in directive.lower()

    def test_low_confidence_excluded(self):
        prefs = {
            "user_preferred_verbosity": {"value": "terse", "confidence": 0.3},
        }
        directive = generate_style_directive(prefs)
        assert directive == ""

    def test_empty_preferences(self):
        assert generate_style_directive({}) == ""


class TestWorkflowTracker:
    def test_record_and_detect(self):
        tracker = WorkflowTracker()
        # Record a pattern twice
        for _ in range(3):
            tracker.record_tool_call("read_file")
            tracker.record_tool_call("patch")
            tracker.record_tool_call("terminal")

        patterns = tracker.detect_patterns()
        assert len(patterns) > 0
        # Should detect read_file → patch or similar subsequences
        found_pattern = False
        for pattern, freq in patterns:
            if freq >= 2:
                found_pattern = True
        assert found_pattern

    def test_reset(self):
        tracker = WorkflowTracker()
        tracker.record_tool_call("test")
        tracker.reset()
        assert tracker.detect_patterns() == []


class TestInferPriorities:
    def test_speed_priority(self):
        messages = [
            {"role": "user", "content": "just do it quick"},
            {"role": "user", "content": "fast please"},
        ]
        priorities = infer_priorities(messages)
        assert priorities["speed"] > 0.1

    def test_accuracy_priority(self):
        messages = [
            {"role": "user", "content": "make sure to verify and test everything"},
            {"role": "user", "content": "check it carefully please"},
        ]
        priorities = infer_priorities(messages)
        assert priorities["accuracy"] > 0.1

    def test_cost_priority(self):
        messages = [
            {"role": "user", "content": "use the cheapest option, budget is tight"},
        ]
        priorities = infer_priorities(messages)
        assert priorities["cost"] > 0.1


class TestTimeContext:
    def test_morning(self):
        ctx = get_time_context(hour=8)
        assert ctx["period"] == "morning"
        assert ctx["suggest_briefing"] is True

    def test_afternoon(self):
        ctx = get_time_context(hour=15)
        assert ctx["period"] == "afternoon"
        assert ctx["mode"] == "execution"

    def test_night(self):
        ctx = get_time_context(hour=2)
        assert ctx["period"] == "night"
        assert ctx["suggest_briefing"] is False


class TestPersonaManager:
    def test_switch_persona(self):
        pm = PersonaManager(config={
            "work": {"style": "professional", "active_hours": "9-17"},
            "personal": {"style": "casual", "active_hours": "17-23"},
        })
        assert pm.active == "default"
        assert pm.switch("work") is True
        assert pm.active == "work"

    def test_auto_select(self):
        pm = PersonaManager(config={
            "work": {"style": "professional", "active_hours": "9-17"},
            "personal": {"style": "casual", "active_hours": "17-23"},
        })
        selected = pm.auto_select(hour=10)
        assert selected == "work"

        selected = pm.auto_select(hour=20)
        assert selected == "personal"

    def test_get_style_directive(self):
        pm = PersonaManager(config={
            "work": {"style": "professional, concise"},
        })
        pm.switch("work")
        directive = pm.get_style_directive()
        assert "work" in directive
        assert "professional" in directive

    def test_list_personas(self):
        pm = PersonaManager(config={
            "work": {"style": "pro"},
        })
        personas = pm.list_personas()
        assert len(personas) == 2  # default + work
