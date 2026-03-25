"""
Think Tank & Institutional Crawler

Fetches analysis from major geopolitical think tanks and research institutions.
These are the highest-quality sources — peer-reviewed analysis, not breaking news.
"""
import asyncio
import logging
import re
from datetime import datetime, timezone

import feedparser
from dateutil.parser import parse as parse_date

from narad.sources.base import RawArticle, SourceAdapter

logger = logging.getLogger(__name__)

# Curated think tank and institutional feeds
FEEDS = {
    # Indian think tanks
    "ORF India": "https://www.orfonline.org/feed",
    "IDSA India": "https://www.idsa.in/RSS/rss.html",
    "Carnegie India": "https://carnegieindia.org/rss/feeds",
    # Global think tanks
    "CSIS": "https://www.csis.org/analysis/feed",
    "Brookings": "https://www.brookings.edu/feed/",
    "Chatham House": "https://www.chathamhouse.org/rss/publications",
    "RAND": "https://www.rand.org/content/rand/blog.xml",
    "CFR": "https://www.cfr.org/rss/feeds",
    # Government/Institutional
    "EU External Action": "https://www.eeas.europa.eu/eeas/press-material_en?page=0&_format=rss",
    "NATO": "https://www.nato.int/cps/en/natolive/news.xml",
    # OSINT
    "Bellingcat": "https://www.bellingcat.com/feed/",
    "War on the Rocks": "https://warontherocks.com/feed/",
    "The Diplomat": "https://thediplomat.com/feed/",
    "Defense One": "https://www.defenseone.com/rss/",
}


class ThinkTankAdapter(SourceAdapter):
    def __init__(self, source_name: str, feed_url: str):
        self.source_name = source_name
        self.feed_url = feed_url

    async def fetch(self) -> list[RawArticle]:
        try:
            feed = await asyncio.to_thread(feedparser.parse, self.feed_url)
        except Exception as e:
            logger.error(f"Think tank {self.source_name} fetch failed: {e}")
            return []

        articles = []
        for entry in feed.entries[:15]:  # Cap at 15 per source
            title = (entry.get("title") or "").strip()
            link = entry.get("link", "").strip()
            if not title or not link:
                continue

            published = None
            for df in ("published", "updated", "created"):
                raw = entry.get(df)
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

            summary = entry.get("summary", "") or entry.get("description", "")
            summary = re.sub(r"<[^>]+>", "", summary).strip() if summary else None
            if summary and len(summary) > 500:
                summary = summary[:500] + "..."

            articles.append(
                RawArticle(
                    title=title,
                    url=link,
                    summary=summary,
                    published_at=published,
                    image_url=None,
                    source_name=self.source_name,
                )
            )

        logger.info(f"Think tank {self.source_name}: fetched {len(articles)} articles")
        return articles


class MultiThinkTankAdapter(SourceAdapter):
    """Fetches from all think tank feeds in one go."""
    def __init__(self, source_name: str = "Think Tanks"):
        self.source_name = source_name

    async def fetch(self) -> list[RawArticle]:
        all_articles = []
        tasks = []

        for name, url in FEEDS.items():
            adapter = ThinkTankAdapter(source_name=name, feed_url=url)
            tasks.append(adapter.fetch())

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, list):
                all_articles.extend(result)
            elif isinstance(result, Exception):
                logger.error(f"Think tank fetch error: {result}")

        logger.info(f"Think tanks: {len(all_articles)} total articles from {len(FEEDS)} sources")
        return all_articles
