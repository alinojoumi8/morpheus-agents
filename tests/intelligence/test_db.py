"""Tests for IntelligenceDB."""

import json
import os
import tempfile
import time
from pathlib import Path

import pytest

from intelligence.db import IntelligenceDB


@pytest.fixture
def db(tmp_path):
    """Create a temporary IntelligenceDB for testing."""
    db_path = tmp_path / "test_intelligence.db"
    db = IntelligenceDB(db_path=db_path, vector_dimensions=8)
    yield db
    db.close()


class TestEmbeddingOperations:
    def test_store_and_retrieve_embedding(self, db):
        row_id = db.store_embedding(
            content="test content",
            content_type="episode",
            metadata={"key": "value"},
            session_id="session-1",
            tier="warm",
        )
        assert row_id > 0

    def test_store_embedding_with_vector(self, db):
        embedding = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
        row_id = db.store_embedding(
            content="vector test",
            content_type="strategy",
            embedding=embedding,
        )
        assert row_id > 0

    def test_fallback_text_search(self, db):
        db.store_embedding(content="debugging python errors", content_type="episode")
        db.store_embedding(content="deploying docker containers", content_type="episode")
        db.store_embedding(content="writing unit tests", content_type="strategy")

        results = db._fallback_text_search(content_type="episode", limit=5)
        assert len(results) == 2
        assert all(r["content_type"] == "episode" for r in results)

    def test_update_access(self, db):
        row_id = db.store_embedding(content="test", content_type="episode")
        db.update_access(row_id)
        db.update_access(row_id)

        result = db._execute_read(
            lambda conn: conn.execute(
                "SELECT access_count FROM embeddings WHERE id = ?", (row_id,)
            ).fetchone()
        )
        assert result["access_count"] == 2

    def test_update_tier(self, db):
        row_id = db.store_embedding(content="test", content_type="episode", tier="warm")
        db.update_tier(row_id, "hot")

        result = db._execute_read(
            lambda conn: conn.execute(
                "SELECT tier FROM embeddings WHERE id = ?", (row_id,)
            ).fetchone()
        )
        assert result["tier"] == "hot"

    def test_decay_relevance(self, db):
        # Store an entry with old last_accessed_at
        row_id = db.store_embedding(content="old entry", content_type="episode")
        # Manually set last_accessed_at to 30 days ago
        db._execute_write(
            lambda conn: conn.execute(
                "UPDATE embeddings SET last_accessed_at = ? WHERE id = ?",
                (time.time() - 30 * 86400, row_id),
            )
        )

        count = db.decay_relevance(decay_factor=0.5, inactive_days=7)
        assert count >= 1

    def test_get_hot_memories(self, db):
        db.store_embedding(content="hot1", content_type="episode", tier="hot")
        db.store_embedding(content="warm1", content_type="episode", tier="warm")
        db.store_embedding(content="hot2", content_type="strategy", tier="hot")

        hot = db.get_hot_memories(limit=10)
        assert len(hot) == 2
        assert all(r["content"].startswith("hot") for r in hot)


class TestEpisodeOperations:
    def test_store_episode(self, db):
        ep_id = db.store_episode(
            session_id="sess-1",
            summary="Fixed a critical bug in the auth module",
            decisions=[{"decision": "used JWT", "context": "security", "outcome": "success"}],
            problems_solved=[{"problem": "auth crash", "solution": "fixed null check"}],
            user_sentiment="positive",
            sentiment_signals=["user said 'great'"],
        )
        assert ep_id > 0

    def test_get_recent_episodes(self, db):
        db.store_episode(session_id="s1", summary="First session")
        db.store_episode(session_id="s2", summary="Second session")
        db.store_episode(session_id="s3", summary="Third session")

        recent = db.get_recent_episodes(limit=2)
        assert len(recent) == 2
        assert recent[0]["summary"] == "Third session"


class TestSentimentOperations:
    def test_log_sentiment(self, db):
        sid = db.log_sentiment("sess-1", "positive", 0.8, ["user thanked"])
        assert sid > 0

    def test_get_sentiment_trend(self, db):
        db.log_sentiment("s1", "positive", 0.8)
        db.log_sentiment("s2", "neutral", 0.5)
        db.log_sentiment("s3", "frustrated", 0.7)

        trend = db.get_sentiment_trend(limit=10)
        assert len(trend) == 3


class TestSkillScoring:
    def test_log_and_aggregate_scores(self, db):
        db.log_skill_score("git-helper", "success", 0.9, "s1")
        db.log_skill_score("git-helper", "success", 0.8, "s2")
        db.log_skill_score("git-helper", "failure", 0.2, "s3")

        scores = db.get_skill_aggregate_scores()
        assert "git-helper" in scores
        assert scores["git-helper"]["total"] == 3
        assert scores["git-helper"]["successes"] == 2


class TestStrategyPlaybook:
    def test_store_and_retrieve_strategy(self, db):
        sid = db.store_strategy(
            task_type="debugging",
            approach="use print statements then narrow down",
            tool_chain=["terminal", "read_file", "patch"],
        )
        assert sid > 0

        strategies = db.get_strategies_for_task("debugging")
        assert len(strategies) == 1
        assert strategies[0]["approach"] == "use print statements then narrow down"

    def test_update_strategy_usage(self, db):
        sid = db.store_strategy("coding", "write tests first")
        db.update_strategy_usage(sid, success=True)
        db.update_strategy_usage(sid, success=True)
        db.update_strategy_usage(sid, success=False)

        strategies = db.get_strategies_for_task("coding")
        assert strategies[0]["use_count"] == 4  # 1 initial + 3 updates


class TestFailureJournal:
    def test_log_failure(self, db):
        fid = db.log_failure(
            error_type="tool_error",
            error_message="Permission denied",
            session_id="s1",
            root_cause="Missing sudo",
            preventable=True,
            prevention_strategy="Check permissions first",
        )
        assert fid > 0


class TestKnowledgeGraph:
    def test_upsert_entity(self, db):
        eid = db.upsert_entity("Alice", "person", {"role": "developer"}, "s1")
        assert eid > 0

        # Upsert again should update mention count
        eid2 = db.upsert_entity("Alice", "person", {"team": "backend"}, "s2")
        assert eid2 == eid

    def test_add_relationship(self, db):
        e1 = db.upsert_entity("Alice", "person")
        e2 = db.upsert_entity("ProjectX", "project")

        rid = db.add_relationship(e1, e2, "works_on")
        assert rid > 0

    def test_query_entity_relationships(self, db):
        e1 = db.upsert_entity("Bob", "person")
        e2 = db.upsert_entity("React", "tool")
        db.add_relationship(e1, e2, "uses")

        result = db.query_entity_relationships("Bob")
        assert result["entity"] is not None
        assert len(result["relationships"]) == 1
        assert result["relationships"][0]["relationship_type"] == "uses"


class TestBookmarks:
    def test_store_bookmark(self, db):
        bid = db.store_bookmark(
            url="https://example.com",
            title="Example",
            resource_type="url",
            tags=["reference", "docs"],
            context="Found during research",
        )
        assert bid > 0


class TestUserPreferences:
    def test_set_and_get_preferences(self, db):
        db.set_preference("verbosity", "terse", 0.8, ["short messages"])
        db.set_preference("formality", "casual", 0.6)

        prefs = db.get_preferences(min_confidence=0.5)
        assert "verbosity" in prefs
        assert prefs["verbosity"]["value"] == "terse"
        assert "formality" in prefs

    def test_upsert_preference(self, db):
        db.set_preference("verbosity", "terse", 0.5)
        db.set_preference("verbosity", "verbose", 0.9)

        prefs = db.get_preferences()
        assert prefs["verbosity"]["value"] == "verbose"
        assert prefs["verbosity"]["confidence"] == 0.9


class TestWorkflowPatterns:
    def test_record_and_retrieve_patterns(self, db):
        pid = db.record_workflow_pattern("edit", ["read_file", "patch", "terminal"])
        assert pid > 0

        # Record same pattern again — frequency should increase
        pid2 = db.record_workflow_pattern("edit", ["read_file", "patch", "terminal"])
        assert pid2 == pid

        patterns = db.get_frequent_patterns(min_frequency=2)
        assert len(patterns) == 1
        assert patterns[0]["frequency"] == 2


class TestPlans:
    def test_create_and_update_plan(self, db):
        pid = db.create_plan(
            goal="Deploy application",
            steps=[
                {"id": 1, "description": "Build", "status": "pending"},
                {"id": 2, "description": "Test", "status": "pending"},
                {"id": 3, "description": "Deploy", "status": "pending"},
            ],
        )
        assert pid > 0

        db.update_plan(pid, steps=[
            {"id": 1, "description": "Build", "status": "completed"},
            {"id": 2, "description": "Test", "status": "in_progress"},
            {"id": 3, "description": "Deploy", "status": "pending"},
        ], status="active")


class TestReflections:
    def test_store_reflection(self, db):
        rid = db.store_reflection(
            session_id="s1",
            went_well="Good tool selection",
            could_improve="Should verify before committing",
            new_patterns="Always run tests after edit",
        )
        assert rid > 0


class TestExpertiseMap:
    def test_update_and_get_expertise(self, db):
        db.update_expertise("python", "expert", ["corrected agent twice"])
        db.update_expertise("react", "beginner", ["asked basic questions"])

        expertise = db.get_expertise_map()
        assert expertise["python"]["proficiency"] == "expert"
        assert expertise["react"]["proficiency"] == "beginner"
