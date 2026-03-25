"""
Entity Knowledge Graph Engine

Processes events → extracts entities → builds persistent graph
with evolving relationships and co-occurrence tracking.
"""
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from narad.database import async_session
from narad.models import Entity, EntityMention, EntityRelation, Event

logger = logging.getLogger(__name__)


def _canonical(name: str) -> str:
    """Normalize entity name for dedup."""
    return name.strip().lower().replace("'", "'").replace('"', '')


async def update_entity_graph() -> None:
    """Scan summarized events and update the entity knowledge graph."""
    async with async_session() as session:
        # Get events that have entities but haven't been processed into the graph yet
        # We track this by checking if EntityMention exists for the event
        stmt = (
            select(Event)
            .where(Event.entities_json.isnot(None))
            .where(Event.is_active == True)
            .order_by(Event.last_updated_at.desc())
            .limit(100)
        )
        result = await session.execute(stmt)
        events = list(result.scalars().all())

        processed = 0
        for event in events:
            # Check if already processed
            existing = await session.execute(
                select(EntityMention.id).where(EntityMention.event_id == event.id).limit(1)
            )
            if existing.scalar_one_or_none() is not None:
                continue

            try:
                entities_data = json.loads(event.entities_json)
            except (json.JSONDecodeError, TypeError):
                continue

            if not entities_data:
                continue

            # Resolve entities (get or create)
            event_entities = []
            for ent_data in entities_data:
                name = ent_data.get("name", "").strip()
                etype = ent_data.get("type", "unknown")
                if not name or len(name) < 2:
                    continue

                canonical = _canonical(name)
                entity = await _get_or_create_entity(session, name, etype, canonical)
                event_entities.append(entity)

                # Create mention
                session.add(EntityMention(
                    entity_id=entity.id,
                    event_id=event.id,
                    sentiment=_estimate_sentiment(event, name),
                    mentioned_at=event.first_seen_at or datetime.now(timezone.utc),
                ))

                # Update mention count
                entity.mention_count += 1
                entity.last_seen_at = datetime.now(timezone.utc)

            # Update co-occurrence relationships between all entity pairs in this event
            for i, ent_a in enumerate(event_entities):
                for ent_b in event_entities[i + 1:]:
                    await _update_relation(session, ent_a, ent_b, event)

            processed += 1

        if processed:
            await session.commit()
            logger.info(f"Entity graph: processed {processed} events")


async def _get_or_create_entity(
    session: AsyncSession, name: str, entity_type: str, canonical: str
) -> Entity:
    """Get existing entity or create new one."""
    result = await session.execute(
        select(Entity).where(Entity.canonical_name == canonical).limit(1)
    )
    entity = result.scalar_one_or_none()

    if entity:
        # Update type if more specific (e.g., was 'unknown', now 'country')
        if entity.entity_type == "unknown" and entity_type != "unknown":
            entity.entity_type = entity_type
        return entity

    now = datetime.now(timezone.utc)
    entity = Entity(
        name=name,
        entity_type=entity_type,
        canonical_name=canonical,
        first_seen_at=now,
        last_seen_at=now,
        mention_count=0,
    )
    session.add(entity)
    await session.flush()
    return entity


async def _update_relation(
    session: AsyncSession, ent_a: Entity, ent_b: Entity, event: Event
) -> None:
    """Update or create a relationship between two entities based on co-occurrence."""
    # Ensure consistent ordering (smaller ID first)
    if ent_a.id > ent_b.id:
        ent_a, ent_b = ent_b, ent_a

    result = await session.execute(
        select(EntityRelation)
        .where(EntityRelation.entity_a_id == ent_a.id)
        .where(EntityRelation.entity_b_id == ent_b.id)
        .limit(1)
    )
    relation = result.scalar_one_or_none()

    # Determine relation type from event category
    category = (event.category or "").lower()
    if category in ("conflict",):
        rel_type = "conflict"
    elif category in ("diplomacy",):
        rel_type = "diplomacy"
    elif category in ("economy",):
        rel_type = "trade"
    elif category in ("defense",):
        rel_type = "defense"
    else:
        rel_type = "general"

    # Build context snippet
    context = {"event_id": event.id, "title": event.title[:100], "category": category}

    now = datetime.now(timezone.utc)

    if relation:
        relation.co_occurrence_count += 1
        relation.last_updated_at = now
        relation.relation_type = rel_type  # update to most recent
        # Append to context
        try:
            existing_ctx = json.loads(relation.context_json or "[]")
        except (json.JSONDecodeError, TypeError):
            existing_ctx = []
        existing_ctx.append(context)
        # Keep last 10 contexts
        relation.context_json = json.dumps(existing_ctx[-10:])
    else:
        relation = EntityRelation(
            entity_a_id=ent_a.id,
            entity_b_id=ent_b.id,
            relation_type=rel_type,
            weight=0.0,
            co_occurrence_count=1,
            last_updated_at=now,
            trend="stable",
            context_json=json.dumps([context]),
        )
        session.add(relation)


def _estimate_sentiment(event: Event, entity_name: str) -> float:
    """Quick sentiment estimate based on event category and content."""
    category = (event.category or "").lower()
    title_lower = (event.title or "").lower()
    entity_lower = entity_name.lower()

    # Conflict events involving this entity = negative sentiment
    if category == "conflict":
        return -0.5
    elif category == "diplomacy" or "cooperation" in title_lower or "agreement" in title_lower:
        return 0.3
    elif category == "humanitarian":
        return -0.3
    elif "sanctions" in title_lower:
        return -0.4
    elif "trade" in title_lower or "deal" in title_lower:
        return 0.3
    return 0.0
