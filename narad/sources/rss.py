import asyncio
import logging
from datetime import datetime, timezone

import feedparser
from dateutil.parser import parse as parse_date

from narad.sources.base import RawArticle, SourceAdapter

logger = logging.getLogger(__name__)


class RSSAdapter(SourceAdapter):
    def __init__(self, source_name: str, feed_url: str):
        self.source_name = source_name
        self.feed_url = feed_url

    async def fetch(self) -> list[RawArticle]:
        try:
            feed = await asyncio.to_thread(feedparser.parse, self.feed_url)
        except Exception as e:
            logger.error(f"Failed to fetch RSS feed {self.feed_url}: {e}")
            return []

        articles = []
        for entry in feed.entries:
            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()
            if not title or not link:
                continue

            # Parse published date
            published = None
            for date_field in ("published", "updated", "created"):
                raw = entry.get(date_field)
                if raw:
                    try:
                        published = parse_date(raw)
                        if published.tzinfo is None:
                            published = published.replace(tzinfo=timezone.utc)
                        break
                    except (ValueError, TypeError):
                        continue

            if published is None:
                published = datetime.now(timezone.utc)

            # Summary
            summary = entry.get("summary", "") or entry.get("description", "")
            # Strip HTML tags simply
            import re
            summary = re.sub(r"<[^>]+>", "", summary).strip() if summary else None

            # Image
            image_url = None
            media = entry.get("media_content") or entry.get("media_thumbnail")
            if media and isinstance(media, list) and len(media) > 0:
                image_url = media[0].get("url")
            if not image_url:
                # Try enclosures
                enclosures = entry.get("enclosures", [])
                for enc in enclosures:
                    if enc.get("type", "").startswith("image/"):
                        image_url = enc.get("href")
                        break

            articles.append(
                RawArticle(
                    title=title,
                    url=link,
                    summary=summary if summary else None,
                    published_at=published,
                    image_url=image_url,
                    source_name=self.source_name,
                )
            )

        logger.info(f"Fetched {len(articles)} articles from {self.source_name}")
        return articles
