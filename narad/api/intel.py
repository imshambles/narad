import json

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from narad.database import get_session
from narad.models import Entity, EntityRelation, Signal, ThreatMatrix

router = APIRouter(tags=["intel"])


@router.get("/intel/entities")
async def list_entities(
    entity_type: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(Entity).order_by(Entity.mention_count.desc()).limit(limit)
    if entity_type:
        stmt = stmt.where(Entity.entity_type == entity_type)
    result = await session.execute(stmt)
    return [
        {
            "id": e.id, "name": e.name, "type": e.entity_type,
            "mentions": e.mention_count, "last_seen": e.last_seen_at,
        }
        for e in result.scalars().all()
    ]


@router.get("/intel/threat-matrix")
async def get_threat_matrix(session: AsyncSession = Depends(get_session)):
    stmt = select(ThreatMatrix).order_by(desc(ThreatMatrix.tension_score + ThreatMatrix.cooperation_score))
    result = await session.execute(stmt)
    entries = []
    for tm in result.scalars().all():
        country = await session.get(Entity, tm.country_entity_id)
        if not country:
            continue
        entries.append({
            "country": country.name,
            "country_id": country.id,
            "cooperation": round(tm.cooperation_score, 2),
            "tension": round(tm.tension_score, 2),
            "trend": tm.trend,
            "recent_events": json.loads(tm.recent_events_json or "[]"),
            "updated_at": tm.updated_at,
        })
    return entries


@router.get("/intel/signals")
async def list_signals(
    active_only: bool = Query(True),
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(Signal).order_by(Signal.detected_at.desc()).limit(limit)
    if active_only:
        stmt = stmt.where(Signal.is_active == True)
    result = await session.execute(stmt)
    return [
        {
            "id": s.id, "type": s.signal_type, "title": s.title,
            "description": s.description, "severity": s.severity,
            "detected_at": s.detected_at, "data": json.loads(s.data_json or "{}"),
        }
        for s in result.scalars().all()
    ]


@router.get("/intel/entity-graph")
async def get_entity_graph(
    min_mentions: int = Query(3),
    session: AsyncSession = Depends(get_session),
):
    """Return entity nodes and relationship edges for visualization."""
    entities_stmt = select(Entity).where(Entity.mention_count >= min_mentions).order_by(Entity.mention_count.desc()).limit(100)
    result = await session.execute(entities_stmt)
    entities = list(result.scalars().all())
    entity_ids = {e.id for e in entities}

    nodes = [
        {"id": e.id, "name": e.name, "type": e.entity_type, "mentions": e.mention_count}
        for e in entities
    ]

    # Get relations between these entities
    relations_stmt = select(EntityRelation).where(
        EntityRelation.entity_a_id.in_(entity_ids),
        EntityRelation.entity_b_id.in_(entity_ids),
    )
    result = await session.execute(relations_stmt)
    edges = [
        {
            "source": r.entity_a_id, "target": r.entity_b_id,
            "type": r.relation_type, "weight": r.co_occurrence_count,
        }
        for r in result.scalars().all()
    ]

    return {"nodes": nodes, "edges": edges}
