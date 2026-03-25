from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from narad.config import settings
from narad.models import Base

engine = create_async_engine(settings.database_url, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.execute(
            __import__("sqlalchemy").text("PRAGMA journal_mode=WAL")
        )
        await conn.run_sync(Base.metadata.create_all)
        # Migrate: add columns that may be missing from older DBs
        text = __import__("sqlalchemy").text
        for migration in [
            "ALTER TABLE briefings ADD COLUMN outlook_json TEXT",
            "ALTER TABLE events ADD COLUMN timeline_json TEXT",
        ]:
            try:
                await conn.execute(text(migration))
            except Exception:
                pass  # Column already exists


async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session
