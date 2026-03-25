import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from narad.database import async_session
from narad.models import Event, EventRelationship

logger = logging.getLogger(__name__)


async def build_relationships() -> None:
    """Scan active events and create relationship edges."""
    async with async_session() as session:
        stmt = select(Event).where(Event.is_active == True).where(Event.entities_json.isnot(None))
        result = await session.execute(stmt)
        events = list(result.scalars().all())

        if len(events) < 2:
            return

        # Clear existing relationships for active events (rebuild each time)
        event_ids = [e.id for e in events]
        await session.execute(
            delete(EventRelationship).where(
                EventRelationship.source_event_id.in_(event_ids)
            )
        )

        # Parse entities for all events
        event_entities: dict[int, list[dict]] = {}
        for event in events:
            try:
                entities = json.loads(event.entities_json) if event.entities_json else []
                event_entities[event.id] = entities
            except json.JSONDecodeError:
                event_entities[event.id] = []

        new_edges = 0
        now = datetime.now(timezone.utc)

        for i, event_a in enumerate(events):
            for event_b in events[i + 1:]:
                edges = _find_edges(event_a, event_b, event_entities)
                for edge in edges:
                    session.add(EventRelationship(
                        source_event_id=event_a.id,
                        target_event_id=event_b.id,
                        relationship_type=edge["type"],
                        shared_entities=json.dumps(edge.get("shared", [])),
                        weight=edge["weight"],
                        created_at=now,
                    ))
                    new_edges += 1

        await session.commit()
        logger.info(f"Graph builder: {new_edges} relationship edges for {len(events)} events")


def _find_edges(event_a: Event, event_b: Event, entities: dict) -> list[dict]:
    """Find all relationship edges between two events."""
    edges = []

    # 1. Shared entities
    ents_a = {e["name"].lower() for e in entities.get(event_a.id, [])}
    ents_b = {e["name"].lower() for e in entities.get(event_b.id, [])}
    shared = ents_a & ents_b

    if shared:
        max_ents = max(len(ents_a), len(ents_b), 1)
        weight = len(shared) / max_ents
        edges.append({
            "type": "shared_entity",
            "shared": sorted(shared),
            "weight": round(weight, 3),
        })

    # 2. Temporal proximity (same category, within 24h)
    if (event_a.category and event_b.category
            and event_a.category == event_b.category):
        time_a = event_a.first_seen_at or event_a.last_updated_at
        time_b = event_b.first_seen_at or event_b.last_updated_at
        if time_a and time_b:
            # Ensure both are offset-naive or offset-aware for comparison
            if time_a.tzinfo is None:
                time_a = time_a.replace(tzinfo=timezone.utc)
            if time_b.tzinfo is None:
                time_b = time_b.replace(tzinfo=timezone.utc)
            hour_gap = abs((time_a - time_b).total_seconds()) / 3600
            if hour_gap <= 24:
                weight = round(1.0 - (hour_gap / 24), 3)
                if weight > 0.3:
                    edges.append({
                        "type": "temporal",
                        "weight": weight,
                    })

    return edges
