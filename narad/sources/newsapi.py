import logging
from datetime import datetime, timezone

import httpx
from dateutil.parser import parse as parse_date

from narad.config import settings
from narad.sources.base import RawArticle, SourceAdapter

logger = logging.getLogger(__name__)

NEWSAPI_URL = "https://newsapi.org/v2/top-headlines"


class NewsAPIAdapter(SourceAdapter):
    def __init__(self, source_name: str = "NewsAPI"):
        self.source_name = source_name

    async def fetch(self) -> list[RawArticle]:
        if not settings.newsapi_key:
            logger.debug("NewsAPI key not configured, skipping")
            return []

        params = {
            "category": "general",
            "language": "en",
            "pageSize": "50",
            "apiKey": settings.newsapi_key,
        }

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(NEWSAPI_URL, params=params)
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            logger.error(f"NewsAPI fetch failed: {e}")
            return []

        if data.get("status") != "ok":
            logger.error(f"NewsAPI error: {data.get('message', 'unknown')}")
            return []

        articles = []
        for item in data.get("articles", []):
            title = (item.get("title") or "").strip()
            url = (item.get("url") or "").strip()
            if not title or not url or title == "[Removed]":
                continue

            published = None
            pub_str = item.get("publishedAt")
            if pub_str:
                try:
                    published = parse_date(pub_str)
                    if published.tzinfo is None:
                        published = published.replace(tzinfo=timezone.utc)
                except (ValueError, TypeError):
                    pass
            if published is None:
                published = datetime.now(timezone.utc)

            source_name = item.get("source", {}).get("name", self.source_name)

            articles.append(
                RawArticle(
                    title=title,
                    url=url,
                    summary=item.get("description"),
                    published_at=published,
                    image_url=item.get("urlToImage"),
                    source_name=source_name,
                )
            )

        logger.info(f"Fetched {len(articles)} articles from NewsAPI")
        return articles
