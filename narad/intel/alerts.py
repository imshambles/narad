"""
Telegram Alert System

Sends real-time trading alerts when high-conviction signals fire.
Covers: correlation signals, commodity signals, analyst assessments.
"""
import json
import logging
from datetime import datetime, timezone

import httpx

from narad.config import settings

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

# Severity icons (text-based, no emojis)
SEVERITY_TAG = {
    "critical": "[CRITICAL]",
    "high": "[HIGH]",
    "medium": "[MEDIUM]",
    "low": "[LOW]",
}


async def send_telegram(message: str) -> bool:
    """Send a message via Telegram Bot API."""
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        logger.debug("Telegram not configured, skipping alert")
        return False

    url = TELEGRAM_API.format(token=settings.telegram_bot_token)
    payload = {
        "chat_id": settings.telegram_chat_id,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                logger.info("Telegram alert sent")
                return True
            else:
                logger.error(f"Telegram API error: {resp.status_code} {resp.text}")
                return False
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")
        return False


def format_correlation_alert(signal) -> str:
    """Format a cross-domain correlation signal for Telegram."""
    data = json.loads(signal.data_json or "{}")
    tag = SEVERITY_TAG.get(signal.severity, "[SIGNAL]")

    factors = data.get("factors", [])
    factor_lines = []
    for f in factors[:5]:
        domain = f.get("domain", "unknown").upper()
        if domain == "MARKET":
            factor_lines.append(
                f"  - MARKET: {f.get('name', f.get('symbol', '?'))} "
                f"{f.get('change_1d', 0):+.1f}% @ {f.get('price', '?')}"
            )
        elif domain == "GEOINT":
            factor_lines.append(f"  - GEOINT: {f.get('title', '?')}")
        else:
            factor_lines.append(f"  - {domain}: {f.get('title', '?')}")

    factors_str = "\n".join(factor_lines)
    impact = data.get("india_impact", "")

    msg = (
        f"{tag} *COMPOUND SIGNAL*\n"
        f"*{data.get('rule_name', signal.title)}*\n\n"
        f"Factors ({data.get('factor_count', len(factors))}):\n{factors_str}\n\n"
        f"India impact: {impact[:200]}\n\n"
        f"Domains: {' + '.join(data.get('domains', []))}"
    )
    return msg


def format_commodity_alert(signal) -> str:
    """Format a commodity trading signal for Telegram."""
    data = json.loads(signal.data_json or "{}")
    tag = SEVERITY_TAG.get(signal.severity, "[SIGNAL]")
    conviction = data.get("conviction", "N/A")

    # Indian trades
    indian_trades = data.get("top_indian_trades", [])
    if not indian_trades:
        stocks = data.get("stocks_india", [])
        indian_trades = [f"{s['name']}: {s['direction']} -- {s['reason']}" for s in stocks[:4]]

    trades_str = "\n".join(f"  - {t}" for t in indian_trades[:5]) if indian_trades else "  None"

    # Market context
    market_ctx = data.get("market_context", {})
    market_lines = []
    for sym, vals in market_ctx.items():
        if isinstance(vals, dict):
            market_lines.append(f"  {sym}: {vals.get('price', '?')} ({vals.get('change_1d', 0):+.1f}%)")
    market_str = "\n".join(market_lines) if market_lines else "  N/A"

    risk = data.get("risk", "")
    timeframe = data.get("timeframe", "")

    msg = (
        f"{tag} *TRADING SIGNAL*\n"
        f"*{data.get('bucket_name', signal.title)}*\n"
        f"Conviction: {conviction}\n\n"
        f"Indian trades:\n{trades_str}\n\n"
        f"Market:\n{market_str}\n"
    )
    if risk:
        msg += f"\nRisk: {risk}"
    if timeframe:
        msg += f"\nTimeframe: {timeframe}"

    return msg


def format_analyst_alert(signal) -> str:
    """Format an analyst assessment for Telegram."""
    data = json.loads(signal.data_json or "{}")
    tag = SEVERITY_TAG.get(signal.severity, "[SIGNAL]")

    msg = (
        f"{tag} *INTEL ASSESSMENT*\n"
        f"*{signal.title}*\n\n"
        f"{signal.description[:300]}\n"
    )
    implication = data.get("india_implication", "")
    if implication:
        msg += f"\nIndia: {implication[:200]}"

    confidence = data.get("confidence", "")
    horizon = data.get("time_horizon", "")
    if confidence or horizon:
        msg += f"\nConfidence: {confidence} | Horizon: {horizon}"

    return msg


async def alert_on_signal(signal) -> bool:
    """Send a Telegram alert for a signal if it meets severity threshold.

    Call this after creating a signal. Only alerts on high/critical severity
    for correlation/commodity, and medium+ for analyst assessments.
    """
    if not settings.telegram_bot_token:
        return False

    stype = signal.signal_type
    severity = signal.severity

    # Determine if this signal warrants an alert
    if stype == "correlation" and severity in ("high", "critical"):
        msg = format_correlation_alert(signal)
    elif stype == "commodity" and severity in ("high", "critical"):
        msg = format_commodity_alert(signal)
    elif stype == "assessment" and severity in ("medium", "high", "critical"):
        msg = format_analyst_alert(signal)
    else:
        return False

    return await send_telegram(msg)


async def send_alert_batch(signals: list) -> int:
    """Send alerts for a batch of signals. Returns count sent."""
    sent = 0
    for sig in signals:
        if await alert_on_signal(sig):
            sent += 1
    return sent


# ── Paper Trading Alerts ──

def format_trade_alert(order) -> str:
    """Format a paper trade order for Telegram."""
    sl = f"Stop Loss: {order.stop_loss_price}" if order.stop_loss_price else ""
    tp = f"Take Profit: {order.take_profit_price}" if order.take_profit_price else ""
    value = order.quantity * order.fill_price if order.fill_price else 0

    msg = (
        f"[PAPER TRADE] *{order.side} {order.symbol}*\n"
        f"Price: {order.fill_price:.2f} | Qty: {order.quantity} | Value: {value:,.0f}\n"
        f"Conviction: {order.conviction} | Size: {order.position_size_pct:.1f}%\n"
    )
    if sl:
        msg += f"{sl}\n"
    if tp:
        msg += f"{tp}\n"
    if order.notes:
        msg += f"Reason: {order.notes[:150]}"
    return msg


def format_trade_close_alert(trade) -> str:
    """Format a closed trade for Telegram."""
    pnl_sign = "+" if trade.realized_pnl >= 0 else ""
    duration = ""
    if trade.opened_at and trade.closed_at:
        delta = trade.closed_at - trade.opened_at
        if delta.days > 0:
            duration = f"{delta.days}d"
        else:
            hours = delta.seconds // 3600
            duration = f"{hours}h"

    msg = (
        f"[CLOSED] *{trade.symbol}* ({trade.close_reason})\n"
        f"Entry: {trade.entry_price:.2f} -> Exit: {trade.exit_price:.2f}\n"
        f"P&L: {pnl_sign}{trade.realized_pnl:,.2f} ({pnl_sign}{trade.realized_pnl_pct:.1f}%)\n"
    )
    if duration:
        msg += f"Duration: {duration}"
    return msg


async def send_trade_alerts(orders: list) -> int:
    """Send Telegram alerts for paper trade orders."""
    if not settings.telegram_bot_token:
        return 0
    sent = 0
    for order in orders:
        msg = format_trade_alert(order)
        if await send_telegram(msg):
            sent += 1
    return sent


async def send_trade_close_alert(trade) -> bool:
    """Send Telegram alert for a closed trade."""
    if not settings.telegram_bot_token:
        return False
    msg = format_trade_close_alert(trade)
    return await send_telegram(msg)
