"""
Shared fixtures for the Narad test suite.

Provides:
- In-memory async SQLite database (fresh per test)
- Session factory that patches narad.database.async_session
- Factory helpers for common model creation
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from narad.models import (
    Base, Source, Article, Event, EventArticle, Entity, EntityMention,
    EntityRelation, ThreatMatrix, ThreatMatrixHistory, MarketDataPoint,
    Signal, SignalOutcome, Briefing, FetchLog,
    PaperAccount, PaperOrder, PaperPosition, PaperTrade,
)


@pytest.fixture(scope="session")
def event_loop():
    """Single event loop for all tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def db_engine():
    """Create a fresh in-memory SQLite engine per test."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine):
    """Provide an async session bound to the in-memory DB."""
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def patched_session(db_engine):
    """
    Patch narad.database.async_session everywhere it's imported so that
    all module-level code uses our test DB.
    Returns a session factory.
    """
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    targets = [
        "narad.database.async_session",
        "narad.intel.entity_graph.async_session",
        "narad.intel.threat_matrix.async_session",
        "narad.intel.signals.async_session",
        "narad.intel.correlator.async_session",
        "narad.intel.analyst.async_session",
        "narad.intel.market_data.async_session",
        "narad.intel.geospatial.async_session",
        "narad.intel.commodity.async_session",
        "narad.intel.query.async_session",
        "narad.intel.backtest.async_session",
        "narad.intel.trader.async_session",
        "narad.intel.portfolio.async_session",
        "narad.pipeline.clusterer.async_session",
        "narad.pipeline.briefing.async_session",
        "narad.pipeline.graph_builder.async_session",
    ]
    patches = [patch(t, factory) for t in targets]
    for p in patches:
        p.start()
    yield factory
    for p in patches:
        p.stop()


# ── Factory helpers ──

def make_source(name="TestSource", source_type="rss", url="https://example.com/feed"):
    return Source(name=name, source_type=source_type, url=url, fetch_interval_sec=300, is_active=True)


def make_article(
    source_id=1,
    title="Test Article",
    url="https://example.com/article",
    fingerprint=None,
    published_at=None,
):
    from narad.pipeline.normalizer import make_fingerprint
    fp = fingerprint or make_fingerprint(title, url)
    pub = published_at or datetime.now(timezone.utc)
    return Article(
        source_id=source_id,
        title=title,
        summary=f"Summary of {title}",
        external_url=url,
        published_at=pub,
        fingerprint=fp,
    )


def make_event(
    title="Test Event",
    category="conflict",
    article_count=3,
    source_count=2,
    entities_json=None,
    summary=None,
    is_active=True,
    first_seen_at=None,
):
    now = datetime.now(timezone.utc)
    return Event(
        title=title,
        summary=summary or f"Summary of {title}",
        category=category,
        article_count=article_count,
        source_count=source_count,
        entities_json=entities_json or json.dumps([
            {"name": "India", "type": "country"},
            {"name": "China", "type": "country"},
        ]),
        first_seen_at=first_seen_at or now,
        last_updated_at=now,
        is_active=is_active,
    )


def make_entity(name="India", entity_type="country", mention_count=10, canonical_name=None):
    now = datetime.now(timezone.utc)
    return Entity(
        name=name,
        entity_type=entity_type,
        canonical_name=canonical_name or name.strip().lower(),
        first_seen_at=now,
        last_seen_at=now,
        mention_count=mention_count,
    )


def make_signal(
    signal_type="spike",
    title="Test Signal",
    description="Test signal description",
    severity="medium",
    data_json=None,
    is_active=True,
    detected_at=None,
):
    return Signal(
        signal_type=signal_type,
        title=title,
        description=description,
        severity=severity,
        entity_ids_json=json.dumps([]),
        data_json=data_json or json.dumps({}),
        detected_at=detected_at or datetime.now(timezone.utc),
        is_active=is_active,
    )


def make_market_point(symbol="BZ=F", price=85.0, change_1d=3.5):
    return MarketDataPoint(
        symbol=symbol,
        name="Brent Crude Oil",
        category="commodity",
        unit="USD/barrel",
        price=price,
        change_1d=change_1d,
        change_7d=1.2,
        change_30d=-2.0,
        fetched_at=datetime.now(timezone.utc),
    )


SYMBOL_NAMES = {
    "BZ=F": ("Brent Crude Oil", "commodity", "USD/barrel"),
    "CL=F": ("WTI Crude Oil", "commodity", "USD/barrel"),
    "GC=F": ("Gold", "commodity", "USD/oz"),
    "NG=F": ("Natural Gas", "commodity", "USD/MMBtu"),
    "INR=X": ("USD/INR", "forex", "INR per USD"),
    "^NSEI": ("Nifty 50", "index", "points"),
}


def make_market_point_at(symbol="BZ=F", price=85.0, fetched_at=None, change_1d=0.0):
    """Create a market point at a specific time — for backtest tests."""
    name, category, unit = SYMBOL_NAMES.get(symbol, ("Unknown", "commodity", "USD"))
    return MarketDataPoint(
        symbol=symbol,
        name=name,
        category=category,
        unit=unit,
        price=price,
        change_1d=change_1d,
        change_7d=0.0,
        change_30d=0.0,
        fetched_at=fetched_at or datetime.now(timezone.utc),
    )


def make_signal_outcome(
    signal_id=1,
    signal_type="commodity",
    rule_id="hormuz_oil",
    severity="high",
    hit_rate=65.0,
    verdict="hit",
    detected_at=None,
    evaluated_at=None,
):
    now = datetime.now(timezone.utc)
    return SignalOutcome(
        signal_id=signal_id,
        signal_type=signal_type,
        rule_id=rule_id,
        severity=severity,
        detected_at=detected_at or now - timedelta(days=5),
        symbols_json=json.dumps(["BZ=F"]),
        trigger_prices_json=json.dumps({"BZ=F": 85.0}),
        results_json=json.dumps({"BZ=F": {"trigger_price": 85.0, "windows": {}}}),
        hit_rate=hit_rate,
        verdict=verdict,
        evaluated_at=evaluated_at or now,
    )
