"""
Paper Trading Engine

Converts intelligence signals into simulated trade orders.
Handles position sizing, risk management, and order execution.
"""
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func

from narad.config import settings
from narad.database import async_session
from narad.models import PaperAccount, PaperOrder, PaperPosition, Signal
from narad.intel.market_data import resolve_ticker, get_exchange, fetch_single_price

logger = logging.getLogger(__name__)

# Position sizing by conviction
POSITION_SIZE_PCT = {
    "high": 5.0,
    "medium": 3.0,
    "low": 1.5,
}

SEVERITY_TO_CONVICTION = {
    "critical": "high",
    "high": "high",
    "medium": "medium",
    "low": "low",
}

MAX_SINGLE_POSITION_PCT = 8.0
MAX_DAILY_TRADES = 10


async def get_or_create_account(session) -> PaperAccount:
    """Get default paper account, creating it if it doesn't exist."""
    result = await session.execute(
        select(PaperAccount).where(PaperAccount.name == "default").limit(1)
    )
    account = result.scalar_one_or_none()
    if account is None:
        account = PaperAccount(
            name="default",
            initial_capital=settings.paper_trading_capital,
            current_cash=settings.paper_trading_capital,
            created_at=datetime.now(timezone.utc),
            is_active=True,
        )
        session.add(account)
        await session.flush()
    return account


async def execute_signal_trades(signal: Signal) -> list[PaperOrder]:
    """Convert a signal into paper trade orders.

    Parses the signal's stock/commodity recommendations,
    sizes positions, checks risk limits, and executes paper fills.
    """
    if not settings.paper_trading_enabled:
        return []

    data = json.loads(signal.data_json or "{}")
    trades_to_make = _extract_trades(signal.signal_type, signal.severity, data)

    if not trades_to_make:
        return []

    orders = []
    async with async_session() as session:
        account = await get_or_create_account(session)
        now = datetime.now(timezone.utc)

        # Pre-check daily trade count
        daily_count = await _get_daily_trade_count(session, account.id, now)
        if daily_count >= MAX_DAILY_TRADES:
            logger.info(f"Paper trading: daily limit reached ({daily_count}/{MAX_DAILY_TRADES})")
            return []

        conviction = data.get("conviction") or SEVERITY_TO_CONVICTION.get(signal.severity, "medium")
        size_pct = POSITION_SIZE_PCT.get(conviction, 3.0)

        for stock_name, direction, reason in trades_to_make:
            ticker = resolve_ticker(stock_name)
            if not ticker:
                continue

            # Skip if position already exists
            existing = await session.execute(
                select(PaperPosition)
                .where(PaperPosition.account_id == account.id)
                .where(PaperPosition.symbol == ticker)
                .limit(1)
            )
            if existing.scalar_one_or_none():
                continue

            # Check total exposure
            total_deployed = await _get_total_deployed(session, account.id)
            max_deploy = account.initial_capital * (settings.paper_trading_max_exposure_pct / 100)
            if total_deployed >= max_deploy:
                logger.info(f"Paper trading: max exposure reached ({total_deployed:.0f}/{max_deploy:.0f})")
                break

            # Fetch current price
            price = await fetch_single_price(ticker)
            if price is None or price <= 0:
                continue

            # Calculate position size
            position_value = account.current_cash * (size_pct / 100)
            position_value = min(position_value, account.initial_capital * (MAX_SINGLE_POSITION_PCT / 100))
            position_value = min(position_value, account.current_cash)

            if position_value < 100:  # minimum order value
                continue

            quantity = max(1, int(position_value / price))
            actual_value = quantity * price

            # Determine side
            side = _direction_to_side(direction)
            if side is None:
                continue

            # Calculate stop loss and take profit
            sl_pct = settings.paper_trading_stop_loss_pct / 100
            tp_pct = settings.paper_trading_take_profit_pct / 100
            if side == "BUY":
                stop_loss = round(price * (1 - sl_pct), 2)
                take_profit = round(price * (1 + tp_pct), 2)
            else:
                stop_loss = round(price * (1 + sl_pct), 2)
                take_profit = round(price * (1 - tp_pct), 2)

            exchange = get_exchange(ticker)

            # Create filled order
            order = PaperOrder(
                account_id=account.id,
                signal_id=signal.id,
                symbol=ticker,
                exchange=exchange,
                side=side,
                quantity=quantity,
                target_price=price,
                fill_price=price,
                status="filled",
                conviction=conviction,
                position_size_pct=round(actual_value / account.initial_capital * 100, 2),
                stop_loss_price=stop_loss,
                take_profit_price=take_profit,
                created_at=now,
                filled_at=now,
                notes=f"{reason[:200]}" if reason else None,
            )
            session.add(order)

            # Create position
            pos_side = "LONG" if side == "BUY" else "SHORT"
            session.add(PaperPosition(
                account_id=account.id,
                symbol=ticker,
                exchange=exchange,
                side=pos_side,
                quantity=quantity,
                avg_entry_price=price,
                current_price=price,
                unrealized_pnl=0.0,
                unrealized_pnl_pct=0.0,
                stop_loss_price=stop_loss,
                take_profit_price=take_profit,
                signal_id=signal.id,
                opened_at=now,
                last_updated_at=now,
            ))

            # Deduct cash
            account.current_cash -= actual_value
            orders.append(order)

            daily_count += 1
            if daily_count >= MAX_DAILY_TRADES:
                break

        await session.commit()

    if orders:
        logger.info(f"Paper trading: {len(orders)} orders filled from signal #{signal.id}")
        # Send trade alerts
        try:
            from narad.intel.alerts import send_trade_alerts
            await send_trade_alerts(orders)
        except Exception as e:
            logger.debug(f"Trade alert dispatch failed: {e}")

    return orders


def _extract_trades(signal_type: str, severity: str, data: dict) -> list[tuple[str, str, str]]:
    """Extract (stock_name, direction, reason) tuples from signal data."""
    trades = []

    if signal_type == "commodity":
        # First try LLM-refined trades
        for trade_str in data.get("top_indian_trades", []):
            parts = trade_str.split(":", 1)
            if len(parts) == 2:
                name = parts[0].strip()
                rest = parts[1].strip()
                # Parse "direction -- reason" or "direction — reason"
                for sep in ["--", "—", "-"]:
                    if sep in rest:
                        direction, reason = rest.split(sep, 1)
                        trades.append((name, direction.strip(), reason.strip()))
                        break
                else:
                    trades.append((name, rest, ""))

        # Fallback to stocks_india
        if not trades:
            for stock in data.get("stocks_india", []):
                name = stock.get("name", "")
                direction = stock.get("direction", "")
                reason = stock.get("reason", "")
                if name and direction:
                    trades.append((name, direction, reason))

        # Also trade commodities directly
        for comm in data.get("commodities", []):
            sym = comm.get("symbol", "")
            direction = comm.get("direction", "")
            reason = comm.get("reason", "")
            if sym and direction:
                trades.append((sym, direction, reason))

    elif signal_type == "correlation":
        # Correlations: trade based on rule defaults
        rule_id = data.get("rule_id", "")
        rule_trades = {
            "hormuz_oil": [("BZ=F", "long", "Hormuz disruption"), ("HAL", "long", "Defense spending")],
            "lac_tension_defense": [("HAL", "long", "LAC tension"), ("BEL", "long", "Defense electronics"), ("GC=F", "long", "Safe haven")],
            "pak_border_escalation": [("HAL", "long", "Border escalation"), ("BDL", "long", "Missile demand"), ("GC=F", "long", "Safe haven")],
            "gulf_aden_shipping": [("BZ=F", "long", "Shipping disruption"), ("SCI", "long", "Freight rates")],
            "gold_rush_geopolitical": [("GC=F", "long", "Flight to safety")],
            "inr_pressure": [("ONGC", "long", "INR hedge via oil producer")],
            "scs_maritime": [("HAL", "long", "Indo-Pacific defense"), ("GC=F", "long", "Safe haven")],
        }
        trades = rule_trades.get(rule_id, [])

    return trades


def _direction_to_side(direction: str) -> str | None:
    """Convert signal direction to order side."""
    d = direction.lower().strip()
    if d in ("long", "positive", "buy"):
        return "BUY"
    elif d in ("short", "negative", "sell", "short-term negative"):
        return "SELL"
    return None  # skip mixed/ambiguous


async def _get_daily_trade_count(session, account_id: int, now: datetime) -> int:
    """Count trades placed today."""
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    result = await session.execute(
        select(func.count(PaperOrder.id))
        .where(PaperOrder.account_id == account_id)
        .where(PaperOrder.status == "filled")
        .where(PaperOrder.filled_at >= today_start)
    )
    return result.scalar() or 0


async def _get_total_deployed(session, account_id: int) -> float:
    """Calculate total capital currently deployed in open positions."""
    result = await session.execute(
        select(func.sum(PaperPosition.quantity * PaperPosition.avg_entry_price))
        .where(PaperPosition.account_id == account_id)
    )
    return result.scalar() or 0.0
