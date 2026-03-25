import logging
from datetime import datetime, timedelta, timezone

import numpy as np
from sklearn.cluster import AgglomerativeClustering
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from narad.database import async_session
from narad.models import Article, Event, EventArticle, Source

logger = logging.getLogger(__name__)

ASSIGNMENT_THRESHOLD = 0.20
CLUSTER_DISTANCE_THRESHOLD = 0.80
LOOKBACK_HOURS = 48
STALE_HOURS = 72


def _article_text(title: str, summary: str | None) -> str:
    text = title
    if summary:
        text += " " + summary
    return text


async def run_clustering() -> None:
    """Full clustering pass: assign unclustered articles to events, create new events."""
    async with async_session() as session:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)

        # 1. Get unclustered articles from last 48h
        unclustered_stmt = (
            select(Article)
            .outerjoin(EventArticle, EventArticle.article_id == Article.id)
            .where(EventArticle.id.is_(None))
            .where(Article.published_at >= cutoff)
            .options(joinedload(Article.source))
        )
        result = await session.execute(unclustered_stmt)
        unclustered = list(result.scalars().unique().all())

        if not unclustered:
            logger.info("Clustering: no unclustered articles")
            return

        logger.info(f"Clustering: {len(unclustered)} unclustered articles")

        # 2. Get active events with their article titles for centroids
        events_stmt = (
            select(Event)
            .where(Event.is_active == True)
            .where(Event.last_updated_at >= cutoff)
            .options(joinedload(Event.articles).joinedload(EventArticle.article))
        )
        result = await session.execute(events_stmt)
        existing_events = list(result.scalars().unique().all())

        # Build centroid texts for existing events
        event_centroids = []
        event_objs = []
        for event in existing_events:
            titles = [ea.article.title for ea in event.articles if ea.article]
            centroid_text = event.title + " " + " ".join(titles)
            event_centroids.append(centroid_text)
            event_objs.append(event)

        # 3. Build TF-IDF corpus
        unclustered_texts = [_article_text(a.title, a.summary) for a in unclustered]
        all_texts = event_centroids + unclustered_texts

        if len(all_texts) < 2:
            # Only one article, create a single-article event
            await _create_event_for_articles(session, unclustered)
            await session.commit()
            return

        vectorizer = TfidfVectorizer(
            max_features=5000, stop_words="english", ngram_range=(1, 2)
        )
        tfidf_matrix = vectorizer.fit_transform(all_texts)

        n_centroids = len(event_centroids)
        centroid_vectors = tfidf_matrix[:n_centroids] if n_centroids > 0 else None
        unclustered_vectors = tfidf_matrix[n_centroids:]

        # 4. Try to assign unclustered articles to existing events
        assigned_indices = set()
        if centroid_vectors is not None and n_centroids > 0:
            similarities = cosine_similarity(unclustered_vectors, centroid_vectors)
            for i, article in enumerate(unclustered):
                best_idx = int(np.argmax(similarities[i]))
                best_score = float(similarities[i][best_idx])
                if best_score >= ASSIGNMENT_THRESHOLD:
                    event = event_objs[best_idx]
                    session.add(EventArticle(
                        event_id=event.id,
                        article_id=article.id,
                        similarity_score=best_score,
                        assigned_at=datetime.now(timezone.utc),
                    ))
                    assigned_indices.add(i)

        # 5. Cluster remaining unassigned articles
        remaining_indices = [i for i in range(len(unclustered)) if i not in assigned_indices]
        remaining_articles = [unclustered[i] for i in remaining_indices]

        if len(remaining_articles) >= 2:
            remaining_vectors = unclustered_vectors[remaining_indices]
            # Convert sparse to dense for AgglomerativeClustering
            dense = remaining_vectors.toarray()

            try:
                clustering = AgglomerativeClustering(
                    n_clusters=None,
                    distance_threshold=CLUSTER_DISTANCE_THRESHOLD,
                    metric="cosine",
                    linkage="average",
                )
                labels = clustering.fit_predict(dense)
            except Exception as e:
                logger.warning(f"Agglomerative clustering failed: {e}, creating individual events")
                labels = list(range(len(remaining_articles)))

            # Group by cluster label
            clusters: dict[int, list[Article]] = {}
            for idx, label in enumerate(labels):
                clusters.setdefault(label, []).append(remaining_articles[idx])

            for cluster_articles in clusters.values():
                await _create_event_for_articles(session, cluster_articles)

        elif len(remaining_articles) == 1:
            await _create_event_for_articles(session, remaining_articles)

        # 6. Update counts on all affected events
        await _update_event_counts(session)

        # 7. Mark stale events
        stale_cutoff = datetime.now(timezone.utc) - timedelta(hours=STALE_HOURS)
        stale_stmt = (
            select(Event)
            .where(Event.is_active == True)
            .where(Event.last_updated_at < stale_cutoff)
        )
        result = await session.execute(stale_stmt)
        for event in result.scalars().all():
            event.is_active = False

        await session.commit()
        logger.info(f"Clustering complete: {len(assigned_indices)} assigned to existing, "
                     f"{len(remaining_articles)} in new clusters")


async def _create_event_for_articles(session: AsyncSession, articles: list[Article]) -> Event:
    """Create a new event from a list of articles."""
    now = datetime.now(timezone.utc)
    # Use earliest article's title as default event title
    sorted_articles = sorted(articles, key=lambda a: a.published_at)
    event = Event(
        title=sorted_articles[0].title,
        first_seen_at=sorted_articles[0].published_at,
        last_updated_at=now,
        article_count=len(articles),
        source_count=len(set(a.source_id for a in articles)),
    )
    session.add(event)
    await session.flush()  # get event.id

    for article in articles:
        session.add(EventArticle(
            event_id=event.id,
            article_id=article.id,
            similarity_score=1.0,
            assigned_at=now,
        ))
    return event


async def _update_event_counts(session: AsyncSession) -> None:
    """Refresh article_count and source_count for all active events."""
    events_stmt = select(Event).where(Event.is_active == True)
    result = await session.execute(events_stmt)
    for event in result.scalars().all():
        # Count articles
        count_stmt = select(func.count()).select_from(EventArticle).where(
            EventArticle.event_id == event.id
        )
        count_result = await session.execute(count_stmt)
        event.article_count = count_result.scalar() or 0

        # Count distinct sources
        source_stmt = (
            select(func.count(func.distinct(Article.source_id)))
            .select_from(EventArticle)
            .join(Article, Article.id == EventArticle.article_id)
            .where(EventArticle.event_id == event.id)
        )
        source_result = await session.execute(source_stmt)
        event.source_count = source_result.scalar() or 0

        event.last_updated_at = datetime.now(timezone.utc)
