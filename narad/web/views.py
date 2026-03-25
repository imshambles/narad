import json
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from narad.database import get_session
from narad.models import (
    Article, Briefing, Event, EventArticle, EventRelationship, FetchLog, Source,
)

from datetime import datetime, timedelta, timezone

router = APIRouter(tags=["web"])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))

IST = timezone(timedelta(hours=5, minutes=30))


def _to_ist(dt):
    """Convert a datetime to IST."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST)


templates.env.filters["ist"] = _to_ist

INDIA_SOURCES = {"Reuters India", "AP India", "India Diplomacy", "India Defence", "India Geopolitics", "ANI Wire"}


def _parse_json(raw: str | None) -> list:
    if not raw:
        return []
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []


# ──────────────────────────────────────────────
# /  — Daily Briefing (the front door)
# ──────────────────────────────────────────────
@router.get("/")
async def briefing_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    # Get current briefing
    stmt = select(Briefing).where(Briefing.is_current == True).limit(1)
    result = await session.execute(stmt)
    briefing = result.scalar_one_or_none()

    stories = _parse_json(briefing.stories_json) if briefing else []
    connections = _parse_json(briefing.connections_json) if briefing else []

    # Stats for empty state
    total_articles = (await session.execute(select(func.count()).select_from(Article))).scalar() or 0
    total_events = (await session.execute(select(func.count()).select_from(Event).where(Event.is_active == True))).scalar() or 0
    events_summarized = (await session.execute(
        select(func.count()).select_from(Event).where(Event.summary.isnot(None))
    )).scalar() or 0

    return templates.TemplateResponse(
        request,
        "briefing.html",
        {
            "active_tab": "briefing",
            "briefing": briefing,
            "stories": stories,
            "connections": connections,
            "total_articles": total_articles,
            "total_events": total_events,
            "events_summarized": events_summarized,
        },
    )


# ──────────────────────────────────────────────
# /explore  — All events list
# ──────────────────────────────────────────────
@router.get("/explore")
async def events_dashboard(
    request: Request,
    category: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
):
    stmt = (
        select(Event)
        .where(Event.is_active == True)
        .order_by(Event.article_count.desc(), Event.last_updated_at.desc())
        .limit(50)
    )
    if category:
        stmt = stmt.where(Event.category == category)

    result = await session.execute(stmt)
    events = result.scalars().all()

    for e in events:
        e.entities = _parse_json(e.entities_json)

    cat_result = await session.execute(
        select(func.distinct(Event.category))
        .where(Event.is_active == True)
        .where(Event.category.isnot(None))
    )
    categories = sorted([c[0] for c in cat_result.all()])

    return templates.TemplateResponse(
        request,
        "events.html",
        {
            "events": events,
            "categories": categories,
            "selected_category": category,
            "active_tab": "explore",
        },
    )


# ──────────────────────────────────────────────
# /events/{id}  — Event detail
# ──────────────────────────────────────────────
@router.get("/events/{event_id}")
async def event_detail(
    request: Request,
    event_id: int,
    session: AsyncSession = Depends(get_session),
):
    stmt = (
        select(Event)
        .where(Event.id == event_id)
        .options(joinedload(Event.articles).joinedload(EventArticle.article).joinedload(Article.source))
    )
    result = await session.execute(stmt)
    event = result.scalars().unique().one_or_none()
    if not event:
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/")

    articles = sorted(
        [ea.article for ea in event.articles if ea.article],
        key=lambda a: a.published_at,
        reverse=True,
    )

    entities = _parse_json(event.entities_json)
    key_facts = _parse_json(event.key_facts)
    disagreements = _parse_json(event.disagreements)
    timeline = _parse_json(event.timeline_json)
    # Parse timeline timestamps and sort chronologically
    from dateutil.parser import parse as parse_date
    for m in timeline:
        try:
            m["parsed_time"] = parse_date(m["time"])
        except Exception:
            m["parsed_time"] = None
    timeline = sorted(timeline, key=lambda m: m.get("parsed_time") or datetime.min)

    rel_stmt = select(EventRelationship).where(
        or_(
            EventRelationship.source_event_id == event_id,
            EventRelationship.target_event_id == event_id,
        )
    )
    rel_result = await session.execute(rel_stmt)
    relationships = rel_result.scalars().all()

    related_events = []
    for r in relationships:
        other_id = r.target_event_id if r.source_event_id == event_id else r.source_event_id
        other = await session.get(Event, other_id)
        if other:
            related_events.append({
                "event_id": other.id,
                "title": other.title,
                "relationship_type": r.relationship_type,
                "shared_entities": _parse_json(r.shared_entities),
                "weight": r.weight,
            })

    return templates.TemplateResponse(
        request,
        "event_detail.html",
        {
            "event": event,
            "articles": articles,
            "entities": entities,
            "key_facts": key_facts,
            "disagreements": disagreements,
            "timeline": timeline,
            "related_events": related_events,
            "active_tab": "explore",
        },
    )


# ──────────────────────────────────────────────
# /feed  — Raw article feed
# ──────────────────────────────────────────────
@router.get("/feed")
async def feed(
    request: Request,
    source: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
):
    stmt = (
        select(Article)
        .options(joinedload(Article.source))
        .order_by(Article.published_at.desc())
        .limit(100)
    )

    if source == "India":
        stmt = stmt.join(Source).where(Source.name.in_(INDIA_SOURCES))
    elif source:
        stmt = stmt.join(Source).where(Source.name == source)

    result = await session.execute(stmt)
    articles = result.scalars().unique().all()

    src_result = await session.execute(select(Source).where(Source.is_active == True).order_by(Source.name))
    all_sources = src_result.scalars().all()

    global_sources = [s for s in all_sources if s.name not in INDIA_SOURCES]
    india_sources = [s for s in all_sources if s.name in INDIA_SOURCES]

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "articles": articles,
            "global_sources": global_sources,
            "india_sources": india_sources,
            "selected_source": source,
            "active_tab": "feed",
        },
    )


# ──────────────────────────────────────────────
# /graph  — Graph visualization (hidden from nav)
# ──────────────────────────────────────────────
@router.get("/graph")
async def graph_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    return templates.TemplateResponse(request, "graph.html", {"active_tab": "graph"})


# ──────────────────────────────────────────────
# /admin/status  — Pipeline status (admin)
# ──────────────────────────────────────────────
@router.get("/admin/status")
async def status_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    total_articles = (await session.execute(select(func.count()).select_from(Article))).scalar() or 0
    total_events = (await session.execute(select(func.count()).select_from(Event).where(Event.is_active == True))).scalar() or 0
    clustered_articles = (await session.execute(select(func.count()).select_from(EventArticle))).scalar() or 0
    unclustered = total_articles - clustered_articles

    events_summarized = (await session.execute(
        select(func.count()).select_from(Event).where(Event.is_active == True).where(Event.summary.isnot(None))
    )).scalar() or 0
    events_pending_summary = (await session.execute(
        select(func.count()).select_from(Event)
        .where(Event.is_active == True)
        .where(Event.summary.is_(None))
        .where(Event.article_count >= 3)
        .where(Event.source_count >= 2)
    )).scalar() or 0
    events_with_entities = (await session.execute(
        select(func.count()).select_from(Event).where(Event.is_active == True).where(Event.entities_json.isnot(None))
    )).scalar() or 0
    total_edges = (await session.execute(select(func.count()).select_from(EventRelationship))).scalar() or 0

    # Current briefing
    briefing_stmt = select(Briefing).where(Briefing.is_current == True).limit(1)
    briefing_result = await session.execute(briefing_stmt)
    current_briefing = briefing_result.scalar_one_or_none()

    from narad.config import settings
    gemini_configured = bool(settings.gemini_api_key)

    # Sources
    src_result = await session.execute(select(Source).where(Source.is_active == True).order_by(Source.name))
    sources = src_result.scalars().all()
    source_stats = []
    for s in sources:
        log_stmt = select(FetchLog).where(FetchLog.source_id == s.id).order_by(FetchLog.fetched_at.desc()).limit(1)
        log_result = await session.execute(log_stmt)
        last_log = log_result.scalar_one_or_none()
        count_result = await session.execute(select(func.count()).select_from(Article).where(Article.source_id == s.id))
        source_stats.append({
            "name": s.name,
            "source_type": s.source_type,
            "article_count": count_result.scalar() or 0,
            "last_fetched": s.last_fetched_at,
            "last_status": last_log.status if last_log else "never",
            "last_found": last_log.articles_found if last_log else 0,
            "last_new": last_log.articles_new if last_log else 0,
            "last_error": last_log.error_msg if last_log and last_log.status == "error" else None,
        })

    # Recent logs
    recent_result = await session.execute(select(FetchLog).order_by(FetchLog.fetched_at.desc()).limit(20))
    recent_logs = []
    for log in recent_result.scalars().all():
        src = await session.get(Source, log.source_id)
        recent_logs.append({
            "source_name": src.name if src else "?",
            "fetched_at": log.fetched_at,
            "articles_found": log.articles_found,
            "articles_new": log.articles_new,
            "status": log.status,
            "error_msg": log.error_msg,
        })

    # Recently summarized
    recent_summ_result = await session.execute(
        select(Event).where(Event.summarized_at.isnot(None)).order_by(Event.summarized_at.desc()).limit(5)
    )
    recently_summarized = recent_summ_result.scalars().all()

    return templates.TemplateResponse(
        request,
        "status.html",
        {
            "active_tab": "status",
            "source_stats": source_stats,
            "total_articles": total_articles,
            "total_events": total_events,
            "clustered_articles": clustered_articles,
            "unclustered": unclustered,
            "events_summarized": events_summarized,
            "events_pending_summary": events_pending_summary,
            "events_with_entities": events_with_entities,
            "total_edges": total_edges,
            "gemini_configured": gemini_configured,
            "recently_summarized": recently_summarized,
            "recent_logs": recent_logs,
            "current_briefing": current_briefing,
        },
    )
