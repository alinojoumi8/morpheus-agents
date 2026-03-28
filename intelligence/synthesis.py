"""
Cross-session synthesis — aggregate episodes into big-picture summaries.

Queries episodic memory across N recent sessions about a topic and generates
a synthesized overview: project status, recurring themes, evolution of approach.
"""

import json
import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

SYNTHESIS_PROMPT = """\
You are a synthesis engine. Given multiple session summaries and episodes about a topic,
generate a concise big-picture overview.

Include:
1. Overall status/progress
2. Key decisions made across sessions
3. Recurring themes or patterns
4. Unresolved issues
5. Suggested next steps

Be concise but comprehensive. Focus on the big picture, not individual session details.

Topic: {topic}

Session episodes (chronological):
{episodes}
"""


def synthesize_topic(
    topic: str,
    intelligence_db,
    embedding_provider=None,
    sync_llm_call=None,
    max_episodes: int = 15,
) -> Optional[str]:
    """Generate a cross-session synthesis on a given topic.

    Args:
        topic: What to synthesize about (e.g., "Project X", "authentication work")
        intelligence_db: IntelligenceDB instance
        embedding_provider: For semantic search of relevant episodes
        sync_llm_call: Callable(system, user) -> str
        max_episodes: Max episodes to include

    Returns:
        Synthesis text, or None on failure
    """
    if not intelligence_db or not sync_llm_call:
        return None

    # Find relevant episodes
    episodes = _find_relevant_episodes(
        topic, intelligence_db, embedding_provider, max_episodes
    )

    if not episodes:
        return f"No sessions found related to '{topic}'."

    # Format episodes for synthesis
    episodes_text = _format_episodes(episodes)

    try:
        prompt = SYNTHESIS_PROMPT.format(topic=topic, episodes=episodes_text)
        result = sync_llm_call(
            "You are a project synthesis and status tracking system.",
            prompt,
        )
        return result if result else None

    except Exception as exc:
        logger.warning("Cross-session synthesis failed: %s", exc)
        return None


def synthesize_recent(
    intelligence_db,
    sync_llm_call=None,
    days: int = 7,
    max_episodes: int = 20,
) -> Optional[str]:
    """Synthesize all recent activity into a big-picture summary.

    Args:
        intelligence_db: IntelligenceDB instance
        sync_llm_call: Callable(system, user) -> str
        days: Look back N days
        max_episodes: Max episodes to include

    Returns:
        Synthesis text, or None on failure
    """
    if not intelligence_db or not sync_llm_call:
        return None

    cutoff = time.time() - (days * 86400)

    def _read(conn):
        return conn.execute(
            """SELECT * FROM episodes
               WHERE created_at > ?
               ORDER BY created_at ASC
               LIMIT ?""",
            (cutoff, max_episodes),
        ).fetchall()

    episodes = intelligence_db._execute_read(_read)

    if not episodes:
        return f"No sessions found in the last {days} days."

    episodes_text = _format_episodes([dict(e) for e in episodes])

    try:
        prompt = SYNTHESIS_PROMPT.format(
            topic=f"all activity in the last {days} days",
            episodes=episodes_text,
        )
        result = sync_llm_call(
            "You are a weekly activity synthesis system.",
            prompt,
        )
        return result if result else None

    except Exception as exc:
        logger.warning("Recent synthesis failed: %s", exc)
        return None


def _find_relevant_episodes(
    topic: str,
    db,
    embedding_provider=None,
    max_episodes: int = 15,
) -> List[Dict[str, Any]]:
    """Find episodes relevant to a topic using vector search + text matching."""
    results = []

    # Vector search if available
    if embedding_provider:
        try:
            query_emb = embedding_provider.embed(topic)
            vec_results = db.vector_search(
                query_embedding=query_emb,
                content_type="episode",
                limit=max_episodes,
            )
            # Get the full episode data for matched embeddings
            for vr in vec_results:
                session_id = vr.get("session_id")
                if session_id:
                    def _read(conn, sid=session_id):
                        return conn.execute(
                            "SELECT * FROM episodes WHERE session_id = ? LIMIT 1",
                            (sid,),
                        ).fetchone()
                    ep = db._execute_read(_read)
                    if ep:
                        results.append(dict(ep))
        except Exception as exc:
            logger.debug("Vector episode search failed: %s", exc)

    # Fallback: text search in summaries
    if not results:
        topic_lower = topic.lower()

        def _read(conn):
            return conn.execute(
                """SELECT * FROM episodes
                   WHERE LOWER(summary) LIKE ?
                   ORDER BY created_at DESC
                   LIMIT ?""",
                (f"%{topic_lower}%", max_episodes),
            ).fetchall()

        rows = db._execute_read(_read)
        results = [dict(r) for r in rows]

    return results


def _format_episodes(episodes: List[Dict[str, Any]]) -> str:
    """Format episodes into readable text for the synthesis prompt."""
    parts = []
    for i, ep in enumerate(episodes, 1):
        summary = ep.get("summary", "No summary")
        sentiment = ep.get("user_sentiment", "?")

        decisions = ""
        if ep.get("decisions"):
            try:
                decs = json.loads(ep["decisions"]) if isinstance(ep["decisions"], str) else ep["decisions"]
                if decs:
                    decisions = "\n    Decisions: " + "; ".join(
                        d.get("decision", "") for d in decs if d.get("decision")
                    )
            except (json.JSONDecodeError, TypeError):
                pass

        problems = ""
        if ep.get("problems_solved"):
            try:
                probs = json.loads(ep["problems_solved"]) if isinstance(ep["problems_solved"], str) else ep["problems_solved"]
                if probs:
                    problems = "\n    Problems solved: " + "; ".join(
                        p.get("problem", "") for p in probs if p.get("problem")
                    )
            except (json.JSONDecodeError, TypeError):
                pass

        parts.append(
            f"  Session {i} [sentiment: {sentiment}]:\n"
            f"    {summary}{decisions}{problems}"
        )

    return "\n\n".join(parts)
