"""
Paper Trading API endpoints.

Portfolio overview, positions, orders, trades, performance metrics.
"""
import json

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from narad.database import get_session
from narad.models import PaperAccount, PaperOrder, PaperPosition, PaperTrade

router = APIRouter(tags=["trading"])


@router.get("/trading/portfolio")
async def get_portfolio():
    """Full portfolio summary: account, positions, P&L, performance."""
    from narad.intel.portfolio import get_portfolio_summary
    return await get_portfolio_summary()


@router.get("/trading/positions")
async def get_positions(session: AsyncSession = Depends(get_session)):
    """List all open positions."""
    result = await session.execute(
        select(PaperPosition).order_by(PaperPosition.opened_at.desc())
    )
    return [
        {
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
        }
        for p in result.scalars().all()
    ]


@router.get("/trading/orders")
async def get_orders(
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
):
    """Order history."""
    stmt = select(PaperOrder).order_by(PaperOrder.created_at.desc()).limit(limit)
    if status:
        stmt = stmt.where(PaperOrder.status == status)
    result = await session.execute(stmt)
    return [
        {
            "id": o.id,
            "symbol": o.symbol,
            "exchange": o.exchange,
            "side": o.side,
            "quantity": o.quantity,
            "fill_price": o.fill_price,
            "status": o.status,
            "conviction": o.conviction,
            "position_size_pct": o.position_size_pct,
            "stop_loss": o.stop_loss_price,
            "take_profit": o.take_profit_price,
            "signal_id": o.signal_id,
            "notes": o.notes,
            "created_at": o.created_at,
            "filled_at": o.filled_at,
        }
        for o in result.scalars().all()
    ]


@router.get("/trading/trades")
async def get_trades(
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
):
    """Closed trade history with realized P&L."""
    result = await session.execute(
        select(PaperTrade).order_by(PaperTrade.closed_at.desc()).limit(limit)
    )
    return [
        {
            "symbol": t.symbol,
            "exchange": t.exchange,
            "side": t.side,
            "quantity": t.quantity,
            "entry_price": round(t.entry_price, 2),
            "exit_price": round(t.exit_price, 2),
            "realized_pnl": round(t.realized_pnl, 2),
            "realized_pnl_pct": round(t.realized_pnl_pct, 2),
            "signal_type": t.signal_type,
            "signal_severity": t.signal_severity,
            "close_reason": t.close_reason,
            "opened_at": t.opened_at,
            "closed_at": t.closed_at,
        }
        for t in result.scalars().all()
    ]


@router.get("/trading/performance")
async def get_performance():
    """Performance metrics: win rate, profit factor, returns."""
    from narad.intel.portfolio import get_portfolio_summary
    summary = await get_portfolio_summary()
    if "performance" not in summary:
        return {"message": "No trading data yet"}
    return {
        "account": summary.get("account", {}),
        "performance": summary["performance"],
        "unrealized_pnl": summary.get("unrealized_pnl", 0),
        "realized_pnl": summary.get("realized_pnl", 0),
    }


@router.post("/trading/close/{position_id}")
async def close_position(position_id: int):
    """Manually close a position at current market price."""
    from narad.intel.portfolio import close_position_manually
    result = await close_position_manually(position_id)
    if result is None:
        return {"error": "Position not found"}
    return result


@router.post("/trading/reset")
async def reset_account():
    """Reset paper trading account — wipe all data, restart with initial capital."""
    from narad.intel.portfolio import reset_account
    return await reset_account()
