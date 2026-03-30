"""
CLI slash command handler for /intelligence (/intel).

Provides on-demand access to intelligence module features:
  /intel              — Quick status overview
  /intel digest       — Daily briefing
  /intel skills       — Skill health report
  /intel entities     — Knowledge graph entities
  /intel expertise    — Expertise map
  /intel synthesis    — Cross-session synthesis
  /intel consolidate  — Run memory consolidation now
  /intel preferences  — User preferences
  /intel suggestions  — Prompt optimization suggestions
  /intel patterns     — Detected workflow patterns
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def handle_intelligence_command(command: str, print_fn=None) -> None:
    """Handle /intelligence (or /intel) slash command.

    Args:
        command: Full command string (e.g. "/intel digest")
        print_fn: Optional print function (defaults to builtin print)
    """
    _print = print_fn or print
    parts = command.strip().split(None, 2)
    subcommand = parts[1].lower() if len(parts) > 1 else "status"
    args = parts[2] if len(parts) > 2 else ""

    try:
        from intelligence.integration import is_enabled, get_db, get_embedding_provider
        if not is_enabled():
            _print("  Intelligence module is not enabled.")
            _print("  Enable it in config.yaml: intelligence.enabled: true")
            return

        db = get_db()
        provider = get_embedding_provider()

        if subcommand == "status":
            _show_status(db, provider, _print)
        elif subcommand == "digest":
            _show_digest(db, provider, _print)
        elif subcommand in ("skills", "skill-health"):
            _show_skill_health(db, _print)
        elif subcommand in ("entities", "graph", "kg"):
            _show_entities(db, args, _print)
        elif subcommand in ("expertise", "expert"):
            _show_expertise(db, _print)
        elif subcommand in ("synthesis", "synth"):
            _show_synthesis(db, provider, args, _print)
        elif subcommand in ("consolidate", "consolidation"):
            _run_consolidation(db, provider, _print)
        elif subcommand in ("preferences", "prefs"):
            _show_preferences(db, _print)
        elif subcommand in ("suggestions", "optimize"):
            _show_suggestions(db, _print)
        elif subcommand in ("patterns", "workflows"):
            _show_patterns(db, _print)
        elif subcommand in ("sentiment", "mood"):
            _show_sentiment(db, _print)
        elif subcommand == "help":
            _show_help(_print)
        else:
            _print(f"  Unknown subcommand: {subcommand}")
            _show_help(_print)

    except ImportError:
        _print("  Intelligence module is not installed.")
        _print("  Install with: pip install morpheus-agent[intelligence]")
    except Exception as exc:
        _print(f"  Error: {exc}")


def _show_status(db, provider, _print):
    """Show intelligence module status overview."""
    _print("  Intelligence Module Status")
    _print("  " + "=" * 35)
    _print(f"  Database: {db.db_path}")
    _print(f"  Vector search: {'available' if db.vec_available else 'unavailable (hash fallback)'}")
    _print(f"  Embedding provider: {provider.name}")

    # Counts
    counts = {}
    for table in ["embeddings", "episodes", "entities", "strategies",
                   "failure_journal", "reflections", "skill_scores",
                   "bookmarks", "workflow_patterns"]:
        try:
            result = db._execute_read(
                lambda conn, t=table: conn.execute(
                    f"SELECT COUNT(*) as cnt FROM {t}"
                ).fetchone()
            )
            counts[table] = result["cnt"]
        except Exception:
            counts[table] = 0

    _print("")
    _print(f"  Memories: {counts['embeddings']}  |  Episodes: {counts['episodes']}")
    _print(f"  Entities: {counts['entities']}  |  Strategies: {counts['strategies']}")
    _print(f"  Failures: {counts['failure_journal']}  |  Reflections: {counts['reflections']}")
    _print(f"  Skills scored: {counts['skill_scores']}  |  Bookmarks: {counts['bookmarks']}")
    _print(f"  Workflow patterns: {counts['workflow_patterns']}")

    # Tier distribution
    try:
        tiers = db._execute_read(
            lambda conn: conn.execute(
                "SELECT tier, COUNT(*) as cnt FROM embeddings GROUP BY tier"
            ).fetchall()
        )
        tier_str = ", ".join(f"{r['tier']}: {r['cnt']}" for r in tiers)
        _print(f"  Memory tiers: {tier_str or 'empty'}")
    except Exception:
        pass

    _print("")
    _print("  Use /intel help for available subcommands")


def _show_digest(db, provider, _print):
    """Show daily briefing."""
    from intelligence.monitors import generate_daily_digest
    digest = generate_daily_digest(db, embedding_provider=provider)
    _print(f"  {digest}")


def _show_skill_health(db, _print):
    """Show skill health report."""
    from intelligence.skill_eval import get_skill_health_report, format_skill_scores_for_display
    report = get_skill_health_report(db)
    _print(f"  {format_skill_scores_for_display(report)}")


def _show_entities(db, filter_type, _print):
    """Show knowledge graph entities."""
    def _read(conn):
        if filter_type:
            return conn.execute(
                """SELECT name, entity_type, mention_count
                   FROM entities WHERE entity_type = ?
                   ORDER BY mention_count DESC LIMIT 30""",
                (filter_type,),
            ).fetchall()
        return conn.execute(
            """SELECT name, entity_type, mention_count
               FROM entities ORDER BY mention_count DESC LIMIT 30""",
        ).fetchall()

    entities = db._execute_read(_read)
    if not entities:
        _print("  No entities in knowledge graph yet.")
        return

    _print(f"  Knowledge Graph ({len(entities)} entities):")
    for e in entities:
        _print(f"    [{e['entity_type']}] {e['name']} (mentions: {e['mention_count']})")


def _show_expertise(db, _print):
    """Show expertise map."""
    expertise = db.get_expertise_map()
    if not expertise:
        _print("  No expertise data yet.")
        return

    _print("  Expertise Map:")
    for domain, data in expertise.items():
        _print(f"    {domain}: {data['proficiency']}")


def _show_synthesis(db, provider, topic, _print):
    """Show cross-session synthesis."""
    if not topic:
        _print("  Usage: /intel synthesis <topic>")
        _print("  Example: /intel synthesis authentication")
        return

    _print(f"  Synthesizing sessions about '{topic}'...")

    try:
        from intelligence.integration import _ensure_initialized
        from intelligence.synthesis import synthesize_topic

        # Need an LLM for synthesis — try auxiliary
        try:
            from agent.auxiliary_client import call_llm as _aux_call

            def _sync_call(system, user):
                resp = _aux_call(
                    task="flush_memories",
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    temperature=0.3,
                    max_tokens=4096,
                    timeout=45.0,
                )
                return resp.choices[0].message.content if resp and resp.choices else ""

            result = synthesize_topic(topic, db, provider, sync_llm_call=_sync_call)
            _print(f"  {result or 'No synthesis generated.'}")
        except RuntimeError:
            _print("  No auxiliary LLM available for synthesis.")
    except Exception as exc:
        _print(f"  Synthesis failed: {exc}")


def _run_consolidation(db, provider, _print):
    """Run memory consolidation now."""
    _print("  Running memory consolidation...")
    from intelligence.consolidation import run_consolidation
    stats = run_consolidation(db, embedding_provider=provider)
    _print(f"  Done in {stats['duration_s']}s:")
    _print(f"    Decayed: {stats['decayed']}")
    _print(f"    Promoted: {stats['promoted']}")
    _print(f"    Demoted: {stats['demoted']}")
    _print(f"    Deduplicated: {stats['deduplicated']}")
    _print(f"    Distilled: {stats['distilled']}")


def _show_preferences(db, _print):
    """Show user preferences."""
    prefs = db.get_preferences()
    if not prefs:
        _print("  No preferences detected yet.")
        return

    _print("  User Preferences:")
    for key, data in prefs.items():
        _print(f"    {key}: {data['value']} (confidence: {data['confidence']:.0%})")


def _show_suggestions(db, _print):
    """Show prompt optimization suggestions."""
    from intelligence.prompt_optimizer import get_pending_suggestions
    suggestions = get_pending_suggestions(db)
    if not suggestions:
        _print("  No optimization suggestions available.")
        _print("  Run /intel optimize after using Morpheus for a while.")
        return

    _print("  Optimization Suggestions:")
    for i, s in enumerate(suggestions, 1):
        _print(f"    {i}. [{s.get('area', '?')}] {s.get('suggested_change', 'N/A')}")
        if s.get("rationale"):
            _print(f"       Why: {s['rationale'][:150]}")


def _show_patterns(db, _print):
    """Show detected workflow patterns."""
    patterns = db.get_frequent_patterns(min_frequency=2)
    if not patterns:
        _print("  No workflow patterns detected yet.")
        return

    _print(f"  Workflow Patterns ({len(patterns)} detected):")
    for p in patterns:
        import json
        tools = json.loads(p["tool_sequence"]) if isinstance(p["tool_sequence"], str) else p["tool_sequence"]
        name = p.get("pattern_name") or " -> ".join(tools[:4])
        _print(f"    {name} (frequency: {p['frequency']})")
        auto = "offered" if p.get("automation_offered") else "not offered"
        _print(f"      Automation: {auto}")


def _show_sentiment(db, _print):
    """Show sentiment trend."""
    sentiments = db.get_sentiment_trend(limit=15)
    if not sentiments:
        _print("  No sentiment data yet.")
        return

    _print(f"  Sentiment Trend (last {len(sentiments)} sessions):")
    counts = {}
    for s in sentiments:
        overall = s.get("overall", "unknown")
        counts[overall] = counts.get(overall, 0) + 1

    for sentiment, count in sorted(counts.items(), key=lambda x: -x[1]):
        bar = "#" * count
        _print(f"    {sentiment:12s} {bar} ({count})")


def _show_help(_print):
    """Show available subcommands."""
    _print("  /intel subcommands:")
    _print("    status        — Module status overview (default)")
    _print("    digest        — Daily briefing")
    _print("    skills        — Skill health report")
    _print("    entities [type] — Knowledge graph entities")
    _print("    expertise     — User expertise map")
    _print("    synthesis <topic> — Cross-session synthesis")
    _print("    consolidate   — Run memory consolidation now")
    _print("    preferences   — User preferences")
    _print("    suggestions   — Prompt optimization suggestions")
    _print("    patterns      — Workflow patterns")
    _print("    sentiment     — Sentiment trend")
    _print("    help          — This help")
