"""Tests for episodic memory extraction."""

import json
import pytest

from intelligence.episodic import (
    _parse_json_response,
    _truncate_conversation,
    extract_episode_sync,
)


class TestTruncateConversation:
    def test_basic_formatting(self):
        messages = [
            {"role": "user", "content": "Fix the bug"},
            {"role": "assistant", "content": "I'll look into it"},
            {"role": "user", "content": "Thanks"},
        ]
        result = _truncate_conversation(messages)
        assert "User: Fix the bug" in result
        assert "Assistant: I'll look into it" in result

    def test_skips_system_messages(self):
        messages = [
            {"role": "system", "content": "You are an AI"},
            {"role": "user", "content": "Hello"},
        ]
        result = _truncate_conversation(messages)
        assert "You are an AI" not in result
        assert "User: Hello" in result

    def test_truncates_long_tool_results(self):
        messages = [
            {"role": "tool", "tool_name": "terminal", "content": "x" * 1000},
        ]
        result = _truncate_conversation(messages)
        assert "[truncated]" in result
        assert len(result) < 1000


class TestParseJsonResponse:
    def test_direct_json(self):
        data = {"summary": "test", "decisions": []}
        result = _parse_json_response(json.dumps(data))
        assert result["summary"] == "test"

    def test_markdown_code_block(self):
        response = '```json\n{"summary": "test"}\n```'
        result = _parse_json_response(response)
        assert result["summary"] == "test"

    def test_json_with_surrounding_text(self):
        response = 'Here is the result:\n{"summary": "test"}\nDone.'
        result = _parse_json_response(response)
        assert result["summary"] == "test"

    def test_empty_response(self):
        assert _parse_json_response("") is None
        assert _parse_json_response(None) is None

    def test_invalid_json(self):
        assert _parse_json_response("not json at all") is None


class TestExtractEpisodeSync:
    def test_skips_short_conversations(self):
        messages = [{"role": "user", "content": "hi"}]
        result = extract_episode_sync(messages, "s1", lambda s, u: "")
        assert result is None

    def test_successful_extraction(self):
        messages = [
            {"role": "user", "content": "Fix the authentication bug in login.py"},
            {"role": "assistant", "content": "I'll investigate the issue"},
            {"role": "tool", "tool_name": "read_file", "content": "file contents..."},
            {"role": "assistant", "content": "Found the bug, fixing now"},
            {"role": "user", "content": "Great, that works!"},
        ]

        mock_response = json.dumps({
            "summary": "Fixed auth bug in login.py",
            "decisions": [{"decision": "patched null check", "context": "auth", "outcome": "fixed"}],
            "problems_solved": [{"problem": "auth crash", "solution": "null check", "tools_used": ["read_file"]}],
            "key_events": [{"event": "bug fix", "importance": "high"}],
            "user_sentiment": "positive",
            "sentiment_signals": ["user said 'Great'"],
            "entities_mentioned": [{"name": "login.py", "type": "tool", "context": "auth module"}],
            "unfinished_business": [],
            "task_type": "debugging",
        })

        def mock_llm(system, user):
            return mock_response

        result = extract_episode_sync(messages, "s1", mock_llm)
        assert result is not None
        assert result["summary"] == "Fixed auth bug in login.py"
        assert result["user_sentiment"] == "positive"

    def test_handles_llm_failure(self):
        messages = [
            {"role": "user", "content": "Do something"},
            {"role": "assistant", "content": "Ok"},
            {"role": "user", "content": "Thanks"},
        ]

        def failing_llm(system, user):
            raise RuntimeError("LLM unavailable")

        result = extract_episode_sync(messages, "s1", failing_llm)
        assert result is None
