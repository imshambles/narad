import json

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from narad.database import get_session
from pydantic import BaseModel

from narad.models import Entity, EntityRelation, MarketDataPoint, Signal, ThreatMatrix, ThreatMatrixHistory

router = APIRouter(tags=["intel"])


class QueryRequest(BaseModel):
    question: str


@router.post("/intel/query")
async def query_narad(req: QueryRequest):
    from narad.intel.query import ask_narad
    return await ask_narad(req.question)


@router.get("/intel/market")
async def get_market_data(session: AsyncSession = Depends(get_session)):
    from narad.intel.market_data import get_latest_prices
    return await get_latest_prices()


@router.get("/intel/commodity")
async def get_commodity_signals(session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(Signal).where(Signal.signal_type == "commodity").where(Signal.is_active == True)
        .order_by(Signal.severity.desc(), Signal.detected_at.desc()).limit(10)
    )
    signals_out = []
    # Get current prices for live delta calculation
    current_prices = {}
    for sym in ["BZ=F", "CL=F", "GC=F", "ZW=F", "NG=F", "INR=X", "^NSEI"]:
        point = await session.execute(
            select(MarketDataPoint).where(MarketDataPoint.symbol == sym)
            .order_by(MarketDataPoint.fetched_at.desc()).limit(1)
        )
        p = point.scalar_one_or_none()
        if p:
            current_prices[sym] = p.price

    for s in result.scalars().all():
        data = json.loads(s.data_json or "{}")
        # Compute price change since signal was triggered
        price_deltas = {}
        pat = data.get("price_at_trigger", {})
        for sym, trigger_price in pat.items():
            if sym in current_prices and trigger_price:
                curr = current_prices[sym]
                delta_pct = ((curr - trigger_price) / trigger_price) * 100
                price_deltas[sym] = {
                    "trigger_price": trigger_price,
                    "current_price": curr,
                    "delta_pct": round(delta_pct, 2),
                }
        data["price_deltas"] = price_deltas
        signals_out.append({
            "title": s.title, "description": s.description, "severity": s.severity,
            "data": data, "detected_at": s.detected_at,
        })
    return signals_out


@router.get("/intel/market/history")
async def get_market_history(
    symbol: str = Query(..., description="Market symbol e.g. BZ=F"),
    limit: int = Query(48, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    """Return price history for sparkline rendering."""
    result = await session.execute(
        select(MarketDataPoint).where(MarketDataPoint.symbol == symbol)
        .order_by(MarketDataPoint.fetched_at.desc()).limit(limit)
    )
    points = list(result.scalars().all())
    points.reverse()  # chronological order
    return [
        {"price": p.price, "fetched_at": p.fetched_at}
        for p in points
    ]


@router.get("/intel/geoint")
async def get_geoint():
    from narad.intel.geospatial import get_geoint_summary
    return await get_geoint_summary()


@router.get("/intel/vessels")
async def get_vessels(session: AsyncSession = Depends(get_session)):
    """Get current vessel positions. Uses real AIS data if available, otherwise simulated."""
    # Check for real AIS signals first
    result = await session.execute(
        select(Signal)
        .where(Signal.signal_type == "vessel_tracking")
        .where(Signal.is_active == True)
        .order_by(Signal.detected_at.desc())
    )
    real_zones = []
    for s in result.scalars().all():
        data = json.loads(s.data_json or "{}")
        real_zones.append({
            "zone": data.get("zone"),
            "zone_name": data.get("zone_name"),
            "vessel_count": data.get("vessel_count", 0),
            "type_counts": data.get("type_counts", {}),
            "vessels": data.get("vessels", []),
            "detected_at": s.detected_at,
        })
    # Supplement with simulated vessels for zones without real AIS data
    from narad.intel.vessel_sim import generate_vessels
    sim_zones = generate_vessels()

    if real_zones:
        real_zone_ids = {z["zone"] for z in real_zones}
        # Add simulated data for zones we don't have real data for
        for sz in sim_zones:
            if sz["zone"] not in real_zone_ids:
                real_zones.append(sz)
        return real_zones

    return sim_zones


@router.get("/intel/entities")
async def list_entities(
    entity_type: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(Entity).order_by(Entity.mention_count.desc()).limit(limit)
    if entity_type:
        stmt = stmt.where(Entity.entity_type == entity_type)
    result = await session.execute(stmt)
    return [
        {
            "id": e.id, "name": e.name, "type": e.entity_type,
            "mentions": e.mention_count, "last_seen": e.last_seen_at,
        }
        for e in result.scalars().all()
    ]


@router.get("/intel/threat-matrix")
async def get_threat_matrix(session: AsyncSession = Depends(get_session)):
    stmt = select(ThreatMatrix).order_by(desc(ThreatMatrix.tension_score + ThreatMatrix.cooperation_score))
    result = await session.execute(stmt)
    entries = []
    for tm in result.scalars().all():
        country = await session.get(Entity, tm.country_entity_id)
        if not country:
            continue
        entries.append({
            "country": country.name,
            "country_id": country.id,
            "cooperation": round(tm.cooperation_score, 2),
            "tension": round(tm.tension_score, 2),
            "trend": tm.trend,
            "recent_events": json.loads(tm.recent_events_json or "[]"),
            "updated_at": tm.updated_at,
        })
    return entries


@router.get("/intel/threat-matrix/history")
async def get_threat_matrix_history(
    country_id: int | None = Query(None, description="Filter by country entity ID"),
    days: int = Query(7, ge=1, le=90),
    session: AsyncSession = Depends(get_session),
):
    """Return threat matrix trend data for sparkline/chart rendering."""
    from datetime import datetime, timedelta, timezone
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    stmt = (
        select(ThreatMatrixHistory)
        .where(ThreatMatrixHistory.snapshot_at >= cutoff)
        .order_by(ThreatMatrixHistory.snapshot_at.asc())
    )
    if country_id:
        stmt = stmt.where(ThreatMatrixHistory.country_entity_id == country_id)

    result = await session.execute(stmt)
    snapshots = result.scalars().all()

    # Group by country
    by_country = {}
    for s in snapshots:
        cid = s.country_entity_id
        if cid not in by_country:
            country = await session.get(Entity, cid)
            by_country[cid] = {
                "country_id": cid,
                "country": country.name if country else "Unknown",
                "points": [],
            }
        by_country[cid]["points"].append({
            "cooperation": round(s.cooperation_score, 3),
            "tension": round(s.tension_score, 3),
            "trend": s.trend,
            "time": s.snapshot_at,
        })

    return list(by_country.values())


@router.get("/intel/signals")
async def list_signals(
    active_only: bool = Query(True),
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(Signal).order_by(Signal.detected_at.desc()).limit(limit)
    if active_only:
        stmt = stmt.where(Signal.is_active == True)
    result = await session.execute(stmt)
    return [
        {
            "id": s.id, "type": s.signal_type, "title": s.title,
            "description": s.description, "severity": s.severity,
            "detected_at": s.detected_at, "data": json.loads(s.data_json or "{}"),
        }
        for s in result.scalars().all()
    ]


@router.get("/intel/entity-graph")
async def get_entity_graph(
    min_mentions: int = Query(3),
    session: AsyncSession = Depends(get_session),
):
    """Return entity nodes and relationship edges for visualization."""
    entities_stmt = select(Entity).where(Entity.mention_count >= min_mentions).order_by(Entity.mention_count.desc()).limit(100)
    result = await session.execute(entities_stmt)
    entities = list(result.scalars().all())
    entity_ids = {e.id for e in entities}

    nodes = [
        {"id": e.id, "name": e.name, "type": e.entity_type, "mentions": e.mention_count}
        for e in entities
    ]

    # Get relations between these entities
    relations_stmt = select(EntityRelation).where(
        EntityRelation.entity_a_id.in_(entity_ids),
        EntityRelation.entity_b_id.in_(entity_ids),
    )
    result = await session.execute(relations_stmt)
    edges = [
        {
            "source": r.entity_a_id, "target": r.entity_b_id,
            "type": r.relation_type, "weight": r.co_occurrence_count,
        }
        for r in result.scalars().all()
    ]

    return {"nodes": nodes, "edges": edges}
