from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path

import logging

from narad.database import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await init_db()
    await seed_sources()
    from narad.scheduler import start_scheduler, scheduler
    await start_scheduler()
    yield
    # Shutdown
    scheduler.shutdown(wait=False)


async def seed_sources():
    """Insert default RSS sources if the sources table is empty."""
    from sqlalchemy import select
    from narad.database import async_session
    from narad.models import Source

    # Wire services & OSINT — factual, minimal editorial bias
    default_sources = [
        # Wire services (raw reporting, minimal editorializing)
        {"name": "AP News", "source_type": "rss", "url": "https://feedx.net/rss/ap.xml", "fetch_interval_sec": 300},
        {"name": "Reuters", "source_type": "rss", "url": "https://news.google.com/rss/search?q=site:reuters.com+world&hl=en-US&gl=US&ceid=US:en", "fetch_interval_sec": 300},
        {"name": "AFP / France24", "source_type": "rss", "url": "https://www.france24.com/en/rss", "fetch_interval_sec": 300},
        # Institutional / multilateral
        {"name": "UN News", "source_type": "rss", "url": "https://news.un.org/feed/subscribe/en/news/all/rss.xml", "fetch_interval_sec": 600},
        {"name": "ReliefWeb", "source_type": "rss", "url": "https://reliefweb.int/updates/rss.xml", "fetch_interval_sec": 600},
        # India — wire services filtered for India
        {"name": "Reuters India", "source_type": "rss", "url": "https://news.google.com/rss/search?q=site:reuters.com+India&hl=en-IN&gl=IN&ceid=IN:en", "fetch_interval_sec": 300},
        {"name": "AP India", "source_type": "rss", "url": "https://news.google.com/rss/search?q=site:apnews.com+India&hl=en-IN&gl=IN&ceid=IN:en", "fetch_interval_sec": 300},
        # India — geopolitics, diplomacy, defence
        {"name": "India Diplomacy", "source_type": "rss", "url": "https://news.google.com/rss/search?q=India+%22foreign+minister%22+OR+%22Jaishankar%22+OR+%22bilateral%22+OR+%22diplomacy%22+OR+%22foreign+policy%22&hl=en-IN&gl=IN&ceid=IN:en", "fetch_interval_sec": 300},
        {"name": "India Defence", "source_type": "rss", "url": "https://news.google.com/rss/search?q=India+defence+OR+%22Indian+Navy%22+OR+%22Indian+Army%22+OR+%22Indian+Air+Force%22+OR+DRDO&hl=en-IN&gl=IN&ceid=IN:en", "fetch_interval_sec": 300},
        {"name": "India Geopolitics", "source_type": "rss", "url": "https://news.google.com/rss/search?q=India+geopolitics+OR+sanctions+OR+%22trade+war%22+OR+BRICS+OR+SCO+OR+%22Quad%22&hl=en-IN&gl=IN&ceid=IN:en", "fetch_interval_sec": 300},
        # India — wire service (ANI)
        {"name": "ANI Wire", "source_type": "rss", "url": "https://news.google.com/rss/search?q=site:aninews.in+India+foreign+OR+diplomacy+OR+defence+OR+minister&hl=en-IN&gl=IN&ceid=IN:en", "fetch_interval_sec": 300},
        # OSINT / conflict tracking
        {"name": "GDELT", "source_type": "gdelt", "url": "https://api.gdeltproject.org/api/v2/doc/doc", "fetch_interval_sec": 300},
        # Aggregator (disabled until API key added)
        {"name": "NewsAPI", "source_type": "newsapi", "url": "https://newsapi.org/v2/top-headlines", "fetch_interval_sec": 900},
        # OSINT crawlers
        {"name": "Reddit OSINT", "source_type": "reddit", "url": "https://reddit.com", "fetch_interval_sec": 600},
        {"name": "Think Tanks", "source_type": "thinktank", "url": "https://thinktanks.narad", "fetch_interval_sec": 900},
        {"name": "OSINT Twitter", "source_type": "osint_twitter", "url": "https://twitter.com", "fetch_interval_sec": 600},
    ]

    # Sources to deactivate (biased/propaganda)
    remove_sources = {"BBC World", "Al Jazeera", "NPR World"}

    async with async_session() as session:
        # Deactivate unwanted sources
        for name in remove_sources:
            result = await session.execute(
                select(Source).where(Source.name == name).limit(1)
            )
            src = result.scalar_one_or_none()
            if src:
                src.is_active = False

        # Upsert sources — insert if new, update URL if changed
        for src in default_sources:
            result = await session.execute(
                select(Source).where(Source.name == src["name"]).limit(1)
            )
            existing = result.scalar_one_or_none()
            if existing is None:
                session.add(Source(**src))
            elif existing.url != src["url"]:
                existing.url = src["url"]
        await session.commit()


app = FastAPI(title="Narad", lifespan=lifespan)

static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Register routes
from narad.api.articles import router as articles_router
from narad.api.events import router as events_router
from narad.api.intel import router as intel_router
from narad.web.views import router as web_router

app.include_router(articles_router, prefix="/api")
app.include_router(events_router, prefix="/api")
app.include_router(intel_router, prefix="/api")
app.include_router(web_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
