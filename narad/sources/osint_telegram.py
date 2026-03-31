"""
OSINT Telegram Channel Monitor

Monitors public Telegram channels used by OSINT analysts, conflict monitors,
and geopolitical intelligence accounts. These channels typically break news
15-45 minutes before wire services.

Uses two methods for reliability:
1. Direct scraping of t.me/s/<channel> (public web preview, no auth needed)
2. RSSHub fallback (converts Telegram channels to RSS)

No additional API keys or dependencies required.
"""
import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from html import unescape

import httpx
from dateutil.parser import parse as parse_date

from narad.sources.base import RawArticle, SourceAdapter

logger = logging.getLogger(__name__)

# OSINT and geopolitical Telegram channels
# Format: (display_name, channel_username, category)
# Categories help with signal routing: conflict, india, commodity, geoint, military
# Only channels with public web previews (t.me/s/) — verified working
OSINT_CHANNELS = [
    # Breaking news — fastest alert channels
    ("BNO News", "BNONews", "conflict"),
    ("Breaking Alerts", "breakingalerts", "conflict"),
    ("NewsBrk", "NewsBrk", "conflict"),
    ("Insider Paper", "insiderpaper", "conflict"),

    # Conflict monitoring
    ("War Monitors", "warmonitors", "conflict"),
    ("Liveuamap", "liveuamap", "conflict"),
    ("Spectator Index", "spectatorindex", "conflict"),
    ("Flash News", "Flash_news_ua", "conflict"),

    # India / South Asia / Indo-Pacific
    ("DD Geopolitics", "DDGeopolitics", "india"),
    ("South China Sea News", "SouthChinaSeaNews", "india"),

    # Middle East / energy (Hormuz, Gulf, oil)
    ("Iran International", "iranintl_en", "commodity"),

    # Defence / military
    ("Defence Alerts", "defencealerts", "military"),
    ("Naval News", "NavalNews", "military"),
]

# RSSHub instances for Telegram channel RSS feeds
RSSHUB_INSTANCES = [
    "https://rsshub.app",
    "https://rsshub.rssforever.com",
    "https://rsshub-instance.zeabur.app",
]

# Keywords that indicate geopolitically relevant content (for filtering noise)
RELEVANCE_KEYWORDS = [
    "india", "pakistan", "china", "modi", "military", "border", "lac", "loc",
    "missile", "nuclear", "defense", "defence", "navy", "air force", "army",
    "sanctions", "oil", "crude", "hormuz", "strait", "shipping", "vessel",
    "drone", "strike", "attack", "conflict", "war", "ceasefire", "tension",
    "diplomatic", "ambassador", "foreign minister", "summit", "bilateral",
    "nato", "quad", "brics", "aukus", "taiwan", "south china sea",
    "kashmir", "ladakh", "arunachal", "aksai chin", "galwan",
    "houthi", "iran", "israel", "gaza", "hezbollah", "syria",
    "russia", "ukraine", "crimea", "wagner", "mobilization",
    "gold", "wheat", "rupee", "nifty", "sensex", "forex", "inflation",
    "satellite", "intelligence", "osint", "breaking", "urgent", "alert",
    "aircraft", "fighter", "submarine", "warship", "destroyer", "carrier",
    "explosion", "fire", "blast", "radar", "airspace", "intercept",
]


class OSINTTelegramAdapter(SourceAdapter):
    def __init__(self, source_name: str = "OSINT Telegram"):
        self.source_name = source_name

    async def fetch(self) -> list[RawArticle]:
        all_articles = []
        tasks = [
            self._fetch_channel(name, username, category)
            for name, username, category in OSINT_CHANNELS
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, list):
                all_articles.extend(result)
            elif isinstance(result, Exception):
                logger.debug(f"Telegram channel fetch error: {result}")

        logger.info(f"OSINT Telegram: fetched {len(all_articles)} posts from {len(OSINT_CHANNELS)} channels")
        return all_articles

    async def _fetch_channel(self, display_name: str, username: str, category: str) -> list[RawArticle]:
        """Fetch posts from a channel. Try direct scraping first, then RSSHub."""
        articles = await self._fetch_via_web(username, category)
        if articles:
            return articles

        # Fallback to RSSHub
        articles = await self._fetch_via_rsshub(username, category)
        if articles:
            return articles

        return []

    async def _fetch_via_web(self, username: str, category: str) -> list[RawArticle]:
        """Scrape the public web preview of a Telegram channel."""
        url = f"https://t.me/s/{username}"
        try:
            async with httpx.AsyncClient(
                timeout=15,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                    "Accept-Language": "en-US,en;q=0.9",
                },
                follow_redirects=True,
            ) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    return []
                return self._parse_web_preview(resp.text, username, category)
        except Exception as e:
            logger.debug(f"Telegram web fetch failed for {username}: {e}")
            return []

    def _parse_web_preview(self, html: str, username: str, category: str) -> list[RawArticle]:
        """Parse the t.me/s/<channel> HTML page to extract messages."""
        articles = []

        # Extract message blocks
        # Each message is in a div with class "tgme_widget_message_wrap"
        # The text is in "tgme_widget_message_text"
        # The time is in a "time" tag with datetime attribute
        message_pattern = re.compile(
            r'<div class="tgme_widget_message_wrap[^"]*"[^>]*>'
            r'(.*?)'
            r'(?=<div class="tgme_widget_message_wrap|$)',
            re.DOTALL,
        )

        # More targeted: extract individual message components
        text_pattern = re.compile(
            r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
            re.DOTALL,
        )
        time_pattern = re.compile(
            r'<time[^>]*datetime="([^"]+)"',
        )
        link_pattern = re.compile(
            r'data-post="([^"]+)"',
        )
        image_pattern = re.compile(
            r"background-image:url\('([^']+)'\)",
        )

        messages = message_pattern.findall(html)
        if not messages:
            # Try a simpler split approach
            parts = html.split('tgme_widget_message_wrap')
            messages = parts[1:] if len(parts) > 1 else []

        for msg_html in messages[-10:]:  # last 10 messages
            # Extract text
            text_match = text_pattern.search(msg_html)
            if not text_match:
                continue
            raw_text = text_match.group(1)

            # Strip HTML tags, decode entities
            text = re.sub(r"<br\s*/?>", "\n", raw_text)
            text = re.sub(r"<[^>]+>", "", text)
            text = unescape(text).strip()

            if not text or len(text) < 30:
                continue

            # Check relevance
            if not self._is_relevant(text, category):
                continue

            # Extract timestamp
            time_match = time_pattern.search(msg_html)
            published = None
            if time_match:
                try:
                    published = parse_date(time_match.group(1))
                    if published.tzinfo is None:
                        published = published.replace(tzinfo=timezone.utc)
                except (ValueError, TypeError):
                    published = datetime.now(timezone.utc)
            else:
                published = datetime.now(timezone.utc)

            # Extract post link
            link_match = link_pattern.search(msg_html)
            post_url = f"https://t.me/{username}"
            if link_match:
                post_id = link_match.group(1)
                post_url = f"https://t.me/{post_id}"

            # Extract image
            image_match = image_pattern.search(msg_html)
            image_url = image_match.group(1) if image_match else None

            # First line or first 200 chars as title
            lines = text.split("\n")
            title = lines[0][:200].strip()
            summary = text[:500] if len(text) > len(title) else None

            articles.append(RawArticle(
                title=f"[TG @{username}] {title}",
                url=post_url,
                summary=summary,
                published_at=published,
                image_url=image_url,
                source_name=f"Telegram/@{username}",
            ))

        return articles

    async def _fetch_via_rsshub(self, username: str, category: str) -> list[RawArticle]:
        """Fallback: fetch channel via RSSHub Telegram RSS feeds."""
        import feedparser

        for instance in RSSHUB_INSTANCES:
            url = f"{instance}/telegram/channel/{username}"
            try:
                feed = await asyncio.to_thread(feedparser.parse, url)
                if not feed.entries:
                    continue

                articles = []
                for entry in feed.entries[:10]:
                    title = (entry.get("title") or "").strip()
                    link = entry.get("link", "").strip()
                    if not title or not link:
                        continue
                    if len(title) < 30:
                        continue

                    # Check relevance
                    full_text = f"{title} {entry.get('summary', '')}".lower()
                    if not self._is_relevant(full_text, category):
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

                    articles.append(RawArticle(
                        title=f"[TG @{username}] {title[:200]}",
                        url=link,
                        summary=content[:500] if content else None,
                        published_at=published,
                        image_url=None,
                        source_name=f"Telegram/@{username}",
                    ))

                if articles:
                    return articles

            except Exception:
                continue

        return []

    def _is_relevant(self, text: str, category: str) -> bool:
        """Filter messages for geopolitical relevance.

        India-focused channels pass everything through.
        Other channels need at least one relevance keyword.
        """
        if category == "india":
            return True

        text_lower = text.lower()
        return any(kw in text_lower for kw in RELEVANCE_KEYWORDS)
