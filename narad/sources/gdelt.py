import logging
from datetime import datetime, timezone

import httpx
from dateutil.parser import parse as parse_date

from narad.sources.base import RawArticle, SourceAdapter

logger = logging.getLogger(__name__)

GDELT_API = "https://api.gdeltproject.org/api/v2/doc/doc"


class GDELTAdapter(SourceAdapter):
    def __init__(self, source_name: str = "GDELT"):
        self.source_name = source_name

    async def fetch(self) -> list[RawArticle]:
        params = {
            "query": 'sourcelang:eng (India OR "New Delhi" OR Modi OR Jaishankar) (geopolitics OR diplomacy OR defence OR military OR sanctions OR trade OR bilateral)',
            "mode": "artlist",
            "maxrecords": "75",
            "format": "json",
            "sort": "datedesc",
            "timespan": "60min",
        }

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(GDELT_API, params=params)
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            logger.error(f"GDELT fetch failed: {e}")
            return []

        articles_data = data.get("articles", [])
        articles = []
        for item in articles_data:
            title = (item.get("title") or "").strip()
            url = (item.get("url") or "").strip()
            if not title or not url:
                continue

            published = None
            seen = item.get("seendate")
            if seen:
                try:
                    published = parse_date(seen)
                    if published.tzinfo is None:
                        published = published.replace(tzinfo=timezone.utc)
                except (ValueError, TypeError):
                    pass
            if published is None:
                published = datetime.now(timezone.utc)

            image_url = item.get("socialimage") or None

            articles.append(
                RawArticle(
                    title=title,
                    url=url,
                    summary=None,  # GDELT artlist doesn't provide summaries
                    published_at=published,
                    image_url=image_url,
                    source_name=item.get("domain", self.source_name),
                )
            )

        logger.info(f"Fetched {len(articles)} articles from GDELT")
        return articles
