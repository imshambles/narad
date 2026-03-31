import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from narad.config import settings
from narad.database import async_session
from narad.models import Article, FetchLog, Source
from narad.pipeline.deduplicator import is_duplicate
from narad.pipeline.normalizer import normalize_article
from narad.sources.rss import RSSAdapter
from narad.sources.gdelt import GDELTAdapter
from narad.sources.newsapi import NewsAPIAdapter
from narad.sources.reddit import RedditAdapter
from narad.sources.thinktanks import MultiThinkTankAdapter
from narad.sources.osint_twitter import OSINTTwitterAdapter
from narad.sources.osint_telegram import OSINTTelegramAdapter

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(job_defaults={"misfire_grace_time": 120})


def get_adapter(source: Source):
    """Return the appropriate adapter for a source."""
    if source.source_type == "rss":
        return RSSAdapter(source_name=source.name, feed_url=source.url)
    elif source.source_type == "gdelt":
        return GDELTAdapter(source_name=source.name)
    elif source.source_type == "newsapi":
        return NewsAPIAdapter(source_name=source.name)
    elif source.source_type == "reddit":
        return RedditAdapter(source_name=source.name)
    elif source.source_type == "thinktank":
        return MultiThinkTankAdapter(source_name=source.name)
    elif source.source_type == "osint_twitter":
        return OSINTTwitterAdapter(source_name=source.name)
    elif source.source_type == "osint_telegram":
        return OSINTTelegramAdapter(source_name=source.name)
    return None


# Source types that trigger the priority fast-track pipeline
PRIORITY_SOURCE_TYPES = {"osint_telegram"}

# Minimum new articles from a priority source to trigger fast-track
PRIORITY_THRESHOLD = 2


async def fetch_source(source_id: int):
    """Fetch articles from a single source and store new ones."""
    async with async_session() as session:
        source = await session.get(Source, source_id)
        if not source or not source.is_active:
            return

        adapter = get_adapter(source)
        if not adapter:
            logger.warning(f"No adapter for source type: {source.source_type}")
            return

        try:
            raw_articles = await adapter.fetch()
        except Exception as e:
            logger.error(f"Fetch failed for {source.name}: {e}")
            session.add(FetchLog(
                source_id=source.id,
                articles_found=0,
                articles_new=0,
                status="error",
                error_msg=str(e),
            ))
            await session.commit()
            return

        new_count = 0
        for raw in raw_articles:
            normalized = normalize_article(raw)
            if await is_duplicate(session, normalized["fingerprint"], normalized["title"]):
                continue

            article = Article(
                source_id=source.id,
                title=normalized["title"],
                summary=normalized["summary"],
                external_url=normalized["external_url"],
                published_at=normalized["published_at"],
                fingerprint=normalized["fingerprint"],
                image_url=normalized["image_url"],
            )
            session.add(article)
            # Flush each article so duplicate fingerprints are caught early
            try:
                await session.flush()
                new_count += 1
            except Exception:
                await session.rollback()
                # Re-fetch source since rollback invalidates it
                source = await session.get(Source, source_id)

        # Update source last_fetched_at
        from datetime import datetime, timezone
        source.last_fetched_at = datetime.now(timezone.utc)

        session.add(FetchLog(
            source_id=source.id,
            articles_found=len(raw_articles),
            articles_new=new_count,
            status="success",
        ))
        await session.commit()
        logger.info(f"{source.name}: {new_count} new articles (of {len(raw_articles)} found)")

    # Priority fast-track: when high-priority sources deliver new articles,
    # immediately run the intelligence pipeline instead of waiting for scheduled intervals
    if source.source_type in PRIORITY_SOURCE_TYPES and new_count >= PRIORITY_THRESHOLD:
        await _run_priority_pipeline(source.name, new_count)


async def _run_priority_pipeline(source_name: str, new_count: int):
    """Fast-track pipeline for priority sources.

    Runs clustering → entity graph → signals → correlations → commodity signals
    immediately, bypassing scheduled intervals. This cuts detection latency
    from ~27 min to under 2 min for Telegram OSINT articles.
    """
    logger.info(f"PRIORITY PIPELINE: {source_name} delivered {new_count} new articles, fast-tracking")

    try:
        from narad.pipeline.clusterer import run_clustering
        await run_clustering()
    except Exception as e:
        logger.error(f"Priority clustering failed: {e}")
        return  # no point continuing if clustering fails

    try:
        from narad.pipeline.summarizer import summarize_events
        await summarize_events()
    except Exception as e:
        logger.warning(f"Priority summarization failed: {e}")
        # continue — signals can still work with unsummarized events

    try:
        from narad.intel.entity_graph import update_entity_graph
        await update_entity_graph()
    except Exception as e:
        logger.warning(f"Priority entity graph failed: {e}")

    try:
        from narad.intel.signals import detect_signals
        await detect_signals()
    except Exception as e:
        logger.warning(f"Priority signal detection failed: {e}")

    try:
        from narad.intel.commodity import generate_commodity_signals
        await generate_commodity_signals()
    except Exception as e:
        logger.warning(f"Priority commodity signals failed: {e}")

    try:
        from narad.intel.correlator import run_correlations
        await run_correlations()
    except Exception as e:
        logger.warning(f"Priority correlations failed: {e}")

    logger.info(f"PRIORITY PIPELINE: complete for {source_name}")


async def start_scheduler():
    """Load active sources from DB and schedule fetch jobs."""
    async with async_session() as session:
        result = await session.execute(
            select(Source).where(Source.is_active == True)
        )
        sources = result.scalars().all()

    for source in sources:
        scheduler.add_job(
            fetch_source,
            "interval",
            seconds=source.fetch_interval_sec,
            args=[source.id],
            id=f"fetch_{source.id}",
            replace_existing=True,
        )
        # Also run immediately
        scheduler.add_job(
            fetch_source,
            args=[source.id],
            id=f"fetch_{source.id}_initial",
        )

    # Pipeline jobs: clustering → summarization → graph building
    from datetime import datetime, timedelta, timezone
    from narad.pipeline.clusterer import run_clustering
    from narad.pipeline.summarizer import summarize_events
    from narad.pipeline.graph_builder import build_relationships

    now = datetime.now(timezone.utc)

    scheduler.add_job(
        run_clustering, "interval", minutes=10,
        id="clustering", replace_existing=True,
    )
    # Initial clustering after 60s (let first fetch cycle finish)
    scheduler.add_job(
        run_clustering, id="clustering_initial",
        next_run_time=now + timedelta(seconds=60),
    )

    scheduler.add_job(
        summarize_events, "interval", minutes=10,
        id="summarization", replace_existing=True,
        next_run_time=now + timedelta(minutes=2),
    )

    scheduler.add_job(
        build_relationships, "interval", minutes=15,
        id="graph_builder", replace_existing=True,
        next_run_time=now + timedelta(minutes=4),
    )

    from narad.pipeline.briefing import generate_briefing
    from narad.intel.entity_graph import update_entity_graph, merge_duplicate_entities
    from narad.intel.threat_matrix import update_threat_matrix
    from narad.intel.signals import detect_signals
    from narad.intel.analyst import run_intelligence_analysis
    from narad.intel.market_data import fetch_market_data
    from narad.intel.geospatial import fetch_geoint
    from narad.intel.commodity import generate_commodity_signals
    from narad.intel.correlator import run_correlations
    from narad.intel.backtest import evaluate_signals

    scheduler.add_job(
        generate_briefing, "interval", minutes=30,
        id="briefing", replace_existing=True,
        next_run_time=now + timedelta(minutes=5),
    )

    # Intelligence layer — runs after summarization populates entities
    scheduler.add_job(
        update_entity_graph, "interval", minutes=10,
        id="entity_graph", replace_existing=True,
        next_run_time=now + timedelta(minutes=3),
    )
    scheduler.add_job(
        update_threat_matrix, "interval", minutes=15,
        id="threat_matrix", replace_existing=True,
        next_run_time=now + timedelta(minutes=6),
    )
    scheduler.add_job(
        detect_signals, "interval", minutes=15,
        id="signals", replace_existing=True,
        next_run_time=now + timedelta(minutes=7),
    )

    # Market data — commodity prices, forex, indices
    scheduler.add_job(
        fetch_market_data, "interval", minutes=15,
        id="market_data", replace_existing=True,
        next_run_time=now + timedelta(seconds=30),
    )

    # GEOINT — satellite/aircraft/ship monitoring
    scheduler.add_job(
        fetch_geoint, "interval", minutes=10,
        id="geoint", replace_existing=True,
        next_run_time=now + timedelta(seconds=45),
    )

    # Commodity intelligence
    scheduler.add_job(
        generate_commodity_signals, "interval", minutes=30,
        id="commodity", replace_existing=True,
        next_run_time=now + timedelta(minutes=9),
    )

    # Cross-domain correlation engine
    scheduler.add_job(
        run_correlations, "interval", minutes=10,
        id="correlator", replace_existing=True,
        next_run_time=now + timedelta(minutes=10),
    )

    # Entity deduplication — merge near-duplicates periodically
    scheduler.add_job(
        merge_duplicate_entities, "interval", hours=6,
        id="entity_merge", replace_existing=True,
        next_run_time=now + timedelta(minutes=12),
    )

    # Intelligence analyst — runs after entity graph and threat matrix are populated
    scheduler.add_job(
        run_intelligence_analysis, "interval", minutes=30,
        id="intel_analyst", replace_existing=True,
        next_run_time=now + timedelta(minutes=8),
    )

    # Paper trading — update positions, check stop-loss/take-profit
    if settings.paper_trading_enabled:
        from narad.intel.portfolio import update_positions
        scheduler.add_job(
            update_positions, "interval", minutes=15,
            id="paper_portfolio", replace_existing=True,
            next_run_time=now + timedelta(minutes=11),
        )
        logger.info("Paper trading enabled — portfolio updates scheduled every 15 min")

    # Backtest — evaluate past signal accuracy against market data
    scheduler.add_job(
        evaluate_signals, "interval", hours=6,
        id="backtest", replace_existing=True,
        next_run_time=now + timedelta(minutes=15),
    )

    # Self-ping keep-alive — prevents Render free tier from spinning down
    async def self_ping():
        """Ping our own /health endpoint to stay alive on Render."""
        import os
        render_url = os.environ.get("RENDER_EXTERNAL_URL")
        if not render_url:
            return  # not on Render, skip
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{render_url}/ping")
                logger.debug(f"Self-ping: {resp.status_code}")
        except Exception as e:
            logger.debug(f"Self-ping failed: {e}")

    scheduler.add_job(
        self_ping, "interval", minutes=10,
        id="self_ping", replace_existing=True,
        next_run_time=now + timedelta(minutes=5),
    )

    scheduler.start()
    logger.info(f"Scheduler started with {len(sources)} source(s) + full intelligence pipeline")
