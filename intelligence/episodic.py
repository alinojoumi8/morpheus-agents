"""
Episodic memory extraction — auto-extract key events from sessions.

At session end, analyzes the conversation to extract:
- Summary (2-3 sentences)
- Decisions made (with context and outcome)
- Problems solved (problem, solution, tools used)
- Key events (important moments)
- User sentiment (positive/neutral/frustrated/confused)

Uses an auxiliary LLM call (cheap model) for extraction.
"""

import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Extraction prompt ──

EXTRACTION_PROMPT = """\
You are analyzing a conversation between a user and an AI assistant. Extract the following information in JSON format:

{
  "summary": "2-3 sentence summary of what happened in this session",
  "decisions": [
    {"decision": "what was decided", "context": "why", "outcome": "result"}
  ],
  "problems_solved": [
    {"problem": "what the issue was", "solution": "how it was resolved", "tools_used": ["list", "of", "tools"]}
  ],
  "key_events": [
    {"event": "what happened", "importance": "high/medium/low"}
  ],
  "user_sentiment": "positive|neutral|frustrated|confused|mixed",
  "sentiment_signals": ["evidence for the sentiment classification"],
  "entities_mentioned": [
    {"name": "entity name", "type": "person|project|tool|concept|org", "context": "how it was mentioned"}
  ],
  "unfinished_business": ["things mentioned but not completed"],
  "task_type": "debugging|coding|refactoring|deployment|research|planning|other"
}

Rules:
- Be concise but accurate
- Only include decisions/problems that actually occurred (empty arrays if none)
- Sentiment should reflect the USER's apparent satisfaction, not the AI's
- entities_mentioned should capture people, projects, tools, and concepts discussed
- unfinished_business tracks items the user might want to follow up on later
- Respond with ONLY valid JSON, no markdown or explanation

Conversation:
"""

# Max chars of conversation to send for extraction
MAX_EXTRACTION_CHARS = 50_000


def _truncate_conversation(messages: List[Dict[str, Any]]) -> str:
    """Format messages into a readable transcript, truncated to limit."""
    parts = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if not content:
            continue
        # Skip system messages and tool results (too verbose)
        if role == "system":
            continue
        if role == "tool":
            tool_name = msg.get("tool_name", msg.get("name", "tool"))
            # Truncate long tool results
            if len(content) > 500:
                content = content[:500] + "... [truncated]"
            parts.append(f"[Tool: {tool_name}]: {content}")
        elif role == "assistant":
            # Include tool calls summaries
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                try:
                    calls = json.loads(tool_calls) if isinstance(tool_calls, str) else tool_calls
                    for tc in calls:
                        func = tc.get("function", {})
                        parts.append(f"[Assistant calls {func.get('name', '?')}]")
                except (json.JSONDecodeError, TypeError):
                    pass
            if content:
                parts.append(f"Assistant: {content}")
        else:
            parts.append(f"User: {content}")

    text = "\n".join(parts)
    if len(text) > MAX_EXTRACTION_CHARS:
        # Keep beginning and end (most important parts)
        half = MAX_EXTRACTION_CHARS // 2
        text = text[:half] + "\n\n... [middle truncated] ...\n\n" + text[-half:]
    return text


async def extract_episode_async(
    messages: List[Dict[str, Any]],
    session_id: str,
    aux_llm_call,
    intelligence_db=None,
    embedding_provider=None,
) -> Optional[Dict[str, Any]]:
    """Extract episodic memory from a conversation.

    Args:
        messages: Conversation messages (OpenAI format)
        session_id: Session identifier
        aux_llm_call: Async callable(system_prompt, user_prompt) -> str
            Should use a cheap/fast model (e.g., Gemini Flash, GPT-4o-mini)
        intelligence_db: Optional IntelligenceDB instance for storage
        embedding_provider: Optional EmbeddingProvider for vector storage

    Returns:
        Extracted episode data dict, or None on failure
    """
    if not messages or len(messages) < 3:
        logger.debug("Skipping episodic extraction: too few messages (%d)", len(messages))
        return None

    transcript = _truncate_conversation(messages)
    if len(transcript.strip()) < 100:
        logger.debug("Skipping episodic extraction: transcript too short")
        return None

    try:
        response = await aux_llm_call(
            "You are a conversation analyst. Extract structured information from conversations.",
            EXTRACTION_PROMPT + transcript,
        )

        # Parse JSON from response
        episode = _parse_json_response(response)
        if not episode:
            logger.warning("Failed to parse episodic extraction response")
            return None

        # Store in database if available
        if intelligence_db:
            _store_episode(intelligence_db, session_id, episode, embedding_provider)

        return episode

    except Exception as exc:
        logger.warning("Episodic extraction failed: %s", exc)
        return None


def extract_episode_sync(
    messages: List[Dict[str, Any]],
    session_id: str,
    sync_llm_call,
    intelligence_db=None,
    embedding_provider=None,
) -> Optional[Dict[str, Any]]:
    """Synchronous version of extract_episode_async.

    Args:
        sync_llm_call: Callable(system_prompt, user_prompt) -> str
    """
    if not messages or len(messages) < 3:
        return None

    transcript = _truncate_conversation(messages)
    if len(transcript.strip()) < 100:
        return None

    try:
        response = sync_llm_call(
            "You are a conversation analyst. Extract structured information from conversations.",
            EXTRACTION_PROMPT + transcript,
        )

        episode = _parse_json_response(response)
        if not episode:
            return None

        if intelligence_db:
            _store_episode(intelligence_db, session_id, episode, embedding_provider)

        return episode

    except Exception as exc:
        logger.warning("Episodic extraction failed: %s", exc)
        return None


def _parse_json_response(response: str) -> Optional[Dict[str, Any]]:
    """Parse JSON from LLM response, handling common formatting issues."""
    if not response:
        return None

    # Try direct parse first
    try:
        return json.loads(response)
    except json.JSONDecodeError:
        pass

    # Try extracting JSON from markdown code block
    import re
    json_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', response, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass

    # Try finding JSON object in the response
    brace_start = response.find('{')
    brace_end = response.rfind('}')
    if brace_start >= 0 and brace_end > brace_start:
        try:
            return json.loads(response[brace_start:brace_end + 1])
        except json.JSONDecodeError:
            pass

    return None


def _store_episode(
    db,
    session_id: str,
    episode: Dict[str, Any],
    embedding_provider=None,
):
    """Store extracted episode in IntelligenceDB."""
    summary = episode.get("summary", "")
    if not summary:
        return

    # Generate embedding for the summary
    embedding_id = None
    if embedding_provider and summary:
        try:
            embedding = embedding_provider.embed(summary)
            embedding_id = db.store_embedding(
                content=summary,
                content_type="episode",
                embedding=embedding,
                metadata={
                    "task_type": episode.get("task_type", "other"),
                    "sentiment": episode.get("user_sentiment", "neutral"),
                    "has_unfinished": bool(episode.get("unfinished_business")),
                },
                session_id=session_id,
                tier="warm",
            )
        except Exception as exc:
            logger.warning("Failed to embed episode: %s", exc)

    # Store episode
    db.store_episode(
        session_id=session_id,
        summary=summary,
        decisions=episode.get("decisions"),
        problems_solved=episode.get("problems_solved"),
        key_events=episode.get("key_events"),
        user_sentiment=episode.get("user_sentiment", "neutral"),
        sentiment_signals=episode.get("sentiment_signals"),
        embedding_id=embedding_id,
    )

    # Log sentiment
    db.log_sentiment(
        session_id=session_id,
        overall=episode.get("user_sentiment", "neutral"),
        confidence=0.7,
        signals=episode.get("sentiment_signals"),
    )

    # Store entities in knowledge graph
    for entity in episode.get("entities_mentioned", []):
        try:
            db.upsert_entity(
                name=entity.get("name", ""),
                entity_type=entity.get("type", "concept"),
                attributes={"context": entity.get("context", "")},
                session_id=session_id,
            )
        except Exception as exc:
            logger.debug("Failed to store entity %s: %s", entity.get("name"), exc)

    # Store unfinished business as embeddings for follow-up detection
    for item in episode.get("unfinished_business", []):
        if item and embedding_provider:
            try:
                emb = embedding_provider.embed(item)
                db.store_embedding(
                    content=item,
                    content_type="unfinished",
                    embedding=emb,
                    metadata={"session_id": session_id},
                    session_id=session_id,
                    tier="warm",
                )
            except Exception:
                pass

    logger.info("Stored episode for session %s (sentiment: %s)",
                session_id, episode.get("user_sentiment", "neutral"))
