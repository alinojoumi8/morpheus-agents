"""Tests for prompt self-optimization."""

import json
import time

import pytest

from intelligence.db import IntelligenceDB
from intelligence.prompt_optimizer import (
    format_suggestions_for_display,
    generate_optimization_suggestions,
)


@pytest.fixture
def db(tmp_path):
    db = IntelligenceDB(db_path=tmp_path / "test.db", vector_dimensions=8)
    yield db
    db.close()


class TestGenerateOptimizationSuggestions:
    def test_no_data(self, db):
        def mock_llm(s, u):
            return json.dumps({"suggestions": [], "overall_assessment": "No data."})

        result = generate_optimization_suggestions(db, sync_llm_call=mock_llm)
        # No reflections or failures → returns None (not enough data)
        assert result is None

    def test_with_reflections(self, db):
        db.store_reflection(
            session_id="s1",
            went_well="Good tool selection",
            could_improve="Too verbose in responses",
            new_patterns="Check file existence before editing",
        )
        db.log_sentiment("s1", "neutral", 0.6)

        expected = {
            "suggestions": [
                {
                    "area": "communication",
                    "priority": "high",
                    "current_behavior": "Verbose responses",
                    "suggested_change": "Be more concise",
                    "rationale": "User prefers shorter answers",
                    "prompt_snippet": "Keep responses concise.",
                }
            ],
            "overall_assessment": "Performance is acceptable with room for conciseness.",
        }

        def mock_llm(s, u):
            return json.dumps(expected)

        result = generate_optimization_suggestions(db, sync_llm_call=mock_llm)
        assert result is not None
        assert len(result["suggestions"]) == 1
        assert result["suggestions"][0]["area"] == "communication"

    def test_llm_failure(self, db):
        db.store_reflection(session_id="s1", went_well="ok")

        def failing_llm(s, u):
            raise RuntimeError("LLM down")

        result = generate_optimization_suggestions(db, sync_llm_call=failing_llm)
        assert result is None


class TestFormatSuggestions:
    def test_format_empty(self):
        result = format_suggestions_for_display(None)
        assert "No optimization" in result

    def test_format_with_suggestions(self):
        data = {
            "overall_assessment": "Good performance overall.",
            "suggestions": [
                {
                    "area": "communication",
                    "priority": "high",
                    "suggested_change": "Be more concise",
                    "rationale": "User prefers brevity",
                    "prompt_snippet": "Keep it short.",
                },
                {
                    "area": "tool_usage",
                    "priority": "low",
                    "suggested_change": "Check permissions first",
                    "rationale": "Avoids common errors",
                },
            ],
        }
        result = format_suggestions_for_display(data)
        assert "Good performance" in result
        assert "Be more concise" in result
        assert "communication" in result
        assert "tool_usage" in result
