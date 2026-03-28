"""
Memory consolidation — periodic background job for memory maintenance.

Runs as a system cron job (daily) to:
1. Decay relevance scores for inactive entries
2. Promote frequently-accessed entries to hot tier
3. Demote low-relevance entries to cold tier
4. Deduplicate near-identical entries (cosine similarity > 0.95)
5. Distill clusters of cold entries into single summaries
6. Update hot cache for system prompt injection
"""

import json
import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Consolidation parameters ──
DECAY_FACTOR = 0.95            # 5% decay per run
INACTIVE_DAYS = 7              # Only decay entries not accessed in 7+ days
PROMOTE_ACCESS_THRESHOLD = 3   # Access 3+ times in a week → promote
DEMOTE_RELEVANCE_THRESHOLD = 0.3  # Below this → cold tier
DEDUP_SIMILARITY_THRESHOLD = 0.95  # Cosine similarity for dedup
HOT_TIER_MAX = 10              # Max entries in hot tier
COLD_CLUSTER_SIZE = 5          # Min entries to cluster for distillation


def run_consolidation(
    intelligence_db,
    embedding_provider=None,
    aux_llm_call=None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Run full memory consolidation cycle.

    Args:
        intelligence_db: IntelligenceDB instance
        embedding_provider: Optional, for deduplication similarity
        aux_llm_call: Optional sync callable(system, user) -> str for distillation
        dry_run: If True, report what would happen without making changes

    Returns:
        Stats dict with counts of actions taken
    """
    stats = {
        "decayed": 0,
        "promoted": 0,
        "demoted": 0,
        "deduplicated": 0,
        "distilled": 0,
        "hot_updated": 0,
        "duration_s": 0,
    }
    start = time.time()

    try:
        # Step 1: Decay relevance
        if not dry_run:
            stats["decayed"] = intelligence_db.decay_relevance(
                decay_factor=DECAY_FACTOR,
                inactive_days=INACTIVE_DAYS,
            )
        else:
            stats["decayed"] = _count_decayable(intelligence_db)
        logger.info("Consolidation: decayed %d entries", stats["decayed"])

        # Step 2: Promote frequently-accessed warm entries to hot
        stats["promoted"] = _promote_entries(intelligence_db, dry_run)
        logger.info("Consolidation: promoted %d entries to hot tier", stats["promoted"])

        # Step 3: Demote low-relevance entries to cold
        stats["demoted"] = _demote_entries(intelligence_db, dry_run)
        logger.info("Consolidation: demoted %d entries to cold tier", stats["demoted"])

        # Step 4: Deduplicate near-identical entries
        if embedding_provider:
            stats["deduplicated"] = _deduplicate_entries(
                intelligence_db, embedding_provider, dry_run
            )
            logger.info("Consolidation: deduplicated %d entries", stats["deduplicated"])

        # Step 5: Distill cold entry clusters
        if aux_llm_call and not dry_run:
            stats["distilled"] = _distill_cold_entries(
                intelligence_db, aux_llm_call, embedding_provider
            )
            logger.info("Consolidation: distilled %d cold clusters", stats["distilled"])

        # Step 6: Ensure hot tier is capped
        stats["hot_updated"] = _cap_hot_tier(intelligence_db, dry_run)

    except Exception as exc:
        logger.error("Consolidation failed: %s", exc)

    stats["duration_s"] = round(time.time() - start, 2)
    logger.info("Consolidation complete: %s", stats)
    return stats


def _count_decayable(db) -> int:
    """Count entries that would be decayed (dry run)."""
    cutoff = time.time() - (INACTIVE_DAYS * 86400)
    result = db._execute_read(
        lambda conn: conn.execute(
            """SELECT COUNT(*) as cnt FROM embeddings
               WHERE (last_accessed_at IS NULL OR last_accessed_at < ?)
               AND relevance_score > 0.05""",
            (cutoff,),
        ).fetchone()
    )
    return result["cnt"] if result else 0


def _promote_entries(db, dry_run: bool) -> int:
    """Promote frequently-accessed warm entries to hot tier."""
    week_ago = time.time() - (7 * 86400)
    count = 0

    def _read(conn):
        return conn.execute(
            """SELECT id, access_count FROM embeddings
               WHERE tier = 'warm'
               AND access_count >= ?
               AND last_accessed_at > ?
               ORDER BY access_count DESC, relevance_score DESC
               LIMIT ?""",
            (PROMOTE_ACCESS_THRESHOLD, week_ago, HOT_TIER_MAX),
        ).fetchall()

    candidates = db._execute_read(_read)

    for row in candidates:
        if not dry_run:
            db.update_tier(row["id"], "hot")
        count += 1

    return count


def _demote_entries(db, dry_run: bool) -> int:
    """Demote low-relevance entries to cold tier."""
    count = 0

    def _read(conn):
        return conn.execute(
            """SELECT id FROM embeddings
               WHERE tier IN ('warm', 'hot')
               AND relevance_score < ?""",
            (DEMOTE_RELEVANCE_THRESHOLD,),
        ).fetchall()

    candidates = db._execute_read(_read)

    for row in candidates:
        if not dry_run:
            db.update_tier(row["id"], "cold")
        count += 1

    return count


def _deduplicate_entries(db, embedding_provider, dry_run: bool) -> int:
    """Remove near-duplicate entries based on embedding similarity."""
    from intelligence.embeddings import cosine_similarity

    count = 0

    # Get recent warm/hot entries for dedup (limit scope for performance)
    def _read(conn):
        return conn.execute(
            """SELECT id, content, content_type FROM embeddings
               WHERE tier IN ('warm', 'hot')
               ORDER BY created_at DESC
               LIMIT 200""",
        ).fetchall()

    entries = db._execute_read(_read)
    if len(entries) < 2:
        return 0

    # Embed all entries
    texts = [row["content"] for row in entries]
    try:
        embeddings = embedding_provider.embed_batch(texts)
    except Exception:
        return 0

    # Find duplicates (O(n^2) but limited to 200 entries)
    to_remove = set()
    for i in range(len(entries)):
        if entries[i]["id"] in to_remove:
            continue
        for j in range(i + 1, len(entries)):
            if entries[j]["id"] in to_remove:
                continue
            sim = cosine_similarity(embeddings[i], embeddings[j])
            if sim > DEDUP_SIMILARITY_THRESHOLD:
                # Keep the one with higher relevance (by index = more recent)
                to_remove.add(entries[j]["id"])

    if not dry_run:
        for entry_id in to_remove:
            try:
                db._execute_write(
                    lambda conn, eid=entry_id: conn.execute(
                        "DELETE FROM embeddings WHERE id = ?", (eid,)
                    )
                )
                count += 1
            except Exception:
                pass
    else:
        count = len(to_remove)

    return count


def _distill_cold_entries(db, aux_llm_call, embedding_provider=None) -> int:
    """Cluster and distill cold entries into summaries."""
    count = 0

    def _read(conn):
        return conn.execute(
            """SELECT id, content, content_type, metadata FROM embeddings
               WHERE tier = 'cold'
               ORDER BY content_type, created_at
               LIMIT 100""",
        ).fetchall()

    cold_entries = db._execute_read(_read)

    # Group by content_type
    groups: Dict[str, List] = {}
    for entry in cold_entries:
        ct = entry["content_type"]
        groups.setdefault(ct, []).append(entry)

    for content_type, entries in groups.items():
        if len(entries) < COLD_CLUSTER_SIZE:
            continue

        # Distill cluster into single summary
        combined = "\n\n".join(
            f"- {e['content']}" for e in entries[:20]  # Cap at 20
        )

        try:
            summary = aux_llm_call(
                "You are a memory consolidation system. Distill multiple related memories "
                "into a single concise summary that preserves all important information.",
                f"Distill these {content_type} memories into one concise entry:\n\n{combined}",
            )

            if summary and len(summary) > 20:
                # Store distilled version
                embedding = None
                if embedding_provider:
                    embedding = embedding_provider.embed(summary)

                db.store_embedding(
                    content=summary,
                    content_type=content_type,
                    embedding=embedding,
                    metadata={"distilled_from": len(entries), "source_type": content_type},
                    tier="warm",
                )

                # Remove originals
                for entry in entries:
                    db._execute_write(
                        lambda conn, eid=entry["id"]: conn.execute(
                            "DELETE FROM embeddings WHERE id = ?", (eid,)
                        )
                    )

                count += 1

        except Exception as exc:
            logger.warning("Distillation failed for %s cluster: %s", content_type, exc)

    return count


def _cap_hot_tier(db, dry_run: bool) -> int:
    """Ensure hot tier doesn't exceed max size. Demote excess to warm."""
    count = 0

    def _read(conn):
        return conn.execute(
            """SELECT id FROM embeddings
               WHERE tier = 'hot'
               ORDER BY relevance_score DESC, access_count DESC""",
        ).fetchall()

    hot_entries = db._execute_read(_read)

    if len(hot_entries) > HOT_TIER_MAX:
        excess = hot_entries[HOT_TIER_MAX:]
        for entry in excess:
            if not dry_run:
                db.update_tier(entry["id"], "warm")
            count += 1

    return count
