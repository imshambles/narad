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
            .where(Signal.signal_type.in_(["thermal_anomaly", "aircraft_activity", "naval_activity", "vessel_tracking"]))
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
    Fetch live vessel positions via AISStream.io websocket.
    Opens a connection, subscribes to our monitored zones, collects
    position reports for ~15 seconds, then stores as signals.
    Falls back to simulation if no API key configured.
    """
    from narad.config import settings
    api_key = settings.aisstream_api_key
    if not api_key:
        logger.debug("AIS: no aisstream_api_key configured, skipping live tracking")
        return 0

    # Our monitored bounding boxes: [[lat_min, lon_min], [lat_max, lon_max]]
    MARITIME_ZONES = {
        "strait_of_hormuz": {"bbox": [[24.0, 54.0], [28.0, 58.0]], "name": "Strait of Hormuz"},
        "gulf_of_aden":     {"bbox": [[10.0, 42.0], [16.0, 52.0]], "name": "Gulf of Aden"},
        "arabian_sea":      {"bbox": [[8.0, 55.0], [22.0, 72.0]], "name": "Arabian Sea"},
        "indian_ocean_w":   {"bbox": [[-5.0, 60.0], [12.0, 80.0]], "name": "Indian Ocean (West)"},
        "south_china_sea":  {"bbox": [[5.0, 108.0], [22.0, 122.0]], "name": "South China Sea"},
        "malacca":          {"bbox": [[-2.0, 98.0], [8.0, 106.0]], "name": "Malacca Strait"},
        "red_sea":          {"bbox": [[12.0, 32.0], [30.0, 44.0]], "name": "Red Sea"},
    }

    # AIS vessel type codes
    def classify_vessel(ship_type: int) -> str:
        if 80 <= ship_type < 90:
            return "tanker"
        elif 70 <= ship_type < 80:
            return "cargo"
        elif 60 <= ship_type < 70:
            return "passenger"
        elif 30 <= ship_type < 40:
            return "fishing"
        elif 50 <= ship_type < 60:
            return "military"
        return "other"

    def find_zone(lat, lon):
        for zid, zone in MARITIME_ZONES.items():
            bb = zone["bbox"]
            if bb[0][0] <= lat <= bb[1][0] and bb[0][1] <= lon <= bb[1][1]:
                return zid, zone["name"]
        return None, None

    signals = 0
    vessels_by_zone = {}  # zone_id → list of vessel dicts

    try:
        import websockets
        import asyncio

        # Build subscription for all zones
        bboxes = [zone["bbox"] for zone in MARITIME_ZONES.values()]

        subscribe_msg = json.dumps({
            "APIKey": api_key,
            "BoundingBoxes": bboxes,
            "FilterMessageTypes": ["PositionReport", "ShipStaticData"],
        })

        # Connect and collect for ~15 seconds
        async with websockets.connect("wss://stream.aisstream.io/v0/stream", close_timeout=5) as ws:
            await ws.send(subscribe_msg)

            deadline = asyncio.get_event_loop().time() + 15  # 15 second window
            seen_mmsi = set()

            while asyncio.get_event_loop().time() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=2)
                    msg = json.loads(raw)
                except (asyncio.TimeoutError, json.JSONDecodeError):
                    continue

                msg_type = msg.get("MessageType", "")
                meta = msg.get("MetaData", {})
                mmsi = meta.get("MMSI", 0)
                if not mmsi:
                    continue

                # Collect ship type from static data messages
                if msg_type == "ShipStaticData":
                    ssd = msg.get("Message", {}).get("ShipStaticData", {})
                    st = ssd.get("Type", 0) or 0
                    if st and mmsi not in seen_mmsi:
                        # Store type for later use
                        _ship_types = getattr(_fetch_ships, '_types', {})
                        _ship_types[mmsi] = st
                        _fetch_ships._types = _ship_types
                    continue

                if msg_type != "PositionReport":
                    continue

                if mmsi in seen_mmsi:
                    continue
                seen_mmsi.add(mmsi)

                pos = msg.get("Message", {}).get("PositionReport", {})
                lat = pos.get("Latitude", 0)
                lon = pos.get("Longitude", 0)
                if lat == 0 and lon == 0:
                    continue

                zone_id, zone_name = find_zone(lat, lon)
                if not zone_id:
                    continue

                # Get ship type from static data cache or metadata
                _ship_types = getattr(_fetch_ships, '_types', {})
                ship_type = _ship_types.get(mmsi, 0) or meta.get("ShipType", 0) or 0
                vtype = classify_vessel(ship_type)

                # Name-based classification fallback
                ship_name = (meta.get("ShipName") or "UNKNOWN").strip().upper()
                if vtype == "other":
                    name_lower = ship_name.lower()
                    if any(k in name_lower for k in ["mt ", "vlcc", "tanker", "crude", "oil", "lng", "lpg", "chemical"]):
                        vtype = "tanker"
                    elif any(k in name_lower for k in ["bulk", "cargo", "mv ", "carrier", "grain", "coal"]):
                        vtype = "cargo"
                    elif any(k in name_lower for k in ["msc ", "cosco", "maersk", "cma", "container", "express"]):
                        vtype = "container"
                    elif any(k in name_lower for k in ["navy", "warship", "patrol", "ins ", "hms ", "uss "]):
                        vtype = "military"

                vessel = {
                    "mmsi": mmsi,
                    "name": ship_name,
                    "type": vtype,
                    "lat": round(lat, 4),
                    "lon": round(lon, 4),
                    "speed": round(pos.get("Sog", 0), 1),
                    "heading": round(pos.get("TrueHeading", pos.get("Cog", 0)), 0),
                    "country": (meta.get("country_iso") or meta.get("Flag", "") or "").strip(),
                    "destination": (meta.get("Destination") or "").strip(),
                }

                if zone_id not in vessels_by_zone:
                    vessels_by_zone[zone_id] = []
                if len(vessels_by_zone[zone_id]) < 50:  # cap per zone
                    vessels_by_zone[zone_id].append(vessel)

        logger.info(f"AIS: collected {sum(len(v) for v in vessels_by_zone.values())} vessels across {len(vessels_by_zone)} zones")

    except Exception as e:
        logger.error(f"AISStream connection failed: {e}")
        return 0

    # Deactivate old vessel signals
    old_vessel = await session.execute(
        select(Signal)
        .where(Signal.signal_type == "vessel_tracking")
        .where(Signal.is_active == True)
    )
    for s in old_vessel.scalars().all():
        s.is_active = False

    # Store new signals
    for zone_id, vessels in vessels_by_zone.items():
        if not vessels:
            continue

        zone_name = MARITIME_ZONES.get(zone_id, {}).get("name", zone_id)
        type_counts = {}
        for v in vessels:
            type_counts[v["type"]] = type_counts.get(v["type"], 0) + 1

        tanker_count = type_counts.get("tanker", 0)
        military_count = type_counts.get("military", 0)
        severity = "low"
        if military_count >= 3:
            severity = "high"
        elif military_count >= 1 or tanker_count >= 15:
            severity = "medium"

        type_summary = ", ".join(f"{c} {t}" for t, c in sorted(type_counts.items(), key=lambda x: -x[1]))

        session.add(Signal(
            signal_type="vessel_tracking",
            title=f"{len(vessels)} vessels in {zone_name}",
            description=f"Live AIS: {type_summary}.",
            severity=severity,
            entity_ids_json=json.dumps([]),
            data_json=json.dumps({
                "zone": zone_id,
                "zone_name": zone_name,
                "vessel_count": len(vessels),
                "type_counts": type_counts,
                "vessels": vessels[:30],
                "type": "ais_live",
            }),
            detected_at=now,
            is_active=True,
        ))
        signals += 1

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
