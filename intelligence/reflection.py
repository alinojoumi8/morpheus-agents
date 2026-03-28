"""
Post-session reflection — lightweight self-evaluation at session end.

After episodic extraction, analyzes what went well, what could improve,
and what new patterns to remember. Feeds strategies into the playbook.
"""

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

REFLECTION_PROMPT = """\
You are a self-reflective AI assistant analyzing your own performance in a conversation session.

Based on the session summary and extracted information below, provide a structured reflection:

{
  "went_well": "What approaches, tool choices, or communication patterns worked effectively",
  "could_improve": "What could be done better next time (be specific and actionable)",
  "new_patterns": "Any reusable strategies or patterns discovered that should be remembered",
  "strategies": [
    {
      "task_type": "the type of task (debugging, coding, refactoring, deployment, research, planning, etc.)",
      "approach": "concise description of the approach that worked",
      "tool_chain": ["ordered", "list", "of", "tools", "used"]
    }
  ],
  "style_observations": {
    "user_preferred_verbosity": "terse|moderate|verbose|unknown",
    "user_preferred_formality": "casual|neutral|formal|unknown",
    "user_feedback_style": "direct|indirect|unknown"
  }
}

Respond with ONLY valid JSON.

Session Information:
"""


def reflect_on_session_sync(
    episode_data: Dict[str, Any],
    session_id: str,
    sync_llm_call,
    intelligence_db=None,
    embedding_provider=None,
) -> Optional[Dict[str, Any]]:
    """Run post-session reflection.

    Args:
        episode_data: Output from episodic extraction
        session_id: Session identifier
        sync_llm_call: Callable(system_prompt, user_prompt) -> str
        intelligence_db: Optional IntelligenceDB for storage
        embedding_provider: Optional for vector storage

    Returns:
        Reflection data dict, or None on failure
    """
    if not episode_data or not episode_data.get("summary"):
        return None

    context = json.dumps(episode_data, indent=2, default=str)

    try:
        response = sync_llm_call(
            "You are a reflective AI system analyzing your own performance.",
            REFLECTION_PROMPT + context,
        )

        reflection = _parse_json(response)
        if not reflection:
            return None

        if intelligence_db:
            _store_reflection(
                intelligence_db, session_id, reflection,
                episode_data, embedding_provider,
            )

        return reflection

    except Exception as exc:
        logger.warning("Post-session reflection failed: %s", exc)
        return None


async def reflect_on_session_async(
    episode_data: Dict[str, Any],
    session_id: str,
    async_llm_call,
    intelligence_db=None,
    embedding_provider=None,
) -> Optional[Dict[str, Any]]:
    """Async version of reflect_on_session."""
    if not episode_data or not episode_data.get("summary"):
        return None

    context = json.dumps(episode_data, indent=2, default=str)

    try:
        response = await async_llm_call(
            "You are a reflective AI system analyzing your own performance.",
            REFLECTION_PROMPT + context,
        )

        reflection = _parse_json(response)
        if not reflection:
            return None

        if intelligence_db:
            _store_reflection(
                intelligence_db, session_id, reflection,
                episode_data, embedding_provider,
            )

        return reflection

    except Exception as exc:
        logger.warning("Async post-session reflection failed: %s", exc)
        return None


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


def _store_reflection(
    db,
    session_id: str,
    reflection: Dict[str, Any],
    episode_data: Dict[str, Any],
    embedding_provider=None,
):
    """Store reflection and derived data in IntelligenceDB."""
    # Store the reflection itself
    reflection_text = (
        f"Went well: {reflection.get('went_well', 'N/A')}\n"
        f"Could improve: {reflection.get('could_improve', 'N/A')}\n"
        f"New patterns: {reflection.get('new_patterns', 'N/A')}"
    )

    embedding_id = None
    if embedding_provider:
        try:
            emb = embedding_provider.embed(reflection_text)
            embedding_id = db.store_embedding(
                content=reflection_text,
                content_type="reflection",
                embedding=emb,
                metadata={"session_id": session_id},
                session_id=session_id,
                tier="warm",
            )
        except Exception:
            pass

    db.store_reflection(
        session_id=session_id,
        went_well=reflection.get("went_well"),
        could_improve=reflection.get("could_improve"),
        new_patterns=reflection.get("new_patterns"),
        embedding_id=embedding_id,
    )

    # Store strategies in playbook
    for strategy in reflection.get("strategies", []):
        task_type = strategy.get("task_type", "other")
        approach = strategy.get("approach", "")
        tool_chain = strategy.get("tool_chain", [])

        if approach:
            strat_embedding_id = None
            if embedding_provider:
                try:
                    emb = embedding_provider.embed(f"{task_type}: {approach}")
                    strat_embedding_id = db.store_embedding(
                        content=f"{task_type}: {approach}",
                        content_type="strategy",
                        embedding=emb,
                        session_id=session_id,
                        tier="warm",
                    )
                except Exception:
                    pass

            db.store_strategy(
                task_type=task_type,
                approach=approach,
                tool_chain=tool_chain,
                embedding_id=strat_embedding_id,
            )

    # Store style observations as user preferences
    style = reflection.get("style_observations", {})
    for key, value in style.items():
        if value and value != "unknown":
            db.set_preference(
                key=key,
                value=value,
                confidence=0.4,  # Low initial confidence, grows with more data
                evidence=[f"session:{session_id}"],
            )

    logger.info("Stored reflection for session %s", session_id)
