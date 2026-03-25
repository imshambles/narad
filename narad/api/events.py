import json

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from narad.database import get_session
from narad.models import Article, Event, EventArticle, EventRelationship
from narad.schemas import (
    ArticleOut, EventDetailOut, EventOut, GraphEdgeOut, GraphNodeOut,
    GraphOut, RelatedEventOut,
)

router = APIRouter(tags=["events"])


def _parse_json(raw: str | None) -> list:
    if not raw:
        return []
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []


@router.get("/events", response_model=list[EventOut])
async def list_events(
    category: str | None = Query(None),
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    stmt = (
        select(Event)
        .where(Event.is_active == True)
        .order_by(Event.last_updated_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if category:
        stmt = stmt.where(Event.category == category)

    result = await session.execute(stmt)
    events = result.scalars().all()

    return [
        EventOut(
            id=e.id,
            title=e.title,
            summary=e.summary,
            category=e.category,
            article_count=e.article_count,
            source_count=e.source_count,
            first_seen_at=e.first_seen_at,
            last_updated_at=e.last_updated_at,
            entities=_parse_json(e.entities_json),
        )
        for e in events
    ]


@router.get("/events/graph", response_model=GraphOut)
async def get_event_graph(
    hours: int = Query(48, ge=1, le=168),
    session: AsyncSession = Depends(get_session),
):
    from datetime import datetime, timedelta, timezone
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    events_stmt = (
        select(Event)
        .where(Event.is_active == True)
        .where(Event.last_updated_at >= cutoff)
    )
    result = await session.execute(events_stmt)
    events = result.scalars().all()
    event_ids = [e.id for e in events]

    nodes = [
        GraphNodeOut(id=e.id, title=e.title, category=e.category, article_count=e.article_count)
        for e in events
    ]

    edges_stmt = select(EventRelationship).where(
        or_(
            EventRelationship.source_event_id.in_(event_ids),
            EventRelationship.target_event_id.in_(event_ids),
        )
    )
    result = await session.execute(edges_stmt)
    relationships = result.scalars().all()

    edges = [
        GraphEdgeOut(
            source=r.source_event_id,
            target=r.target_event_id,
            relationship_type=r.relationship_type,
            weight=r.weight,
            shared_entities=_parse_json(r.shared_entities),
        )
        for r in relationships
    ]

    return GraphOut(nodes=nodes, edges=edges)


@router.get("/events/{event_id}", response_model=EventDetailOut)
async def get_event(event_id: int, session: AsyncSession = Depends(get_session)):
    stmt = (
        select(Event)
        .where(Event.id == event_id)
        .options(joinedload(Event.articles).joinedload(EventArticle.article).joinedload(Article.source))
    )
    result = await session.execute(stmt)
    event = result.scalars().unique().one_or_none()
    if not event:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Event not found")

    articles = [
        ArticleOut(
            id=ea.article.id,
            title=ea.article.title,
            summary=ea.article.summary,
            external_url=ea.article.external_url,
            published_at=ea.article.published_at,
            source_name=ea.article.source.name,
            image_url=ea.article.image_url,
        )
        for ea in event.articles if ea.article
    ]

    # Get related events
    rel_stmt = select(EventRelationship).where(
        or_(
            EventRelationship.source_event_id == event_id,
            EventRelationship.target_event_id == event_id,
        )
    )
    result = await session.execute(rel_stmt)
    relationships = result.scalars().all()

    related = []
    for r in relationships:
        other_id = r.target_event_id if r.source_event_id == event_id else r.source_event_id
        other_event = await session.get(Event, other_id)
        if other_event:
            related.append(RelatedEventOut(
                event_id=other_event.id,
                title=other_event.title,
                relationship_type=r.relationship_type,
                shared_entities=_parse_json(r.shared_entities),
                weight=r.weight,
            ))

    return EventDetailOut(
        id=event.id,
        title=event.title,
        summary=event.summary,
        category=event.category,
        article_count=event.article_count,
        source_count=event.source_count,
        first_seen_at=event.first_seen_at,
        last_updated_at=event.last_updated_at,
        entities=_parse_json(event.entities_json),
        key_facts=_parse_json(event.key_facts),
        disagreements=_parse_json(event.disagreements),
        articles=articles,
        related_events=related,
    )
