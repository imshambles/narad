"""
Tests for the Telegram alert system:
- send_telegram (HTTP calls, error handling)
- format_correlation_alert, format_commodity_alert, format_analyst_alert
- alert_on_signal (severity gating)
- send_alert_batch (batch dispatch)
"""
import json
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock, MagicMock

import httpx
import pytest

from narad.intel.alerts import (
    send_telegram,
    format_correlation_alert,
    format_commodity_alert,
    format_analyst_alert,
    alert_on_signal,
    send_alert_batch,
    SEVERITY_TAG,
)


def _make_signal_obj(signal_type="correlation", severity="high", title="Test", description="Desc", data=None):
    """Create a lightweight signal-like object for formatter tests."""
    return SimpleNamespace(
        signal_type=signal_type,
        severity=severity,
        title=title,
        description=description,
        data_json=json.dumps(data or {}),
    )


# ═══════════════════════════════════════════
# send_telegram
# ═══════════════════════════════════════════

class TestSendTelegram:
    @pytest.mark.asyncio
    async def test_success(self):
        mock_resp = MagicMock(status_code=200)
        with patch("narad.intel.alerts.settings") as mock_settings, \
             patch("narad.intel.alerts.httpx.AsyncClient") as MockClient:
            mock_settings.telegram_bot_token = "test_token"
            mock_settings.telegram_chat_id = "12345"
            instance = AsyncMock()
            instance.post.return_value = mock_resp
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = await send_telegram("test message")
            assert result is True
            instance.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_not_configured_no_token(self):
        with patch("narad.intel.alerts.settings") as mock_settings:
            mock_settings.telegram_bot_token = ""
            mock_settings.telegram_chat_id = "12345"
            result = await send_telegram("test")
            assert result is False

    @pytest.mark.asyncio
    async def test_not_configured_no_chat_id(self):
        with patch("narad.intel.alerts.settings") as mock_settings:
            mock_settings.telegram_bot_token = "test_token"
            mock_settings.telegram_chat_id = ""
            result = await send_telegram("test")
            assert result is False

    @pytest.mark.asyncio
    async def test_api_error_returns_false(self):
        mock_resp = MagicMock(status_code=429, text="rate limited")
        with patch("narad.intel.alerts.settings") as mock_settings, \
             patch("narad.intel.alerts.httpx.AsyncClient") as MockClient:
            mock_settings.telegram_bot_token = "test_token"
            mock_settings.telegram_chat_id = "12345"
            instance = AsyncMock()
            instance.post.return_value = mock_resp
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = await send_telegram("test")
            assert result is False

    @pytest.mark.asyncio
    async def test_network_failure_returns_false(self):
        with patch("narad.intel.alerts.settings") as mock_settings, \
             patch("narad.intel.alerts.httpx.AsyncClient") as MockClient:
            mock_settings.telegram_bot_token = "test_token"
            mock_settings.telegram_chat_id = "12345"
            instance = AsyncMock()
            instance.post.side_effect = httpx.ConnectError("connection refused")
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = await send_telegram("test")
            assert result is False

    @pytest.mark.asyncio
    async def test_timeout_returns_false(self):
        with patch("narad.intel.alerts.settings") as mock_settings, \
             patch("narad.intel.alerts.httpx.AsyncClient") as MockClient:
            mock_settings.telegram_bot_token = "test_token"
            mock_settings.telegram_chat_id = "12345"
            instance = AsyncMock()
            instance.post.side_effect = httpx.TimeoutException("timed out")
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = await send_telegram("test")
            assert result is False


# ═══════════════════════════════════════════
# format_correlation_alert
# ═══════════════════════════════════════════

class TestFormatCorrelationAlert:
    def test_basic_format(self):
        sig = _make_signal_obj(severity="critical", data={
            "rule_name": "Hormuz Disruption",
            "factors": [
                {"domain": "geoint", "title": "15 heat sigs in Hormuz", "severity": "high"},
                {"domain": "market", "symbol": "BZ=F", "name": "Brent Crude", "change_1d": 4.5, "price": 95.0},
            ],
            "factor_count": 2,
            "domains": ["geoint", "market"],
            "india_impact": "India imports 85% crude via Hormuz.",
        })
        msg = format_correlation_alert(sig)
        assert "[CRITICAL]" in msg
        assert "COMPOUND SIGNAL" in msg
        assert "Hormuz Disruption" in msg
        assert "GEOINT" in msg
        assert "MARKET" in msg
        assert "Brent Crude" in msg
        assert "+4.5%" in msg
        assert "India imports" in msg

    def test_empty_factors(self):
        sig = _make_signal_obj(data={
            "rule_name": "Empty Rule",
            "factors": [],
            "factor_count": 0,
            "domains": [],
            "india_impact": "None",
        })
        msg = format_correlation_alert(sig)
        assert "COMPOUND SIGNAL" in msg
        assert "Empty Rule" in msg

    def test_truncates_to_5_factors(self):
        factors = [
            {"domain": "geoint", "title": f"Signal {i}"}
            for i in range(10)
        ]
        sig = _make_signal_obj(data={
            "rule_name": "Many Factors",
            "factors": factors,
            "factor_count": 10,
            "domains": ["geoint"],
            "india_impact": "Test",
        })
        msg = format_correlation_alert(sig)
        assert "Signal 4" in msg
        assert "Signal 5" not in msg

    def test_truncates_india_impact(self):
        long_impact = "A" * 300
        sig = _make_signal_obj(data={
            "rule_name": "Test",
            "factors": [],
            "factor_count": 0,
            "domains": [],
            "india_impact": long_impact,
        })
        msg = format_correlation_alert(sig)
        # Impact truncated to 200 chars
        assert "A" * 200 in msg
        assert "A" * 201 not in msg

    def test_entity_signal_domain(self):
        sig = _make_signal_obj(data={
            "rule_name": "LAC Test",
            "factors": [
                {"domain": "entity_signal", "title": "India mentions surged 5x"},
            ],
            "factor_count": 1,
            "domains": ["entity_signal"],
            "india_impact": "Test",
        })
        msg = format_correlation_alert(sig)
        assert "ENTITY_SIGNAL" in msg
        assert "India mentions surged 5x" in msg

    def test_null_data_json(self):
        sig = SimpleNamespace(
            signal_type="correlation", severity="high",
            title="Fallback", description="",
            data_json=None,
        )
        msg = format_correlation_alert(sig)
        assert "COMPOUND SIGNAL" in msg


# ═══════════════════════════════════════════
# format_commodity_alert
# ═══════════════════════════════════════════

class TestFormatCommodityAlert:
    def test_with_top_indian_trades(self):
        sig = _make_signal_obj(signal_type="commodity", severity="high", data={
            "bucket_name": "Hormuz Disruption",
            "conviction": "high",
            "top_indian_trades": ["HAL: long -- defense orders", "IOC: short -- crude impact"],
            "market_context": {"BZ=F": {"price": 95.0, "change_1d": 3.5}},
            "risk": "De-escalation talks",
            "timeframe": "days",
        })
        msg = format_commodity_alert(sig)
        assert "[HIGH]" in msg
        assert "TRADING SIGNAL" in msg
        assert "Hormuz Disruption" in msg
        assert "high" in msg  # conviction
        assert "HAL" in msg
        assert "IOC" in msg
        assert "De-escalation" in msg
        assert "days" in msg

    def test_fallback_to_stocks_india(self):
        sig = _make_signal_obj(signal_type="commodity", data={
            "bucket_name": "Oil Spike",
            "conviction": "medium",
            "stocks_india": [
                {"name": "ONGC", "direction": "positive", "reason": "Higher realizations"},
                {"name": "BPCL", "direction": "negative", "reason": "Under-recovery"},
            ],
            "market_context": {},
        })
        msg = format_commodity_alert(sig)
        assert "ONGC" in msg
        assert "BPCL" in msg

    def test_no_trades(self):
        sig = _make_signal_obj(signal_type="commodity", data={
            "bucket_name": "Empty Signal",
            "conviction": "low",
            "market_context": {},
        })
        msg = format_commodity_alert(sig)
        assert "None" in msg

    def test_market_context_rendering(self):
        sig = _make_signal_obj(signal_type="commodity", data={
            "bucket_name": "Test",
            "conviction": "medium",
            "market_context": {
                "BZ=F": {"price": 85.5, "change_1d": 2.1},
                "GC=F": {"price": 2050.0, "change_1d": -0.5},
            },
        })
        msg = format_commodity_alert(sig)
        assert "BZ=F" in msg
        assert "85.5" in msg
        assert "GC=F" in msg

    def test_non_dict_market_context_skipped(self):
        """Non-dict values in market_context should not crash."""
        sig = _make_signal_obj(signal_type="commodity", data={
            "bucket_name": "Test",
            "conviction": "low",
            "market_context": {"BZ=F": "invalid"},
        })
        msg = format_commodity_alert(sig)
        assert "TRADING SIGNAL" in msg  # doesn't crash

    def test_without_risk_and_timeframe(self):
        sig = _make_signal_obj(signal_type="commodity", data={
            "bucket_name": "Test",
            "conviction": "medium",
            "market_context": {},
        })
        msg = format_commodity_alert(sig)
        assert "Risk:" not in msg
        assert "Timeframe:" not in msg


# ═══════════════════════════════════════════
# format_analyst_alert
# ═══════════════════════════════════════════

class TestFormatAnalystAlert:
    def test_basic_format(self):
        sig = _make_signal_obj(
            signal_type="assessment", severity="high",
            title="Strategic shift in Indo-Pacific",
            description="Analysis of the recent alignment changes." * 10,
            data={"india_implication": "QUAD realignment likely."},
        )
        msg = format_analyst_alert(sig)
        assert "[HIGH]" in msg
        assert "INTEL ASSESSMENT" in msg
        assert "Strategic shift" in msg
        assert "India:" in msg
        assert "QUAD realignment" in msg

    def test_description_truncated_to_300(self):
        sig = _make_signal_obj(
            signal_type="assessment",
            description="B" * 500,
            data={},
        )
        msg = format_analyst_alert(sig)
        assert "B" * 300 in msg
        assert "B" * 301 not in msg

    def test_india_implication_truncated(self):
        sig = _make_signal_obj(
            signal_type="assessment",
            data={"india_implication": "C" * 300},
        )
        msg = format_analyst_alert(sig)
        assert "C" * 200 in msg
        assert "C" * 201 not in msg

    def test_confidence_and_horizon(self):
        sig = _make_signal_obj(
            signal_type="assessment",
            data={"confidence": "high", "time_horizon": "short-term"},
        )
        msg = format_analyst_alert(sig)
        assert "Confidence: high" in msg
        assert "Horizon: short-term" in msg

    def test_no_confidence_or_horizon(self):
        sig = _make_signal_obj(signal_type="assessment", data={})
        msg = format_analyst_alert(sig)
        assert "Confidence:" not in msg


# ═══════════════════════════════════════════
# alert_on_signal — severity gating
# ═══════════════════════════════════════════

class TestAlertOnSignal:
    @pytest.mark.asyncio
    async def test_correlation_high_fires(self):
        sig = _make_signal_obj("correlation", "high", data={
            "rule_name": "Test", "factors": [], "factor_count": 0,
            "domains": [], "india_impact": "Test",
        })
        with patch("narad.intel.alerts.settings") as ms, \
             patch("narad.intel.alerts.send_telegram", new_callable=AsyncMock, return_value=True) as mock_send:
            ms.telegram_bot_token = "token"
            result = await alert_on_signal(sig)
            assert result is True
            mock_send.assert_called_once()

    @pytest.mark.asyncio
    async def test_correlation_critical_fires(self):
        sig = _make_signal_obj("correlation", "critical", data={
            "rule_name": "Test", "factors": [], "factor_count": 0,
            "domains": [], "india_impact": "",
        })
        with patch("narad.intel.alerts.settings") as ms, \
             patch("narad.intel.alerts.send_telegram", new_callable=AsyncMock, return_value=True):
            ms.telegram_bot_token = "token"
            result = await alert_on_signal(sig)
            assert result is True

    @pytest.mark.asyncio
    async def test_correlation_medium_skipped(self):
        sig = _make_signal_obj("correlation", "medium")
        with patch("narad.intel.alerts.settings") as ms:
            ms.telegram_bot_token = "token"
            result = await alert_on_signal(sig)
            assert result is False

    @pytest.mark.asyncio
    async def test_correlation_low_skipped(self):
        sig = _make_signal_obj("correlation", "low")
        with patch("narad.intel.alerts.settings") as ms:
            ms.telegram_bot_token = "token"
            result = await alert_on_signal(sig)
            assert result is False

    @pytest.mark.asyncio
    async def test_commodity_high_fires(self):
        sig = _make_signal_obj("commodity", "high", data={
            "bucket_name": "Test", "conviction": "high", "market_context": {},
        })
        with patch("narad.intel.alerts.settings") as ms, \
             patch("narad.intel.alerts.send_telegram", new_callable=AsyncMock, return_value=True):
            ms.telegram_bot_token = "token"
            result = await alert_on_signal(sig)
            assert result is True

    @pytest.mark.asyncio
    async def test_commodity_medium_skipped(self):
        sig = _make_signal_obj("commodity", "medium")
        with patch("narad.intel.alerts.settings") as ms:
            ms.telegram_bot_token = "token"
            result = await alert_on_signal(sig)
            assert result is False

    @pytest.mark.asyncio
    async def test_assessment_medium_fires(self):
        sig = _make_signal_obj("assessment", "medium", data={})
        with patch("narad.intel.alerts.settings") as ms, \
             patch("narad.intel.alerts.send_telegram", new_callable=AsyncMock, return_value=True):
            ms.telegram_bot_token = "token"
            result = await alert_on_signal(sig)
            assert result is True

    @pytest.mark.asyncio
    async def test_assessment_low_skipped(self):
        sig = _make_signal_obj("assessment", "low")
        with patch("narad.intel.alerts.settings") as ms:
            ms.telegram_bot_token = "token"
            result = await alert_on_signal(sig)
            assert result is False

    @pytest.mark.asyncio
    async def test_spike_signal_always_skipped(self):
        """Spike signals are never alerted, regardless of severity."""
        sig = _make_signal_obj("spike", "critical")
        with patch("narad.intel.alerts.settings") as ms:
            ms.telegram_bot_token = "token"
            result = await alert_on_signal(sig)
            assert result is False

    @pytest.mark.asyncio
    async def test_no_token_returns_false(self):
        sig = _make_signal_obj("correlation", "critical")
        with patch("narad.intel.alerts.settings") as ms:
            ms.telegram_bot_token = ""
            result = await alert_on_signal(sig)
            assert result is False


# ═══════════════════════════════════════════
# send_alert_batch
# ═══════════════════════════════════════════

class TestSendAlertBatch:
    @pytest.mark.asyncio
    async def test_sends_qualifying_signals(self):
        signals = [
            _make_signal_obj("correlation", "critical", data={
                "rule_name": "X", "factors": [], "factor_count": 0,
                "domains": [], "india_impact": "",
            }),
            _make_signal_obj("commodity", "low", data={}),  # below threshold
            _make_signal_obj("assessment", "high", data={}),
        ]
        with patch("narad.intel.alerts.settings") as ms, \
             patch("narad.intel.alerts.send_telegram", new_callable=AsyncMock, return_value=True):
            ms.telegram_bot_token = "token"
            sent = await send_alert_batch(signals)
            assert sent == 2

    @pytest.mark.asyncio
    async def test_empty_list(self):
        sent = await send_alert_batch([])
        assert sent == 0

    @pytest.mark.asyncio
    async def test_all_below_threshold(self):
        signals = [
            _make_signal_obj("correlation", "low"),
            _make_signal_obj("commodity", "medium"),
            _make_signal_obj("spike", "critical"),
        ]
        with patch("narad.intel.alerts.settings") as ms:
            ms.telegram_bot_token = "token"
            sent = await send_alert_batch(signals)
            assert sent == 0


# ═══════════════════════════════════════════
# Severity tags
# ═══════════════════════════════════════════

class TestSeverityTags:
    def test_all_severities_have_tags(self):
        for sev in ["critical", "high", "medium", "low"]:
            assert sev in SEVERITY_TAG
            assert SEVERITY_TAG[sev].startswith("[")

    def test_unknown_severity_fallback(self):
        """SEVERITY_TAG.get with unknown key uses default."""
        tag = SEVERITY_TAG.get("unknown", "[SIGNAL]")
        assert tag == "[SIGNAL]"
