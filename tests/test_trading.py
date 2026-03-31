"""
Tests for the paper trading system:
- Models (PaperAccount, PaperOrder, PaperPosition, PaperTrade)
- NSE ticker resolution
- Trade engine (signal-to-order conversion, position sizing, risk limits)
- Portfolio manager (P&L calculation, stop-loss/TP, summary)
- Trade alerts formatting
- API endpoints
"""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

import pytest
from sqlalchemy import select

from narad.models import (
    PaperAccount, PaperOrder, PaperPosition, PaperTrade, Signal,
)
from narad.intel.market_data import resolve_ticker, get_exchange, NSE_TICKER_MAP
from narad.intel.trader import (
    _extract_trades, _direction_to_side,
    execute_signal_trades, POSITION_SIZE_PCT, SEVERITY_TO_CONVICTION,
)
from narad.intel.alerts import format_trade_alert, format_trade_close_alert
from tests.conftest import make_signal


# ═══════════════════════════════════════════
# NSE Ticker Resolution
# ═══════════════════════════════════════════

class TestTickerResolution:
    def test_direct_match(self):
        assert resolve_ticker("HAL") == "HAL.NS"
        assert resolve_ticker("BEL") == "BEL.NS"
        assert resolve_ticker("ONGC") == "ONGC.NS"

    def test_already_ticker(self):
        assert resolve_ticker("HAL.NS") == "HAL.NS"
        assert resolve_ticker("BZ=F") == "BZ=F"
        assert resolve_ticker("^NSEI") == "^NSEI"

    def test_partial_match(self):
        assert resolve_ticker("IOC / BPCL / HPCL") is not None

    def test_no_match(self):
        assert resolve_ticker("RandomCompany") is None

    def test_reliance_variants(self):
        assert resolve_ticker("Reliance") == "RELIANCE.NS"
        assert resolve_ticker("Reliance Industries") == "RELIANCE.NS"

    def test_ticker_map_has_key_stocks(self):
        for stock in ["HAL", "BEL", "BDL", "ONGC", "IOC", "BPCL"]:
            assert stock in NSE_TICKER_MAP

    def test_get_exchange_nse(self):
        assert get_exchange("HAL.NS") == "NSE"
        assert get_exchange("RELIANCE.NS") == "NSE"

    def test_get_exchange_commodity(self):
        assert get_exchange("BZ=F") == "MCX"
        assert get_exchange("GC=F") == "MCX"

    def test_get_exchange_index(self):
        assert get_exchange("^NSEI") == "INDEX"

    def test_get_exchange_forex(self):
        assert get_exchange("INR=X") == "FOREX"


# ═══════════════════════════════════════════
# Trade Extraction from Signals
# ═══════════════════════════════════════════

class TestExtractTrades:
    def test_commodity_with_top_indian_trades(self):
        data = {
            "top_indian_trades": [
                "HAL: positive -- Fighter jet orders accelerate",
                "BPCL: negative -- Under-recovery on fuel",
            ],
        }
        trades = _extract_trades("commodity", "high", data)
        assert len(trades) >= 2
        names = [t[0] for t in trades]
        assert "HAL" in names
        assert "BPCL" in names

    def test_commodity_fallback_to_stocks_india(self):
        data = {
            "stocks_india": [
                {"name": "ONGC", "direction": "positive", "reason": "Higher realizations"},
            ],
        }
        trades = _extract_trades("commodity", "high", data)
        assert len(trades) >= 1
        assert trades[0][0] == "ONGC"
        assert trades[0][1] == "positive"

    def test_commodity_includes_commodities(self):
        data = {
            "commodities": [
                {"symbol": "BZ=F", "direction": "long", "reason": "Oil supply shock"},
            ],
        }
        trades = _extract_trades("commodity", "high", data)
        assert ("BZ=F", "long", "Oil supply shock") in trades

    def test_correlation_hormuz(self):
        data = {"rule_id": "hormuz_oil"}
        trades = _extract_trades("correlation", "critical", data)
        symbols = [t[0] for t in trades]
        assert "BZ=F" in symbols
        assert "HAL" in symbols

    def test_correlation_lac(self):
        data = {"rule_id": "lac_tension_defense"}
        trades = _extract_trades("correlation", "high", data)
        symbols = [t[0] for t in trades]
        assert "HAL" in symbols
        assert "BEL" in symbols
        assert "GC=F" in symbols

    def test_correlation_unknown_rule(self):
        data = {"rule_id": "unknown"}
        trades = _extract_trades("correlation", "high", data)
        assert trades == []

    def test_empty_data(self):
        trades = _extract_trades("commodity", "high", {})
        assert trades == []


# ═══════════════════════════════════════════
# Direction to Side
# ═══════════════════════════════════════════

class TestDirectionToSide:
    def test_long_to_buy(self):
        assert _direction_to_side("long") == "BUY"
        assert _direction_to_side("positive") == "BUY"
        assert _direction_to_side("buy") == "BUY"

    def test_short_to_sell(self):
        assert _direction_to_side("short") == "SELL"
        assert _direction_to_side("negative") == "SELL"
        assert _direction_to_side("sell") == "SELL"
        assert _direction_to_side("short-term negative") == "SELL"

    def test_mixed_returns_none(self):
        assert _direction_to_side("mixed") is None

    def test_empty_returns_none(self):
        assert _direction_to_side("") is None

    def test_case_insensitive(self):
        assert _direction_to_side("LONG") == "BUY"
        assert _direction_to_side("SHORT") == "SELL"


# ═══════════════════════════════════════════
# Config Constants
# ═══════════════════════════════════════════

class TestTradingConfig:
    def test_position_sizes(self):
        assert POSITION_SIZE_PCT["high"] > POSITION_SIZE_PCT["medium"]
        assert POSITION_SIZE_PCT["medium"] > POSITION_SIZE_PCT["low"]

    def test_severity_mapping(self):
        assert SEVERITY_TO_CONVICTION["critical"] == "high"
        assert SEVERITY_TO_CONVICTION["high"] == "high"
        assert SEVERITY_TO_CONVICTION["medium"] == "medium"
        assert SEVERITY_TO_CONVICTION["low"] == "low"


# ═══════════════════════════════════════════
# Paper Trading Models
# ═══════════════════════════════════════════

class TestPaperModels:
    @pytest.mark.asyncio
    async def test_create_account(self, db_session):
        account = PaperAccount(
            name="test",
            initial_capital=1000000.0,
            current_cash=1000000.0,
            created_at=datetime.now(timezone.utc),
        )
        db_session.add(account)
        await db_session.flush()
        assert account.id is not None
        assert account.is_active is True

    @pytest.mark.asyncio
    async def test_create_order(self, db_session):
        account = PaperAccount(
            name="test_order", initial_capital=100000, current_cash=100000,
            created_at=datetime.now(timezone.utc),
        )
        db_session.add(account)
        await db_session.flush()

        order = PaperOrder(
            account_id=account.id, signal_id=1, symbol="HAL.NS",
            exchange="NSE", side="BUY", quantity=10, target_price=4500.0,
            fill_price=4500.0, status="filled", conviction="high",
            position_size_pct=4.5, stop_loss_price=4275.0,
            take_profit_price=5175.0,
            created_at=datetime.now(timezone.utc),
            filled_at=datetime.now(timezone.utc),
        )
        db_session.add(order)
        await db_session.flush()
        assert order.id is not None

    @pytest.mark.asyncio
    async def test_create_position(self, db_session):
        account = PaperAccount(
            name="test_pos", initial_capital=100000, current_cash=100000,
            created_at=datetime.now(timezone.utc),
        )
        db_session.add(account)
        await db_session.flush()

        now = datetime.now(timezone.utc)
        pos = PaperPosition(
            account_id=account.id, symbol="BZ=F", exchange="MCX",
            side="LONG", quantity=5, avg_entry_price=85.0, current_price=87.0,
            unrealized_pnl=10.0, unrealized_pnl_pct=2.35,
            signal_id=1, opened_at=now, last_updated_at=now,
        )
        db_session.add(pos)
        await db_session.flush()
        assert pos.id is not None

    @pytest.mark.asyncio
    async def test_create_trade(self, db_session):
        account = PaperAccount(
            name="test_trade", initial_capital=100000, current_cash=100000,
            created_at=datetime.now(timezone.utc),
        )
        db_session.add(account)
        await db_session.flush()

        now = datetime.now(timezone.utc)
        trade = PaperTrade(
            account_id=account.id, symbol="HAL.NS", exchange="NSE",
            side="LONG", quantity=10, entry_price=4500.0, exit_price=4800.0,
            realized_pnl=3000.0, realized_pnl_pct=6.67,
            signal_id=1, signal_type="commodity", signal_severity="high",
            opened_at=now - timedelta(days=2), closed_at=now,
            close_reason="take_profit",
        )
        db_session.add(trade)
        await db_session.flush()
        assert trade.id is not None
        assert trade.realized_pnl == 3000.0


# ═══════════════════════════════════════════
# Execute Signal Trades
# ═══════════════════════════════════════════

class TestExecuteSignalTrades:
    @pytest.mark.asyncio
    async def test_disabled_returns_empty(self, patched_session):
        sig = make_signal(signal_type="commodity", severity="high", data_json=json.dumps({
            "stocks_india": [{"name": "HAL", "direction": "positive", "reason": "test"}],
        }))
        with patch("narad.intel.trader.settings") as ms:
            ms.paper_trading_enabled = False
            orders = await execute_signal_trades(sig)
            assert orders == []

    @pytest.mark.asyncio
    async def test_executes_trades_when_enabled(self, patched_session):
        factory = patched_session
        now = datetime.now(timezone.utc)

        async with factory() as session:
            account = PaperAccount(
                name="default", initial_capital=1000000, current_cash=1000000,
                created_at=now, is_active=True,
            )
            session.add(account)

            sig = make_signal(signal_type="commodity", severity="high", data_json=json.dumps({
                "commodities": [{"symbol": "GC=F", "direction": "long", "reason": "safe haven"}],
                "conviction": "high",
            }))
            session.add(sig)
            await session.commit()

        with patch("narad.intel.trader.settings") as ms, \
             patch("narad.intel.trader.fetch_single_price", new_callable=AsyncMock, return_value=2050.0), \
             patch("narad.intel.alerts.send_telegram", new_callable=AsyncMock, return_value=True):
            ms.paper_trading_enabled = True
            ms.paper_trading_capital = 1000000.0
            ms.paper_trading_max_exposure_pct = 60.0
            ms.paper_trading_stop_loss_pct = 5.0
            ms.paper_trading_take_profit_pct = 15.0
            orders = await execute_signal_trades(sig)

        assert len(orders) >= 1
        assert orders[0].symbol == "GC=F"
        assert orders[0].side == "BUY"
        assert orders[0].status == "filled"

    @pytest.mark.asyncio
    async def test_skips_existing_position(self, patched_session):
        factory = patched_session
        now = datetime.now(timezone.utc)

        async with factory() as session:
            account = PaperAccount(
                name="default", initial_capital=1000000, current_cash=1000000,
                created_at=now, is_active=True,
            )
            session.add(account)
            await session.flush()

            # Pre-existing position for GC=F
            session.add(PaperPosition(
                account_id=account.id, symbol="GC=F", exchange="MCX",
                side="LONG", quantity=5, avg_entry_price=2000.0, current_price=2050.0,
                signal_id=1, opened_at=now, last_updated_at=now,
            ))
            await session.commit()

        sig = make_signal(signal_type="commodity", severity="high", data_json=json.dumps({
            "commodities": [{"symbol": "GC=F", "direction": "long", "reason": "test"}],
        }))

        with patch("narad.intel.trader.settings") as ms, \
             patch("narad.intel.trader.fetch_single_price", new_callable=AsyncMock, return_value=2050.0):
            ms.paper_trading_enabled = True
            ms.paper_trading_capital = 1000000.0
            ms.paper_trading_max_exposure_pct = 60.0
            ms.paper_trading_stop_loss_pct = 5.0
            ms.paper_trading_take_profit_pct = 15.0
            orders = await execute_signal_trades(sig)

        assert len(orders) == 0  # skipped because position exists


# ═══════════════════════════════════════════
# Portfolio Summary
# ═══════════════════════════════════════════

class TestPortfolioSummary:
    @pytest.mark.asyncio
    async def test_no_account(self, patched_session):
        from narad.intel.portfolio import get_portfolio_summary
        with patch("narad.intel.portfolio.settings") as ms:
            ms.paper_trading_enabled = True
            result = await get_portfolio_summary()
        assert result["status"] == "no_account"

    @pytest.mark.asyncio
    async def test_with_account_and_positions(self, patched_session):
        from narad.intel.portfolio import get_portfolio_summary
        factory = patched_session
        now = datetime.now(timezone.utc)

        async with factory() as session:
            account = PaperAccount(
                name="default", initial_capital=1000000, current_cash=900000,
                created_at=now, is_active=True,
            )
            session.add(account)
            await session.flush()

            session.add(PaperPosition(
                account_id=account.id, symbol="HAL.NS", exchange="NSE",
                side="LONG", quantity=20, avg_entry_price=4500.0,
                current_price=4700.0, unrealized_pnl=4000.0,
                unrealized_pnl_pct=4.44, signal_id=1,
                opened_at=now, last_updated_at=now,
            ))

            session.add(PaperTrade(
                account_id=account.id, symbol="BEL.NS", exchange="NSE",
                side="LONG", quantity=50, entry_price=300.0, exit_price=330.0,
                realized_pnl=1500.0, realized_pnl_pct=10.0,
                signal_id=2, signal_type="commodity", signal_severity="high",
                opened_at=now - timedelta(days=3), closed_at=now,
                close_reason="take_profit",
            ))
            await session.commit()

        result = await get_portfolio_summary()
        assert result["positions_count"] == 1
        assert result["unrealized_pnl"] == 4000.0
        assert result["realized_pnl"] == 1500.0
        assert result["performance"]["wins"] == 1
        assert result["performance"]["win_rate"] == 100.0


# ═══════════════════════════════════════════
# Trade Alert Formatting
# ═══════════════════════════════════════════

class TestTradeAlerts:
    def test_format_trade_alert(self):
        order = SimpleNamespace(
            side="BUY", symbol="HAL.NS", fill_price=4500.0,
            quantity=22, conviction="high", position_size_pct=5.0,
            stop_loss_price=4275.0, take_profit_price=5175.0,
            notes="Fighter jet orders accelerate",
        )
        msg = format_trade_alert(order)
        assert "PAPER TRADE" in msg
        assert "BUY HAL.NS" in msg
        assert "4500" in msg
        assert "Stop Loss" in msg
        assert "Take Profit" in msg
        assert "Fighter jet" in msg

    def test_format_trade_close_alert(self):
        trade = SimpleNamespace(
            symbol="HAL.NS", entry_price=4500.0, exit_price=4800.0,
            realized_pnl=6600.0, realized_pnl_pct=6.67,
            close_reason="take_profit",
            opened_at=datetime(2025, 6, 10, tzinfo=timezone.utc),
            closed_at=datetime(2025, 6, 13, tzinfo=timezone.utc),
        )
        msg = format_trade_close_alert(trade)
        assert "CLOSED" in msg
        assert "HAL.NS" in msg
        assert "take_profit" in msg
        assert "+6,600" in msg
        assert "3d" in msg

    def test_format_trade_close_loss(self):
        trade = SimpleNamespace(
            symbol="BPCL.NS", entry_price=300.0, exit_price=285.0,
            realized_pnl=-1500.0, realized_pnl_pct=-5.0,
            close_reason="stop_loss",
            opened_at=datetime(2025, 6, 10, 10, 0, tzinfo=timezone.utc),
            closed_at=datetime(2025, 6, 10, 18, 0, tzinfo=timezone.utc),
        )
        msg = format_trade_close_alert(trade)
        assert "stop_loss" in msg
        assert "-1,500" in msg
        assert "8h" in msg


# ═══════════════════════════════════════════
# Reset Account
# ═══════════════════════════════════════════

class TestResetAccount:
    @pytest.mark.asyncio
    async def test_reset_clears_everything(self, patched_session):
        from narad.intel.portfolio import reset_account
        factory = patched_session
        now = datetime.now(timezone.utc)

        async with factory() as session:
            account = PaperAccount(
                name="default", initial_capital=1000000, current_cash=800000,
                created_at=now, is_active=True,
            )
            session.add(account)
            await session.flush()

            session.add(PaperOrder(
                account_id=account.id, signal_id=1, symbol="HAL.NS",
                exchange="NSE", side="BUY", quantity=10, target_price=4500,
                status="filled", created_at=now,
            ))
            session.add(PaperPosition(
                account_id=account.id, symbol="HAL.NS", exchange="NSE",
                side="LONG", quantity=10, avg_entry_price=4500, current_price=4500,
                signal_id=1, opened_at=now, last_updated_at=now,
            ))
            await session.commit()

        result = await reset_account()
        assert result["status"] == "reset"
        assert result["capital"] == 1000000

        async with factory() as session:
            positions = (await session.execute(select(PaperPosition))).scalars().all()
            orders = (await session.execute(select(PaperOrder))).scalars().all()
            assert len(positions) == 0
            assert len(orders) == 0

            account = (await session.execute(
                select(PaperAccount).where(PaperAccount.name == "default")
            )).scalar_one()
            assert account.current_cash == 1000000
