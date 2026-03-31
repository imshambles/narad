"""
Cross-Domain Correlation Engine

Detects compound signals by correlating across domains:
- GEOINT (thermal, aircraft, vessels) + Events
- Market movements + Events
- Entity spikes + GEOINT
- Threat matrix shifts + Market moves

This is the layer that turns data aggregation into intelligence.
"""
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from narad.database import async_session
from narad.models import (
    Entity, EntityMention, Event, MarketDataPoint, Signal, ThreatMatrix,
)

logger = logging.getLogger(__name__)

# Correlation rules: each rule defines what to look for across domains
CORRELATION_RULES = [
    {
        "id": "hormuz_oil",
        "name": "Hormuz Disruption → Oil Spike",
        "description": "Thermal/military activity in Strait of Hormuz correlated with oil price surge",
        "geoint_zones": ["strait_of_hormuz"],
        "geoint_types": ["thermal_anomaly", "aircraft_activity", "vessel_tracking"],
        "market_symbols": ["BZ=F", "CL=F"],
        "market_threshold_pct": 2.0,
        "severity": "critical",
        "india_impact": "India imports ~85% of crude oil, ~40% transits Hormuz. Disruption directly impacts current account, fiscal deficit, and INR stability.",
    },
    {
        "id": "lac_tension_defense",
        "name": "LAC Activity → India Defense Posture",
        "description": "Military activity at India-China border zones correlated with entity mention spikes",
        "geoint_zones": ["india_china_ladakh", "india_china_east"],
        "geoint_types": ["thermal_anomaly", "aircraft_activity"],
        "entity_keywords": ["china", "india", "ladakh", "lac", "pla"],
        "severity": "high",
        "india_impact": "Direct border security threat. Escalation would impact India-China trade ($136B bilateral), QUAD alignment, and defense spending.",
    },
    {
        "id": "pak_border_escalation",
        "name": "India-Pakistan Border Escalation",
        "description": "Thermal/aircraft near LOC + entity spikes for Pakistan/India",
        "geoint_zones": ["india_pakistan_border"],
        "geoint_types": ["thermal_anomaly", "aircraft_activity"],
        "entity_keywords": ["pakistan", "india", "loc", "kashmir"],
        "severity": "high",
        "india_impact": "Direct security threat. Would impact bilateral trade, CPEC corridor, and regional stability.",
    },
    {
        "id": "gulf_aden_shipping",
        "name": "Gulf of Aden Maritime Threat → Supply Chain",
        "description": "Vessel/military activity in Gulf of Aden with commodity price moves",
        "geoint_zones": ["gulf_of_aden"],
        "geoint_types": ["vessel_tracking", "aircraft_activity", "thermal_anomaly"],
        "market_symbols": ["BZ=F", "CL=F", "NG=F"],
        "market_threshold_pct": 1.5,
        "severity": "high",
        "india_impact": "Houthi disruptions reroute India-bound tankers via Cape of Good Hope (+10 days). Insurance premiums spike, shipping costs rise.",
    },
    {
        "id": "gold_rush_geopolitical",
        "name": "Geopolitical Stress → Gold Surge",
        "description": "Multiple high-severity signals + gold price spike indicates flight to safety",
        "market_symbols": ["GC=F"],
        "market_threshold_pct": 2.0,
        "min_active_signals": 3,
        "severity": "medium",
        "india_impact": "India is world's 2nd largest gold consumer. Price surges impact current account, jewellery sector, and RBI reserves valuation.",
    },
    {
        "id": "inr_pressure",
        "name": "Multi-Factor INR Pressure",
        "description": "Oil spike + geopolitical tension + capital outflow signals = INR depreciation risk",
        "market_symbols": ["INR=X", "BZ=F"],
        "market_threshold_pct": 1.0,
        "entity_keywords": ["india", "rbi", "rupee"],
        "severity": "high",
        "india_impact": "Compound pressure on INR. RBI intervention likely, forex reserves draw-down. Import bill rises, fiscal deficit widens.",
    },
    {
        "id": "scs_maritime",
        "name": "South China Sea Escalation → Indo-Pacific",
        "description": "Military activity in SCS with diplomatic entity spikes",
        "geoint_zones": ["south_china_sea"],
        "geoint_types": ["aircraft_activity", "vessel_tracking"],
        "entity_keywords": ["china", "taiwan", "philippines", "quad", "aukus"],
        "severity": "high",
        "india_impact": "India's Act East policy and QUAD alignment mean SCS escalation pulls India into Indo-Pacific security architecture decisions.",
    },
]


async def run_correlations() -> None:
    """Check all correlation rules against current data."""
    async with async_session() as session:
        now = datetime.now(timezone.utc)
        lookback = now - timedelta(hours=6)

        # Load active GEOINT signals
        geoint_result = await session.execute(
            select(Signal)
            .where(Signal.signal_type.in_(["thermal_anomaly", "aircraft_activity", "vessel_tracking", "naval_activity"]))
            .where(Signal.is_active == True)
            .where(Signal.detected_at >= lookback)
        )
        active_geoint = list(geoint_result.scalars().all())

        # Load active non-GEOINT signals (spikes, sentiment shifts, etc.)
        other_signals_result = await session.execute(
            select(Signal)
            .where(Signal.signal_type.in_(["spike", "trend_shift", "new_entity"]))
            .where(Signal.is_active == True)
            .where(Signal.detected_at >= lookback)
        )
        active_other_signals = list(other_signals_result.scalars().all())

        # Load latest market data
        market_latest = {}
        for sym in ["BZ=F", "CL=F", "GC=F", "NG=F", "INR=X", "^NSEI", "^BSESN", "ZW=F"]:
            point = await session.execute(
                select(MarketDataPoint)
                .where(MarketDataPoint.symbol == sym)
                .order_by(MarketDataPoint.fetched_at.desc())
                .limit(1)
            )
            p = point.scalar_one_or_none()
            if p:
                market_latest[sym] = p

        total_active_signals = len(active_geoint) + len(active_other_signals)
        correlations_found = 0

        for rule in CORRELATION_RULES:
            # Check if we already have an active correlation signal for this rule
            existing = await session.execute(
                select(Signal)
                .where(Signal.signal_type == "correlation")
                .where(Signal.is_active == True)
                .where(Signal.data_json.contains(rule["id"]))
                .where(Signal.detected_at >= now - timedelta(hours=4))
                .limit(1)
            )
            if existing.scalar_one_or_none():
                continue

            triggered_factors = []

            # Check GEOINT match
            geoint_zones = rule.get("geoint_zones", [])
            geoint_types = rule.get("geoint_types", [])
            if geoint_zones:
                for sig in active_geoint:
                    data = json.loads(sig.data_json or "{}")
                    zone = data.get("zone", "")
                    if zone in geoint_zones and sig.signal_type in geoint_types:
                        triggered_factors.append({
                            "domain": "geoint",
                            "signal_id": sig.id,
                            "title": sig.title,
                            "severity": sig.severity,
                            "zone": zone,
                        })

            # Check market threshold
            market_symbols = rule.get("market_symbols", [])
            market_threshold = rule.get("market_threshold_pct", 999)
            for sym in market_symbols:
                mp = market_latest.get(sym)
                if mp and abs(mp.change_1d) >= market_threshold:
                    triggered_factors.append({
                        "domain": "market",
                        "symbol": sym,
                        "name": mp.name,
                        "price": mp.price,
                        "change_1d": mp.change_1d,
                    })

            # Check entity keyword spikes
            entity_keywords = rule.get("entity_keywords", [])
            if entity_keywords:
                for sig in active_other_signals:
                    sig_text = (sig.title + " " + sig.description).lower()
                    if any(kw in sig_text for kw in entity_keywords):
                        triggered_factors.append({
                            "domain": "entity_signal",
                            "signal_id": sig.id,
                            "title": sig.title,
                            "type": sig.signal_type,
                        })

            # Check minimum active signals threshold
            min_signals = rule.get("min_active_signals", 0)
            if min_signals and total_active_signals < min_signals:
                continue

            # Must have at least 2 cross-domain factors to be a correlation
            domains_hit = set(f["domain"] for f in triggered_factors)
            if len(domains_hit) < 2 and not (len(triggered_factors) >= 2 and geoint_zones):
                continue

            if not triggered_factors:
                continue

            # Generate correlation signal
            severity = rule["severity"]
            # Escalate if many factors
            if len(triggered_factors) >= 4:
                severity = "critical"
            elif len(triggered_factors) >= 3 and severity != "critical":
                severity = "high"

            domains_str = " + ".join(sorted(domains_hit)).upper()
            description = (
                f"{rule['description']}. "
                f"Triggered by {len(triggered_factors)} cross-domain factors ({domains_str}). "
                f"{rule['india_impact']}"
            )

            session.add(Signal(
                signal_type="correlation",
                title=f"COMPOUND: {rule['name']}",
                description=description,
                severity=severity,
                entity_ids_json=json.dumps([]),
                data_json=json.dumps({
                    "rule_id": rule["id"],
                    "rule_name": rule["name"],
                    "factors": triggered_factors,
                    "domains": list(domains_hit),
                    "factor_count": len(triggered_factors),
                    "india_impact": rule["india_impact"],
                }),
                detected_at=now,
                is_active=True,
            ))
            correlations_found += 1
            logger.info(f"Correlation triggered: {rule['name']} ({len(triggered_factors)} factors)")

        # Deactivate old correlation signals (older than 8h)
        old_corr = await session.execute(
            select(Signal)
            .where(Signal.signal_type == "correlation")
            .where(Signal.detected_at < now - timedelta(hours=8))
            .where(Signal.is_active == True)
        )
        for s in old_corr.scalars().all():
            s.is_active = False

        if correlations_found:
            await session.commit()
            logger.info(f"Correlator: {correlations_found} compound signals detected")

            # Send Telegram alerts and execute paper trades for correlation signals
            try:
                from narad.intel.alerts import send_alert_batch
                new_signals_result = await session.execute(
                    select(Signal)
                    .where(Signal.signal_type == "correlation")
                    .where(Signal.is_active == True)
                    .where(Signal.detected_at >= now - timedelta(minutes=2))
                )
                new_signals = list(new_signals_result.scalars().all())
                await send_alert_batch(new_signals)

                # Execute paper trades
                from narad.intel.trader import execute_signal_trades
                for sig in new_signals:
                    await execute_signal_trades(sig)
            except Exception as e:
                logger.debug(f"Alert/trade dispatch failed: {e}")
