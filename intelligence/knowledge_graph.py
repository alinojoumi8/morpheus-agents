"""
Personal knowledge graph — extract and manage entities + relationships.

Automatically extracts entities (people, projects, tools, concepts) and their
relationships from conversations. Builds a persistent knowledge graph that
enriches future context.
"""

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

ENTITY_EXTRACTION_PROMPT = """\
Extract entities and relationships from this conversation excerpt.

Return JSON:
{
  "entities": [
    {"name": "entity name", "type": "person|project|tool|concept|org", "attributes": {"key": "value"}}
  ],
  "relationships": [
    {"source": "entity1 name", "target": "entity2 name", "type": "works_on|uses|knows|manages|depends_on|created|part_of", "context": "brief context"}
  ]
}

Rules:
- Only extract clearly mentioned entities (don't infer)
- Use consistent naming (prefer full names)
- Types: person (humans), project (repos/products), tool (software/frameworks), concept (ideas/patterns), org (companies/teams)
- Relationships should be factual, not speculative
- Respond with ONLY valid JSON

Conversation excerpt:
"""


def extract_entities_from_episode(
    episode_data: Dict[str, Any],
    session_id: str,
    sync_llm_call=None,
    intelligence_db=None,
    embedding_provider=None,
) -> Optional[Dict[str, Any]]:
    """Extract entities and relationships from an episode.

    If episode_data already has entities_mentioned (from episodic extraction),
    use those directly. Otherwise, use LLM for deeper extraction.
    """
    if not intelligence_db:
        return None

    # Use entities already extracted by episodic module
    entities_mentioned = episode_data.get("entities_mentioned", [])

    if entities_mentioned:
        return _store_entities_from_list(
            intelligence_db, entities_mentioned, session_id, embedding_provider
        )

    # If no pre-extracted entities and LLM available, do deeper extraction
    if sync_llm_call and episode_data.get("summary"):
        return _extract_with_llm(
            sync_llm_call, episode_data, session_id,
            intelligence_db, embedding_provider
        )

    return None


def _store_entities_from_list(
    db, entities: List[Dict], session_id: str, embedding_provider=None
) -> Dict[str, Any]:
    """Store pre-extracted entities in the knowledge graph."""
    stored = {"entities": 0, "relationships": 0}

    for entity in entities:
        name = entity.get("name", "").strip()
        if not name or len(name) < 2:
            continue

        entity_type = entity.get("type", "concept")
        attributes = {}
        if entity.get("context"):
            attributes["context"] = entity["context"]

        try:
            embedding_id = None
            if embedding_provider:
                emb = embedding_provider.embed(f"{entity_type}: {name}")
                embedding_id = db.store_embedding(
                    content=f"{entity_type}: {name}",
                    content_type="entity",
                    embedding=emb,
                    metadata={"entity_name": name, "entity_type": entity_type},
                    session_id=session_id,
                )

            db.upsert_entity(
                name=name,
                entity_type=entity_type,
                attributes=attributes,
                session_id=session_id,
                embedding_id=embedding_id,
            )
            stored["entities"] += 1
        except Exception as exc:
            logger.debug("Failed to store entity '%s': %s", name, exc)

    return stored


def _extract_with_llm(
    sync_llm_call, episode_data: Dict, session_id: str,
    db, embedding_provider=None,
) -> Optional[Dict[str, Any]]:
    """Use LLM for deeper entity/relationship extraction."""
    summary = episode_data.get("summary", "")
    decisions = json.dumps(episode_data.get("decisions", []), default=str)
    problems = json.dumps(episode_data.get("problems_solved", []), default=str)

    context = f"Summary: {summary}\nDecisions: {decisions}\nProblems: {problems}"

    try:
        response = sync_llm_call(
            "You are an information extraction system.",
            ENTITY_EXTRACTION_PROMPT + context[:5000],
        )

        data = _parse_json(response)
        if not data:
            return None

        stored = {"entities": 0, "relationships": 0}

        # Store entities
        entity_name_to_id = {}
        for entity in data.get("entities", []):
            name = entity.get("name", "").strip()
            if not name:
                continue

            entity_id = db.upsert_entity(
                name=name,
                entity_type=entity.get("type", "concept"),
                attributes=entity.get("attributes"),
                session_id=session_id,
            )
            entity_name_to_id[name.lower()] = entity_id
            stored["entities"] += 1

        # Store relationships
        for rel in data.get("relationships", []):
            source = rel.get("source", "").strip().lower()
            target = rel.get("target", "").strip().lower()
            rel_type = rel.get("type", "related_to")

            source_id = entity_name_to_id.get(source)
            target_id = entity_name_to_id.get(target)

            if source_id and target_id and source_id != target_id:
                db.add_relationship(
                    source_entity_id=source_id,
                    target_entity_id=target_id,
                    relationship_type=rel_type,
                    context=rel.get("context"),
                )
                stored["relationships"] += 1

        return stored

    except Exception as exc:
        logger.warning("LLM entity extraction failed: %s", exc)
        return None


def _parse_json(response: str) -> Optional[Dict]:
    """Parse JSON from LLM response."""
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


def get_entity_context_for_prompt(
    intelligence_db,
    query: str,
    embedding_provider=None,
    max_entities: int = 5,
) -> str:
    """Get relevant entity context to inject into system prompt.

    Searches knowledge graph for entities relevant to the current query.
    Returns formatted context string.
    """
    if not intelligence_db or not embedding_provider:
        return ""

    try:
        query_emb = embedding_provider.embed(query)
        results = intelligence_db.vector_search(
            query_embedding=query_emb,
            content_type="entity",
            limit=max_entities,
        )

        if not results:
            return ""

        parts = ["Known context:"]
        for r in results:
            meta = r.get("metadata", {}) or {}
            name = meta.get("entity_name", r["content"])
            etype = meta.get("entity_type", "")
            parts.append(f"- {name} ({etype})")

        return "\n".join(parts)

    except Exception:
        return ""
