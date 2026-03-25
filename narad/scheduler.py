import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from narad.database import async_session
from narad.models import Article, FetchLog, Source
from narad.pipeline.deduplicator import is_duplicate
from narad.pipeline.normalizer import normalize_article
from narad.sources.rss import RSSAdapter
from narad.sources.gdelt import GDELTAdapter
from narad.sources.newsapi import NewsAPIAdapter

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
    return None


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

    scheduler.add_job(
        generate_briefing, "interval", minutes=30,
        id="briefing", replace_existing=True,
        next_run_time=now + timedelta(minutes=5),
    )

    scheduler.start()
    logger.info(f"Scheduler started with {len(sources)} source(s) + clustering/summarization/graph/briefing jobs")
