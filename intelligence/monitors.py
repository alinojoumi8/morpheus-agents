"""
Proactive intelligence — monitors, daily digest, smart follow-ups.

Provides:
- Proactive monitors (GitHub, cost, calendar) via cron jobs
- Anticipatory task prep for morning sessions
- Daily digest generation
- Smart follow-ups based on unfinished business
- Context-aware suggestions
"""

import json
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# Smart Follow-Ups
# ══════════════════════════════════════════════════════════════════

def get_smart_followups(
    intelligence_db,
    embedding_provider=None,
    limit: int = 3,
) -> List[Dict[str, Any]]:
    """Get smart follow-up suggestions based on unfinished business.

    Queries episodic memory for recent unfinished items.
    Returns list of suggestion dicts.
    """
    if not intelligence_db:
        return []

    suggestions = []

    try:
        # Check for unfinished business from recent episodes
        recent_episodes = intelligence_db.get_recent_episodes(limit=10)

        for episode in recent_episodes:
            # Parse key events and check for unfinished items
            unfinished_items = []
            if episode.get("problems_solved"):
                try:
                    problems = json.loads(episode["problems_solved"])
                    for p in problems:
                        if p.get("solution", "").lower() in ("none", "", "unresolved"):
                            unfinished_items.append(p.get("problem", ""))
                except (json.JSONDecodeError, TypeError):
                    pass

            for item in unfinished_items:
                if item:
                    suggestions.append({
                        "type": "unfinished_business",
                        "text": f"Previously unresolved: {item}",
                        "source_session": episode.get("session_id"),
                        "created_at": episode.get("created_at"),
                    })

        # Also check vector store for explicit unfinished items
        if embedding_provider:
            try:
                query_emb = embedding_provider.embed("unfinished task todo pending")
                results = intelligence_db.vector_search(
                    query_embedding=query_emb,
                    content_type="unfinished",
                    limit=limit,
                )
                for r in results:
                    suggestions.append({
                        "type": "unfinished_task",
                        "text": r["content"],
                        "source_session": r.get("session_id"),
                        "created_at": r.get("created_at"),
                    })
            except Exception:
                pass

    except Exception as exc:
        logger.warning("Failed to get smart follow-ups: %s", exc)

    # Deduplicate and limit
    seen = set()
    unique = []
    for s in suggestions:
        key = s["text"][:100]
        if key not in seen:
            seen.add(key)
            unique.append(s)
        if len(unique) >= limit:
            break

    return unique


# ══════════════════════════════════════════════════════════════════
# Daily Digest
# ══════════════════════════════════════════════════════════════════

def generate_daily_digest(
    intelligence_db,
    sync_llm_call=None,
    embedding_provider=None,
) -> str:
    """Generate a daily digest/briefing.

    Aggregates: recent episodes, pending follow-ups, sentiment trends,
    skill health, and suggested actions.
    """
    if not intelligence_db:
        return "Intelligence module not available."

    parts = ["Daily Briefing", "=" * 40, ""]

    # Recent activity
    recent = intelligence_db.get_recent_episodes(limit=5)
    if recent:
        parts.append("Recent Sessions:")
        for ep in recent:
            summary = ep.get("summary", "No summary")[:200]
            sentiment = ep.get("user_sentiment", "?")
            parts.append(f"  [{sentiment}] {summary}")
        parts.append("")

    # Pending follow-ups
    followups = get_smart_followups(intelligence_db, embedding_provider, limit=3)
    if followups:
        parts.append("Follow-ups:")
        for f in followups:
            parts.append(f"  - {f['text'][:150]}")
        parts.append("")

    # Sentiment trend
    sentiments = intelligence_db.get_sentiment_trend(limit=10)
    if sentiments:
        positive = sum(1 for s in sentiments if s.get("overall") == "positive")
        negative = sum(1 for s in sentiments if s.get("overall") in ("negative", "frustrated"))
        total = len(sentiments)
        parts.append(f"Sentiment trend (last {total} sessions): "
                     f"{positive} positive, {negative} negative")
        parts.append("")

    # Skill health summary
    try:
        from intelligence.skill_eval import get_skill_health_report
        report = get_skill_health_report(intelligence_db)
        flagged = [name for name, data in report.items()
                   if data.get("status") in ("flagged", "critical")]
        if flagged:
            parts.append(f"Skills needing attention: {', '.join(flagged)}")
            parts.append("")
    except Exception:
        pass

    # Strategies available
    try:
        def _read(conn):
            return conn.execute(
                """SELECT task_type, COUNT(*) as cnt, AVG(success_rate) as avg_rate
                   FROM strategies GROUP BY task_type ORDER BY cnt DESC LIMIT 5""",
            ).fetchall()
        strategies = intelligence_db._execute_read(_read)
        if strategies:
            parts.append("Top strategy areas:")
            for s in strategies:
                parts.append(f"  {s['task_type']}: {s['cnt']} strategies (avg success: {s['avg_rate']:.0%})")
            parts.append("")
    except Exception:
        pass

    if len(parts) <= 3:
        return "No activity to report yet. Start using Hermes to build up your intelligence profile!"

    return "\n".join(parts)


# ══════════════════════════════════════════════════════════════════
# Context-Aware Suggestions
# ══════════════════════════════════════════════════════════════════

def get_context_suggestions(
    intelligence_db,
    current_query: str = "",
    embedding_provider=None,
    hour: Optional[int] = None,
    limit: int = 3,
) -> List[str]:
    """Get context-aware suggestions for the current session.

    Considers: time of day, recent episodes, current query, strategies.
    """
    if not intelligence_db:
        return []

    suggestions = []
    if hour is None:
        hour = datetime.now().hour

    # Morning suggestions
    if 6 <= hour < 11:
        suggestions.append("Would you like a briefing of recent activity and pending follow-ups?")

    # Follow-up suggestions
    followups = get_smart_followups(intelligence_db, embedding_provider, limit=2)
    for f in followups:
        suggestions.append(f"Continue from last session: {f['text'][:100]}")

    # Strategy suggestions based on query
    if current_query and embedding_provider:
        try:
            query_emb = embedding_provider.embed(current_query)
            results = intelligence_db.vector_search(
                query_embedding=query_emb,
                content_type="strategy",
                limit=2,
            )
            for r in results:
                if r.get("distance") is not None and r["distance"] < 0.5:
                    suggestions.append(f"Known strategy: {r['content'][:100]}")
        except Exception:
            pass

    return suggestions[:limit]


# ══════════════════════════════════════════════════════════════════
# Proactive Monitor Framework
# ══════════════════════════════════════════════════════════════════

class MonitorResult:
    """Result from a proactive monitor check."""

    def __init__(self, monitor_name: str, has_update: bool,
                 message: str = "", priority: str = "low"):
        self.monitor_name = monitor_name
        self.has_update = has_update
        self.message = message
        self.priority = priority  # "low", "medium", "high"


class BaseMonitor:
    """Base class for proactive monitors."""

    name: str = "base"
    check_interval_minutes: int = 15

    def check(self) -> Optional[MonitorResult]:
        """Run the monitor check. Returns result if there's an update."""
        raise NotImplementedError


class CostMonitor(BaseMonitor):
    """Monitor API spending and alert on unusual spikes."""

    name = "cost"

    def __init__(self, threshold_usd: float = 10.0):
        self.threshold = threshold_usd
        self._last_cost = 0.0

    def check(self, session_db=None) -> Optional[MonitorResult]:
        """Check if daily cost exceeds threshold."""
        if not session_db:
            return None

        try:
            today_start = time.time() - 86400  # Last 24h
            result = session_db._execute_read(
                lambda conn: conn.execute(
                    """SELECT COALESCE(SUM(estimated_cost_usd), 0) as total
                       FROM sessions WHERE started_at > ?""",
                    (today_start,),
                ).fetchone()
            )

            total = result["total"] if result else 0
            if total > self.threshold and total > self._last_cost * 1.4:
                self._last_cost = total
                return MonitorResult(
                    monitor_name="cost",
                    has_update=True,
                    message=f"API spend in last 24h: ${total:.2f} (threshold: ${self.threshold:.2f})",
                    priority="high" if total > self.threshold * 2 else "medium",
                )
            self._last_cost = total

        except Exception as exc:
            logger.debug("Cost monitor check failed: %s", exc)

        return None


# ══════════════════════════════════════════════════════════════════
# Cron Job Definitions
# ══════════════════════════════════════════════════════════════════

CONSOLIDATION_CRON_JOB = {
    "id": "intelligence-consolidation",
    "description": "Memory consolidation — decay, promote, deduplicate, distill",
    "schedule": {"kind": "cron", "cron": "0 3 * * *"},  # Daily at 3 AM
    "prompt": (
        "Run memory consolidation: decay old memories, promote frequently-accessed ones, "
        "deduplicate near-identical entries, and distill cold memory clusters."
    ),
    "deliver": None,  # Silent
    "model": None,    # Use default
}

DAILY_DIGEST_CRON_JOB = {
    "id": "intelligence-daily-digest",
    "description": "Daily briefing — summary of activity, follow-ups, suggestions",
    "schedule": {"kind": "cron", "cron": "0 8 * * 1-5"},  # Weekdays at 8 AM
    "prompt": "Generate and deliver the daily briefing digest.",
    "deliver": None,  # Deliver to home channel
}

PROMPT_OPTIMIZATION_CRON_JOB = {
    "id": "intelligence-prompt-optimization",
    "description": "Weekly prompt optimization suggestions",
    "schedule": {"kind": "cron", "cron": "0 2 * * 0"},  # Sunday at 2 AM
    "prompt": (
        "Review accumulated feedback, reflections, and strategy data from the past week. "
        "Generate optimization suggestions for the system prompt and agent behavior."
    ),
    "deliver": None,
}
