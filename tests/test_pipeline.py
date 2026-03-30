"""
Tests for the processing pipeline:
- normalizer (fingerprinting, normalization)
- deduplicator (exact + fuzzy matching)
- clusterer (TF-IDF assignment, event creation)
- graph_builder (entity edge detection, temporal edges)
"""
import json
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from narad.models import Article, Event, EventArticle, EventRelationship
from narad.pipeline.normalizer import make_fingerprint, normalize_article
from narad.pipeline.deduplicator import is_duplicate, FUZZY_THRESHOLD
from narad.pipeline.graph_builder import _find_edges
from narad.sources.base import RawArticle
from tests.conftest import make_source, make_article, make_event


# ═══════════════════════════════════════════
# Normalizer
# ═══════════════════════════════════════════

class TestNormalizer:
    def test_fingerprint_deterministic(self):
        fp1 = make_fingerprint("India deploys troops to LAC", "https://reuters.com/article1")
        fp2 = make_fingerprint("India deploys troops to LAC", "https://reuters.com/article1")
        assert fp1 == fp2

    def test_fingerprint_case_insensitive(self):
        fp1 = make_fingerprint("India Deploys Troops", "https://reuters.com/a")
        fp2 = make_fingerprint("india deploys troops", "https://reuters.com/a")
        assert fp1 == fp2

    def test_fingerprint_different_domains(self):
        fp1 = make_fingerprint("Same Title", "https://reuters.com/a")
        fp2 = make_fingerprint("Same Title", "https://ap.com/a")
        assert fp1 != fp2

    def test_fingerprint_different_titles(self):
        fp1 = make_fingerprint("Title A", "https://reuters.com/a")
        fp2 = make_fingerprint("Title B", "https://reuters.com/a")
        assert fp1 != fp2

    def test_fingerprint_is_sha256(self):
        fp = make_fingerprint("test", "https://example.com")
        assert len(fp) == 64
        assert all(c in "0123456789abcdef" for c in fp)

    def test_normalize_article_basic(self):
        raw = RawArticle(
            title="  India Tests Missile  ",
            url="https://reuters.com/article",
            summary="India conducted a missile test.",
            published_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
            image_url="https://img.com/pic.jpg",
            source_name="Reuters",
        )
        result = normalize_article(raw)
        assert result["title"] == "India Tests Missile"  # stripped
        assert result["summary"] == "India conducted a missile test."
        assert result["external_url"] == "https://reuters.com/article"
        assert result["fingerprint"] is not None
        assert result["image_url"] == "https://img.com/pic.jpg"

    def test_normalize_article_naive_datetime_gets_utc(self):
        raw = RawArticle(
            title="Test", url="https://x.com", summary=None,
            published_at=datetime(2025, 6, 1),  # naive
            image_url=None, source_name="Test",
        )
        result = normalize_article(raw)
        assert result["published_at"].tzinfo is not None

    def test_normalize_article_none_published_at(self):
        raw = RawArticle(
            title="Test", url="https://x.com", summary=None,
            published_at=None,
            image_url=None, source_name="Test",
        )
        result = normalize_article(raw)
        assert result["published_at"] is not None
        assert result["published_at"].tzinfo is not None


# ═══════════════════════════════════════════
# Deduplicator
# ═══════════════════════════════════════════

class TestDeduplicator:
    @pytest_asyncio.fixture
    async def session_with_articles(self, db_session):
        src = make_source()
        db_session.add(src)
        await db_session.flush()

        articles = [
            make_article(src.id, "India launches Agni-V missile test successfully", "https://reuters.com/agni"),
            make_article(src.id, "China deploys jets near Taiwan strait", "https://ap.com/china"),
            make_article(src.id, "Oil prices surge amid Middle East tensions", "https://bbc.com/oil"),
        ]
        for a in articles:
            db_session.add(a)
        await db_session.flush()
        return db_session

    @pytest.mark.asyncio
    async def test_exact_fingerprint_match(self, session_with_articles):
        session = session_with_articles
        fp = make_fingerprint("India launches Agni-V missile test successfully", "https://reuters.com/agni")
        assert await is_duplicate(session, fp, "India launches Agni-V missile test successfully") is True

    @pytest.mark.asyncio
    async def test_no_match(self, session_with_articles):
        session = session_with_articles
        fp = make_fingerprint("Completely different article", "https://other.com/new")
        assert await is_duplicate(session, fp, "Completely different article") is False

    @pytest.mark.asyncio
    async def test_fuzzy_title_match(self, session_with_articles):
        session = session_with_articles
        fp = make_fingerprint("different fp", "https://new.com/x")
        # Very similar title should fuzzy-match
        assert await is_duplicate(session, fp, "India launches Agni V missile test successfully") is True

    @pytest.mark.asyncio
    async def test_fuzzy_below_threshold(self, session_with_articles):
        session = session_with_articles
        fp = make_fingerprint("different fp", "https://new.com/x")
        # Very different title should not match
        assert await is_duplicate(session, fp, "Pakistan announces new trade agreement with UK") is False

    def test_fuzzy_threshold_value(self):
        assert FUZZY_THRESHOLD == 85


# ═══════════════════════════════════════════
# Clusterer (unit tests for assignment logic)
# ═══════════════════════════════════════════

class TestClusterer:
    def test_article_text_with_summary(self):
        from narad.pipeline.clusterer import _article_text
        text = _article_text("India border clash", "Troops exchanged fire at LAC")
        assert "India border clash" in text
        assert "Troops exchanged fire" in text

    def test_article_text_without_summary(self):
        from narad.pipeline.clusterer import _article_text
        text = _article_text("India border clash", None)
        assert text == "India border clash"

    def test_constants(self):
        from narad.pipeline.clusterer import ASSIGNMENT_THRESHOLD, CLUSTER_DISTANCE_THRESHOLD
        assert 0 < ASSIGNMENT_THRESHOLD < 1
        assert 0 < CLUSTER_DISTANCE_THRESHOLD < 1

    @pytest.mark.asyncio
    async def test_create_event_for_articles(self, db_session):
        from narad.pipeline.clusterer import _create_event_for_articles
        src = make_source()
        db_session.add(src)
        await db_session.flush()

        articles = [
            make_article(src.id, "Article One", "https://a.com/1",
                         published_at=datetime(2025, 1, 1, tzinfo=timezone.utc)),
            make_article(src.id, "Article Two", "https://b.com/2",
                         published_at=datetime(2025, 1, 2, tzinfo=timezone.utc)),
        ]
        for a in articles:
            db_session.add(a)
        await db_session.flush()

        event = await _create_event_for_articles(db_session, articles)
        assert event.id is not None
        assert event.title == "Article One"  # earliest article's title
        assert event.article_count == 2

    @pytest.mark.asyncio
    async def test_full_clustering_run(self, patched_session):
        """End-to-end clustering: creates events from unclustered articles."""
        from narad.pipeline.clusterer import run_clustering
        factory = patched_session

        async with factory() as session:
            src = make_source()
            session.add(src)
            await session.flush()

            # Add articles about the same topic
            for i, title in enumerate([
                "India and China hold border talks at LAC",
                "India China border discussions continue in Ladakh",
                "Diplomatic talks between India and China on LAC dispute",
            ]):
                session.add(make_article(
                    src.id, title, f"https://src{i}.com/a",
                    published_at=datetime.now(timezone.utc) - timedelta(hours=1),
                ))
            await session.commit()

        await run_clustering()

        async with factory() as session:
            events = (await session.execute(select(Event))).scalars().all()
            assert len(events) >= 1  # articles should cluster into at least one event
            links = (await session.execute(select(EventArticle))).scalars().all()
            assert len(links) == 3  # all 3 articles assigned


# ═══════════════════════════════════════════
# Graph Builder (edge detection)
# ═══════════════════════════════════════════

class TestGraphBuilder:
    def test_shared_entity_edge(self):
        now = datetime.now(timezone.utc)
        event_a = Event(
            id=1, title="A", category="conflict",
            first_seen_at=now, last_updated_at=now,
            article_count=2, source_count=1,
        )
        event_b = Event(
            id=2, title="B", category="conflict",
            first_seen_at=now, last_updated_at=now,
            article_count=2, source_count=1,
        )
        entities = {
            1: [{"name": "India"}, {"name": "China"}],
            2: [{"name": "China"}, {"name": "Pakistan"}],
        }
        edges = _find_edges(event_a, event_b, entities)
        shared_edges = [e for e in edges if e["type"] == "shared_entity"]
        assert len(shared_edges) == 1
        assert "china" in shared_edges[0]["shared"]

    def test_no_shared_entities(self):
        now = datetime.now(timezone.utc)
        event_a = Event(id=1, title="A", category="conflict", first_seen_at=now, last_updated_at=now, article_count=1, source_count=1)
        event_b = Event(id=2, title="B", category="economy", first_seen_at=now, last_updated_at=now, article_count=1, source_count=1)
        entities = {
            1: [{"name": "India"}],
            2: [{"name": "Brazil"}],
        }
        edges = _find_edges(event_a, event_b, entities)
        shared_edges = [e for e in edges if e["type"] == "shared_entity"]
        assert len(shared_edges) == 0

    def test_temporal_edge_same_category(self):
        now = datetime.now(timezone.utc)
        event_a = Event(id=1, title="A", category="conflict", first_seen_at=now, last_updated_at=now, article_count=1, source_count=1)
        event_b = Event(id=2, title="B", category="conflict", first_seen_at=now + timedelta(hours=6), last_updated_at=now, article_count=1, source_count=1)
        entities = {1: [], 2: []}
        edges = _find_edges(event_a, event_b, entities)
        temporal = [e for e in edges if e["type"] == "temporal"]
        assert len(temporal) == 1
        assert temporal[0]["weight"] > 0.3

    def test_no_temporal_edge_different_category(self):
        now = datetime.now(timezone.utc)
        event_a = Event(id=1, title="A", category="conflict", first_seen_at=now, last_updated_at=now, article_count=1, source_count=1)
        event_b = Event(id=2, title="B", category="economy", first_seen_at=now + timedelta(hours=1), last_updated_at=now, article_count=1, source_count=1)
        entities = {1: [], 2: []}
        edges = _find_edges(event_a, event_b, entities)
        temporal = [e for e in edges if e["type"] == "temporal"]
        assert len(temporal) == 0

    def test_temporal_edge_too_far_apart(self):
        now = datetime.now(timezone.utc)
        event_a = Event(id=1, title="A", category="conflict", first_seen_at=now, last_updated_at=now, article_count=1, source_count=1)
        event_b = Event(id=2, title="B", category="conflict", first_seen_at=now + timedelta(hours=48), last_updated_at=now, article_count=1, source_count=1)
        entities = {1: [], 2: []}
        edges = _find_edges(event_a, event_b, entities)
        temporal = [e for e in edges if e["type"] == "temporal"]
        assert len(temporal) == 0

    @pytest.mark.asyncio
    async def test_full_graph_build(self, patched_session):
        from narad.pipeline.graph_builder import build_relationships
        factory = patched_session

        now = datetime.now(timezone.utc)
        async with factory() as session:
            e1 = make_event(
                title="India-China LAC standoff",
                entities_json=json.dumps([{"name": "India"}, {"name": "China"}]),
            )
            e2 = make_event(
                title="China naval exercises in SCS",
                entities_json=json.dumps([{"name": "China"}, {"name": "Philippines"}]),
            )
            session.add_all([e1, e2])
            await session.commit()

        await build_relationships()

        async with factory() as session:
            rels = (await session.execute(select(EventRelationship))).scalars().all()
            assert len(rels) >= 1  # shared "China" entity
            shared_rel = [r for r in rels if r.relationship_type == "shared_entity"]
            assert len(shared_rel) == 1
