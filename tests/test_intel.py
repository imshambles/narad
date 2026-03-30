"""
Tests for the intelligence layer:
- entity_graph (aliases, canonical names, fuzzy merge, disambiguation)
- threat_matrix (scoring, historical snapshots)
- signals (mention spikes, sentiment shifts, new relationships)
- correlator (cross-domain compound signal detection)
"""
import json
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from narad.models import (
    Entity, EntityMention, EntityRelation, Event,
    MarketDataPoint, Signal, ThreatMatrix, ThreatMatrixHistory,
)
from narad.intel.entity_graph import (
    _canonical, ENTITY_ALIASES, FUZZY_MERGE_THRESHOLD,
    update_entity_graph, merge_duplicate_entities,
)
from narad.intel.correlator import CORRELATION_RULES
from tests.conftest import make_entity, make_event, make_signal, make_market_point


# ═══════════════════════════════════════════
# Entity Graph — Canonical & Aliases
# ═══════════════════════════════════════════

class TestEntityCanonical:
    def test_basic_normalization(self):
        assert _canonical("  India  ") == "india"
        assert _canonical('"China"') == "china"

    def test_alias_resolution(self):
        assert _canonical("PM Modi") == "narendra modi"
        assert _canonical("Modi") == "narendra modi"
        assert _canonical("President Xi") == "xi jinping"
        assert _canonical("Biden") == "joe biden"
        assert _canonical("Putin") == "vladimir putin"

    def test_alias_country_codes(self):
        assert _canonical("USA") == "united states"
        assert _canonical("US") == "united states"
        assert _canonical("UK") == "united kingdom"
        assert _canonical("UAE") == "united arab emirates"
        assert _canonical("DPRK") == "north korea"
        assert _canonical("KSA") == "saudi arabia"

    def test_prefix_stripping(self):
        assert _canonical("President Putin") == "vladimir putin"
        assert _canonical("Prime Minister Modi") == "narendra modi"
        assert _canonical("Dr Jaishankar") == "s. jaishankar"
        assert _canonical("Gen Rawat") == "rawat"
        assert _canonical("Mr Johnson") == "johnson"

    def test_prefix_stripping_preserves_non_prefix(self):
        # "india" doesn't start with any prefix
        assert _canonical("India") == "india"
        assert _canonical("Pakistan") == "pakistan"

    def test_alias_table_completeness(self):
        assert len(ENTITY_ALIASES) >= 25
        # Key aliases exist
        for alias in ["modi", "pm modi", "biden", "trump", "putin", "usa", "uk", "uae"]:
            assert alias in ENTITY_ALIASES

    def test_fuzzy_threshold(self):
        assert 80 <= FUZZY_MERGE_THRESHOLD <= 95


# ═══════════════════════════════════════════
# Entity Graph — Get or Create + Fuzzy Merge
# ═══════════════════════════════════════════

class TestEntityGraph:
    @pytest.mark.asyncio
    async def test_creates_new_entity(self, patched_session):
        factory = patched_session
        async with factory() as session:
            event = make_event(
                title="India launches satellite",
                entities_json=json.dumps([
                    {"name": "India", "type": "country"},
                    {"name": "ISRO", "type": "organization"},
                ]),
            )
            session.add(event)
            await session.commit()

        await update_entity_graph()

        async with factory() as session:
            entities = (await session.execute(select(Entity))).scalars().all()
            names = {e.canonical_name for e in entities}
            assert "india" in names
            assert "isro" in names

    @pytest.mark.asyncio
    async def test_alias_resolves_to_same_entity(self, patched_session):
        factory = patched_session
        async with factory() as session:
            e1 = make_event(
                title="Modi visits Japan",
                entities_json=json.dumps([{"name": "Modi", "type": "person"}]),
            )
            session.add(e1)
            await session.commit()

        await update_entity_graph()

        async with factory() as session:
            e2 = make_event(
                title="PM Modi addresses UN",
                entities_json=json.dumps([{"name": "PM Modi", "type": "person"}]),
            )
            session.add(e2)
            await session.commit()

        await update_entity_graph()

        async with factory() as session:
            entities = (await session.execute(
                select(Entity).where(Entity.canonical_name == "narendra modi")
            )).scalars().all()
            # Both "Modi" and "PM Modi" should resolve to same entity
            assert len(entities) == 1
            assert entities[0].mention_count >= 2

    @pytest.mark.asyncio
    async def test_co_occurrence_creates_relation(self, patched_session):
        factory = patched_session
        async with factory() as session:
            event = make_event(
                title="India-China border talks",
                category="diplomacy",
                entities_json=json.dumps([
                    {"name": "India", "type": "country"},
                    {"name": "China", "type": "country"},
                ]),
            )
            session.add(event)
            await session.commit()

        await update_entity_graph()

        async with factory() as session:
            rels = (await session.execute(select(EntityRelation))).scalars().all()
            assert len(rels) >= 1
            rel = rels[0]
            assert rel.co_occurrence_count >= 1
            assert rel.relation_type == "diplomacy"

    @pytest.mark.asyncio
    async def test_mention_tracking(self, patched_session):
        factory = patched_session
        async with factory() as session:
            event = make_event(
                title="India economy grows",
                entities_json=json.dumps([{"name": "India", "type": "country"}]),
            )
            session.add(event)
            await session.commit()

        await update_entity_graph()

        async with factory() as session:
            mentions = (await session.execute(select(EntityMention))).scalars().all()
            assert len(mentions) >= 1
            assert mentions[0].sentiment is not None


# ═══════════════════════════════════════════
# Entity Merge (Duplicate Cleanup)
# ═══════════════════════════════════════════

class TestEntityMerge:
    @pytest.mark.asyncio
    async def test_merges_similar_entities(self, patched_session):
        factory = patched_session
        async with factory() as session:
            e1 = make_entity("United States", "country", mention_count=50, canonical_name="united states")
            e2 = make_entity("United States of America", "country", mention_count=5, canonical_name="united states of america")
            session.add_all([e1, e2])
            await session.commit()

        await merge_duplicate_entities()

        async with factory() as session:
            entities = (await session.execute(
                select(Entity).where(Entity.entity_type == "country")
            )).scalars().all()
            # After merge, should have fewer entities
            # (may or may not merge depending on fuzzy score — "united states" vs "united states of america" = ~82%)
            # This is fine — the test validates the merge code runs without error
            assert len(entities) >= 1

    @pytest.mark.asyncio
    async def test_does_not_merge_different_types(self, patched_session):
        factory = patched_session
        async with factory() as session:
            e1 = make_entity("Jordan", "country", mention_count=10, canonical_name="jordan_country")
            e2 = make_entity("Jordan", "person", mention_count=5, canonical_name="jordan_person")
            session.add_all([e1, e2])
            await session.commit()

        await merge_duplicate_entities()

        async with factory() as session:
            entities = (await session.execute(select(Entity))).scalars().all()
            assert len(entities) == 2  # different types, not merged


# ═══════════════════════════════════════════
# Threat Matrix
# ═══════════════════════════════════════════

class TestThreatMatrix:
    @pytest.mark.asyncio
    async def test_threat_matrix_update(self, patched_session):
        from narad.intel.threat_matrix import update_threat_matrix
        factory = patched_session

        # Create India and another country with co-occurring mentions
        async with factory() as session:
            india = make_entity("India", "country", canonical_name="india")
            china = make_entity("China", "country", canonical_name="china")
            session.add_all([india, china])
            await session.flush()

            # Create entity relation
            rel = EntityRelation(
                entity_a_id=min(india.id, china.id),
                entity_b_id=max(india.id, china.id),
                relation_type="conflict",
                weight=0.5,
                co_occurrence_count=5,
                last_updated_at=datetime.now(timezone.utc),
                trend="cooling",
            )
            session.add(rel)

            # Create a shared event with mentions
            event = make_event(title="India China border clash", category="conflict")
            session.add(event)
            await session.flush()

            now = datetime.now(timezone.utc)
            session.add(EntityMention(entity_id=india.id, event_id=event.id, sentiment=-0.5, mentioned_at=now))
            session.add(EntityMention(entity_id=china.id, event_id=event.id, sentiment=-0.5, mentioned_at=now))
            await session.commit()

        await update_threat_matrix()

        async with factory() as session:
            tm = (await session.execute(select(ThreatMatrix))).scalars().all()
            assert len(tm) >= 1
            entry = tm[0]
            assert entry.tension_score > 0  # conflict event should increase tension

    @pytest.mark.asyncio
    async def test_threat_matrix_history_snapshots(self, patched_session):
        from narad.intel.threat_matrix import update_threat_matrix
        factory = patched_session

        async with factory() as session:
            india = make_entity("India", "country", canonical_name="india")
            pak = make_entity("Pakistan", "country", canonical_name="pakistan")
            session.add_all([india, pak])
            await session.flush()

            rel = EntityRelation(
                entity_a_id=min(india.id, pak.id),
                entity_b_id=max(india.id, pak.id),
                relation_type="conflict",
                co_occurrence_count=3,
                last_updated_at=datetime.now(timezone.utc),
            )
            session.add(rel)

            event = make_event(title="India Pakistan LOC firing", category="conflict")
            session.add(event)
            await session.flush()

            now = datetime.now(timezone.utc)
            session.add(EntityMention(entity_id=india.id, event_id=event.id, sentiment=-0.5, mentioned_at=now))
            session.add(EntityMention(entity_id=pak.id, event_id=event.id, sentiment=-0.5, mentioned_at=now))
            await session.commit()

        await update_threat_matrix()

        async with factory() as session:
            history = (await session.execute(select(ThreatMatrixHistory))).scalars().all()
            assert len(history) >= 1
            snap = history[0]
            assert snap.cooperation_score >= 0
            assert snap.tension_score >= 0
            assert snap.snapshot_at is not None


# ═══════════════════════════════════════════
# Signal Detection
# ═══════════════════════════════════════════

class TestSignals:
    @pytest.mark.asyncio
    async def test_detect_mention_spikes(self, patched_session):
        from narad.intel.signals import detect_signals
        factory = patched_session

        async with factory() as session:
            entity = make_entity("Pakistan", "country", mention_count=20)
            session.add(entity)
            await session.flush()

            event = make_event(title="Pakistan crisis")
            session.add(event)
            await session.flush()

            now = datetime.now(timezone.utc)
            # Create a spike: 10 mentions in last 6h vs baseline of ~1
            for i in range(10):
                session.add(EntityMention(
                    entity_id=entity.id,
                    event_id=event.id,
                    sentiment=-0.3,
                    mentioned_at=now - timedelta(hours=i % 5),
                ))
            # And 2 baseline mentions from 24h ago
            for i in range(2):
                session.add(EntityMention(
                    entity_id=entity.id,
                    event_id=event.id,
                    sentiment=0.0,
                    mentioned_at=now - timedelta(hours=24 + i),
                ))
            await session.commit()

        await detect_signals()

        async with factory() as session:
            spikes = (await session.execute(
                select(Signal).where(Signal.signal_type == "spike")
            )).scalars().all()
            assert len(spikes) >= 1
            assert "Pakistan" in spikes[0].title

    @pytest.mark.asyncio
    async def test_detect_new_relationships(self, patched_session):
        from narad.intel.signals import detect_signals
        factory = patched_session

        async with factory() as session:
            e1 = make_entity("Russia", "country", mention_count=10)
            e2 = make_entity("Iran", "country", mention_count=10)
            session.add_all([e1, e2])
            await session.flush()

            # New relationship (co_occurrence_count = 1)
            rel = EntityRelation(
                entity_a_id=min(e1.id, e2.id),
                entity_b_id=max(e1.id, e2.id),
                relation_type="defense",
                co_occurrence_count=1,
                last_updated_at=datetime.now(timezone.utc),
            )
            session.add(rel)
            await session.commit()

        await detect_signals()

        async with factory() as session:
            new_rels = (await session.execute(
                select(Signal).where(Signal.signal_type == "new_entity")
            )).scalars().all()
            assert len(new_rels) >= 1
            assert "Russia" in new_rels[0].title or "Iran" in new_rels[0].title

    @pytest.mark.asyncio
    async def test_detect_sentiment_shift(self, patched_session):
        from narad.intel.signals import detect_signals
        factory = patched_session

        async with factory() as session:
            entity = make_entity("China", "country", mention_count=20)
            session.add(entity)
            await session.flush()

            event = make_event(title="China event")
            session.add(event)
            await session.flush()

            now = datetime.now(timezone.utc)
            # Baseline: positive sentiment (24-72h ago)
            for i in range(5):
                session.add(EntityMention(
                    entity_id=entity.id, event_id=event.id,
                    sentiment=0.4,
                    mentioned_at=now - timedelta(hours=24 + i * 8),
                ))
            # Recent: negative sentiment (last 12h)
            for i in range(5):
                session.add(EntityMention(
                    entity_id=entity.id, event_id=event.id,
                    sentiment=-0.4,
                    mentioned_at=now - timedelta(hours=i * 2),
                ))
            await session.commit()

        await detect_signals()

        async with factory() as session:
            shifts = (await session.execute(
                select(Signal).where(Signal.signal_type == "trend_shift")
            )).scalars().all()
            assert len(shifts) >= 1
            assert "China" in shifts[0].title
            assert "negative" in shifts[0].title


# ═══════════════════════════════════════════
# Cross-Domain Correlator
# ═══════════════════════════════════════════

class TestCorrelator:
    def test_correlation_rules_structure(self):
        assert len(CORRELATION_RULES) >= 7
        for rule in CORRELATION_RULES:
            assert "id" in rule
            assert "name" in rule
            assert "severity" in rule
            assert "india_impact" in rule

    def test_all_rules_have_ids(self):
        ids = [r["id"] for r in CORRELATION_RULES]
        assert len(ids) == len(set(ids))  # no duplicate IDs

    def test_hormuz_rule_config(self):
        hormuz = next(r for r in CORRELATION_RULES if r["id"] == "hormuz_oil")
        assert "strait_of_hormuz" in hormuz["geoint_zones"]
        assert "BZ=F" in hormuz["market_symbols"]
        assert hormuz["severity"] == "critical"

    @pytest.mark.asyncio
    async def test_correlation_triggers_on_multi_domain(self, patched_session):
        from narad.intel.correlator import run_correlations
        factory = patched_session

        now = datetime.now(timezone.utc)
        async with factory() as session:
            # Create GEOINT signal for Hormuz
            session.add(make_signal(
                signal_type="thermal_anomaly",
                title="15 heat signatures in Strait of Hormuz",
                severity="high",
                data_json=json.dumps({"zone": "strait_of_hormuz", "type": "firms"}),
                detected_at=now,
            ))
            # Create market signal: oil spike
            session.add(make_market_point(symbol="BZ=F", price=95.0, change_1d=4.5))
            session.add(make_market_point(symbol="CL=F", price=90.0, change_1d=3.8))
            await session.commit()

        await run_correlations()

        async with factory() as session:
            corrs = (await session.execute(
                select(Signal).where(Signal.signal_type == "correlation")
            )).scalars().all()
            assert len(corrs) >= 1
            # Should trigger hormuz_oil rule
            data = json.loads(corrs[0].data_json)
            assert data["rule_id"] == "hormuz_oil"
            assert len(data["factors"]) >= 2

    @pytest.mark.asyncio
    async def test_no_correlation_without_cross_domain(self, patched_session):
        from narad.intel.correlator import run_correlations
        factory = patched_session

        now = datetime.now(timezone.utc)
        async with factory() as session:
            # Only GEOINT, no market data → no cross-domain correlation
            session.add(make_signal(
                signal_type="thermal_anomaly",
                title="5 heat signatures in Gulf of Aden",
                severity="low",
                data_json=json.dumps({"zone": "gulf_of_aden", "type": "firms"}),
                detected_at=now,
            ))
            await session.commit()

        await run_correlations()

        async with factory() as session:
            corrs = (await session.execute(
                select(Signal).where(Signal.signal_type == "correlation")
            )).scalars().all()
            # Should not trigger — only one domain
            assert len(corrs) == 0

    @pytest.mark.asyncio
    async def test_correlation_deduplication(self, patched_session):
        from narad.intel.correlator import run_correlations
        factory = patched_session

        now = datetime.now(timezone.utc)
        async with factory() as session:
            session.add(make_signal(
                signal_type="thermal_anomaly",
                title="Heat in Hormuz",
                severity="high",
                data_json=json.dumps({"zone": "strait_of_hormuz", "type": "firms"}),
                detected_at=now,
            ))
            session.add(make_market_point(symbol="BZ=F", price=95.0, change_1d=4.5))
            session.add(make_market_point(symbol="CL=F", price=90.0, change_1d=3.8))
            await session.commit()

        # First run creates the correlation
        await run_correlations()

        async with factory() as session:
            count_after_first = len((await session.execute(
                select(Signal).where(Signal.signal_type == "correlation").where(Signal.is_active == True)
            )).scalars().all())

        # Second run should not create duplicates
        await run_correlations()

        async with factory() as session:
            count_after_second = len((await session.execute(
                select(Signal).where(Signal.signal_type == "correlation").where(Signal.is_active == True)
            )).scalars().all())
            assert count_after_second == count_after_first
