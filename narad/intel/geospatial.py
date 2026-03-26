"""
Geospatial Intelligence (GEOINT)

Integrates:
1. NASA FIRMS — thermal anomalies (fires, explosions, artillery)
2. OpenSky ADS-B — aircraft tracking (military flights, surveillance)
3. AIS ship positions — naval movements, tanker rerouting
4. Sentinel-2 — satellite imagery change detection (future)

Monitors key zones relevant to India:
- Strait of Hormuz (oil route)
- South China Sea / Indian Ocean (Chinese navy)
- India-Pakistan border (LAC/LOC)
- India-China border (Ladakh/Arunachal)
- Middle East conflict zones
- Gulf of Aden (piracy, Houthi activity)
"""
import json
import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select, delete

from narad.database import async_session
from narad.models import Signal

logger = logging.getLogger(__name__)

# NASA FIRMS API key — loaded from env, fallback to registered key
import os
FIRMS_KEY = os.environ.get("FIRMS_API_KEY", "572bdd5a0d011133b86cabb69a3520b5")

# Monitored zones — bounding boxes [lat_min, lon_min, lat_max, lon_max]
ZONES = {
    "strait_of_hormuz": {"bbox": [24, 54, 28, 58], "name": "Strait of Hormuz", "threat": "oil_route"},
    "india_pakistan_border": {"bbox": [28, 66, 36, 78], "name": "India-Pakistan Border", "threat": "border"},
    "india_china_ladakh": {"bbox": [32, 76, 36, 80], "name": "Ladakh/LAC", "threat": "border"},
    "india_china_east": {"bbox": [26, 90, 30, 97], "name": "Arunachal/Eastern LAC", "threat": "border"},
    "middle_east": {"bbox": [25, 44, 38, 56], "name": "Middle East Conflict Zone", "threat": "conflict"},
    "gulf_of_aden": {"bbox": [10, 42, 16, 52], "name": "Gulf of Aden", "threat": "maritime"},
    "south_china_sea": {"bbox": [5, 108, 22, 122], "name": "South China Sea", "threat": "maritime"},
    "indian_ocean": {"bbox": [-5, 60, 15, 85], "name": "Indian Ocean", "threat": "maritime"},
}

# Known military-associated callsign prefixes
MILITARY_CALLSIGNS = {
    "IAF", "NAVY", "RCH", "FORTE", "JAKE", "DUKE", "DOOM",  # US military
    "CHN", "PLA",  # Chinese military
    "PAF",  # Pakistan Air Force
    "IFC", "INS",  # Indian military
    "RAF", "RRR",  # UK military
    "LAGR", "CASA",  # Various military
}


async def fetch_geoint() -> None:
    """Fetch all GEOINT sources and generate signals."""
    async with async_session() as session:
        now = datetime.now(timezone.utc)

        # Deactivate old GEOINT signals (older than 6h)
        old_cutoff = now - timedelta(hours=6)
        old_signals = await session.execute(
            select(Signal)
            .where(Signal.signal_type.in_(["thermal_anomaly", "aircraft_activity", "naval_activity"]))
            .where(Signal.detected_at < old_cutoff)
            .where(Signal.is_active == True)
        )
        for s in old_signals.scalars().all():
            s.is_active = False

        total_signals = 0

        # 1. NASA FIRMS — Thermal anomalies
        total_signals += await _fetch_firms(session, now)

        # 2. OpenSky ADS-B — Aircraft tracking
        total_signals += await _fetch_aircraft(session, now)

        # 3. AIS Ship tracking (via free sources)
        total_signals += await _fetch_ships(session, now)

        if total_signals:
            await session.commit()
        logger.info(f"GEOINT: {total_signals} new signals across all zones")


async def _fetch_firms(session, now: datetime) -> int:
    """Fetch NASA FIRMS thermal anomalies for monitored zones."""
    signals = 0
    async with httpx.AsyncClient(timeout=20) as client:
        for zone_id, zone in ZONES.items():
            bbox = zone["bbox"]
            # FIRMS API expects: W,S,E,N (lon_min,lat_min,lon_max,lat_max)
            bbox_str = f"{bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]}"
            url = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{FIRMS_KEY}/VIIRS_SNPP_NRT/{bbox_str}/1"

            try:
                resp = await client.get(url)
                if resp.status_code != 200:
                    continue

                lines = resp.text.strip().split("\n")
                fire_count = len(lines) - 1

                if fire_count <= 0:
                    continue

                # Parse FIRMS CSV for detailed analysis
                # Columns: latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight
                high_confidence = 0
                total_frp = 0  # Fire Radiative Power — higher = more intense
                max_frp = 0
                bright_spots = []  # high-intensity detections
                night_count = 0

                header = lines[0].split(",") if lines else []
                frp_idx = header.index("frp") if "frp" in header else -1
                conf_idx = header.index("confidence") if "confidence" in header else -1
                dn_idx = header.index("daynight") if "daynight" in header else -1
                lat_idx = header.index("latitude") if "latitude" in header else 0
                lon_idx = header.index("longitude") if "longitude" in header else 1
                bright_idx = header.index("bright_ti4") if "bright_ti4" in header else -1

                for line in lines[1:]:
                    parts = line.split(",")
                    try:
                        if conf_idx >= 0 and parts[conf_idx].strip().lower() in ("high", "h", "nominal", "n"):
                            high_confidence += 1
                        if frp_idx >= 0 and parts[frp_idx].strip():
                            frp = float(parts[frp_idx])
                            total_frp += frp
                            max_frp = max(max_frp, frp)
                            if frp > 50:  # High intensity
                                bright_spots.append({"lat": parts[lat_idx], "lon": parts[lon_idx], "frp": frp})
                        if dn_idx >= 0 and parts[dn_idx].strip().upper() == "N":
                            night_count += 1
                    except (ValueError, IndexError):
                        continue

                # Build meaningful description
                threat_type = zone["threat"]
                avg_frp = total_frp / fire_count if fire_count > 0 else 0

                # Interpret the data based on zone type
                if threat_type == "border":
                    if max_frp > 100 or night_count > fire_count * 0.5:
                        interpretation = "High-intensity nighttime detections near border — could indicate military exercises, artillery fire, or controlled burns. Requires monitoring."
                    elif fire_count < 10:
                        interpretation = "Low-level thermal activity — likely agricultural burning or small fires. No immediate concern."
                    else:
                        interpretation = "Elevated thermal activity near border region. Pattern could indicate troop movements with vehicle heat signatures or camp activity."
                elif threat_type == "conflict":
                    if max_frp > 200:
                        interpretation = "Very high intensity heat signatures detected — consistent with explosive ordnance, airstrikes, or large-scale fires in active conflict zone."
                    elif fire_count > 20:
                        interpretation = "Widespread thermal activity across conflict zone — could indicate ongoing military operations, infrastructure fires, or oil facility damage."
                    else:
                        interpretation = "Moderate thermal activity in conflict zone — possibly localized fighting, industrial fires, or oil burns."
                elif threat_type == "maritime":
                    interpretation = f"Thermal detections over maritime zone — could indicate vessel activity, offshore platform flaring, or naval exercises."
                else:
                    interpretation = "Thermal activity detected — requires correlation with news events to determine significance."

                # Only signal if significant
                if fire_count >= 5 or (threat_type == "border" and fire_count >= 2):
                    existing = await session.execute(
                        select(Signal)
                        .where(Signal.signal_type == "thermal_anomaly")
                        .where(Signal.is_active == True)
                        .where(Signal.data_json.contains(zone_id))
                        .where(Signal.detected_at >= now - timedelta(hours=3))
                        .limit(1)
                    )
                    if existing.scalar_one_or_none():
                        continue

                    severity = "low"
                    if threat_type == "border" and (fire_count >= 5 or max_frp > 100):
                        severity = "high"
                    elif fire_count >= 50 or max_frp > 200:
                        severity = "high"
                    elif fire_count >= 15 or max_frp > 50:
                        severity = "medium"

                    title = f"{fire_count} heat signatures in {zone['name']}"
                    if high_confidence > 0:
                        title += f" ({high_confidence} high-confidence)"

                    description = (
                        f"{interpretation} "
                        f"Details: {fire_count} detections, avg intensity {avg_frp:.0f} FRP, "
                        f"peak intensity {max_frp:.0f} FRP, "
                        f"{night_count} nighttime. "
                        f"{len(bright_spots)} high-intensity clusters detected."
                    )

                    session.add(Signal(
                        signal_type="thermal_anomaly",
                        title=title,
                        description=description,
                        severity=severity,
                        entity_ids_json=json.dumps([]),
                        data_json=json.dumps({
                            "zone": zone_id, "zone_name": zone["name"],
                            "fire_count": fire_count, "high_confidence": high_confidence,
                            "avg_frp": round(avg_frp, 1), "max_frp": round(max_frp, 1),
                            "night_count": night_count, "bright_spots": len(bright_spots),
                            "bbox": bbox, "type": "firms",
                            "interpretation": interpretation,
                        }),
                        detected_at=now,
                        is_active=True,
                    ))
                    signals += 1

            except Exception as e:
                logger.debug(f"FIRMS {zone_id}: {e}")

    return signals


async def _fetch_aircraft(session, now: datetime) -> int:
    """Fetch aircraft positions from OpenSky for monitored zones."""
    signals = 0
    async with httpx.AsyncClient(timeout=15) as client:
        for zone_id, zone in ZONES.items():
            bbox = zone["bbox"]
            url = f"https://opensky-network.org/api/states/all?lamin={bbox[0]}&lomin={bbox[1]}&lamax={bbox[2]}&lomax={bbox[3]}"

            try:
                resp = await client.get(url)
                if resp.status_code != 200:
                    continue

                data = resp.json()
                states = data.get("states", [])

                if not states:
                    continue

                # Identify military aircraft by callsign
                military_count = 0
                military_details = []
                total_aircraft = len(states)

                for s in states:
                    callsign = (s[1] or "").strip().upper()
                    country = s[2] or "unknown"
                    altitude = s[7] or 0
                    velocity = s[9] or 0

                    is_military = False
                    for prefix in MILITARY_CALLSIGNS:
                        if callsign.startswith(prefix):
                            is_military = True
                            break

                    # High altitude + high speed with no commercial callsign = possibly military
                    if not is_military and altitude > 10000 and not any(c.isdigit() for c in callsign[:3]):
                        is_military = True

                    if is_military:
                        military_count += 1
                        military_details.append({
                            "callsign": callsign,
                            "country": country,
                            "altitude_m": round(altitude),
                            "speed_ms": round(velocity),
                        })

                # Signal if unusual military activity
                if military_count >= 2 or (zone["threat"] == "border" and military_count >= 1):
                    existing = await session.execute(
                        select(Signal)
                        .where(Signal.signal_type == "aircraft_activity")
                        .where(Signal.is_active == True)
                        .where(Signal.data_json.contains(zone_id))
                        .where(Signal.detected_at >= now - timedelta(hours=2))
                        .limit(1)
                    )
                    if existing.scalar_one_or_none():
                        continue

                    severity = "medium" if military_count >= 3 else "low"
                    if zone["threat"] == "border":
                        severity = "high" if military_count >= 2 else "medium"

                    session.add(Signal(
                        signal_type="aircraft_activity",
                        title=f"{military_count} possible military aircraft over {zone['name']}",
                        description=f"OpenSky ADS-B shows {total_aircraft} aircraft in {zone['name']}, of which {military_count} have military-pattern callsigns or flight profiles. Callsigns: {', '.join(d['callsign'] for d in military_details[:5])}.",
                        severity=severity,
                        entity_ids_json=json.dumps([]),
                        data_json=json.dumps({
                            "zone": zone_id, "zone_name": zone["name"],
                            "total_aircraft": total_aircraft,
                            "military_count": military_count,
                            "details": military_details[:10],
                            "type": "adsb",
                        }),
                        detected_at=now,
                        is_active=True,
                    ))
                    signals += 1

            except Exception as e:
                logger.debug(f"OpenSky {zone_id}: {e}")

    return signals


async def _fetch_ships(session, now: datetime) -> int:
    """
    Monitor naval activity using free ship tracking data.
    Since free AIS APIs are limited, we use a proxy approach:
    count vessels in key straits/chokepoints via publicly available data.
    """
    signals = 0

    # For now, use the fact that we know about shipping from news events.
    # When the Strait of Hormuz is threatened, we can correlate with oil price movements.
    # Full AIS integration would require a registered API key from MarineTraffic or similar.

    # We CAN detect naval activity indirectly:
    # - FIRMS thermal data near ports = potential naval drills
    # - Aircraft activity over shipping lanes = maritime patrol
    # These are handled by the other two functions.

    # TODO: Integrate with free AIS data source when available
    # Candidates: AISHub (requires registration), UN Global Platform

    return signals


async def get_geoint_summary() -> dict:
    """Get current GEOINT status for display."""
    async with async_session() as session:
        now = datetime.now(timezone.utc)
        recent = now - timedelta(hours=12)

        thermal = await session.execute(
            select(Signal)
            .where(Signal.signal_type == "thermal_anomaly")
            .where(Signal.is_active == True)
            .where(Signal.detected_at >= recent)
            .order_by(Signal.detected_at.desc())
        )

        aircraft = await session.execute(
            select(Signal)
            .where(Signal.signal_type == "aircraft_activity")
            .where(Signal.is_active == True)
            .where(Signal.detected_at >= recent)
            .order_by(Signal.detected_at.desc())
        )

        return {
            "thermal": [
                {
                    "title": s.title, "severity": s.severity,
                    "data": json.loads(s.data_json or "{}"),
                    "detected_at": s.detected_at,
                }
                for s in thermal.scalars().all()
            ],
            "aircraft": [
                {
                    "title": s.title, "severity": s.severity,
                    "data": json.loads(s.data_json or "{}"),
                    "detected_at": s.detected_at,
                }
                for s in aircraft.scalars().all()
            ],
            "zones": list(ZONES.keys()),
            "zone_names": {k: v["name"] for k, v in ZONES.items()},
        }
