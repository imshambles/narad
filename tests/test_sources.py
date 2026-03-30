"""
Tests for data source adapters:
- OSINT Twitter (RSSHub + Nitter fallback)
- GDELT (exponential backoff)
- RSS adapter (feed parsing)
- Source base classes
"""
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from narad.sources.base import RawArticle, SourceAdapter
from narad.sources.osint_twitter import (
    OSINTTwitterAdapter, TRUSTED_ACCOUNTS,
    RSSHUB_INSTANCES, NITTER_INSTANCES,
)
from narad.sources.gdelt import GDELTAdapter


# ═══════════════════════════════════════════
# Base classes
# ═══════════════════════════════════════════

class TestBase:
    def test_raw_article_dataclass(self):
        art = RawArticle(
            title="Test", url="https://x.com",
            summary="Summary", published_at=datetime.now(timezone.utc),
            image_url=None, source_name="Test",
        )
        assert art.title == "Test"
        assert art.source_name == "Test"

    def test_source_adapter_is_abstract(self):
        with pytest.raises(TypeError):
            SourceAdapter()


# ═══════════════════════════════════════════
# OSINT Twitter Adapter
# ═══════════════════════════════════════════

class TestOSINTTwitter:
    def test_trusted_accounts_configured(self):
        assert len(TRUSTED_ACCOUNTS) >= 10
        for name, handle in TRUSTED_ACCOUNTS:
            assert len(handle) > 0
            assert len(name) > 0

    def test_rsshub_instances_configured(self):
        assert len(RSSHUB_INSTANCES) >= 3
        for url in RSSHUB_INSTANCES:
            assert url.startswith("https://")

    def test_nitter_instances_configured(self):
        assert len(NITTER_INSTANCES) >= 3
        for url in NITTER_INSTANCES:
            assert url.startswith("https://")

    @pytest.mark.asyncio
    async def test_parse_feed_returns_list(self):
        adapter = OSINTTwitterAdapter()
        # With a fake URL, should return empty list (not crash)
        result = await adapter._parse_feed("https://nonexistent.invalid/feed", "testhandle")
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_fetch_account_tries_rsshub_then_nitter(self):
        adapter = OSINTTwitterAdapter()
        call_log = []

        original_parse = adapter._parse_feed

        async def mock_parse(url, handle):
            call_log.append(url)
            return []

        adapter._parse_feed = mock_parse

        await adapter._fetch_account("Test", "testhandle")

        # Should try RSSHub instances first, then Nitter
        rsshub_calls = [u for u in call_log if any(inst in u for inst in RSSHUB_INSTANCES)]
        nitter_calls = [u for u in call_log if any(inst in u for inst in NITTER_INSTANCES)]
        assert len(rsshub_calls) == len(RSSHUB_INSTANCES)
        assert len(nitter_calls) == len(NITTER_INSTANCES)

    @pytest.mark.asyncio
    async def test_fetch_account_stops_on_success(self):
        adapter = OSINTTwitterAdapter()

        async def mock_parse(url, handle):
            if "rsshub.app" in url:
                return [RawArticle(
                    title="[@test] Breaking news from OSINT",
                    url="https://x.com/test/123",
                    summary="Big event happened",
                    published_at=datetime.now(timezone.utc),
                    image_url=None,
                    source_name="X/@test",
                )]
            return []

        adapter._parse_feed = mock_parse

        result = await adapter._fetch_account("Test", "test")
        assert len(result) == 1
        assert result[0].title.startswith("[@test]")

    @pytest.mark.asyncio
    async def test_fetch_returns_all_accounts(self):
        adapter = OSINTTwitterAdapter()
        call_count = 0

        async def mock_fetch_account(name, handle):
            nonlocal call_count
            call_count += 1
            return []

        adapter._fetch_account = mock_fetch_account
        await adapter.fetch()
        assert call_count == len(TRUSTED_ACCOUNTS)


# ═══════════════════════════════════════════
# GDELT Adapter — Backoff Logic
# ═══════════════════════════════════════════

class TestGDELT:
    def setup_method(self):
        """Reset backoff state before each test."""
        import narad.sources.gdelt as gdelt_mod
        gdelt_mod._gdelt_backoff_until = None
        gdelt_mod._gdelt_consecutive_failures = 0

    @pytest.mark.asyncio
    async def test_backoff_on_429(self):
        import narad.sources.gdelt as gdelt_mod

        adapter = GDELTAdapter()

        mock_resp = MagicMock()
        mock_resp.status_code = 429

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            mock_client_cls.return_value = mock_client

            result = await adapter.fetch()
            assert result == []
            assert gdelt_mod._gdelt_consecutive_failures == 1
            assert gdelt_mod._gdelt_backoff_until is not None

    @pytest.mark.asyncio
    async def test_backoff_skips_during_cooldown(self):
        import narad.sources.gdelt as gdelt_mod

        # Set backoff to 10 minutes from now
        gdelt_mod._gdelt_backoff_until = datetime.now(timezone.utc) + __import__('datetime').timedelta(minutes=10)
        gdelt_mod._gdelt_consecutive_failures = 2

        adapter = GDELTAdapter()
        result = await adapter.fetch()
        assert result == []  # should skip without making any request

    @pytest.mark.asyncio
    async def test_success_resets_backoff(self):
        import narad.sources.gdelt as gdelt_mod
        gdelt_mod._gdelt_consecutive_failures = 3

        adapter = GDELTAdapter()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "articles": [
                {
                    "title": "India signs defense pact",
                    "url": "https://example.com/article",
                    "seendate": "20250101T120000Z",
                    "domain": "example.com",
                }
            ]
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            mock_client_cls.return_value = mock_client

            result = await adapter.fetch()
            assert len(result) == 1
            assert result[0].title == "India signs defense pact"
            assert gdelt_mod._gdelt_consecutive_failures == 0
            assert gdelt_mod._gdelt_backoff_until is None

    @pytest.mark.asyncio
    async def test_exponential_backoff_increases(self):
        import narad.sources.gdelt as gdelt_mod

        adapter = GDELTAdapter()
        mock_resp = MagicMock()
        mock_resp.status_code = 429

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            mock_client_cls.return_value = mock_client

            # First 429
            gdelt_mod._gdelt_backoff_until = None
            gdelt_mod._gdelt_consecutive_failures = 0
            await adapter.fetch()
            failures_1 = gdelt_mod._gdelt_consecutive_failures
            backoff_1 = gdelt_mod._gdelt_backoff_until

            # Second 429
            gdelt_mod._gdelt_backoff_until = None  # clear to allow retry
            await adapter.fetch()
            failures_2 = gdelt_mod._gdelt_consecutive_failures

            assert failures_2 > failures_1  # consecutive failures increase
