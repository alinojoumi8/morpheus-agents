"""Tests for cross-session synthesis."""

import json
import time

import pytest

from intelligence.db import IntelligenceDB
from intelligence.synthesis import (
    _format_episodes,
    synthesize_topic,
    synthesize_recent,
)


@pytest.fixture
def db(tmp_path):
    db = IntelligenceDB(db_path=tmp_path / "test.db", vector_dimensions=8)
    yield db
    db.close()


class TestFormatEpisodes:
    def test_basic_formatting(self):
        episodes = [
            {
                "summary": "Fixed auth bug",
                "user_sentiment": "positive",
                "decisions": json.dumps([{"decision": "used JWT"}]),
                "problems_solved": json.dumps([{"problem": "auth crash"}]),
            },
        ]
        result = _format_episodes(episodes)
        assert "Fixed auth bug" in result
        assert "positive" in result
        assert "used JWT" in result

    def test_empty_episodes(self):
        result = _format_episodes([])
        assert result == ""

    def test_episodes_without_details(self):
        episodes = [
            {"summary": "Quick chat", "user_sentiment": "neutral",
             "decisions": None, "problems_solved": None},
        ]
        result = _format_episodes(episodes)
        assert "Quick chat" in result


class TestSynthesizeTopic:
    def test_no_episodes_found(self, db):
        def mock_llm(s, u):
            return "Synthesis result"

        result = synthesize_topic("nonexistent topic", db, sync_llm_call=mock_llm)
        assert "No sessions found" in result

    def test_with_matching_episodes(self, db):
        db.store_episode(
            session_id="s1",
            summary="Worked on authentication module for Project X",
            user_sentiment="positive",
        )

        def mock_llm(s, u):
            return "Project X is progressing well with auth complete."

        result = synthesize_topic("Project X", db, sync_llm_call=mock_llm)
        assert result == "Project X is progressing well with auth complete."

    def test_llm_failure(self, db):
        db.store_episode(session_id="s1", summary="test topic work")

        def failing_llm(s, u):
            raise RuntimeError("LLM down")

        result = synthesize_topic("test topic", db, sync_llm_call=failing_llm)
        assert result is None


class TestSynthesizeRecent:
    def test_no_recent_episodes(self, db):
        def mock_llm(s, u):
            return "Summary"

        result = synthesize_recent(db, sync_llm_call=mock_llm, days=7)
        assert "No sessions found" in result

    def test_with_recent_episodes(self, db):
        db.store_episode(session_id="s1", summary="Did some coding")
        db.store_episode(session_id="s2", summary="Deployed the app")

        def mock_llm(s, u):
            return "This week: coding and deployment."

        result = synthesize_recent(db, sync_llm_call=mock_llm, days=7)
        assert result == "This week: coding and deployment."
