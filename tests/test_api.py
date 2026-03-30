"""
Tests for API endpoints and the query interface:
- All /api/intel/* endpoints
- Threat matrix history endpoint
- Entity graph endpoint
- Ask Narad query building (without Gemini)
"""
import json
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select
from unittest.mock import patch, AsyncMock

from narad.models import (
    Entity, EntityRelation, MarketDataPoint, Signal,
    ThreatMatrix, ThreatMatrixHistory,
)
from tests.conftest import make_entity, make_signal, make_market_point


@pytest_asyncio.fixture
async def app_client(patched_session):
    """Create a test client with patched DB."""
    from narad.app import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def seeded_db(patched_session):
    """Seed the test DB with sample intel data."""
    factory = patched_session
    async with factory() as session:
        # Entities
        india = make_entity("India", "country", mention_count=50)
        china = make_entity("China", "country", mention_count=30)
        pak = make_entity("Pakistan", "country", mention_count=20)
        session.add_all([india, china, pak])
        await session.flush()

        # Entity relation
        session.add(EntityRelation(
            entity_a_id=india.id, entity_b_id=china.id,
            relation_type="conflict", co_occurrence_count=10,
            last_updated_at=datetime.now(timezone.utc),
        ))

        # Threat matrix
        session.add(ThreatMatrix(
            country_entity_id=china.id,
            cooperation_score=0.3, tension_score=0.6,
            trend="cooling",
            recent_events_json=json.dumps([{"title": "LAC standoff"}]),
            updated_at=datetime.now(timezone.utc),
        ))
        session.add(ThreatMatrix(
            country_entity_id=pak.id,
            cooperation_score=0.1, tension_score=0.8,
            trend="volatile",
            recent_events_json=json.dumps([{"title": "LOC firing"}]),
            updated_at=datetime.now(timezone.utc),
        ))

        # Threat matrix history
        for i in range(5):
            session.add(ThreatMatrixHistory(
                country_entity_id=china.id,
                cooperation_score=0.3 + i * 0.02,
                tension_score=0.6 - i * 0.01,
                trend="cooling",
                snapshot_at=datetime.now(timezone.utc) - timedelta(hours=i * 2),
            ))

        # Market data
        session.add(make_market_point("BZ=F", 85.0, 2.5))
        session.add(make_market_point("GC=F", 2050.0, 1.2))

        # Signals
        session.add(make_signal("spike", "India mentions surged 5x", "spike desc", "high"))
        session.add(make_signal("thermal_anomaly", "10 heat sigs in Hormuz",
                                "thermal desc", "medium",
                                json.dumps({"zone": "strait_of_hormuz"})))
        session.add(make_signal("correlation", "COMPOUND: Hormuz Oil",
                                "correlation desc", "critical",
                                json.dumps({"rule_id": "hormuz_oil", "factors": [], "domains": ["geoint", "market"]})))
        session.add(make_signal("assessment", "Strategic shift in Indo-Pacific",
                                "assessment desc", "high",
                                json.dumps({"strategic_warning": "Watch SCS", "relationship_insights": []})))

        await session.commit()
    return factory


# ═══════════════════════════════════════════
# API Endpoint Tests
# ═══════════════════════════════════════════

class TestMarketAPI:
    @pytest.mark.asyncio
    async def test_get_market_data(self, app_client, seeded_db):
        resp = await app_client.get("/api/intel/market")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)

    @pytest.mark.asyncio
    async def test_get_market_history(self, app_client, seeded_db):
        resp = await app_client.get("/api/intel/market/history?symbol=BZ=F&limit=10")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)


class TestThreatMatrixAPI:
    @pytest.mark.asyncio
    async def test_get_threat_matrix(self, app_client, seeded_db):
        resp = await app_client.get("/api/intel/threat-matrix")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 2
        entry = data[0]
        assert "country" in entry
        assert "cooperation" in entry
        assert "tension" in entry
        assert "trend" in entry

    @pytest.mark.asyncio
    async def test_get_threat_matrix_history(self, app_client, seeded_db):
        resp = await app_client.get("/api/intel/threat-matrix/history?days=7")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        country_data = data[0]
        assert "country" in country_data
        assert "points" in country_data
        assert len(country_data["points"]) >= 1
        point = country_data["points"][0]
        assert "cooperation" in point
        assert "tension" in point

    @pytest.mark.asyncio
    async def test_threat_matrix_history_filter_by_country(self, app_client, seeded_db):
        # Get china's entity ID first
        entities_resp = await app_client.get("/api/intel/entities?limit=50")
        entities = entities_resp.json()
        china = next((e for e in entities if e["name"] == "China"), None)
        assert china is not None

        resp = await app_client.get(f"/api/intel/threat-matrix/history?country_id={china['id']}&days=7")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["country"] == "China"


class TestSignalsAPI:
    @pytest.mark.asyncio
    async def test_list_signals(self, app_client, seeded_db):
        resp = await app_client.get("/api/intel/signals?limit=20")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 3
        sig = data[0]
        assert "type" in sig
        assert "title" in sig
        assert "severity" in sig

    @pytest.mark.asyncio
    async def test_signals_include_correlations(self, app_client, seeded_db):
        resp = await app_client.get("/api/intel/signals?limit=20")
        data = resp.json()
        types = {s["type"] for s in data}
        assert "correlation" in types


class TestEntityAPI:
    @pytest.mark.asyncio
    async def test_list_entities(self, app_client, seeded_db):
        resp = await app_client.get("/api/intel/entities?limit=50")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 3
        names = {e["name"] for e in data}
        assert "India" in names
        assert "China" in names

    @pytest.mark.asyncio
    async def test_filter_entities_by_type(self, app_client, seeded_db):
        resp = await app_client.get("/api/intel/entities?entity_type=country&limit=50")
        data = resp.json()
        for e in data:
            assert e["type"] == "country"

    @pytest.mark.asyncio
    async def test_entity_graph(self, app_client, seeded_db):
        resp = await app_client.get("/api/intel/entity-graph?min_mentions=5")
        assert resp.status_code == 200
        data = resp.json()
        assert "nodes" in data
        assert "edges" in data
        assert len(data["nodes"]) >= 2
        assert len(data["edges"]) >= 1
        edge = data["edges"][0]
        assert "source" in edge
        assert "target" in edge
        assert "type" in edge


class TestGeointAPI:
    @pytest.mark.asyncio
    async def test_get_geoint(self, app_client, seeded_db):
        resp = await app_client.get("/api/intel/geoint")
        assert resp.status_code == 200
        data = resp.json()
        assert "thermal" in data
        assert "aircraft" in data
        assert "zones" in data

    @pytest.mark.asyncio
    async def test_get_vessels(self, app_client, seeded_db):
        resp = await app_client.get("/api/intel/vessels")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)


class TestCommodityAPI:
    @pytest.mark.asyncio
    async def test_get_commodity_signals(self, app_client, seeded_db):
        resp = await app_client.get("/api/intel/commodity")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)


# ═══════════════════════════════════════════
# Query Interface (Ask Narad) — data gathering
# ═══════════════════════════════════════════

class TestQueryInterface:
    @pytest.mark.asyncio
    async def test_query_without_gemini_key(self, app_client, seeded_db):
        """Without a Gemini key, should return graceful error."""
        with patch("narad.intel.query.settings") as mock_settings:
            mock_settings.gemini_api_key = ""
            resp = await app_client.post(
                "/api/intel/query",
                json={"question": "What is happening with India-China?"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "answer" in data
            assert "not configured" in data["answer"].lower() or "confidence" in data

    @pytest.mark.asyncio
    async def test_query_endpoint_accepts_json(self, app_client, seeded_db):
        with patch("narad.intel.query.settings") as mock_settings:
            mock_settings.gemini_api_key = ""
            resp = await app_client.post(
                "/api/intel/query",
                json={"question": "test"},
            )
            assert resp.status_code == 200


# ═══════════════════════════════════════════
# Web Routes (smoke tests)
# ═══════════════════════════════════════════

class TestWebRoutes:
    @pytest.mark.asyncio
    async def test_briefing_page(self, app_client, seeded_db):
        resp = await app_client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    @pytest.mark.asyncio
    async def test_explore_page(self, app_client, seeded_db):
        resp = await app_client.get("/explore")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_feed_page(self, app_client, seeded_db):
        resp = await app_client.get("/feed")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_intel_page(self, app_client, seeded_db):
        resp = await app_client.get("/intel")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_admin_status_page(self, app_client, seeded_db):
        resp = await app_client.get("/admin/status")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_health_endpoint(self, app_client, seeded_db):
        resp = await app_client.get("/health")
        assert resp.status_code == 200
