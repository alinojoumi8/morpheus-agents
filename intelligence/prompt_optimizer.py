"""
Prompt self-optimization — periodic review and refinement suggestions.

Runs weekly (via cron) to analyze accumulated feedback, reflections,
and strategy data. Generates actionable suggestions for improving
the system prompt and agent behavior.

Suggestions are stored and surfaced via the daily digest or
the /intelligence slash command — never auto-applied.
"""

import json
import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

OPTIMIZATION_PROMPT = """\
You are a prompt engineering optimization system. Analyze the following data
about an AI assistant's recent performance and generate specific, actionable
suggestions for improving its system prompt and behavior.

Data from the past week:

**Reflections (what went well / could improve):**
{reflections}

**Sentiment trend:**
{sentiment}

**Strategy success rates:**
{strategies}

**Common failure patterns:**
{failures}

**User preferences:**
{preferences}

Generate a JSON response:
{{
  "suggestions": [
    {{
      "area": "communication|tool_usage|memory|skills|efficiency",
      "priority": "high|medium|low",
      "current_behavior": "what the agent currently does",
      "suggested_change": "specific change to make",
      "rationale": "why this would help",
      "prompt_snippet": "optional: exact text to add/modify in system prompt"
    }}
  ],
  "overall_assessment": "1-2 sentence overall performance assessment"
}}

Be specific and actionable. Only suggest changes backed by evidence from the data.
Respond with ONLY valid JSON.
"""


def generate_optimization_suggestions(
    intelligence_db,
    sync_llm_call=None,
    days: int = 7,
) -> Optional[Dict[str, Any]]:
    """Generate prompt optimization suggestions from recent data.

    Args:
        intelligence_db: IntelligenceDB instance
        sync_llm_call: Callable(system, user) -> str
        days: Look back N days

    Returns:
        Dict with suggestions and assessment, or None on failure
    """
    if not intelligence_db or not sync_llm_call:
        return None

    cutoff = time.time() - (days * 86400)

    # Gather reflections
    reflections_text = _gather_reflections(intelligence_db, cutoff)

    # Gather sentiment trend
    sentiment_text = _gather_sentiment(intelligence_db)

    # Gather strategy data
    strategies_text = _gather_strategies(intelligence_db)

    # Gather failure patterns
    failures_text = _gather_failures(intelligence_db, cutoff)

    # Gather user preferences
    preferences_text = _gather_preferences(intelligence_db)

    if not any([reflections_text, sentiment_text, failures_text]):
        return None  # Not enough data

    prompt = OPTIMIZATION_PROMPT.format(
        reflections=reflections_text or "No reflections recorded.",
        sentiment=sentiment_text or "No sentiment data.",
        strategies=strategies_text or "No strategy data.",
        failures=failures_text or "No failures recorded.",
        preferences=preferences_text or "No preferences detected.",
    )

    try:
        response = sync_llm_call(
            "You are a prompt engineering optimization system.",
            prompt,
        )

        result = _parse_json(response)
        if result:
            # Store suggestions in the database for later retrieval
            _store_suggestions(intelligence_db, result)
            return result
        return None

    except Exception as exc:
        logger.warning("Prompt optimization failed: %s", exc)
        return None


def get_pending_suggestions(intelligence_db) -> List[Dict[str, Any]]:
    """Get stored optimization suggestions that haven't been applied."""
    if not intelligence_db:
        return []

    try:
        results = intelligence_db.vector_search(
            query_embedding=[],  # Will use fallback text search
            content_type="optimization",
            limit=10,
        )
        suggestions = []
        for r in results:
            if r.get("metadata"):
                suggestions.append(r["metadata"])
        return suggestions
    except Exception:
        return []


def format_suggestions_for_display(data: Dict[str, Any]) -> str:
    """Format optimization suggestions for CLI/chat display."""
    if not data:
        return "No optimization suggestions available."

    lines = []

    assessment = data.get("overall_assessment", "")
    if assessment:
        lines.append(f"Overall: {assessment}")
        lines.append("")

    for i, s in enumerate(data.get("suggestions", []), 1):
        priority_icon = {"high": "!!!", "medium": "!!", "low": "!"}.get(s.get("priority", ""), "")
        lines.append(f"{i}. [{s.get('area', '?')}] {priority_icon} {s.get('suggested_change', '')}")
        lines.append(f"   Rationale: {s.get('rationale', 'N/A')}")
        if s.get("prompt_snippet"):
            lines.append(f"   Snippet: {s['prompt_snippet'][:200]}")
        lines.append("")

    return "\n".join(lines) if lines else "No suggestions."


def _gather_reflections(db, cutoff: float) -> str:
    """Gather recent reflections."""
    def _read(conn):
        return conn.execute(
            """SELECT went_well, could_improve, new_patterns FROM reflections
               WHERE created_at > ? ORDER BY created_at DESC LIMIT 10""",
            (cutoff,),
        ).fetchall()

    rows = db._execute_read(_read)
    if not rows:
        return ""

    parts = []
    for r in rows:
        parts.append(
            f"+ {r['went_well'] or 'N/A'}\n"
            f"- {r['could_improve'] or 'N/A'}\n"
            f"* {r['new_patterns'] or 'N/A'}"
        )
    return "\n\n".join(parts)


def _gather_sentiment(db) -> str:
    """Gather sentiment trend."""
    sentiments = db.get_sentiment_trend(limit=20)
    if not sentiments:
        return ""

    counts = {}
    for s in sentiments:
        overall = s.get("overall", "unknown")
        counts[overall] = counts.get(overall, 0) + 1

    parts = [f"{k}: {v}" for k, v in sorted(counts.items(), key=lambda x: -x[1])]
    return f"Last {len(sentiments)} sessions: " + ", ".join(parts)


def _gather_strategies(db) -> str:
    """Gather strategy success rates."""
    def _read(conn):
        return conn.execute(
            """SELECT task_type, COUNT(*) as cnt, AVG(success_rate) as avg_rate
               FROM strategies GROUP BY task_type ORDER BY cnt DESC LIMIT 10""",
        ).fetchall()

    rows = db._execute_read(_read)
    if not rows:
        return ""

    parts = [f"{r['task_type']}: {r['cnt']} strategies, avg success {r['avg_rate']:.0%}" for r in rows]
    return "\n".join(parts)


def _gather_failures(db, cutoff: float) -> str:
    """Gather recent failure patterns."""
    def _read(conn):
        return conn.execute(
            """SELECT error_type, error_message, prevention_strategy
               FROM failure_journal
               WHERE created_at > ?
               ORDER BY created_at DESC LIMIT 10""",
            (cutoff,),
        ).fetchall()

    rows = db._execute_read(_read)
    if not rows:
        return ""

    parts = []
    for r in rows:
        msg = (r["error_message"] or "")[:200]
        prevention = r["prevention_strategy"] or "N/A"
        parts.append(f"[{r['error_type']}] {msg}\n  Prevention: {prevention}")
    return "\n\n".join(parts)


def _gather_preferences(db) -> str:
    """Gather user preferences."""
    prefs = db.get_preferences(min_confidence=0.4)
    if not prefs:
        return ""

    parts = [f"{k}: {v['value']} (confidence: {v['confidence']:.0%})" for k, v in prefs.items()]
    return "\n".join(parts)


def _store_suggestions(db, data: Dict[str, Any]):
    """Store optimization suggestions in the database."""
    for suggestion in data.get("suggestions", []):
        try:
            db.store_embedding(
                content=suggestion.get("suggested_change", ""),
                content_type="optimization",
                metadata=suggestion,
                tier="warm",
            )
        except Exception:
            pass


def _parse_json(response: str) -> Optional[Dict]:
    """Parse JSON from response."""
    if not response:
        return None
    import re
    try:
        return json.loads(response)
    except json.JSONDecodeError:
        pass
    match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', response, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    start = response.find('{')
    end = response.rfind('}')
    if start >= 0 and end > start:
        try:
            return json.loads(response[start:end + 1])
        except json.JSONDecodeError:
            pass
    return None
