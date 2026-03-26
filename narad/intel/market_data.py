"""
Market Data Fetcher

Pulls commodity prices, forex rates, and market indices.
These are hard quantitative signals that correlate with geopolitical events.
No API keys needed — uses Yahoo Finance and open exchange rate APIs.
"""
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import select

from narad.database import async_session
from narad.models import MarketDataPoint

logger = logging.getLogger(__name__)

YF_BASE = "https://query1.finance.yahoo.com/v8/finance/chart"
YF_HEADERS = {"User-Agent": "Mozilla/5.0"}

# Symbols we track — chosen for India geopolitical relevance
TRACKED_SYMBOLS = {
    # Commodities
    "CL=F": {"name": "WTI Crude Oil", "category": "commodity", "unit": "USD/barrel"},
    "BZ=F": {"name": "Brent Crude Oil", "category": "commodity", "unit": "USD/barrel"},
    "GC=F": {"name": "Gold", "category": "commodity", "unit": "USD/oz"},
    "NG=F": {"name": "Natural Gas", "category": "commodity", "unit": "USD/MMBtu"},
    "ZW=F": {"name": "Wheat", "category": "commodity", "unit": "USD/bushel"},
    # Forex
    "INR=X": {"name": "USD/INR", "category": "forex", "unit": "INR per USD"},
    "CNY=X": {"name": "USD/CNY", "category": "forex", "unit": "CNY per USD"},
    "EURINR=X": {"name": "EUR/INR", "category": "forex", "unit": "INR per EUR"},
    # Indices
    "^NSEI": {"name": "Nifty 50", "category": "index", "unit": "points"},
    "^BSESN": {"name": "Sensex", "category": "index", "unit": "points"},
}


async def fetch_market_data() -> None:
    """Fetch current prices for all tracked symbols and store."""
    async with async_session() as session:
        now = datetime.now(timezone.utc)
        fetched = 0

        async with httpx.AsyncClient(timeout=15, headers=YF_HEADERS) as client:
            for symbol, meta in TRACKED_SYMBOLS.items():
                try:
                    resp = await client.get(
                        f"{YF_BASE}/{symbol}",
                        params={"interval": "1d", "range": "30d"},
                    )
                    resp.raise_for_status()
                    data = resp.json()

                    result = data["chart"]["result"][0]
                    current_price = result["meta"]["regularMarketPrice"]

                    # Get historical closes
                    closes = result["indicators"]["quote"][0].get("close", [])
                    closes = [c for c in closes if c is not None]

                    # Calculate changes from actual closes, not chartPreviousClose
                    change_1d = 0
                    change_7d = 0
                    change_30d = 0
                    if len(closes) >= 2:
                        change_1d = ((current_price - closes[-2]) / closes[-2] * 100)
                    if len(closes) >= 7:
                        change_7d = ((current_price - closes[-7]) / closes[-7] * 100)
                    if len(closes) >= 20:
                        change_30d = ((current_price - closes[0]) / closes[0] * 100)

                    # Store
                    session.add(MarketDataPoint(
                        symbol=symbol,
                        name=meta["name"],
                        category=meta["category"],
                        unit=meta["unit"],
                        price=current_price,
                        change_1d=round(change_1d, 2),
                        change_7d=round(change_7d, 2),
                        change_30d=round(change_30d, 2),
                        fetched_at=now,
                    ))
                    fetched += 1

                except Exception as e:
                    logger.error(f"Market data fetch failed for {symbol}: {e}")

        # Clean old data points (keep last 48h only in DB, we store history via changes)
        from sqlalchemy import delete
        cutoff = datetime.now(timezone.utc)
        # Keep only latest per symbol (delete old ones)
        for symbol in TRACKED_SYMBOLS:
            old = await session.execute(
                select(MarketDataPoint)
                .where(MarketDataPoint.symbol == symbol)
                .order_by(MarketDataPoint.fetched_at.desc())
                .offset(48)  # keep last 48 data points
            )
            for old_point in old.scalars().all():
                await session.delete(old_point)

        await session.commit()
        logger.info(f"Market data: fetched {fetched}/{len(TRACKED_SYMBOLS)} symbols")


async def get_latest_prices() -> dict:
    """Get the most recent price for each tracked symbol."""
    async with async_session() as session:
        prices = {}
        for symbol in TRACKED_SYMBOLS:
            result = await session.execute(
                select(MarketDataPoint)
                .where(MarketDataPoint.symbol == symbol)
                .order_by(MarketDataPoint.fetched_at.desc())
                .limit(1)
            )
            point = result.scalar_one_or_none()
            if point:
                prices[symbol] = {
                    "name": point.name,
                    "category": point.category,
                    "unit": point.unit,
                    "price": point.price,
                    "change_1d": point.change_1d,
                    "change_7d": point.change_7d,
                    "change_30d": point.change_30d,
                    "fetched_at": point.fetched_at,
                }
        return prices
