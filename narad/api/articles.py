from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from narad.database import get_session
from narad.models import Article, Source
from narad.schemas import ArticleOut

router = APIRouter(tags=["articles"])


@router.get("/articles", response_model=list[ArticleOut])
async def list_articles(
    source: str | None = Query(None, description="Filter by source name"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    stmt = (
        select(Article)
        .options(joinedload(Article.source))
        .order_by(Article.published_at.desc())
        .limit(limit)
        .offset(offset)
    )

    if source:
        stmt = stmt.join(Source).where(Source.name == source)

    result = await session.execute(stmt)
    articles = result.scalars().unique().all()

    return [
        ArticleOut(
            id=a.id,
            title=a.title,
            summary=a.summary,
            external_url=a.external_url,
            published_at=a.published_at,
            source_name=a.source.name,
            image_url=a.image_url,
        )
        for a in articles
    ]


@router.get("/sources")
async def list_sources(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(Source).order_by(Source.name))
    sources = result.scalars().all()
    return [
        {
            "id": s.id,
            "name": s.name,
            "source_type": s.source_type,
            "is_active": s.is_active,
            "last_fetched_at": s.last_fetched_at,
        }
        for s in sources
    ]
