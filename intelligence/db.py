"""
IntelligenceDB — SQLite-backed storage for the intelligence module.

Separate database (~/.morpheus/intelligence.db) to avoid touching state.db.
Mirrors SessionDB patterns: WAL mode, write-retry with jitter, thread safety.
Optionally loads sqlite-vec for vector similarity search.
"""

import json
import logging
import os
import random
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar

from morpheus_constants import get_morpheus_home

logger = logging.getLogger(__name__)

T = TypeVar("T")

DEFAULT_DB_PATH = get_morpheus_home() / "intelligence.db"

SCHEMA_VERSION = 1

# ── Vector extension availability ──

_VEC_AVAILABLE: Optional[bool] = None


def _check_vec_available() -> bool:
    """Check if sqlite-vec extension is importable."""
    global _VEC_AVAILABLE
    if _VEC_AVAILABLE is not None:
        return _VEC_AVAILABLE
    try:
        import sqlite_vec  # noqa: F401
        _VEC_AVAILABLE = True
    except ImportError:
        _VEC_AVAILABLE = False
        logger.info("sqlite-vec not installed — vector search disabled. "
                     "Install with: pip install sqlite-vec")
    return _VEC_AVAILABLE


def _load_vec_extension(conn: sqlite3.Connection) -> bool:
    """Load sqlite-vec extension into connection. Returns True on success."""
    if not _check_vec_available():
        return False
    try:
        import sqlite_vec
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        return True
    except Exception as exc:
        logger.warning("Failed to load sqlite-vec: %s", exc)
        return False


# ── Schema ──

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

-- Embeddings store (vector search via sqlite-vec)
CREATE TABLE IF NOT EXISTS embeddings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    content_type TEXT NOT NULL,
    metadata TEXT,
    created_at REAL NOT NULL,
    updated_at REAL,
    access_count INTEGER DEFAULT 0,
    last_accessed_at REAL,
    relevance_score REAL DEFAULT 1.0,
    session_id TEXT,
    tier TEXT DEFAULT 'warm',
    persona TEXT DEFAULT 'default'
);

CREATE INDEX IF NOT EXISTS idx_embeddings_type ON embeddings(content_type);
CREATE INDEX IF NOT EXISTS idx_embeddings_tier ON embeddings(tier);
CREATE INDEX IF NOT EXISTS idx_embeddings_relevance ON embeddings(relevance_score DESC);
CREATE INDEX IF NOT EXISTS idx_embeddings_persona ON embeddings(persona);

-- Episodic memory
CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    summary TEXT NOT NULL,
    decisions TEXT,
    problems_solved TEXT,
    key_events TEXT,
    user_sentiment TEXT,
    sentiment_signals TEXT,
    created_at REAL NOT NULL,
    embedding_id INTEGER REFERENCES embeddings(id)
);

CREATE INDEX IF NOT EXISTS idx_episodes_session ON episodes(session_id);
CREATE INDEX IF NOT EXISTS idx_episodes_sentiment ON episodes(user_sentiment);

-- Sentiment tracking
CREATE TABLE IF NOT EXISTS sentiment_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    overall TEXT NOT NULL,
    confidence REAL,
    signals TEXT,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sentiment_session ON sentiment_log(session_id);

-- Skill scoring
CREATE TABLE IF NOT EXISTS skill_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_name TEXT NOT NULL,
    session_id TEXT,
    outcome TEXT NOT NULL,
    score REAL,
    context TEXT,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_skill_scores_name ON skill_scores(skill_name);

-- Strategy playbook
CREATE TABLE IF NOT EXISTS strategies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_type TEXT NOT NULL,
    approach TEXT NOT NULL,
    tool_chain TEXT,
    success_rate REAL DEFAULT 1.0,
    use_count INTEGER DEFAULT 1,
    last_used_at REAL,
    created_at REAL NOT NULL,
    embedding_id INTEGER REFERENCES embeddings(id)
);

CREATE INDEX IF NOT EXISTS idx_strategies_type ON strategies(task_type);
CREATE INDEX IF NOT EXISTS idx_strategies_success ON strategies(success_rate DESC);

-- Failure journal
CREATE TABLE IF NOT EXISTS failure_journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    error_type TEXT NOT NULL,
    error_message TEXT,
    full_context TEXT,
    root_cause TEXT,
    resolution TEXT,
    preventable INTEGER DEFAULT 0,
    prevention_strategy TEXT,
    created_at REAL NOT NULL,
    embedding_id INTEGER REFERENCES embeddings(id)
);

CREATE INDEX IF NOT EXISTS idx_failures_type ON failure_journal(error_type);

-- Knowledge graph: entities
CREATE TABLE IF NOT EXISTS entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    attributes TEXT,
    first_seen_session TEXT,
    last_seen_session TEXT,
    mention_count INTEGER DEFAULT 1,
    created_at REAL NOT NULL,
    updated_at REAL,
    embedding_id INTEGER REFERENCES embeddings(id)
);

CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name);
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type);

-- Knowledge graph: relationships
CREATE TABLE IF NOT EXISTS relationships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_entity_id INTEGER NOT NULL REFERENCES entities(id),
    target_entity_id INTEGER NOT NULL REFERENCES entities(id),
    relationship_type TEXT NOT NULL,
    strength REAL DEFAULT 1.0,
    context TEXT,
    created_at REAL NOT NULL,
    updated_at REAL
);

CREATE INDEX IF NOT EXISTS idx_rel_source ON relationships(source_entity_id);
CREATE INDEX IF NOT EXISTS idx_rel_target ON relationships(target_entity_id);
CREATE INDEX IF NOT EXISTS idx_rel_type ON relationships(relationship_type);

-- Bookmarks
CREATE TABLE IF NOT EXISTS bookmarks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT,
    title TEXT,
    resource_type TEXT,
    tags TEXT,
    context TEXT,
    session_id TEXT,
    created_at REAL NOT NULL,
    embedding_id INTEGER REFERENCES embeddings(id)
);

CREATE INDEX IF NOT EXISTS idx_bookmarks_type ON bookmarks(resource_type);

-- User preferences (personalization)
CREATE TABLE IF NOT EXISTS user_preferences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    preference_key TEXT UNIQUE NOT NULL,
    preference_value TEXT NOT NULL,
    confidence REAL DEFAULT 0.5,
    evidence TEXT,
    updated_at REAL NOT NULL
);

-- Workflow patterns
CREATE TABLE IF NOT EXISTS workflow_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_name TEXT,
    trigger TEXT NOT NULL,
    tool_sequence TEXT NOT NULL,
    frequency INTEGER DEFAULT 1,
    last_seen_at REAL,
    automation_offered INTEGER DEFAULT 0,
    automation_accepted INTEGER,
    created_at REAL NOT NULL
);

-- Expertise mapping
CREATE TABLE IF NOT EXISTS expertise_map (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT NOT NULL,
    proficiency TEXT NOT NULL,
    evidence TEXT,
    updated_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_expertise_domain ON expertise_map(domain);

-- Plans (multi-step planning with backtracking)
CREATE TABLE IF NOT EXISTS plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    goal TEXT NOT NULL,
    steps TEXT NOT NULL,
    status TEXT DEFAULT 'active',
    backtrack_count INTEGER DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL
);

CREATE INDEX IF NOT EXISTS idx_plans_status ON plans(status);

-- Reflections (post-session self-eval)
CREATE TABLE IF NOT EXISTS reflections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    went_well TEXT,
    could_improve TEXT,
    new_patterns TEXT,
    created_at REAL NOT NULL,
    embedding_id INTEGER REFERENCES embeddings(id)
);
"""

# sqlite-vec virtual table (created separately since it needs the extension)
VEC_TABLE_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS vec_embeddings USING vec0(
    embedding float[{dimensions}]
);
"""


class IntelligenceDB:
    """
    SQLite-backed intelligence storage with optional vector search.

    Thread-safe. Mirrors morpheus_state.SessionDB patterns.
    """

    _WRITE_MAX_RETRIES = 15
    _WRITE_RETRY_MIN_S = 0.020
    _WRITE_RETRY_MAX_S = 0.150
    _CHECKPOINT_EVERY_N_WRITES = 50

    def __init__(self, db_path: Path = None, vector_dimensions: int = 384):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.vector_dimensions = vector_dimensions

        self._lock = threading.Lock()
        self._write_count = 0
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            timeout=1.0,
            isolation_level=None,
        )
        self._conn.row_factory = sqlite3.Row

        # Enable WAL mode
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")

        # Try to load vector extension
        self._vec_loaded = _load_vec_extension(self._conn)

        self._init_schema()

    def _init_schema(self):
        """Create tables if they don't exist."""
        with self._lock:
            self._conn.executescript(SCHEMA_SQL)

            # Check schema version
            cur = self._conn.execute("SELECT version FROM schema_version")
            row = cur.fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO schema_version (version) VALUES (?)",
                    (SCHEMA_VERSION,),
                )
            elif row[0] < SCHEMA_VERSION:
                self._migrate(row[0])

            # Create vector table if extension available
            if self._vec_loaded:
                try:
                    self._conn.executescript(
                        VEC_TABLE_SQL.format(dimensions=self.vector_dimensions)
                    )
                except Exception as exc:
                    logger.warning("Failed to create vec table: %s", exc)
                    self._vec_loaded = False

    def _migrate(self, from_version: int):
        """Run schema migrations."""
        # Future migrations go here
        self._conn.execute(
            "UPDATE schema_version SET version = ?", (SCHEMA_VERSION,)
        )

    @property
    def vec_available(self) -> bool:
        """Whether vector search is available."""
        return self._vec_loaded

    # ── Write helper (mirrors SessionDB._execute_write) ──

    def _execute_write(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        """Execute a write transaction with BEGIN IMMEDIATE and jitter retry."""
        last_err: Optional[Exception] = None
        for attempt in range(self._WRITE_MAX_RETRIES):
            try:
                with self._lock:
                    self._conn.execute("BEGIN IMMEDIATE")
                    try:
                        result = fn(self._conn)
                        self._conn.commit()
                    except BaseException:
                        try:
                            self._conn.rollback()
                        except Exception:
                            pass
                        raise
                self._write_count += 1
                if self._write_count % self._CHECKPOINT_EVERY_N_WRITES == 0:
                    self._try_wal_checkpoint()
                return result
            except sqlite3.OperationalError as exc:
                err_msg = str(exc).lower()
                if "locked" in err_msg or "busy" in err_msg:
                    last_err = exc
                    if attempt < self._WRITE_MAX_RETRIES - 1:
                        jitter = random.uniform(
                            self._WRITE_RETRY_MIN_S,
                            self._WRITE_RETRY_MAX_S,
                        )
                        time.sleep(jitter)
                        continue
                raise
        raise last_err or sqlite3.OperationalError(
            "database is locked after max retries"
        )

    def _try_wal_checkpoint(self) -> None:
        """Best-effort PASSIVE WAL checkpoint."""
        try:
            with self._lock:
                self._conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except Exception:
            pass

    # ── Read helper ──

    def _execute_read(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        """Execute a read-only query."""
        with self._lock:
            return fn(self._conn)

    # ══════════════════════════════════════════════════════════════════
    # Embedding operations
    # ══════════════════════════════════════════════════════════════════

    def store_embedding(
        self,
        content: str,
        content_type: str,
        embedding: Optional[List[float]] = None,
        metadata: Optional[Dict] = None,
        session_id: Optional[str] = None,
        tier: str = "warm",
        persona: str = "default",
    ) -> int:
        """Store content with optional vector embedding. Returns embedding row id."""
        now = time.time()

        def _write(conn):
            cur = conn.execute(
                """INSERT INTO embeddings
                   (content, content_type, metadata, created_at, session_id, tier, persona)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (content, content_type, json.dumps(metadata) if metadata else None,
                 now, session_id, tier, persona),
            )
            row_id = cur.lastrowid

            # Store vector if available
            if embedding and self._vec_loaded:
                try:
                    import struct
                    blob = struct.pack(f"{len(embedding)}f", *embedding)
                    conn.execute(
                        "INSERT INTO vec_embeddings (rowid, embedding) VALUES (?, ?)",
                        (row_id, blob),
                    )
                except Exception as exc:
                    logger.warning("Failed to store vector: %s", exc)

            return row_id

        return self._execute_write(_write)

    def vector_search(
        self,
        query_embedding: List[float],
        content_type: Optional[str] = None,
        limit: int = 10,
        min_relevance: float = 0.0,
        tier: Optional[str] = None,
        persona: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Search embeddings by vector similarity. Returns list of matches."""
        if not self._vec_loaded or not query_embedding:
            return self._fallback_text_search(
                content_type=content_type, limit=limit, tier=tier, persona=persona
            )

        def _read(conn):
            import struct
            blob = struct.pack(f"{len(query_embedding)}f", *query_embedding)

            # sqlite-vec similarity search
            rows = conn.execute(
                """SELECT v.rowid, v.distance, e.content, e.content_type,
                          e.metadata, e.created_at, e.tier, e.relevance_score,
                          e.session_id, e.persona
                   FROM vec_embeddings v
                   JOIN embeddings e ON e.id = v.rowid
                   WHERE v.embedding MATCH ?
                   AND k = ?
                   ORDER BY v.distance ASC""",
                (blob, limit * 3),  # over-fetch for filtering
            ).fetchall()

            results = []
            for row in rows:
                # Apply filters
                if content_type and row["content_type"] != content_type:
                    continue
                if tier and row["tier"] != tier:
                    continue
                if persona and row["persona"] != persona:
                    continue
                if row["relevance_score"] < min_relevance:
                    continue

                results.append({
                    "id": row["rowid"],
                    "content": row["content"],
                    "content_type": row["content_type"],
                    "metadata": json.loads(row["metadata"]) if row["metadata"] else None,
                    "distance": row["distance"],
                    "relevance_score": row["relevance_score"],
                    "created_at": row["created_at"],
                    "session_id": row["session_id"],
                    "tier": row["tier"],
                    "persona": row["persona"],
                })
                if len(results) >= limit:
                    break

            return results

        try:
            return self._execute_read(_read)
        except Exception as exc:
            logger.warning("Vector search failed, using text fallback: %s", exc)
            return self._fallback_text_search(
                content_type=content_type, limit=limit, tier=tier, persona=persona
            )

    def _fallback_text_search(
        self,
        content_type: Optional[str] = None,
        limit: int = 10,
        tier: Optional[str] = None,
        persona: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Fallback: return recent entries by relevance when vector search unavailable."""
        def _read(conn):
            conditions = []
            params = []
            if content_type:
                conditions.append("content_type = ?")
                params.append(content_type)
            if tier:
                conditions.append("tier = ?")
                params.append(tier)
            if persona:
                conditions.append("persona = ?")
                params.append(persona)

            where = "WHERE " + " AND ".join(conditions) if conditions else ""
            rows = conn.execute(
                f"""SELECT id, content, content_type, metadata, created_at,
                           tier, relevance_score, session_id, persona
                    FROM embeddings {where}
                    ORDER BY relevance_score DESC, created_at DESC
                    LIMIT ?""",
                params + [limit],
            ).fetchall()

            return [
                {
                    "id": row["id"],
                    "content": row["content"],
                    "content_type": row["content_type"],
                    "metadata": json.loads(row["metadata"]) if row["metadata"] else None,
                    "distance": None,
                    "relevance_score": row["relevance_score"],
                    "created_at": row["created_at"],
                    "session_id": row["session_id"],
                    "tier": row["tier"],
                    "persona": row["persona"],
                }
                for row in rows
            ]

        return self._execute_read(_read)

    def update_access(self, embedding_id: int) -> None:
        """Bump access count and timestamp for an embedding."""
        now = time.time()

        def _write(conn):
            conn.execute(
                """UPDATE embeddings
                   SET access_count = access_count + 1,
                       last_accessed_at = ?
                   WHERE id = ?""",
                (now, embedding_id),
            )

        self._execute_write(_write)

    def update_tier(self, embedding_id: int, new_tier: str) -> None:
        """Change the tier of an embedding."""
        now = time.time()

        def _write(conn):
            conn.execute(
                "UPDATE embeddings SET tier = ?, updated_at = ? WHERE id = ?",
                (new_tier, now, embedding_id),
            )

        self._execute_write(_write)

    def decay_relevance(self, decay_factor: float = 0.95, inactive_days: int = 7) -> int:
        """Decay relevance for entries not accessed recently. Returns count updated."""
        cutoff = time.time() - (inactive_days * 86400)

        def _write(conn):
            cur = conn.execute(
                """UPDATE embeddings
                   SET relevance_score = relevance_score * ?,
                       updated_at = ?
                   WHERE (last_accessed_at IS NULL OR last_accessed_at < ?)
                   AND relevance_score > 0.05""",
                (decay_factor, time.time(), cutoff),
            )
            return cur.rowcount

        return self._execute_write(_write)

    def get_hot_memories(self, limit: int = 10, persona: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get top hot-tier memories for system prompt injection."""
        def _read(conn):
            params = ["hot"]
            extra = ""
            if persona:
                extra = " AND persona = ?"
                params.append(persona)
            params.append(limit)

            rows = conn.execute(
                f"""SELECT id, content, content_type, metadata, relevance_score
                    FROM embeddings
                    WHERE tier = ?{extra}
                    ORDER BY relevance_score DESC, access_count DESC
                    LIMIT ?""",
                params,
            ).fetchall()

            return [
                {
                    "id": row["id"],
                    "content": row["content"],
                    "content_type": row["content_type"],
                    "metadata": json.loads(row["metadata"]) if row["metadata"] else None,
                    "relevance_score": row["relevance_score"],
                }
                for row in rows
            ]

        return self._execute_read(_read)

    # ══════════════════════════════════════════════════════════════════
    # Episode operations
    # ══════════════════════════════════════════════════════════════════

    def store_episode(
        self,
        session_id: str,
        summary: str,
        decisions: Optional[List[Dict]] = None,
        problems_solved: Optional[List[Dict]] = None,
        key_events: Optional[List[Dict]] = None,
        user_sentiment: Optional[str] = None,
        sentiment_signals: Optional[List[str]] = None,
        embedding_id: Optional[int] = None,
    ) -> int:
        """Store an episodic memory. Returns episode id."""
        now = time.time()

        def _write(conn):
            cur = conn.execute(
                """INSERT INTO episodes
                   (session_id, summary, decisions, problems_solved, key_events,
                    user_sentiment, sentiment_signals, created_at, embedding_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id, summary,
                    json.dumps(decisions) if decisions else None,
                    json.dumps(problems_solved) if problems_solved else None,
                    json.dumps(key_events) if key_events else None,
                    user_sentiment,
                    json.dumps(sentiment_signals) if sentiment_signals else None,
                    now, embedding_id,
                ),
            )
            return cur.lastrowid

        return self._execute_write(_write)

    def get_recent_episodes(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent episodic memories."""
        def _read(conn):
            rows = conn.execute(
                """SELECT * FROM episodes ORDER BY created_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

        return self._execute_read(_read)

    # ══════════════════════════════════════════════════════════════════
    # Sentiment operations
    # ══════════════════════════════════════════════════════════════════

    def log_sentiment(
        self,
        session_id: str,
        overall: str,
        confidence: float = 0.5,
        signals: Optional[List[str]] = None,
    ) -> int:
        """Log a sentiment observation."""
        now = time.time()

        def _write(conn):
            cur = conn.execute(
                """INSERT INTO sentiment_log (session_id, overall, confidence, signals, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (session_id, overall, confidence,
                 json.dumps(signals) if signals else None, now),
            )
            return cur.lastrowid

        return self._execute_write(_write)

    def get_sentiment_trend(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get recent sentiment observations for trend analysis."""
        def _read(conn):
            rows = conn.execute(
                "SELECT * FROM sentiment_log ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

        return self._execute_read(_read)

    # ══════════════════════════════════════════════════════════════════
    # Skill scoring
    # ══════════════════════════════════════════════════════════════════

    def log_skill_score(
        self,
        skill_name: str,
        outcome: str,
        score: float,
        session_id: Optional[str] = None,
        context: Optional[Dict] = None,
    ) -> int:
        """Log a skill invocation score."""
        now = time.time()

        def _write(conn):
            cur = conn.execute(
                """INSERT INTO skill_scores (skill_name, session_id, outcome, score, context, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (skill_name, session_id, outcome, score,
                 json.dumps(context) if context else None, now),
            )
            return cur.lastrowid

        return self._execute_write(_write)

    def get_skill_aggregate_scores(self) -> Dict[str, Dict[str, Any]]:
        """Get aggregate scores per skill."""
        def _read(conn):
            rows = conn.execute(
                """SELECT skill_name,
                          COUNT(*) as total,
                          AVG(score) as avg_score,
                          SUM(CASE WHEN outcome = 'success' THEN 1 ELSE 0 END) as successes,
                          SUM(CASE WHEN outcome = 'failure' THEN 1 ELSE 0 END) as failures
                   FROM skill_scores
                   GROUP BY skill_name
                   ORDER BY avg_score DESC""",
            ).fetchall()
            return {
                row["skill_name"]: {
                    "total": row["total"],
                    "avg_score": row["avg_score"],
                    "successes": row["successes"],
                    "failures": row["failures"],
                }
                for row in rows
            }

        return self._execute_read(_read)

    # ══════════════════════════════════════════════════════════════════
    # Strategy playbook
    # ══════════════════════════════════════════════════════════════════

    def store_strategy(
        self,
        task_type: str,
        approach: str,
        tool_chain: Optional[List[str]] = None,
        embedding_id: Optional[int] = None,
    ) -> int:
        """Store a new strategy."""
        now = time.time()

        def _write(conn):
            cur = conn.execute(
                """INSERT INTO strategies
                   (task_type, approach, tool_chain, created_at, last_used_at, embedding_id)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (task_type, approach,
                 json.dumps(tool_chain) if tool_chain else None,
                 now, now, embedding_id),
            )
            return cur.lastrowid

        return self._execute_write(_write)

    def update_strategy_usage(self, strategy_id: int, success: bool) -> None:
        """Update strategy after use."""
        now = time.time()

        def _write(conn):
            # Rolling average update
            row = conn.execute(
                "SELECT success_rate, use_count FROM strategies WHERE id = ?",
                (strategy_id,),
            ).fetchone()
            if row:
                old_rate = row["success_rate"] or 1.0
                old_count = row["use_count"] or 1
                new_rate = (old_rate * old_count + (1.0 if success else 0.0)) / (old_count + 1)
                conn.execute(
                    """UPDATE strategies
                       SET success_rate = ?, use_count = use_count + 1, last_used_at = ?
                       WHERE id = ?""",
                    (new_rate, now, strategy_id),
                )

        self._execute_write(_write)

    def get_strategies_for_task(self, task_type: str, limit: int = 3) -> List[Dict[str, Any]]:
        """Get top strategies for a task type."""
        def _read(conn):
            rows = conn.execute(
                """SELECT * FROM strategies
                   WHERE task_type = ?
                   ORDER BY success_rate DESC, use_count DESC
                   LIMIT ?""",
                (task_type, limit),
            ).fetchall()
            return [dict(row) for row in rows]

        return self._execute_read(_read)

    # ══════════════════════════════════════════════════════════════════
    # Failure journal
    # ══════════════════════════════════════════════════════════════════

    def log_failure(
        self,
        error_type: str,
        error_message: str,
        session_id: Optional[str] = None,
        full_context: Optional[Dict] = None,
        root_cause: Optional[str] = None,
        resolution: Optional[str] = None,
        preventable: bool = False,
        prevention_strategy: Optional[str] = None,
        embedding_id: Optional[int] = None,
    ) -> int:
        """Log a failure to the journal."""
        now = time.time()

        def _write(conn):
            cur = conn.execute(
                """INSERT INTO failure_journal
                   (session_id, error_type, error_message, full_context, root_cause,
                    resolution, preventable, prevention_strategy, created_at, embedding_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id, error_type, error_message,
                    json.dumps(full_context) if full_context else None,
                    root_cause, resolution, 1 if preventable else 0,
                    prevention_strategy, now, embedding_id,
                ),
            )
            return cur.lastrowid

        return self._execute_write(_write)

    # ══════════════════════════════════════════════════════════════════
    # Knowledge graph
    # ══════════════════════════════════════════════════════════════════

    def upsert_entity(
        self,
        name: str,
        entity_type: str,
        attributes: Optional[Dict] = None,
        session_id: Optional[str] = None,
        embedding_id: Optional[int] = None,
    ) -> int:
        """Insert or update an entity. Returns entity id."""
        now = time.time()

        def _write(conn):
            existing = conn.execute(
                "SELECT id, mention_count FROM entities WHERE name = ? AND entity_type = ?",
                (name, entity_type),
            ).fetchone()

            if existing:
                # Merge attributes
                old_attrs = {}
                old_row = conn.execute(
                    "SELECT attributes FROM entities WHERE id = ?",
                    (existing["id"],),
                ).fetchone()
                if old_row and old_row["attributes"]:
                    try:
                        old_attrs = json.loads(old_row["attributes"])
                    except (json.JSONDecodeError, TypeError):
                        pass
                if attributes:
                    old_attrs.update(attributes)

                conn.execute(
                    """UPDATE entities
                       SET mention_count = mention_count + 1,
                           last_seen_session = ?,
                           attributes = ?,
                           updated_at = ?,
                           embedding_id = COALESCE(?, embedding_id)
                       WHERE id = ?""",
                    (session_id, json.dumps(old_attrs) if old_attrs else None,
                     now, embedding_id, existing["id"]),
                )
                return existing["id"]
            else:
                cur = conn.execute(
                    """INSERT INTO entities
                       (name, entity_type, attributes, first_seen_session,
                        last_seen_session, created_at, updated_at, embedding_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (name, entity_type,
                     json.dumps(attributes) if attributes else None,
                     session_id, session_id, now, now, embedding_id),
                )
                return cur.lastrowid

        return self._execute_write(_write)

    def add_relationship(
        self,
        source_entity_id: int,
        target_entity_id: int,
        relationship_type: str,
        strength: float = 1.0,
        context: Optional[str] = None,
    ) -> int:
        """Add or strengthen a relationship between entities."""
        now = time.time()

        def _write(conn):
            existing = conn.execute(
                """SELECT id, strength FROM relationships
                   WHERE source_entity_id = ? AND target_entity_id = ?
                   AND relationship_type = ?""",
                (source_entity_id, target_entity_id, relationship_type),
            ).fetchone()

            if existing:
                new_strength = min(existing["strength"] + 0.1, 5.0)
                conn.execute(
                    """UPDATE relationships
                       SET strength = ?, context = COALESCE(?, context), updated_at = ?
                       WHERE id = ?""",
                    (new_strength, context, now, existing["id"]),
                )
                return existing["id"]
            else:
                cur = conn.execute(
                    """INSERT INTO relationships
                       (source_entity_id, target_entity_id, relationship_type,
                        strength, context, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (source_entity_id, target_entity_id, relationship_type,
                     strength, context, now, now),
                )
                return cur.lastrowid

        return self._execute_write(_write)

    def query_entity_relationships(
        self, entity_name: str, depth: int = 1,
    ) -> Dict[str, Any]:
        """Query entity and its relationships."""
        def _read(conn):
            entity = conn.execute(
                "SELECT * FROM entities WHERE name = ?", (entity_name,)
            ).fetchone()
            if not entity:
                return {"entity": None, "relationships": []}

            rels = conn.execute(
                """SELECT r.*, e1.name as source_name, e2.name as target_name,
                          e1.entity_type as source_type, e2.entity_type as target_type
                   FROM relationships r
                   JOIN entities e1 ON e1.id = r.source_entity_id
                   JOIN entities e2 ON e2.id = r.target_entity_id
                   WHERE r.source_entity_id = ? OR r.target_entity_id = ?
                   ORDER BY r.strength DESC""",
                (entity["id"], entity["id"]),
            ).fetchall()

            return {
                "entity": dict(entity),
                "relationships": [dict(r) for r in rels],
            }

        return self._execute_read(_read)

    # ══════════════════════════════════════════════════════════════════
    # Bookmarks
    # ══════════════════════════════════════════════════════════════════

    def store_bookmark(
        self,
        url: Optional[str] = None,
        title: Optional[str] = None,
        resource_type: str = "url",
        tags: Optional[List[str]] = None,
        context: Optional[str] = None,
        session_id: Optional[str] = None,
        embedding_id: Optional[int] = None,
    ) -> int:
        """Store a bookmarked resource."""
        now = time.time()

        def _write(conn):
            cur = conn.execute(
                """INSERT INTO bookmarks
                   (url, title, resource_type, tags, context, session_id, created_at, embedding_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (url, title, resource_type,
                 json.dumps(tags) if tags else None,
                 context, session_id, now, embedding_id),
            )
            return cur.lastrowid

        return self._execute_write(_write)

    # ══════════════════════════════════════════════════════════════════
    # User preferences
    # ══════════════════════════════════════════════════════════════════

    def set_preference(
        self,
        key: str,
        value: str,
        confidence: float = 0.5,
        evidence: Optional[List[str]] = None,
    ) -> None:
        """Set or update a user preference."""
        now = time.time()

        def _write(conn):
            conn.execute(
                """INSERT INTO user_preferences (preference_key, preference_value, confidence, evidence, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(preference_key) DO UPDATE SET
                       preference_value = excluded.preference_value,
                       confidence = excluded.confidence,
                       evidence = excluded.evidence,
                       updated_at = excluded.updated_at""",
                (key, value, confidence,
                 json.dumps(evidence) if evidence else None, now),
            )

        self._execute_write(_write)

    def get_preferences(self, min_confidence: float = 0.0) -> Dict[str, Dict[str, Any]]:
        """Get all user preferences above confidence threshold."""
        def _read(conn):
            rows = conn.execute(
                """SELECT * FROM user_preferences
                   WHERE confidence >= ?
                   ORDER BY confidence DESC""",
                (min_confidence,),
            ).fetchall()
            return {
                row["preference_key"]: {
                    "value": row["preference_value"],
                    "confidence": row["confidence"],
                    "evidence": json.loads(row["evidence"]) if row["evidence"] else None,
                }
                for row in rows
            }

        return self._execute_read(_read)

    # ══════════════════════════════════════════════════════════════════
    # Workflow patterns
    # ══════════════════════════════════════════════════════════════════

    def record_workflow_pattern(
        self,
        trigger: str,
        tool_sequence: List[str],
        pattern_name: Optional[str] = None,
    ) -> int:
        """Record or update a workflow pattern."""
        now = time.time()

        def _write(conn):
            seq_json = json.dumps(tool_sequence)
            existing = conn.execute(
                "SELECT id, frequency FROM workflow_patterns WHERE tool_sequence = ?",
                (seq_json,),
            ).fetchone()

            if existing:
                conn.execute(
                    """UPDATE workflow_patterns
                       SET frequency = frequency + 1, last_seen_at = ?
                       WHERE id = ?""",
                    (now, existing["id"]),
                )
                return existing["id"]
            else:
                cur = conn.execute(
                    """INSERT INTO workflow_patterns
                       (pattern_name, trigger, tool_sequence, last_seen_at, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (pattern_name, trigger, seq_json, now, now),
                )
                return cur.lastrowid

        return self._execute_write(_write)

    def get_frequent_patterns(self, min_frequency: int = 3) -> List[Dict[str, Any]]:
        """Get workflow patterns that occur frequently."""
        def _read(conn):
            rows = conn.execute(
                """SELECT * FROM workflow_patterns
                   WHERE frequency >= ?
                   ORDER BY frequency DESC""",
                (min_frequency,),
            ).fetchall()
            return [dict(row) for row in rows]

        return self._execute_read(_read)

    # ══════════════════════════════════════════════════════════════════
    # Expertise mapping
    # ══════════════════════════════════════════════════════════════════

    def update_expertise(
        self,
        domain: str,
        proficiency: str,
        evidence: Optional[List[str]] = None,
    ) -> None:
        """Update expertise mapping for a domain."""
        now = time.time()

        def _write(conn):
            existing = conn.execute(
                "SELECT id FROM expertise_map WHERE domain = ?", (domain,),
            ).fetchone()

            if existing:
                conn.execute(
                    """UPDATE expertise_map
                       SET proficiency = ?, evidence = ?, updated_at = ?
                       WHERE id = ?""",
                    (proficiency,
                     json.dumps(evidence) if evidence else None,
                     now, existing["id"]),
                )
            else:
                conn.execute(
                    """INSERT INTO expertise_map (domain, proficiency, evidence, updated_at)
                       VALUES (?, ?, ?, ?)""",
                    (domain, proficiency,
                     json.dumps(evidence) if evidence else None, now),
                )

        self._execute_write(_write)

    def get_expertise_map(self) -> Dict[str, Dict[str, Any]]:
        """Get full expertise map."""
        def _read(conn):
            rows = conn.execute(
                "SELECT * FROM expertise_map ORDER BY domain",
            ).fetchall()
            return {
                row["domain"]: {
                    "proficiency": row["proficiency"],
                    "evidence": json.loads(row["evidence"]) if row["evidence"] else None,
                    "updated_at": row["updated_at"],
                }
                for row in rows
            }

        return self._execute_read(_read)

    # ══════════════════════════════════════════════════════════════════
    # Plans
    # ══════════════════════════════════════════════════════════════════

    def create_plan(
        self,
        goal: str,
        steps: List[Dict[str, Any]],
        session_id: Optional[str] = None,
    ) -> int:
        """Create a multi-step plan."""
        now = time.time()

        def _write(conn):
            cur = conn.execute(
                """INSERT INTO plans (session_id, goal, steps, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (session_id, goal, json.dumps(steps), now, now),
            )
            return cur.lastrowid

        return self._execute_write(_write)

    def update_plan(self, plan_id: int, steps: List[Dict], status: Optional[str] = None,
                    backtrack_count: Optional[int] = None) -> None:
        """Update plan steps and/or status."""
        now = time.time()

        def _write(conn):
            updates = ["steps = ?", "updated_at = ?"]
            params = [json.dumps(steps), now]
            if status:
                updates.append("status = ?")
                params.append(status)
            if backtrack_count is not None:
                updates.append("backtrack_count = ?")
                params.append(backtrack_count)
            params.append(plan_id)
            conn.execute(
                f"UPDATE plans SET {', '.join(updates)} WHERE id = ?",
                params,
            )

        self._execute_write(_write)

    # ══════════════════════════════════════════════════════════════════
    # Reflections
    # ══════════════════════════════════════════════════════════════════

    def store_reflection(
        self,
        session_id: str,
        went_well: Optional[str] = None,
        could_improve: Optional[str] = None,
        new_patterns: Optional[str] = None,
        embedding_id: Optional[int] = None,
    ) -> int:
        """Store a post-session reflection."""
        now = time.time()

        def _write(conn):
            cur = conn.execute(
                """INSERT INTO reflections
                   (session_id, went_well, could_improve, new_patterns, created_at, embedding_id)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (session_id, went_well, could_improve, new_patterns, now, embedding_id),
            )
            return cur.lastrowid

        return self._execute_write(_write)

    # ══════════════════════════════════════════════════════════════════
    # Cleanup
    # ══════════════════════════════════════════════════════════════════

    def close(self):
        """Close database connection."""
        try:
            with self._lock:
                self._conn.close()
        except Exception:
            pass

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
