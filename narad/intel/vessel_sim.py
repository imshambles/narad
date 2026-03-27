"""
Vessel simulation along known shipping lanes.

Generates realistic vessel positions based on:
- Known shipping lane routes (from map_data.json)
- Published daily traffic volumes per route
- Vessel type distributions per commodity corridor

This provides useful visual data until a real AIS feed is integrated.
When a real AIS API key is available (AISStream, MarineTraffic, Datalastic),
the _fetch_ships() in geospatial.py should be updated to use it.
"""
import json
import math
import random
import hashlib
from pathlib import Path
from datetime import datetime

import logging
logger = logging.getLogger(__name__)

# Vessel type distributions per commodity
VESSEL_MIX = {
    "crude_oil": {"tanker": 0.7, "cargo": 0.15, "other": 0.15},
    "oil_lng":   {"tanker": 0.5, "cargo": 0.2, "other": 0.3},
    "lng":       {"tanker": 0.8, "cargo": 0.1, "other": 0.1},
    "coal_lng":  {"cargo": 0.6, "tanker": 0.3, "other": 0.1},
    "grain":     {"cargo": 0.8, "tanker": 0.05, "other": 0.15},
    "iron_soy":  {"cargo": 0.85, "other": 0.15},
    "mixed":     {"cargo": 0.4, "tanker": 0.2, "container": 0.3, "other": 0.1},
}

# Vessel name prefixes by type
NAME_PREFIXES = {
    "tanker":    ["MT ", "VLCC ", "SUEZMAX ", "AFRAMAX "],
    "cargo":     ["MV ", "BULK ", ""],
    "container": ["MSC ", "COSCO ", "CMA CGM "],
    "other":     ["FV ", "SV ", ""],
}

# Common vessel names (will be combined with prefixes)
VESSEL_NAMES = [
    "STAR", "FORTUNE", "GLORY", "HARMONY", "PIONEER", "SPIRIT",
    "PROGRESS", "EAGLE", "PHOENIX", "VICTORY", "PACIFIC", "ATLANTIC",
    "EMERALD", "DIAMOND", "RUBY", "SAPPHIRE", "PEARL", "AMBER",
    "JADE", "CORAL", "VOYAGER", "NAVIGATOR", "ENDEAVOUR", "RESOLVE",
    "HORIZON", "MERIDIAN", "AURORA", "ZENITH", "APEX", "SUMMIT",
    "ASCENT", "VENTURE", "TRANSIT", "LIBERTY", "DIGNITY", "GRACE",
]

FLAGS = ["PA", "LR", "MH", "HK", "SG", "BS", "MT", "CY", "GR", "NO", "IN", "CN", "JP", "KR", "AE"]

DESTINATIONS = {
    "crude_oil": ["JAMNAGAR", "MUMBAI", "PARADIP", "SINGAPORE", "FUJAIRAH", "RAS TANURA", "BASRAH"],
    "oil_lng":   ["DAHEJ", "KOCHI", "HAZIRA", "ENNORE", "SINGAPORE"],
    "lng":       ["DAHEJ", "HAZIRA", "DABHOL", "ENNORE", "TOKYO", "INCHEON"],
    "coal_lng":  ["MUNDRA", "KRISHNAPATNAM", "GANGAVARAM", "PARADIP"],
    "grain":     ["KANDLA", "MUMBAI", "KARACHI", "CHENNAI", "COLOMBO"],
    "iron_soy":  ["SHANGHAI", "QINGDAO", "NAGOYA", "POHANG"],
    "mixed":     ["SINGAPORE", "COLOMBO", "DUBAI", "MUMBAI", "CHENNAI"],
}


def _stable_random(seed_str: str) -> random.Random:
    """Create a deterministic RNG from a string seed, changes hourly."""
    hour_key = datetime.utcnow().strftime("%Y-%m-%d-%H")
    seed = int(hashlib.md5(f"{seed_str}:{hour_key}".encode()).hexdigest()[:8], 16)
    return random.Random(seed)


def _interpolate_along_route(points: list, t: float) -> tuple:
    """Get position at fraction t (0-1) along a polyline route."""
    if not points or len(points) < 2:
        return (0, 0)
    t = max(0, min(1, t))

    # Calculate total distance
    segments = []
    total = 0
    for i in range(len(points) - 1):
        d = math.sqrt((points[i+1][0] - points[i][0])**2 + (points[i+1][1] - points[i][1])**2)
        segments.append(d)
        total += d

    if total == 0:
        return tuple(points[0])

    target = t * total
    running = 0
    for i, seg_len in enumerate(segments):
        if running + seg_len >= target:
            frac = (target - running) / seg_len if seg_len > 0 else 0
            lat = points[i][0] + frac * (points[i+1][0] - points[i][0])
            lon = points[i][1] + frac * (points[i+1][1] - points[i][1])
            # Heading
            heading = math.degrees(math.atan2(points[i+1][1] - points[i][1], points[i+1][0] - points[i][0]))
            heading = (90 - heading) % 360  # convert to compass heading
            return (lat, lon, heading)
        running += seg_len

    return (points[-1][0], points[-1][1], 0)


def _parse_volume(vol_str: str) -> int:
    """Parse volume string to approximate daily vessel count."""
    if not vol_str:
        return 5
    vol_str = vol_str.lower()
    # Extract number
    import re
    m = re.search(r'([\d.]+)', vol_str)
    if not m:
        return 5
    num = float(m.group(1))
    # Rough mapping: M bbl/day → ~2 VLCCs per million, tonnage → ~1 vessel per 50k tons
    if 'bbl' in vol_str or 'barrel' in vol_str:
        return max(3, int(num * 2))
    elif 'mt' in vol_str or 'ton' in vol_str:
        return max(2, int(num * 1.5))
    elif 'bcm' in vol_str:
        return max(2, int(num * 3))
    return max(3, int(num))


def generate_vessels() -> list:
    """
    Generate realistic vessel positions along known shipping lanes.
    Returns list of zone dicts compatible with /api/intel/vessels format.
    """
    map_data_path = Path(__file__).parent.parent / "static" / "map_data.json"
    try:
        with open(map_data_path) as f:
            map_data = json.load(f)
    except Exception as e:
        logger.error(f"Could not load map_data.json: {e}")
        return []

    lanes = map_data.get("shipping_lanes", [])
    if not lanes:
        return []

    all_vessels = []

    for lane in lanes:
        points = lane.get("points", [])
        if len(points) < 2:
            continue

        commodity = lane.get("commodity", "mixed")
        name = lane.get("name", "Unknown Route")
        volume = lane.get("volume", "")
        vessel_count = _parse_volume(volume)

        # Use stable random so vessels don't jump every refresh
        rng = _stable_random(name)

        # Get vessel type distribution
        mix = VESSEL_MIX.get(commodity, VESSEL_MIX["mixed"])
        dests = DESTINATIONS.get(commodity, DESTINATIONS["mixed"])
        name_pool = list(VESSEL_NAMES)
        rng.shuffle(name_pool)

        for i in range(vessel_count):
            # Pick vessel type
            r = rng.random()
            cumulative = 0
            vtype = "cargo"
            for t, prob in mix.items():
                cumulative += prob
                if r <= cumulative:
                    vtype = t
                    break

            # Position along route (spread evenly + jitter)
            t = (i / vessel_count) + rng.uniform(-0.05, 0.05)
            t = max(0.02, min(0.98, t))
            pos = _interpolate_along_route(points, t)
            if not pos or pos[0] == 0:
                continue

            lat, lon = pos[0], pos[1]
            heading = pos[2] if len(pos) > 2 else 0

            # Add realistic jitter (vessels aren't exactly on the line)
            lat += rng.uniform(-0.3, 0.3)
            lon += rng.uniform(-0.3, 0.3)

            # Generate vessel details
            prefix = rng.choice(NAME_PREFIXES.get(vtype, [""]))
            vname = prefix + (name_pool[i % len(name_pool)] if i < len(name_pool) else f"V{rng.randint(100,999)}")

            all_vessels.append({
                "name": vname,
                "type": vtype,
                "lat": round(lat, 4),
                "lon": round(lon, 4),
                "speed": round(rng.uniform(8, 16), 1),
                "heading": round(heading + rng.uniform(-10, 10), 0) % 360,
                "country": rng.choice(FLAGS),
                "destination": rng.choice(dests),
                "lane": name,
                "commodity": commodity,
            })

    # Group by maritime zone for the API
    ZONE_BOXES = {
        "strait_of_hormuz": {"bbox": [23, 53, 28, 58], "name": "Strait of Hormuz"},
        "gulf_of_aden":     {"bbox": [10, 40, 16, 52], "name": "Gulf of Aden"},
        "arabian_sea":      {"bbox": [8, 55, 22, 72], "name": "Arabian Sea"},
        "indian_ocean_w":   {"bbox": [-5, 60, 10, 80], "name": "Indian Ocean (West)"},
        "bay_of_bengal":    {"bbox": [5, 78, 22, 95], "name": "Bay of Bengal"},
        "south_china_sea":  {"bbox": [3, 105, 22, 122], "name": "South China Sea"},
        "malacca":          {"bbox": [-2, 98, 8, 106], "name": "Malacca Strait"},
        "mediterranean":    {"bbox": [30, -6, 45, 36], "name": "Mediterranean"},
        "red_sea":          {"bbox": [12, 32, 30, 44], "name": "Red Sea"},
        "global_other":     {"bbox": [-90, -180, 90, 180], "name": "Other"},
    }

    zones_out = {}
    assigned = set()
    for zid, zinfo in ZONE_BOXES.items():
        if zid == "global_other":
            continue
        bb = zinfo["bbox"]
        zone_vessels = []
        for v in all_vessels:
            vid = f"{v['name']}_{v['lat']}"
            if vid in assigned:
                continue
            if bb[0] <= v["lat"] <= bb[2] and bb[1] <= v["lon"] <= bb[3]:
                zone_vessels.append(v)
                assigned.add(vid)
        if zone_vessels:
            type_counts = {}
            for v in zone_vessels:
                type_counts[v["type"]] = type_counts.get(v["type"], 0) + 1
            zones_out[zid] = {
                "zone": zid,
                "zone_name": zinfo["name"],
                "vessel_count": len(zone_vessels),
                "type_counts": type_counts,
                "vessels": zone_vessels,
            }

    # Remaining unassigned vessels
    remaining = [v for v in all_vessels if f"{v['name']}_{v['lat']}" not in assigned]
    if remaining:
        type_counts = {}
        for v in remaining:
            type_counts[v["type"]] = type_counts.get(v["type"], 0) + 1
        zones_out["global_other"] = {
            "zone": "global_other",
            "zone_name": "Other",
            "vessel_count": len(remaining),
            "type_counts": type_counts,
            "vessels": remaining,
        }

    return list(zones_out.values())
