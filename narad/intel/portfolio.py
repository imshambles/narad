"""
Portfolio Manager

Tracks open positions, executes stop-loss/take-profit,
calculates P&L, and provides portfolio-level metrics.
"""
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func

from narad.config import settings
from narad.database import async_session
from narad.models import PaperAccount, PaperOrder, PaperPosition, PaperTrade
from narad.intel.market_data import fetch_single_price

logger = logging.getLogger(__name__)


async def update_positions() -> None:
    """Update all open position prices and check stop-loss/take-profit triggers."""
    if not settings.paper_trading_enabled:
        return

    async with async_session() as session:
        result = await session.execute(
            select(PaperPosition).join(
                PaperAccount, PaperAccount.id == PaperPosition.account_id
            ).where(PaperAccount.is_active == True)
        )
        positions = list(result.scalars().all())

        if not positions:
            return

        now = datetime.now(timezone.utc)
        closed = 0

        for pos in positions:
            price = await fetch_single_price(pos.symbol)
            if price is None:
                continue

            pos.current_price = price
            pos.last_updated_at = now

            # Calculate unrealized P&L
            if pos.side == "LONG":
                pos.unrealized_pnl = (price - pos.avg_entry_price) * pos.quantity
                pos.unrealized_pnl_pct = ((price - pos.avg_entry_price) / pos.avg_entry_price) * 100
            else:  # SHORT
                pos.unrealized_pnl = (pos.avg_entry_price - price) * pos.quantity
                pos.unrealized_pnl_pct = ((pos.avg_entry_price - price) / pos.avg_entry_price) * 100

            # Check stop-loss
            if pos.stop_loss_price:
                hit_sl = (pos.side == "LONG" and price <= pos.stop_loss_price) or \
                         (pos.side == "SHORT" and price >= pos.stop_loss_price)
                if hit_sl:
                    await _close_position(session, pos, price, "stop_loss", now)
                    closed += 1
                    continue

            # Check take-profit
            if pos.take_profit_price:
                hit_tp = (pos.side == "LONG" and price >= pos.take_profit_price) or \
                         (pos.side == "SHORT" and price <= pos.take_profit_price)
                if hit_tp:
                    await _close_position(session, pos, price, "take_profit", now)
                    closed += 1
                    continue

        await session.commit()
        logger.info(f"Portfolio: updated {len(positions)} positions, closed {closed}")


async def _close_position(session, pos: PaperPosition, exit_price: float, reason: str, now: datetime):
    """Close a position, record the trade, and return cash to account."""
    # Calculate realized P&L
    if pos.side == "LONG":
        realized_pnl = (exit_price - pos.avg_entry_price) * pos.quantity
    else:
        realized_pnl = (pos.avg_entry_price - exit_price) * pos.quantity
    realized_pnl_pct = (realized_pnl / (pos.avg_entry_price * pos.quantity)) * 100

    # Get signal info for the trade record
    signal_result = await session.execute(
        select(PaperOrder.id)
        .where(PaperOrder.signal_id == pos.signal_id)
        .limit(1)
    )
    order = signal_result.scalar_one_or_none()

    # Look up signal type/severity from the original signal
    from narad.models import Signal
    sig = await session.execute(
        select(Signal).where(Signal.id == pos.signal_id).limit(1)
    )
    signal = sig.scalar_one_or_none()

    # Record the closed trade
    trade = PaperTrade(
        account_id=pos.account_id,
        symbol=pos.symbol,
        exchange=pos.exchange,
        side=pos.side,
        quantity=pos.quantity,
        entry_price=pos.avg_entry_price,
        exit_price=exit_price,
        realized_pnl=round(realized_pnl, 2),
        realized_pnl_pct=round(realized_pnl_pct, 2),
        signal_id=pos.signal_id,
        signal_type=signal.signal_type if signal else "",
        signal_severity=signal.severity if signal else "",
        opened_at=pos.opened_at,
        closed_at=now,
        close_reason=reason,
    )
    session.add(trade)

    # Return cash to account
    account = await session.get(PaperAccount, pos.account_id)
    if account:
        returned = pos.avg_entry_price * pos.quantity + realized_pnl
        account.current_cash += returned

    # Delete the position
    await session.delete(pos)

    logger.info(f"Closed {pos.symbol} ({reason}): P&L {realized_pnl:+.2f} ({realized_pnl_pct:+.1f}%)")

    # Send close alert
    try:
        from narad.intel.alerts import send_trade_close_alert
        await send_trade_close_alert(trade)
    except Exception as e:
        logger.debug(f"Close alert failed: {e}")


async def close_position_manually(position_id: int) -> dict | None:
    """Manually close a position at current market price."""
    async with async_session() as session:
        pos = await session.get(PaperPosition, position_id)
        if not pos:
            return None

        price = await fetch_single_price(pos.symbol)
        if price is None:
            return {"error": "Could not fetch current price"}

        await _close_position(session, pos, price, "manual", datetime.now(timezone.utc))
        await session.commit()
        return {"closed": pos.symbol, "exit_price": price}


async def reset_account() -> dict:
    """Reset the paper trading account — wipe all positions, orders, trades."""
    async with async_session() as session:
        account_result = await session.execute(
            select(PaperAccount).where(PaperAccount.name == "default").limit(1)
        )
        account = account_result.scalar_one_or_none()
        if not account:
            return {"status": "no account found"}

        # Delete all positions, orders, trades
        for model in [PaperPosition, PaperOrder, PaperTrade]:
            items = await session.execute(
                select(model).where(model.account_id == account.id)
            )
            for item in items.scalars().all():
                await session.delete(item)

        # Reset cash
        account.current_cash = account.initial_capital
        await session.commit()
        return {"status": "reset", "capital": account.initial_capital}


async def get_portfolio_summary() -> dict:
    """Get complete portfolio overview."""
    async with async_session() as session:
        account_result = await session.execute(
            select(PaperAccount).where(PaperAccount.name == "default").limit(1)
        )
        account = account_result.scalar_one_or_none()
        if not account:
            return {
                "status": "no_account",
                "message": "Paper trading not initialized. Set PAPER_TRADING_ENABLED=true in .env",
            }

        # Open positions
        positions = await session.execute(
            select(PaperPosition).where(PaperPosition.account_id == account.id)
        )
        open_positions = []
        total_unrealized = 0.0
        total_deployed = 0.0
        for p in positions.scalars().all():
            value = p.avg_entry_price * p.quantity
            total_deployed += value
            total_unrealized += p.unrealized_pnl
            open_positions.append({
                "id": p.id,
                "symbol": p.symbol,
                "exchange": p.exchange,
                "side": p.side,
                "quantity": p.quantity,
                "avg_entry": round(p.avg_entry_price, 2),
                "current_price": round(p.current_price, 2),
                "unrealized_pnl": round(p.unrealized_pnl, 2),
                "unrealized_pnl_pct": round(p.unrealized_pnl_pct, 2),
                "stop_loss": p.stop_loss_price,
                "take_profit": p.take_profit_price,
                "opened_at": p.opened_at,
            })

        # Closed trades
        trades_result = await session.execute(
            select(PaperTrade)
            .where(PaperTrade.account_id == account.id)
            .order_by(PaperTrade.closed_at.desc())
            .limit(50)
        )
        closed_trades = []
        total_realized = 0.0
        wins = 0
        losses = 0
        total_win_pnl = 0.0
        total_loss_pnl = 0.0

        for t in trades_result.scalars().all():
            total_realized += t.realized_pnl
            if t.realized_pnl > 0:
                wins += 1
                total_win_pnl += t.realized_pnl
            elif t.realized_pnl < 0:
                losses += 1
                total_loss_pnl += abs(t.realized_pnl)

            closed_trades.append({
                "symbol": t.symbol,
                "side": t.side,
                "quantity": t.quantity,
                "entry_price": round(t.entry_price, 2),
                "exit_price": round(t.exit_price, 2),
                "realized_pnl": round(t.realized_pnl, 2),
                "realized_pnl_pct": round(t.realized_pnl_pct, 2),
                "signal_type": t.signal_type,
                "close_reason": t.close_reason,
                "opened_at": t.opened_at,
                "closed_at": t.closed_at,
            })

        # Total orders
        order_count = await session.execute(
            select(func.count(PaperOrder.id))
            .where(PaperOrder.account_id == account.id)
        )

        total_value = account.current_cash + total_deployed + total_unrealized
        total_return = total_value - account.initial_capital
        total_return_pct = (total_return / account.initial_capital) * 100
        total_trades = wins + losses
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        avg_win = (total_win_pnl / wins) if wins > 0 else 0
        avg_loss = (total_loss_pnl / losses) if losses > 0 else 0
        profit_factor = (total_win_pnl / total_loss_pnl) if total_loss_pnl > 0 else float("inf") if total_win_pnl > 0 else 0

        return {
            "account": {
                "initial_capital": account.initial_capital,
                "current_cash": round(account.current_cash, 2),
                "deployed": round(total_deployed, 2),
                "total_value": round(total_value, 2),
                "total_return": round(total_return, 2),
                "total_return_pct": round(total_return_pct, 2),
            },
            "positions": open_positions,
            "positions_count": len(open_positions),
            "unrealized_pnl": round(total_unrealized, 2),
            "realized_pnl": round(total_realized, 2),
            "performance": {
                "total_trades": total_trades,
                "wins": wins,
                "losses": losses,
                "win_rate": round(win_rate, 1),
                "avg_win": round(avg_win, 2),
                "avg_loss": round(avg_loss, 2),
                "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else "inf",
                "total_orders": order_count.scalar() or 0,
            },
            "recent_trades": closed_trades[:20],
        }
