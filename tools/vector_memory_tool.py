"""
Vector memory tools — semantic search and explicit memory storage.

Registered tools:
- vector_search: Search memories by semantic similarity
- vector_remember: Explicitly store something for long-term recall
- knowledge_query: Query the personal knowledge graph

These complement (not replace) the existing memory tool and Honcho integration.
"""

import json
import logging
from typing import Any, Dict, Optional

from tools.registry import registry

logger = logging.getLogger(__name__)

# ── Lazy singletons ──
_intelligence_db = None
_embedding_provider = None


def _get_intelligence_db():
    global _intelligence_db
    if _intelligence_db is None:
        try:
            from intelligence.db import IntelligenceDB
            from morpheus_cli.config import load_config
            config = load_config()
            intel_config = config.get("intelligence", {})
            if not intel_config.get("enabled", False):
                return None
            db_path = intel_config.get("db_path")
            dimensions = intel_config.get("vector_dimensions", 384)
            _intelligence_db = IntelligenceDB(
                db_path=db_path,
                vector_dimensions=dimensions,
            )
        except Exception as exc:
            logger.warning("Failed to initialize IntelligenceDB: %s", exc)
            return None
    return _intelligence_db


def _get_embedding_provider():
    global _embedding_provider
    if _embedding_provider is None:
        try:
            from intelligence.embeddings import get_embedding_provider
            from morpheus_cli.config import load_config
            config = load_config()
            intel_config = config.get("intelligence", {})
            _embedding_provider = get_embedding_provider(
                provider_type=intel_config.get("embedding_provider", "auto"),
                model=intel_config.get("embedding_model", ""),
                dimensions=intel_config.get("vector_dimensions", 384),
            )
        except Exception as exc:
            logger.warning("Failed to initialize embedding provider: %s", exc)
            from intelligence.embeddings import HashEmbeddingProvider
            _embedding_provider = HashEmbeddingProvider()
    return _embedding_provider


def _check_intelligence_available() -> bool:
    """Check if intelligence module is available and enabled."""
    try:
        from morpheus_cli.config import load_config
        config = load_config()
        return config.get("intelligence", {}).get("enabled", False)
    except Exception:
        return False


# ══════════════════════════════════════════════════════════════════
# vector_search tool
# ══════════════════════════════════════════════════════════════════

VECTOR_SEARCH_SCHEMA = {
    "name": "vector_search",
    "description": (
        "Search long-term memories, past experiences, strategies, and knowledge "
        "using semantic similarity. Returns relevant memories ranked by relevance. "
        "Use this to recall past sessions, decisions, solutions, people, projects, "
        "or any information stored across sessions. Complements session_search "
        "(keyword-based) with semantic understanding."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to search for (natural language query)",
            },
            "content_type": {
                "type": "string",
                "enum": ["episode", "strategy", "failure", "reflection",
                         "entity", "bookmark", "unfinished", "reasoning"],
                "description": "Filter by memory type (optional)",
            },
            "limit": {
                "type": "integer",
                "description": "Max results to return (default: 5)",
                "default": 5,
            },
        },
        "required": ["query"],
    },
}


def vector_search(
    query: str,
    content_type: Optional[str] = None,
    limit: int = 5,
    **kwargs,
) -> str:
    """Search vector memory store."""
    db = _get_intelligence_db()
    if not db:
        return "Intelligence module is not enabled. Enable it in config.yaml under 'intelligence.enabled: true'."

    provider = _get_embedding_provider()

    try:
        query_embedding = provider.embed(query)
        results = db.vector_search(
            query_embedding=query_embedding,
            content_type=content_type,
            limit=limit,
        )

        if not results:
            return f"No memories found matching: {query}"

        # Update access counts
        for r in results:
            try:
                db.update_access(r["id"])
            except Exception:
                pass

        # Format results
        output_parts = [f"Found {len(results)} relevant memories:\n"]
        for i, r in enumerate(results, 1):
            distance = f" (similarity: {1 - r['distance']:.2f})" if r.get("distance") is not None else ""
            meta = ""
            if r.get("metadata"):
                meta_items = []
                for k, v in r["metadata"].items():
                    if v and k not in ("session_id",):
                        meta_items.append(f"{k}={v}")
                if meta_items:
                    meta = f" [{', '.join(meta_items)}]"

            output_parts.append(
                f"{i}. [{r['content_type']}]{distance}{meta}\n"
                f"   {r['content'][:500]}\n"
            )

        return "\n".join(output_parts)

    except Exception as exc:
        logger.error("Vector search failed: %s", exc)
        return f"Search failed: {exc}"


# ══════════════════════════════════════════════════════════════════
# vector_remember tool
# ══════════════════════════════════════════════════════════════════

VECTOR_REMEMBER_SCHEMA = {
    "name": "vector_remember",
    "description": (
        "Store important information in long-term vector memory for future recall. "
        "Use this for insights, strategies, user preferences, important decisions, "
        "or anything that should be remembered across sessions but doesn't fit in "
        "the bounded MEMORY.md. Stored items are searchable via vector_search."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "What to remember (concise, self-contained)",
            },
            "content_type": {
                "type": "string",
                "enum": ["episode", "strategy", "failure", "reflection",
                         "entity", "bookmark", "reasoning"],
                "description": "Type of memory",
                "default": "episode",
            },
            "metadata": {
                "type": "object",
                "description": "Optional metadata (task_type, tags, etc.)",
            },
        },
        "required": ["content"],
    },
}


def vector_remember(
    content: str,
    content_type: str = "episode",
    metadata: Optional[Dict] = None,
    **kwargs,
) -> str:
    """Store content in vector memory."""
    db = _get_intelligence_db()
    if not db:
        return "Intelligence module is not enabled. Enable it in config.yaml under 'intelligence.enabled: true'."

    # Security scan before storage (content may be injected into system prompt)
    try:
        from intelligence.security import scan_content
        threat = scan_content(content)
        if threat:
            return f"Content blocked by security scan: {threat}"
    except ImportError:
        pass

    provider = _get_embedding_provider()

    try:
        embedding = provider.embed(content)
        session_id = kwargs.get("session_id")

        entry_id = db.store_embedding(
            content=content,
            content_type=content_type,
            embedding=embedding,
            metadata=metadata,
            session_id=session_id,
            tier="warm",
        )

        return f"Stored in long-term memory (id={entry_id}, type={content_type}). Searchable via vector_search."

    except Exception as exc:
        logger.error("Vector remember failed: %s", exc)
        return f"Failed to store memory: {exc}"


# ══════════════════════════════════════════════════════════════════
# knowledge_query tool
# ══════════════════════════════════════════════════════════════════

KNOWLEDGE_QUERY_SCHEMA = {
    "name": "knowledge_query",
    "description": (
        "Query the personal knowledge graph about people, projects, tools, "
        "concepts, and their relationships. Use to recall context about "
        "entities mentioned in conversations."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "entity_name": {
                "type": "string",
                "description": "Name of the entity to query",
            },
            "action": {
                "type": "string",
                "enum": ["lookup", "relationships", "list_entities"],
                "description": "What to do: lookup entity details, get relationships, or list all entities",
                "default": "lookup",
            },
            "entity_type": {
                "type": "string",
                "enum": ["person", "project", "tool", "concept", "org"],
                "description": "Filter by entity type (for list_entities action)",
            },
        },
        "required": ["entity_name"],
    },
}


def knowledge_query(
    entity_name: str = "",
    action: str = "lookup",
    entity_type: Optional[str] = None,
    **kwargs,
) -> str:
    """Query the knowledge graph."""
    db = _get_intelligence_db()
    if not db:
        return "Intelligence module is not enabled."

    try:
        if action == "list_entities":
            def _read(conn):
                extra = ""
                params = []
                if entity_type:
                    extra = " WHERE entity_type = ?"
                    params.append(entity_type)
                params.append(50)
                return conn.execute(
                    f"""SELECT name, entity_type, mention_count, attributes
                        FROM entities{extra}
                        ORDER BY mention_count DESC LIMIT ?""",
                    params,
                ).fetchall()

            entities = db._execute_read(_read)
            if not entities:
                return "No entities in knowledge graph yet."

            lines = ["Known entities:\n"]
            for e in entities:
                attrs = ""
                if e["attributes"]:
                    try:
                        a = json.loads(e["attributes"])
                        if a.get("context"):
                            attrs = f" — {a['context'][:100]}"
                    except (json.JSONDecodeError, TypeError):
                        pass
                lines.append(f"  [{e['entity_type']}] {e['name']} (mentions: {e['mention_count']}){attrs}")
            return "\n".join(lines)

        elif action == "relationships":
            result = db.query_entity_relationships(entity_name)
            if not result["entity"]:
                return f"Entity '{entity_name}' not found in knowledge graph."

            entity = result["entity"]
            rels = result["relationships"]

            lines = [f"Entity: {entity['name']} ({entity['entity_type']})"]
            if entity.get("attributes"):
                try:
                    attrs = json.loads(entity["attributes"])
                    for k, v in attrs.items():
                        lines.append(f"  {k}: {v}")
                except (json.JSONDecodeError, TypeError):
                    pass

            if rels:
                lines.append(f"\nRelationships ({len(rels)}):")
                for r in rels:
                    if r["source_entity_id"] == entity["id"]:
                        lines.append(f"  → {r['relationship_type']} → {r['target_name']} ({r['target_type']})")
                    else:
                        lines.append(f"  ← {r['relationship_type']} ← {r['source_name']} ({r['source_type']})")
            else:
                lines.append("\nNo relationships recorded.")

            return "\n".join(lines)

        else:  # lookup
            result = db.query_entity_relationships(entity_name)
            if not result["entity"]:
                return f"Entity '{entity_name}' not found."

            entity = result["entity"]
            lines = [
                f"Entity: {entity['name']}",
                f"Type: {entity['entity_type']}",
                f"Mentions: {entity['mention_count']}",
            ]
            if entity.get("attributes"):
                try:
                    attrs = json.loads(entity["attributes"])
                    for k, v in attrs.items():
                        lines.append(f"{k}: {v}")
                except (json.JSONDecodeError, TypeError):
                    pass

            return "\n".join(lines)

    except Exception as exc:
        logger.error("Knowledge query failed: %s", exc)
        return f"Query failed: {exc}"


# ══════════════════════════════════════════════════════════════════
# run_consolidation tool
# ══════════════════════════════════════════════════════════════════

RUN_CONSOLIDATION_SCHEMA = {
    "name": "run_consolidation",
    "description": (
        "Run memory consolidation: decay old memories, promote frequently-accessed "
        "ones, demote low-relevance entries, deduplicate near-identical entries, "
        "and distill cold memory clusters. Returns stats on actions taken."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "dry_run": {
                "type": "boolean",
                "description": "If true, report what would happen without making changes",
                "default": False,
            },
        },
    },
}


def run_consolidation_tool(
    dry_run: bool = False,
    **kwargs,
) -> str:
    """Run memory consolidation."""
    db = _get_intelligence_db()
    if not db:
        return "Intelligence module is not enabled."

    provider = _get_embedding_provider()

    try:
        from intelligence.consolidation import run_consolidation
        stats = run_consolidation(
            intelligence_db=db,
            embedding_provider=provider,
            dry_run=dry_run,
        )

        prefix = "[DRY RUN] " if dry_run else ""
        return (
            f"{prefix}Consolidation complete in {stats['duration_s']}s:\n"
            f"  Decayed: {stats['decayed']}\n"
            f"  Promoted to hot: {stats['promoted']}\n"
            f"  Demoted to cold: {stats['demoted']}\n"
            f"  Deduplicated: {stats['deduplicated']}\n"
            f"  Cold clusters distilled: {stats['distilled']}"
        )
    except Exception as exc:
        return f"Consolidation failed: {exc}"


# ══════════════════════════════════════════════════════════════════
# Wrapper handlers that extract args from the dict
# ══════════════════════════════════════════════════════════════════

def _handle_vector_search(args: dict, **kwargs) -> str:
    return vector_search(
        query=args.get("query", ""),
        content_type=args.get("content_type"),
        limit=args.get("limit", 5),
        **kwargs,
    )


def _handle_vector_remember(args: dict, **kwargs) -> str:
    return vector_remember(
        content=args.get("content", ""),
        content_type=args.get("content_type", "episode"),
        metadata=args.get("metadata"),
        **kwargs,
    )


def _handle_knowledge_query(args: dict, **kwargs) -> str:
    return knowledge_query(
        entity_name=args.get("entity_name", ""),
        action=args.get("action", "lookup"),
        entity_type=args.get("entity_type"),
        **kwargs,
    )


def _handle_run_consolidation(args: dict, **kwargs) -> str:
    return run_consolidation_tool(
        dry_run=args.get("dry_run", False),
        **kwargs,
    )


# ══════════════════════════════════════════════════════════════════
# Tool Registration
# ══════════════════════════════════════════════════════════════════

registry.register(
    name="vector_search",
    toolset="intelligence",
    schema=VECTOR_SEARCH_SCHEMA,
    handler=_handle_vector_search,
    check_fn=_check_intelligence_available,
    description="Search long-term memories using semantic similarity",
    emoji="🔍",
)

registry.register(
    name="vector_remember",
    toolset="intelligence",
    schema=VECTOR_REMEMBER_SCHEMA,
    handler=_handle_vector_remember,
    check_fn=_check_intelligence_available,
    description="Store important information in long-term memory",
    emoji="💾",
)

registry.register(
    name="knowledge_query",
    toolset="intelligence",
    schema=KNOWLEDGE_QUERY_SCHEMA,
    handler=_handle_knowledge_query,
    check_fn=_check_intelligence_available,
    description="Query the personal knowledge graph",
    emoji="🧠",
)

registry.register(
    name="run_consolidation",
    toolset="intelligence",
    schema=RUN_CONSOLIDATION_SCHEMA,
    handler=_handle_run_consolidation,
    check_fn=_check_intelligence_available,
    description="Run memory consolidation (decay, promote, dedup, distill)",
    emoji="🔄",
)
