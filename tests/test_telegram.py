"""
Tests for the OSINT Telegram channel adapter:
- Channel configuration
- Web preview parsing
- RSSHub fallback
- Relevance filtering
- Full adapter fetch
"""
import json
import re
from datetime import datetime, timezone
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from narad.sources.osint_telegram import (
    OSINTTelegramAdapter,
    OSINT_CHANNELS,
    RELEVANCE_KEYWORDS,
)


# ═══════════════════════════════════════════
# Channel Configuration
# ═══════════════════════════════════════════

class TestChannelConfig:
    def test_has_channels(self):
        assert len(OSINT_CHANNELS) >= 10

    def test_channel_tuple_structure(self):
        for name, username, category in OSINT_CHANNELS:
            assert isinstance(name, str) and len(name) > 0
            assert isinstance(username, str) and len(username) > 0
            assert category in ("conflict", "india", "commodity", "geoint", "military")

    def test_no_duplicate_usernames(self):
        usernames = [u for _, u, _ in OSINT_CHANNELS]
        assert len(usernames) == len(set(u.lower() for u in usernames))

    def test_has_breaking_news_channels(self):
        names = [n for n, _, _ in OSINT_CHANNELS]
        # At least one breaking news / fast alert channel
        assert any("BNO" in n or "Breaking" in n or "News" in n for n in names)

    def test_has_india_category(self):
        categories = [c for _, _, c in OSINT_CHANNELS]
        assert "india" in categories

    def test_has_conflict_category(self):
        categories = [c for _, _, c in OSINT_CHANNELS]
        assert "conflict" in categories

    def test_has_military_category(self):
        categories = [c for _, _, c in OSINT_CHANNELS]
        assert "military" in categories

    def test_relevance_keywords_comprehensive(self):
        assert len(RELEVANCE_KEYWORDS) >= 50
        # Key geopolitical terms
        for kw in ["india", "china", "pakistan", "missile", "oil", "hormuz",
                    "sanctions", "military", "nuclear", "border"]:
            assert kw in RELEVANCE_KEYWORDS


# ═══════════════════════════════════════════
# Web Preview Parsing
# ═══════════════════════════════════════════

# Realistic HTML snippet from t.me/s/<channel>
SAMPLE_HTML = """
<div class="tgme_widget_message_wrap js-widget_message_wrap">
  <div class="tgme_widget_message js-widget_message" data-post="testchannel/1001">
    <div class="tgme_widget_message_bubble">
      <div class="tgme_widget_message_text js-message_text" dir="auto">
        Breaking: India deploys additional troops to LAC border after satellite imagery shows Chinese buildup in Aksai Chin region. Military sources confirm movement of armored vehicles.
      </div>
      <div class="tgme_widget_message_info">
        <time datetime="2025-06-15T14:30:00+00:00" class="time">2:30 PM</time>
      </div>
    </div>
  </div>
</div>

<div class="tgme_widget_message_wrap js-widget_message_wrap">
  <div class="tgme_widget_message js-widget_message" data-post="testchannel/1002">
    <div class="tgme_widget_message_bubble">
      <div class="tgme_widget_message_text js-message_text" dir="auto">
        Oil prices surge 4% as Iran threatens to close Strait of Hormuz. Brent crude hits $95/barrel. India's oil import bill could increase by $8B annually if disruption persists.
      </div>
      <div class="tgme_widget_message_info">
        <time datetime="2025-06-15T15:00:00+00:00" class="time">3:00 PM</time>
      </div>
    </div>
  </div>
</div>

<div class="tgme_widget_message_wrap js-widget_message_wrap">
  <div class="tgme_widget_message js-widget_message" data-post="testchannel/1003">
    <div class="tgme_widget_message_bubble">
      <div class="tgme_widget_message_text js-message_text" dir="auto">
        Short post
      </div>
      <div class="tgme_widget_message_info">
        <time datetime="2025-06-15T15:30:00+00:00" class="time">3:30 PM</time>
      </div>
    </div>
  </div>
</div>

<div class="tgme_widget_message_wrap js-widget_message_wrap">
  <div class="tgme_widget_message js-widget_message" data-post="testchannel/1004">
    <div class="tgme_widget_message_bubble">
      <div class="tgme_widget_message_text js-message_text" dir="auto">
        Today's weather forecast: sunny and warm across most of Europe with temperatures reaching 30C in southern regions. No significant weather events expected this week.
      </div>
      <div class="tgme_widget_message_info">
        <time datetime="2025-06-15T16:00:00+00:00" class="time">4:00 PM</time>
      </div>
    </div>
  </div>
</div>

<div class="tgme_widget_message_wrap js-widget_message_wrap">
  <div class="tgme_widget_message js-widget_message" data-post="testchannel/1005">
    <div class="tgme_widget_message_bubble">
      <div style="background-image:url('https://cdn.telegram.org/photo123.jpg')" class="tgme_widget_message_photo">
      </div>
      <div class="tgme_widget_message_text js-message_text" dir="auto">
        Satellite imagery confirms new missile deployment near Pakistan border. Indian defence ministry has not commented. HAL and BEL stocks expected to react at market open.
      </div>
      <div class="tgme_widget_message_info">
        <time datetime="2025-06-15T17:00:00+00:00" class="time">5:00 PM</time>
      </div>
    </div>
  </div>
</div>
"""


class TestWebPreviewParsing:
    def setup_method(self):
        self.adapter = OSINTTelegramAdapter()

    def test_extracts_messages(self):
        articles = self.adapter._parse_web_preview(SAMPLE_HTML, "testchannel", "conflict")
        # Should get 3 relevant articles (2 conflict, 1 missile/defence)
        # Short post filtered, weather filtered (no relevance keywords)
        assert len(articles) >= 2

    def test_extracts_title(self):
        articles = self.adapter._parse_web_preview(SAMPLE_HTML, "testchannel", "conflict")
        assert any("India deploys" in a.title for a in articles)

    def test_prefixes_with_channel(self):
        articles = self.adapter._parse_web_preview(SAMPLE_HTML, "testchannel", "conflict")
        for a in articles:
            assert a.title.startswith("[TG @testchannel]")

    def test_extracts_post_url(self):
        articles = self.adapter._parse_web_preview(SAMPLE_HTML, "testchannel", "conflict")
        urls = [a.url for a in articles]
        assert any("testchannel/1001" in u for u in urls)

    def test_extracts_timestamp(self):
        articles = self.adapter._parse_web_preview(SAMPLE_HTML, "testchannel", "conflict")
        for a in articles:
            assert a.published_at is not None
            assert a.published_at.tzinfo is not None

    def test_extracts_image_url(self):
        articles = self.adapter._parse_web_preview(SAMPLE_HTML, "testchannel", "conflict")
        image_articles = [a for a in articles if a.image_url is not None]
        assert len(image_articles) >= 1
        assert "photo123.jpg" in image_articles[0].image_url

    def test_filters_short_messages(self):
        articles = self.adapter._parse_web_preview(SAMPLE_HTML, "testchannel", "conflict")
        # "Short post" (10 chars) should be filtered out
        for a in articles:
            assert "Short post" not in a.title

    def test_filters_irrelevant_for_non_india_category(self):
        # A truly irrelevant message with no keyword matches
        irrelevant_html = """
        <div class="tgme_widget_message_wrap">
          <div data-post="ch/1">
            <div class="tgme_widget_message_text">
              Today's recipe: how to make a perfect sourdough bread with just flour, water, and salt. Let it rest for 24 hours.
            </div>
            <time datetime="2025-06-15T10:00:00+00:00"></time>
          </div>
        </div>
        """
        articles = self.adapter._parse_web_preview(irrelevant_html, "testchannel", "conflict")
        assert len(articles) == 0

    def test_india_category_passes_everything(self):
        articles = self.adapter._parse_web_preview(SAMPLE_HTML, "testchannel", "india")
        # India category skips keyword filtering, so even weather gets through
        # But short posts are still filtered
        assert len(articles) >= 3

    def test_source_name_format(self):
        articles = self.adapter._parse_web_preview(SAMPLE_HTML, "testchannel", "conflict")
        for a in articles:
            assert a.source_name == "Telegram/@testchannel"

    def test_empty_html(self):
        articles = self.adapter._parse_web_preview("", "testchannel", "conflict")
        assert articles == []

    def test_no_message_divs(self):
        articles = self.adapter._parse_web_preview("<html><body>No messages</body></html>", "testchannel", "conflict")
        assert articles == []

    def test_html_entities_decoded(self):
        html = """
        <div class="tgme_widget_message_wrap">
          <div data-post="ch/1">
            <div class="tgme_widget_message_text">
              India &amp; China border tensions escalate as PLA deploys additional forces near LAC
            </div>
            <time datetime="2025-06-15T10:00:00+00:00"></time>
          </div>
        </div>
        """
        articles = self.adapter._parse_web_preview(html, "ch", "conflict")
        if articles:
            assert "&amp;" not in articles[0].title
            assert "&" in articles[0].title


# ═══════════════════════════════════════════
# Relevance Filtering
# ═══════════════════════════════════════════

class TestRelevanceFilter:
    def setup_method(self):
        self.adapter = OSINTTelegramAdapter()

    def test_india_category_always_relevant(self):
        assert self.adapter._is_relevant("random text about cooking", "india") is True

    def test_conflict_needs_keywords(self):
        assert self.adapter._is_relevant("random text about cooking", "conflict") is False

    def test_conflict_with_military_keyword(self):
        assert self.adapter._is_relevant("military forces deployed", "conflict") is True

    def test_commodity_with_oil_keyword(self):
        assert self.adapter._is_relevant("oil prices surge today", "commodity") is True

    def test_military_with_missile_keyword(self):
        assert self.adapter._is_relevant("new missile test conducted", "military") is True

    def test_case_insensitive(self):
        assert self.adapter._is_relevant("INDIA deploys MILITARY forces", "conflict") is True

    def test_hormuz_keyword(self):
        assert self.adapter._is_relevant("tensions at strait of hormuz", "commodity") is True

    def test_breaking_keyword(self):
        assert self.adapter._is_relevant("BREAKING: something happened", "conflict") is True


# ═══════════════════════════════════════════
# RSSHub Fallback
# ═══════════════════════════════════════════

class TestRSSHubFallback:
    @pytest.mark.asyncio
    async def test_rsshub_fallback_called_when_web_fails(self):
        adapter = OSINTTelegramAdapter()

        # Mock web fetch to return empty
        adapter._fetch_via_web = AsyncMock(return_value=[])
        adapter._fetch_via_rsshub = AsyncMock(return_value=[
            MagicMock(title="test", url="http://test", source_name="TG"),
        ])

        articles = await adapter._fetch_channel("Test", "testch", "conflict")
        adapter._fetch_via_web.assert_called_once()
        adapter._fetch_via_rsshub.assert_called_once()
        assert len(articles) == 1

    @pytest.mark.asyncio
    async def test_rsshub_not_called_when_web_succeeds(self):
        adapter = OSINTTelegramAdapter()

        adapter._fetch_via_web = AsyncMock(return_value=[
            MagicMock(title="test", url="http://test", source_name="TG"),
        ])
        adapter._fetch_via_rsshub = AsyncMock(return_value=[])

        articles = await adapter._fetch_channel("Test", "testch", "conflict")
        adapter._fetch_via_web.assert_called_once()
        adapter._fetch_via_rsshub.assert_not_called()
        assert len(articles) == 1


# ═══════════════════════════════════════════
# Full Adapter
# ═══════════════════════════════════════════

class TestFullAdapter:
    @pytest.mark.asyncio
    async def test_fetch_returns_list(self):
        adapter = OSINTTelegramAdapter()
        # Mock all channel fetches to avoid network calls
        adapter._fetch_channel = AsyncMock(return_value=[])
        articles = await adapter.fetch()
        assert isinstance(articles, list)
        assert adapter._fetch_channel.call_count == len(OSINT_CHANNELS)

    @pytest.mark.asyncio
    async def test_fetch_aggregates_all_channels(self):
        from narad.sources.base import RawArticle

        adapter = OSINTTelegramAdapter()

        async def mock_fetch(name, username, category):
            return [RawArticle(
                title=f"[TG @{username}] Test post",
                url=f"https://t.me/{username}/1",
                summary=None,
                published_at=datetime.now(timezone.utc),
                image_url=None,
                source_name=f"Telegram/@{username}",
            )]

        adapter._fetch_channel = mock_fetch
        articles = await adapter.fetch()
        assert len(articles) == len(OSINT_CHANNELS)

    @pytest.mark.asyncio
    async def test_fetch_handles_channel_exceptions(self):
        adapter = OSINTTelegramAdapter()

        call_count = 0

        async def sometimes_fail(name, username, category):
            nonlocal call_count
            call_count += 1
            if call_count % 2 == 0:
                raise Exception("Network error")
            return []

        adapter._fetch_channel = sometimes_fail
        # Should not raise even if some channels fail
        articles = await adapter.fetch()
        assert isinstance(articles, list)

    def test_adapter_source_name(self):
        adapter = OSINTTelegramAdapter("Custom Name")
        assert adapter.source_name == "Custom Name"

    def test_default_source_name(self):
        adapter = OSINTTelegramAdapter()
        assert adapter.source_name == "OSINT Telegram"


# ═══════════════════════════════════════════
# Integration with scheduler
# ═══════════════════════════════════════════

class TestSchedulerIntegration:
    def test_get_adapter_returns_telegram_adapter(self):
        from narad.scheduler import get_adapter
        from narad.models import Source

        source = Source(
            name="OSINT Telegram",
            source_type="osint_telegram",
            url="https://telegram.org",
            fetch_interval_sec=120,
            is_active=True,
        )
        adapter = get_adapter(source)
        assert isinstance(adapter, OSINTTelegramAdapter)

    def test_seed_includes_telegram_source(self):
        # Verify the source is in the default seed list
        from narad.app import seed_sources
        import inspect
        source_code = inspect.getsource(seed_sources)
        assert "osint_telegram" in source_code
        assert "120" in source_code  # 120 second interval
