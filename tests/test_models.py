"""
Tests for models, commodity intelligence, and briefing features:
- Model creation and relationships
- Commodity map matching and precedent lookup
- Briefing confidence fields
- Threat matrix history model
- Market data model
"""
import json
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from narad.models import (
    Source, Article, Event, EventArticle, Entity, EntityRelation,
    EntityMention, ThreatMatrix, ThreatMatrixHistory,
    MarketDataPoint, Signal, Briefing, FetchLog,
)
from narad.intel.commodity import COMMODITY_MAP, HISTORICAL_PRECEDENTS, find_precedents
from tests.conftest import (
    make_source, make_article, make_event, make_entity,
    make_signal, make_market_point,
)


# ═══════════════════════════════════════════
# Model Creation
# ═══════════════════════════════════════════

class TestModels:
    @pytest.mark.asyncio
    async def test_create_source(self, db_session):
        src = make_source("Reuters", "rss", "https://reuters.com/feed")
        db_session.add(src)
        await db_session.flush()
        assert src.id is not None
        assert src.is_active is True

    @pytest.mark.asyncio
    async def test_create_article(self, db_session):
        src = make_source()
        db_session.add(src)
        await db_session.flush()

        art = make_article(src.id, "India launches satellite")
        db_session.add(art)
        await db_session.flush()
        assert art.id is not None
        assert len(art.fingerprint) == 64

    @pytest.mark.asyncio
    async def test_article_source_relationship(self, db_session):
        src = make_source()
        db_session.add(src)
        await db_session.flush()

        art = make_article(src.id)
        db_session.add(art)
        await db_session.flush()

        result = await db_session.execute(
            select(Article).where(Article.source_id == src.id)
        )
        assert result.scalar_one_or_none() is not None

    @pytest.mark.asyncio
    async def test_create_event(self, db_session):
        event = make_event("India-China border talks", "diplomacy")
        db_session.add(event)
        await db_session.flush()
        assert event.id is not None
        assert event.is_active is True

    @pytest.mark.asyncio
    async def test_create_entity(self, db_session):
        entity = make_entity("India", "country")
        db_session.add(entity)
        await db_session.flush()
        assert entity.id is not None
        assert entity.canonical_name == "india"

    @pytest.mark.asyncio
    async def test_entity_relation(self, db_session):
        e1 = make_entity("India", "country", canonical_name="india")
        e2 = make_entity("China", "country", canonical_name="china")
        db_session.add_all([e1, e2])
        await db_session.flush()

        rel = EntityRelation(
            entity_a_id=e1.id, entity_b_id=e2.id,
            relation_type="conflict", weight=-0.5,
            co_occurrence_count=5,
            last_updated_at=datetime.now(timezone.utc),
        )
        db_session.add(rel)
        await db_session.flush()
        assert rel.id is not None

    @pytest.mark.asyncio
    async def test_threat_matrix_history_model(self, db_session):
        entity = make_entity("China", "country")
        db_session.add(entity)
        await db_session.flush()

        history = ThreatMatrixHistory(
            country_entity_id=entity.id,
            cooperation_score=0.3,
            tension_score=0.6,
            trend="cooling",
            snapshot_at=datetime.now(timezone.utc),
        )
        db_session.add(history)
        await db_session.flush()
        assert history.id is not None
        assert history.cooperation_score == 0.3

    @pytest.mark.asyncio
    async def test_signal_types(self, db_session):
        for sig_type in ["spike", "trend_shift", "new_entity", "thermal_anomaly",
                         "aircraft_activity", "vessel_tracking", "commodity",
                         "assessment", "correlation"]:
            sig = make_signal(signal_type=sig_type, title=f"Test {sig_type}")
            db_session.add(sig)
        await db_session.flush()

        result = await db_session.execute(select(Signal))
        signals = result.scalars().all()
        assert len(signals) == 9

    @pytest.mark.asyncio
    async def test_market_data_point(self, db_session):
        mp = make_market_point("BZ=F", 85.0, 3.5)
        db_session.add(mp)
        await db_session.flush()
        assert mp.id is not None
        assert mp.symbol == "BZ=F"
        assert mp.change_1d == 3.5

    @pytest.mark.asyncio
    async def test_briefing_model(self, db_session):
        briefing = Briefing(
            generated_at=datetime.now(timezone.utc),
            stories_json=json.dumps([{
                "headline": "Test Story",
                "confidence": "high",
                "confidence_reason": "3 wire sources",
                "evidence_chain": ["AP confirms", "GEOINT supports"],
            }]),
            connections_json=json.dumps([]),
            outlook_json=json.dumps({"next_24h": "Watch closely"}),
            is_current=True,
        )
        db_session.add(briefing)
        await db_session.flush()
        assert briefing.id is not None

        stories = json.loads(briefing.stories_json)
        assert stories[0]["confidence"] == "high"
        assert len(stories[0]["evidence_chain"]) == 2

    @pytest.mark.asyncio
    async def test_fetch_log(self, db_session):
        src = make_source()
        db_session.add(src)
        await db_session.flush()

        log = FetchLog(
            source_id=src.id,
            articles_found=10,
            articles_new=3,
            status="success",
        )
        db_session.add(log)
        await db_session.flush()
        assert log.id is not None


# ═══════════════════════════════════════════
# Commodity Intelligence
# ═══════════════════════════════════════════

class TestCommodityMap:
    def test_commodity_map_has_key_buckets(self):
        keys = list(COMMODITY_MAP.keys())
        # Should have entries for major scenarios
        all_keywords = " ".join(keys)
        assert "hormuz" in all_keywords
        assert "india_china" in all_keywords or "lac" in all_keywords
        assert "wheat" in all_keywords or "food" in all_keywords
        assert "sanctions" in all_keywords

    def test_each_bucket_has_required_fields(self):
        for trigger, bucket in COMMODITY_MAP.items():
            assert "name" in bucket, f"Missing 'name' in bucket {trigger}"
            assert "commodities" in bucket, f"Missing 'commodities' in bucket {trigger}"
            assert "stocks_india" in bucket, f"Missing 'stocks_india' in bucket {trigger}"

    def test_commodities_have_symbols(self):
        for trigger, bucket in COMMODITY_MAP.items():
            for comm in bucket["commodities"]:
                assert "symbol" in comm, f"Missing symbol in {bucket['name']}"
                assert "direction" in comm

    def test_stocks_india_have_names(self):
        for trigger, bucket in COMMODITY_MAP.items():
            for stock in bucket["stocks_india"]:
                assert "name" in stock
                assert "direction" in stock
                assert "reason" in stock

    def test_hormuz_bucket_complete(self):
        hormuz = COMMODITY_MAP["hormuz"]
        assert hormuz["name"] == "Strait of Hormuz Disruption"
        symbols = [c["symbol"] for c in hormuz["commodities"]]
        assert "BZ=F" in symbols
        assert "CL=F" in symbols
        stock_names = [s["name"] for s in hormuz["stocks_india"]]
        assert any("HAL" in n for n in stock_names)
        assert any("IOC" in n or "BPCL" in n for n in stock_names)


class TestHistoricalPrecedents:
    def test_precedents_exist(self):
        assert len(HISTORICAL_PRECEDENTS) >= 6

    def test_each_precedent_has_fields(self):
        for key, precs in HISTORICAL_PRECEDENTS.items():
            for p in precs:
                assert "event" in p
                assert "date" in p
                assert "impacts" in p
                for impact in p["impacts"]:
                    assert "name" in impact
                    assert "change" in impact

    def test_find_precedents_hormuz(self):
        matches = find_precedents("hormuz")
        assert len(matches) >= 1
        assert any("Soleimani" in m["event"] or "Tanker" in m["event"] for m in matches)

    def test_find_precedents_india_china(self):
        matches = find_precedents("india_china")
        assert len(matches) >= 1
        assert any("Galwan" in m["event"] or "Doklam" in m["event"] for m in matches)

    def test_find_precedents_no_match(self):
        matches = find_precedents("nonexistent_scenario")
        assert len(matches) == 0

    def test_find_precedents_max_3(self):
        matches = find_precedents("oil_price")
        assert len(matches) <= 3

    def test_find_precedents_pipe_separated(self):
        """Trigger keys with | should match across all sub-keys."""
        matches = find_precedents("oil_price|crude_surge")
        assert len(matches) >= 1


class TestCommoditySignalGeneration:
    def test_keyword_matching(self):
        """Verify the keyword matching logic works for event text."""
        text = "tensions rise at strait of hormuz as iran threatens shipping"
        triggered = []
        for trigger_keys, bucket in COMMODITY_MAP.items():
            keywords = trigger_keys.split("|")
            if any(kw in text for kw in keywords):
                triggered.append(bucket["name"])
        assert "Strait of Hormuz Disruption" in triggered

    def test_india_china_trigger(self):
        text = "india china lac standoff continues in ladakh"
        triggered = []
        for trigger_keys, bucket in COMMODITY_MAP.items():
            keywords = trigger_keys.split("|")
            if any(kw in text for kw in keywords):
                triggered.append(bucket["name"])
        assert "India-China Border Tension" in triggered

    def test_wheat_trigger(self):
        text = "global wheat shortage worsens food crisis"
        triggered = []
        for trigger_keys, bucket in COMMODITY_MAP.items():
            keywords = trigger_keys.split("|")
            if any(kw in text for kw in keywords):
                triggered.append(bucket["name"])
        assert "Global Food Supply Disruption" in triggered

    def test_sanctions_trigger(self):
        text = "new sanctions imposed on russian energy exports"
        triggered = []
        for trigger_keys, bucket in COMMODITY_MAP.items():
            keywords = trigger_keys.split("|")
            if any(kw in text for kw in keywords):
                triggered.append(bucket["name"])
        assert "Sanctions / Trade War" in triggered

    def test_no_false_positive(self):
        text = "india celebrates republic day with military parade"
        triggered = []
        for trigger_keys, bucket in COMMODITY_MAP.items():
            keywords = trigger_keys.split("|")
            if any(kw in text for kw in keywords):
                triggered.append(bucket["name"])
        # Should not trigger commodity signals
        assert len(triggered) == 0


# ═══════════════════════════════════════════
# Market Data Tracked Symbols
# ═══════════════════════════════════════════

class TestMarketDataConfig:
    def test_tracked_symbols(self):
        from narad.intel.market_data import TRACKED_SYMBOLS
        assert len(TRACKED_SYMBOLS) == 10
        # Key symbols for India geopolitical relevance
        assert "BZ=F" in TRACKED_SYMBOLS  # Brent crude
        assert "CL=F" in TRACKED_SYMBOLS  # WTI crude
        assert "GC=F" in TRACKED_SYMBOLS  # Gold
        assert "INR=X" in TRACKED_SYMBOLS  # USD/INR
        assert "^NSEI" in TRACKED_SYMBOLS  # Nifty 50

    def test_symbol_metadata(self):
        from narad.intel.market_data import TRACKED_SYMBOLS
        for symbol, meta in TRACKED_SYMBOLS.items():
            assert "name" in meta
            assert "category" in meta
            assert "unit" in meta
            assert meta["category"] in ("commodity", "forex", "index")


# ═══════════════════════════════════════════
# GEOINT Configuration
# ═══════════════════════════════════════════

class TestGeointConfig:
    def test_monitored_zones(self):
        from narad.intel.geospatial import ZONES
        assert len(ZONES) == 8
        assert "strait_of_hormuz" in ZONES
        assert "india_pakistan_border" in ZONES
        assert "india_china_ladakh" in ZONES
        assert "south_china_sea" in ZONES

    def test_zone_bboxes_valid(self):
        from narad.intel.geospatial import ZONES
        for zone_id, zone in ZONES.items():
            bbox = zone["bbox"]
            assert len(bbox) == 4
            assert bbox[0] < bbox[2], f"lat_min >= lat_max in {zone_id}"
            assert bbox[1] < bbox[3], f"lon_min >= lon_max in {zone_id}"

    def test_military_callsigns(self):
        from narad.intel.geospatial import MILITARY_CALLSIGNS
        assert "IAF" in MILITARY_CALLSIGNS  # Indian Air Force
        assert "PAF" in MILITARY_CALLSIGNS  # Pakistan Air Force
        assert "FORTE" in MILITARY_CALLSIGNS  # US surveillance
