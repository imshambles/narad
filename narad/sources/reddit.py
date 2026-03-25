"""
Reddit OSINT Crawler

Fetches top posts from geopolitics-related subreddits via RSS.
Reddit exposes RSS feeds at reddit.com/r/{subreddit}/.rss — no API key needed.
Only picks up posts with significant engagement (upvotes as trust signal).
"""
import asyncio
import logging
import re
from datetime import datetime, timezone

import feedparser
from dateutil.parser import parse as parse_date

from narad.sources.base import RawArticle, SourceAdapter

logger = logging.getLogger(__name__)

# Curated subreddits — these are moderated communities with quality discussion
SUBREDDITS = [
    "geopolitics",          # Academic-grade geopolitical analysis
    "IndianDefence",        # Indian defense and security
    "worldnews",            # Global news (high traffic, use top only)
    "IndiaSpeaks",          # Indian current affairs
    "anime_titties",        # Despite the name, this is a serious world news sub
    "CredibleDefense",      # Military/defense analysis
    "ForeignPolicy",        # Foreign policy discussion
]


class RedditAdapter(SourceAdapter):
    def __init__(self, source_name: str = "Reddit OSINT"):
        self.source_name = source_name

    async def fetch(self) -> list[RawArticle]:
        all_articles = []

        for sub in SUBREDDITS:
            url = f"https://www.reddit.com/r/{sub}/top/.rss?t=day&limit=10"
            try:
                feed = await asyncio.to_thread(feedparser.parse, url)
            except Exception as e:
                logger.error(f"Reddit r/{sub} fetch failed: {e}")
                continue

            for entry in feed.entries:
                title = (entry.get("title") or "").strip()
                link = entry.get("link", "").strip()
                if not title or not link:
                    continue

                # Skip low-quality posts (very short titles, memes, etc.)
                if len(title) < 20:
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

                # Extract text content
                content = entry.get("summary", "") or ""
                content = re.sub(r"<[^>]+>", "", content).strip()
                if len(content) > 500:
                    content = content[:500] + "..."

                all_articles.append(
                    RawArticle(
                        title=f"[r/{sub}] {title}",
                        url=link,
                        summary=content if content else None,
                        published_at=published,
                        image_url=None,
                        source_name=f"Reddit r/{sub}",
                    )
                )

        logger.info(f"Reddit: fetched {len(all_articles)} posts from {len(SUBREDDITS)} subreddits")
        return all_articles
