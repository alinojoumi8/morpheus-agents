"""
Integration points for the intelligence module with the agent loop.

This module provides the bridge between intelligence features and run_agent.py.
It handles:
- Lazy initialization of IntelligenceDB and embedding provider
- Session-end processing (episodic extraction, reflection, personalization)
- Hot-tier memory injection into system prompt
- Failure journaling hooks
- Workflow pattern tracking

All operations are best-effort and never block the main agent loop.
"""

import logging
import threading
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Module-level singletons (lazy init) ──
_db = None
_embedding_provider = None
_workflow_tracker = None
_config = None
_initialized = False


def _ensure_initialized(config: Optional[Dict] = None):
    """Lazy-initialize intelligence module from config."""
    global _db, _embedding_provider, _workflow_tracker, _config, _initialized

    if _initialized:
        return _db is not None

    _initialized = True

    try:
        if config is None:
            from hermes_cli.config import load_config
            config = load_config()

        _config = config.get("intelligence", {})
        if not _config.get("enabled", False):
            logger.debug("Intelligence module disabled in config")
            return False

        from intelligence.db import IntelligenceDB
        from intelligence.embeddings import get_embedding_provider
        from intelligence.personalization import WorkflowTracker

        _db = IntelligenceDB(
            db_path=_config.get("db_path"),
            vector_dimensions=_config.get("vector_dimensions", 384),
        )

        _embedding_provider = get_embedding_provider(
            provider_type=_config.get("embedding_provider", "auto"),
            model=_config.get("embedding_model", ""),
            dimensions=_config.get("vector_dimensions", 384),
        )

        if _config.get("personalization", {}).get("workflow_learning", True):
            _workflow_tracker = WorkflowTracker()

        # Register system cron jobs (idempotent — won't duplicate)
        try:
            from intelligence.cron_registration import register_intelligence_cron_jobs
            registered = register_intelligence_cron_jobs(_config)
            if registered:
                logger.info("Registered %d intelligence cron jobs", len(registered))
        except Exception as cron_exc:
            logger.debug("Cron job registration skipped: %s", cron_exc)

        logger.info("Intelligence module initialized (vec=%s, provider=%s)",
                     _db.vec_available, _embedding_provider.name)
        return True

    except Exception as exc:
        logger.warning("Failed to initialize intelligence module: %s", exc)
        _db = None
        return False


def is_enabled() -> bool:
    """Check if intelligence module is active."""
    return _db is not None


def get_db():
    """Get the IntelligenceDB instance (or None if disabled)."""
    return _db


def get_embedding_provider():
    """Get the embedding provider (or None if disabled)."""
    return _embedding_provider


# ══════════════════════════════════════════════════════════════════
# Session-End Processing
# ══════════════════════════════════════════════════════════════════

def on_session_end(
    session_id: str,
    messages: List[Dict[str, Any]],
    sync_llm_call: Optional[Callable] = None,
    completed: bool = True,
    config: Optional[Dict] = None,
):
    """Called at session end to run intelligence processing.

    Spawns a background thread to avoid blocking the response.
    Operations: episodic extraction → reflection → personalization → knowledge graph.
    """
    if not _ensure_initialized(config):
        return

    if not messages or len(messages) < 3:
        return

    # Run in background thread so it doesn't block
    thread = threading.Thread(
        target=_session_end_worker,
        args=(session_id, list(messages), sync_llm_call, completed),
        daemon=True,
        name=f"intelligence-session-end-{session_id[:8]}",
    )
    thread.start()


def _session_end_worker(
    session_id: str,
    messages: List[Dict[str, Any]],
    sync_llm_call: Optional[Callable],
    completed: bool,
):
    """Background worker for session-end intelligence processing."""
    try:
        episode_data = None

        # Step 1: Episodic extraction
        if _config.get("episodic_extraction", True) and sync_llm_call:
            try:
                from intelligence.episodic import extract_episode_sync
                episode_data = extract_episode_sync(
                    messages=messages,
                    session_id=session_id,
                    sync_llm_call=sync_llm_call,
                    intelligence_db=_db,
                    embedding_provider=_embedding_provider,
                )
                logger.info("Episodic extraction complete for session %s", session_id[:8])
            except Exception as exc:
                logger.warning("Episodic extraction failed: %s", exc)

        # Step 2: Post-session reflection
        if _config.get("post_session_reflection", True) and episode_data and sync_llm_call:
            try:
                from intelligence.reflection import reflect_on_session_sync
                reflect_on_session_sync(
                    episode_data=episode_data,
                    session_id=session_id,
                    sync_llm_call=sync_llm_call,
                    intelligence_db=_db,
                    embedding_provider=_embedding_provider,
                )
                logger.info("Post-session reflection complete for session %s", session_id[:8])
            except Exception as exc:
                logger.warning("Post-session reflection failed: %s", exc)

        # Step 3: Personalization update
        if _config.get("personalization", {}).get("enabled", True):
            try:
                from intelligence.personalization import update_preferences_from_session
                update_preferences_from_session(
                    intelligence_db=_db,
                    messages=messages,
                    session_id=session_id,
                )
            except Exception as exc:
                logger.warning("Personalization update failed: %s", exc)

        # Step 4: Knowledge graph update
        if _config.get("knowledge_graph", True) and episode_data:
            try:
                from intelligence.knowledge_graph import extract_entities_from_episode
                extract_entities_from_episode(
                    episode_data=episode_data,
                    session_id=session_id,
                    sync_llm_call=sync_llm_call,
                    intelligence_db=_db,
                    embedding_provider=_embedding_provider,
                )
            except Exception as exc:
                logger.warning("Knowledge graph update failed: %s", exc)

        # Step 5: Persist substantial reasoning chains
        try:
            for msg in messages:
                if msg.get("role") == "assistant":
                    reasoning = msg.get("reasoning", "")
                    if reasoning and len(reasoning.strip()) >= 100:
                        persist_reasoning(
                            reasoning_text=reasoning,
                            session_id=session_id,
                            context=msg.get("content", "")[:200] if msg.get("content") else None,
                        )
        except Exception as exc:
            logger.debug("Reasoning persistence failed: %s", exc)

        # Step 6: Store workflow patterns
        if _workflow_tracker and _config.get("personalization", {}).get("workflow_learning", True):
            try:
                _workflow_tracker.store_patterns(_db, trigger=f"session:{session_id[:8]}")
                _workflow_tracker.reset()
            except Exception as exc:
                logger.warning("Workflow pattern storage failed: %s", exc)

    except Exception as exc:
        logger.error("Intelligence session-end processing failed: %s", exc)


# ══════════════════════════════════════════════════════════════════
# Hot-Tier Memory Injection
# ══════════════════════════════════════════════════════════════════

def get_hot_memories_block(persona: Optional[str] = None) -> str:
    """Get hot-tier memories formatted for system prompt injection.

    Returns a formatted block to append to the system prompt, or empty string.
    """
    if not _db:
        return ""

    try:
        max_entries = _config.get("consolidation", {}).get("hot_tier_max", 10)
        memories = _db.get_hot_memories(limit=max_entries, persona=persona)

        if not memories:
            return ""

        parts = ["# Intelligence Memory (long-term recall)"]
        for m in memories:
            content_type = m.get("content_type", "memory")
            content = m["content"]
            if len(content) > 300:
                content = content[:300] + "..."
            parts.append(f"- [{content_type}] {content}")

        return "\n".join(parts)

    except Exception as exc:
        logger.debug("Failed to get hot memories: %s", exc)
        return ""


def get_strategy_for_query(query: str) -> str:
    """Get relevant strategy for the current query.

    Returns formatted strategy hint for system prompt, or empty string.
    """
    if not _db or not _embedding_provider:
        return ""

    try:
        query_emb = _embedding_provider.embed(query)
        results = _db.vector_search(
            query_embedding=query_emb,
            content_type="strategy",
            limit=2,
        )

        if not results:
            return ""

        # Only include high-relevance strategies
        relevant = [r for r in results if r.get("distance") is not None and r["distance"] < 0.5]
        if not relevant:
            return ""

        parts = ["# Relevant strategies from past experience"]
        for r in relevant:
            parts.append(f"- {r['content'][:200]}")

        return "\n".join(parts)

    except Exception:
        return ""


def get_personalization_directive() -> str:
    """Get personalization directive for system prompt."""
    if not _db:
        return ""

    try:
        from intelligence.personalization import generate_style_directive
        preferences = _db.get_preferences(min_confidence=0.5)
        return generate_style_directive(preferences)
    except Exception:
        return ""


def get_followup_suggestions() -> str:
    """Get smart follow-up suggestions for session start."""
    if not _db:
        return ""

    try:
        from intelligence.monitors import get_smart_followups
        followups = get_smart_followups(_db, _embedding_provider, limit=2)
        if not followups:
            return ""

        parts = ["# Follow-ups from previous sessions"]
        for f in followups:
            parts.append(f"- {f['text'][:150]}")

        return "\n".join(parts)

    except Exception:
        return ""


# ══════════════════════════════════════════════════════════════════
# Tool Call Hooks
# ══════════════════════════════════════════════════════════════════

def on_tool_call(tool_name: str):
    """Called on each tool invocation for workflow tracking."""
    if _workflow_tracker:
        _workflow_tracker.record_tool_call(tool_name)


def on_tool_error(
    tool_name: str,
    error_message: str,
    session_id: Optional[str] = None,
    context: Optional[Dict] = None,
):
    """Called on tool errors for failure journaling."""
    if not _db:
        return

    try:
        embedding_id = None
        if _embedding_provider:
            emb = _embedding_provider.embed(f"Error in {tool_name}: {error_message[:200]}")
            embedding_id = _db.store_embedding(
                content=f"Error in {tool_name}: {error_message[:200]}",
                content_type="failure",
                embedding=emb,
                metadata={"tool": tool_name},
                session_id=session_id,
            )

        _db.log_failure(
            error_type="tool_error",
            error_message=error_message[:1000],
            session_id=session_id,
            full_context=context,
            embedding_id=embedding_id,
        )
    except Exception as exc:
        logger.debug("Failed to log tool error: %s", exc)


def on_skill_invocation(
    skill_name: str,
    session_id: str,
    tool_calls_after: List[Dict],
    error_occurred: bool,
    user_feedback_positive: Optional[bool] = None,
):
    """Called after skill invocation for scoring."""
    if not _db or not _config.get("skill_scoring", True):
        return

    try:
        from intelligence.skill_eval import evaluate_skill_invocation
        evaluate_skill_invocation(
            skill_name=skill_name,
            session_id=session_id,
            tool_calls_after=tool_calls_after,
            final_response=None,
            error_occurred=error_occurred,
            user_feedback_positive=user_feedback_positive,
            intelligence_db=_db,
        )
    except Exception as exc:
        logger.debug("Failed to score skill: %s", exc)


# ══════════════════════════════════════════════════════════════════
# Auto-Bookmarking
# ══════════════════════════════════════════════════════════════════

def on_tool_result(
    tool_name: str,
    tool_args: Dict,
    result: str,
    session_id: Optional[str] = None,
):
    """Called after successful tool execution for auto-bookmarking.

    Auto-captures URLs from web_search and web_extract results.
    """
    if not _db or tool_name not in ("web_search", "web_extract"):
        return

    try:
        import re
        # Extract URLs from tool args and results
        urls_to_bookmark = []

        if tool_name == "web_extract":
            # web_extract has explicit URLs in args
            raw_urls = tool_args.get("urls") or tool_args.get("url", "")
            if isinstance(raw_urls, list):
                urls_to_bookmark.extend(raw_urls)
            elif isinstance(raw_urls, str) and raw_urls:
                urls_to_bookmark.append(raw_urls)

        elif tool_name == "web_search":
            # Extract URLs from search results
            url_pattern = re.compile(r'https?://[^\s\]\)\"\'<>]+')
            found = url_pattern.findall(result[:5000])
            urls_to_bookmark.extend(found[:5])  # Cap at 5

        query = tool_args.get("query", "") or tool_args.get("prompt", "")

        for url in urls_to_bookmark:
            url = url.rstrip(".,;:")
            if len(url) < 10:
                continue
            try:
                _db.store_bookmark(
                    url=url,
                    title=query[:100] if query else url[:100],
                    resource_type="url",
                    tags=["auto-captured", tool_name],
                    context=f"Found via {tool_name}: {query[:200]}" if query else None,
                    session_id=session_id,
                )
            except Exception:
                pass

    except Exception as exc:
        logger.debug("Auto-bookmarking failed: %s", exc)


# ══════════════════════════════════════════════════════════════════
# Chain-of-Thought Persistence
# ══════════════════════════════════════════════════════════════════

def persist_reasoning(
    reasoning_text: str,
    session_id: Optional[str] = None,
    context: Optional[str] = None,
):
    """Store a reasoning chain in intelligence.db for later recall.

    Called when the agent produces reasoning (Claude's thinking blocks).
    """
    if not _db or not _embedding_provider or not reasoning_text:
        return

    # Only store substantial reasoning (skip trivial ones)
    if len(reasoning_text.strip()) < 100:
        return

    try:
        # Truncate very long reasoning to avoid embedding overhead
        text_to_embed = reasoning_text[:2000]
        emb = _embedding_provider.embed(text_to_embed)

        _db.store_embedding(
            content=reasoning_text[:5000],  # Cap storage at 5K chars
            content_type="reasoning",
            embedding=emb,
            metadata={
                "context": context[:200] if context else None,
            },
            session_id=session_id,
            tier="warm",
        )
    except Exception as exc:
        logger.debug("Failed to persist reasoning: %s", exc)


# ══════════════════════════════════════════════════════════════════
# Consolidation Execution (for cron jobs)
# ══════════════════════════════════════════════════════════════════

def run_consolidation_now(sync_llm_call=None) -> Dict:
    """Execute memory consolidation immediately. Used by cron and /intel command."""
    if not _db:
        return {"error": "Intelligence module not initialized"}

    from intelligence.consolidation import run_consolidation
    return run_consolidation(
        intelligence_db=_db,
        embedding_provider=_embedding_provider,
        aux_llm_call=sync_llm_call,
    )
