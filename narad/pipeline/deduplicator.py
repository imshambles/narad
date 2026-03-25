import logging

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from narad.models import Article

logger = logging.getLogger(__name__)

FUZZY_THRESHOLD = 85


async def is_duplicate(
    session: AsyncSession,
    fingerprint: str,
    title: str,
) -> bool:
    """Check if an article already exists by fingerprint or fuzzy title match."""
    # Exact fingerprint match
    result = await session.execute(
        select(Article.id).where(Article.fingerprint == fingerprint).limit(1)
    )
    if result.scalar_one_or_none() is not None:
        return True

    # Fuzzy title match against recent articles (last 500)
    result = await session.execute(
        select(Article.title)
        .order_by(Article.published_at.desc())
        .limit(500)
    )
    existing_titles = [row[0] for row in result.all()]

    for existing_title in existing_titles:
        if fuzz.token_sort_ratio(title.lower(), existing_title.lower()) >= FUZZY_THRESHOLD:
            return True

    return False
