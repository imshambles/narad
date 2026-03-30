"""
OSINT Twitter/X Account Crawler

Follows trusted OSINT analysts and geopolitical accounts via RSS proxies.
Uses RSSHub or Nitter instances to avoid the paid Twitter API.
Falls back gracefully if proxy instances are down.
"""
import asyncio
import logging
import re
from datetime import datetime, timezone

import feedparser
from dateutil.parser import parse as parse_date

from narad.sources.base import RawArticle, SourceAdapter

logger = logging.getLogger(__name__)

# Trusted OSINT and geopolitical accounts
# Format: (display_name, twitter_handle)
TRUSTED_ACCOUNTS = [
    ("Intel Crab", "IntelCrab"),
    ("OSINTdefender", "sentdefender"),
    ("Aurora Intel", "AuroraIntel"),
    ("Faytuks News", "Faytuks"),
    ("Conflict News", "Aborzhemaa"),
    ("Janes", "JasGroup"),
    ("War Monitor", "WarMonitors"),
    ("Indian Military", "ReviewVayu"),
    ("OSINT Aggregator", "OSABORZ"),
    ("LiveuaMap", "Liveuamap"),
]

# RSSHub instances (try multiple, use first that works)
RSSHUB_INSTANCES = [
    "https://rsshub.app",
    "https://rsshub.rssforever.com",
    "https://rsshub-instance.zeabur.app",
]

# Nitter instances (fallback when RSSHub is down)
NITTER_INSTANCES = [
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
    "https://nitter.woodland.cafe",
    "https://nitter.1d4.us",
]


class OSINTTwitterAdapter(SourceAdapter):
    def __init__(self, source_name: str = "OSINT Twitter"):
        self.source_name = source_name

    async def fetch(self) -> list[RawArticle]:
        all_articles = []

        for display_name, handle in TRUSTED_ACCOUNTS:
            articles = await self._fetch_account(display_name, handle)
            all_articles.extend(articles)

        logger.info(f"OSINT Twitter: fetched {len(all_articles)} posts from {len(TRUSTED_ACCOUNTS)} accounts")
        return all_articles

    async def _fetch_account(self, display_name: str, handle: str) -> list[RawArticle]:
        """Try to fetch an account's feed via RSSHub, then Nitter as fallback."""
        # Try RSSHub first
        for instance in RSSHUB_INSTANCES:
            url = f"{instance}/twitter/user/{handle}"
            articles = await self._parse_feed(url, handle)
            if articles:
                return articles

        # Fallback to Nitter RSS
        for instance in NITTER_INSTANCES:
            url = f"{instance}/{handle}/rss"
            articles = await self._parse_feed(url, handle)
            if articles:
                return articles

        return []

    async def _parse_feed(self, url: str, handle: str) -> list[RawArticle]:
        """Parse an RSS feed URL and return articles."""
        try:
            feed = await asyncio.to_thread(feedparser.parse, url)
            if not feed.entries:
                return []

            articles = []
            for entry in feed.entries[:5]:
                title = (entry.get("title") or "").strip()
                link = entry.get("link", "").strip()
                if not title or not link:
                    continue

                if len(title) < 30:
                    continue

                published = None
                for df in ("published", "updated"):
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

                content = entry.get("summary", "") or ""
                content = re.sub(r"<[^>]+>", "", content).strip()

                articles.append(
                    RawArticle(
                        title=f"[@{handle}] {title[:200]}",
                        url=link,
                        summary=content[:400] if content else None,
                        published_at=published,
                        image_url=None,
                        source_name=f"X/@{handle}",
                    )
                )

            return articles

        except Exception:
            return []
