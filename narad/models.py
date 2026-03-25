from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    source_type: Mapped[str] = mapped_column(String, nullable=False)  # rss, api, gdelt
    url: Mapped[str] = mapped_column(String, nullable=False)
    fetch_interval_sec: Mapped[int] = mapped_column(Integer, default=300)
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    articles: Mapped[list["Article"]] = relationship(back_populates="source")


class Article(Base):
    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), nullable=False)
    external_url: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    fingerprint: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    image_url: Mapped[str | None] = mapped_column(String, nullable=True)

    source: Mapped["Source"] = relationship(back_populates="articles")
    event_link: Mapped["EventArticle | None"] = relationship(back_populates="article", uselist=False)

    __table_args__ = (
        Index("idx_articles_published", "published_at"),
        Index("idx_articles_fingerprint", "fingerprint"),
    )


class FetchLog(Base):
    __tablename__ = "fetch_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    articles_found: Mapped[int] = mapped_column(Integer, default=0)
    articles_new: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String, nullable=False)  # success, error, no_change
    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_facts: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list
    disagreements: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    entities_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list of {name, type}
    timeline_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list of milestones
    article_count: Mapped[int] = mapped_column(Integer, default=0)
    source_count: Mapped[int] = mapped_column(Integer, default=0)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    summarized_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    articles: Mapped[list["EventArticle"]] = relationship(back_populates="event")

    __table_args__ = (
        Index("idx_events_last_updated", "last_updated_at"),
        Index("idx_events_category", "category"),
    )


class EventArticle(Base):
    __tablename__ = "event_articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), nullable=False)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id"), nullable=False, unique=True)
    similarity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    assigned_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    event: Mapped["Event"] = relationship(back_populates="articles")
    article: Mapped["Article"] = relationship(back_populates="event_link")

    __table_args__ = (
        Index("idx_ea_event", "event_id"),
        Index("idx_ea_article", "article_id"),
    )


class EventRelationship(Base):
    __tablename__ = "event_relationships"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), nullable=False)
    target_event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), nullable=False)
    relationship_type: Mapped[str] = mapped_column(String, nullable=False)
    shared_entities: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (
        Index("idx_er_source", "source_event_id"),
        Index("idx_er_target", "target_event_id"),
    )


class Briefing(Base):
    __tablename__ = "briefings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    stories_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    connections_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    outlook_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # predictive scenarios
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)
