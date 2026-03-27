/* map_init.js — Map setup, icons, zones, static data layers (all in named layer groups) */

// Geocoding lookup
const GEO_COORDS = {
    'south china sea': [13.5, 115.0], 'strait of hormuz': [26.5, 56.2], 'hormuz': [26.5, 56.2],
    'gulf of aden': [13.0, 47.0], 'indian ocean': [5.0, 72.5], 'arabian sea': [15.0, 65.0],
    'west asia': [31.5, 50.0], 'middle east': [31.5, 50.0], 'persian gulf': [27.0, 51.0],
    'ladakh': [34.0, 78.0], 'lac': [34.0, 78.0], 'arunachal': [28.0, 93.5],
    'line of control': [34.0, 74.5], 'kashmir': [34.0, 75.0], 'galwan': [34.7, 78.2],
    'south asia': [23.0, 80.0], 'southeast asia': [10.0, 106.0], 'east asia': [35.0, 120.0],
    'central asia': [42.0, 65.0], 'arctic': [75.0, 40.0], 'taiwan strait': [24.5, 119.5],
    'red sea': [20.0, 38.5], 'suez': [30.0, 32.5], 'bab el mandeb': [12.5, 43.3],
    'malacca': [3.0, 101.0], 'andaman': [12.0, 92.7],
    'new delhi': [28.6, 77.2], 'delhi': [28.6, 77.2], 'mumbai': [19.0, 72.8],
    'chennai': [13.0, 80.2], 'beijing': [39.9, 116.4], 'islamabad': [33.7, 73.0],
    'tehran': [35.7, 51.4], 'tel aviv': [32.0, 34.8], 'jerusalem': [31.7, 35.2],
    'moscow': [55.7, 37.6], 'kyiv': [50.4, 30.5], 'washington': [38.9, -77.0],
    'brussels': [50.8, 4.3], 'london': [51.5, -0.1], 'paris': [48.8, 2.3],
    'tokyo': [35.6, 139.7], 'canberra': [-35.2, 149.1], 'riyadh': [24.7, 46.7],
    'kabul': [34.5, 69.1], 'dhaka': [23.8, 90.4], 'colombo': [6.9, 79.8],
    'kathmandu': [27.7, 85.3], 'taipei': [25.0, 121.5], 'hanoi': [21.0, 105.8],
    'india': [20.5, 78.9], 'china': [35.8, 104.1], 'pakistan': [30.3, 69.3],
    'iran': [32.4, 53.6], 'israel': [31.0, 34.8], 'united states': [38.0, -97.0],
    'russia': [55.7, 37.6], 'ukraine': [48.3, 31.1],
    'france': [46.2, 2.2], 'united kingdom': [55.3, -3.4],
    'germany': [51.1, 10.4], 'japan': [36.2, 138.2], 'australia': [-25.2, 133.7],
    'saudi arabia': [23.8, 45.0], 'uae': [23.4, 53.8], 'turkey': [38.9, 35.2],
    'iraq': [33.2, 43.6], 'syria': [34.8, 38.9], 'afghanistan': [33.9, 67.7],
    'bangladesh': [23.6, 90.3], 'sri lanka': [7.8, 80.7], 'nepal': [28.3, 84.1],
    'myanmar': [19.7, 96.1], 'thailand': [15.8, 100.9], 'south korea': [35.9, 127.7],
    'north korea': [40.3, 127.5], 'taiwan': [23.6, 120.9], 'philippines': [12.8, 121.7],
    'indonesia': [-0.7, 113.9], 'malaysia': [4.2, 101.9], 'singapore': [1.3, 103.8],
    'egypt': [26.8, 30.8], 'south africa': [-30.5, 22.9], 'brazil': [-14.2, -51.9],
    'canada': [56.1, -106.3], 'kenya': [-0.02, 37.9], 'nigeria': [9.0, 8.6],
    'ethiopia': [9.1, 40.4], 'denmark': [56.2, 9.5], 'greenland': [71.7, -42.6],
};

// Monitored zones
const ZONES = {
    strait_of_hormuz: { bounds: [[24, 54], [28, 58]], color: '#f59e0b', name: 'Strait of Hormuz' },
    india_pakistan_border: { bounds: [[28, 66], [36, 78]], color: '#ef4444', name: 'India-Pakistan Border' },
    india_china_ladakh: { bounds: [[32, 76], [36, 80]], color: '#ef4444', name: 'Ladakh/LAC' },
    india_china_east: { bounds: [[26, 90], [30, 97]], color: '#ef4444', name: 'Arunachal/Eastern LAC' },
    middle_east: { bounds: [[25, 44], [38, 56]], color: '#f59e0b', name: 'Middle East' },
    gulf_of_aden: { bounds: [[10, 42], [16, 52]], color: '#3b82f6', name: 'Gulf of Aden' },
    south_china_sea: { bounds: [[5, 108], [22, 122]], color: '#a855f7', name: 'South China Sea' },
    indian_ocean: { bounds: [[-5, 60], [15, 85]], color: '#3b82f6', name: 'Indian Ocean' },
};

const ZONE_CENTERS = {};
for (const [id, z] of Object.entries(ZONES)) {
    ZONE_CENTERS[id] = [(z.bounds[0][0] + z.bounds[1][0]) / 2, (z.bounds[0][1] + z.bounds[1][1]) / 2];
}

// Init map
const map = L.map('world-map', {
    center: [25, 45], zoom: 3, minZoom: 2, maxZoom: 18,
    maxBounds: [[-85, -180], [85, 180]], maxBoundsViscosity: 1.0,
    zoomControl: false, attributionControl: false,
    zoomDelta: 0.2, zoomSnap: 0.2, wheelDebounceTime: 100, wheelPxPerZoomLevel: 120
});
L.control.zoom({ position: 'topleft' }).addTo(map);
L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', { maxZoom: 18 }).addTo(map);

// Icon factories
function baseIcon(color, size) {
    return L.divIcon({ className: '', iconSize: [size, size], iconAnchor: [size / 2, size / 2],
        html: `<div style="width:${size}px;height:${size}px;border-radius:50%;background:${color};border:2px solid ${color}88;box-shadow:0 0 6px ${color}44;"></div>` });
}
function diamondIcon(color, size) {
    return L.divIcon({ className: '', iconSize: [size, size], iconAnchor: [size / 2, size / 2],
        html: `<div style="width:${size * 0.7}px;height:${size * 0.7}px;background:${color};border:1px solid ${color}88;transform:rotate(45deg);margin:${size * 0.15}px;"></div>` });
}
function triIcon(color, size) {
    return L.divIcon({ className: '', iconSize: [size, size], iconAnchor: [size / 2, size / 2],
        html: `<div style="width:0;height:0;border-left:${size / 2}px solid transparent;border-right:${size / 2}px solid transparent;border-bottom:${size}px solid ${color};opacity:0.8;"></div>` });
}

// ── Layer registry — all toggleable layers go here ──
const mapLayers = {};

// Zones
const zonesLayer = L.layerGroup();
for (const [id, z] of Object.entries(ZONES)) {
    L.rectangle(z.bounds, { color: z.color, weight: 1, fillOpacity: 0.04, opacity: 0.25, dashArray: '4' }).addTo(zonesLayer)
        .bindPopup(`<div class="map-popup"><b>${z.name}</b><br><span style="color:#6b7280">Monitored zone</span></div>`);
}
zonesLayer.addTo(map);
mapLayers.zones = { layer: zonesLayer, name: 'Monitored Zones', icon: 'rect', color: '#f59e0b', on: true };

// Load static data into separate layer groups
fetch('/static/map_data.json').then(r => r.json()).then(md => {
    const baseColors = { indian: '#22c55e', chinese: '#ef4444', pakistan: '#f59e0b', us: '#3b82f6' };
    const typeLabels = { navy: 'NAVAL', air: 'AIR', army: 'ARMY' };

    // ── Military Bases ──
    const basesLayer = L.layerGroup();
    [{ data: md.indian_bases, side: 'indian', label: 'India' },
     { data: md.chinese_bases, side: 'chinese', label: 'China' },
     { data: md.pakistan_bases, side: 'pakistan', label: 'Pakistan' },
     { data: md.us_bases, side: 'us', label: 'US' },
    ].forEach(({ data, side, label }) => {
        (data || []).forEach(b => {
            const c = baseColors[side];
            const icon = b.type === 'navy' ? diamondIcon(c, 10) : b.type === 'air' ? triIcon(c, 10) : baseIcon(c, 7);
            L.marker([b.lat, b.lon], { icon }).addTo(basesLayer)
                .bindPopup(`<div class="map-popup"><span class="tag" style="background:${c}22;color:${c}">${label}</span> <span class="tag" style="background:rgba(255,255,255,0.05);color:#9ca3af">${typeLabels[b.type] || b.type}</span><br><b>${b.name}</b></div>`);
        });
    });
    (md.russian_bases || []).forEach(b => {
        const icon = b.type === 'navy' ? diamondIcon('#a855f7', 9) : b.type === 'air' ? triIcon('#a855f7', 9) : baseIcon('#a855f7', 6);
        L.marker([b.lat, b.lon], { icon }).addTo(basesLayer)
            .bindPopup(`<div class="map-popup"><span class="tag" style="background:#a855f722;color:#a855f7">Russia</span> <span class="tag" style="background:rgba(255,255,255,0.05);color:#9ca3af">${(b.type || '').toUpperCase()}</span><br><b>${b.name}</b></div>`);
    });
    (md.nato_bases || []).forEach(b => {
        const icon = b.type === 'navy' ? diamondIcon('#22c55e', 8) : b.type === 'air' ? triIcon('#22c55e', 8) : baseIcon('#22c55e', 6);
        L.marker([b.lat, b.lon], { icon }).addTo(basesLayer)
            .bindPopup(`<div class="map-popup"><span class="tag" style="background:#22c55e22;color:#22c55e">NATO</span> <span class="tag" style="background:rgba(255,255,255,0.05);color:#9ca3af">${(b.type || '').toUpperCase()}</span><br><b>${b.name}</b></div>`);
    });
    basesLayer.addTo(map);
    mapLayers.bases = { layer: basesLayer, name: 'Military Bases', icon: 'circle', color: '#3b82f6', on: true };

    // ── Nuclear Sites ──
    const nuclearLayer = L.layerGroup();
    (md.nuclear_sites || []).forEach(n => {
        L.circleMarker([n.lat, n.lon], { radius: 5, color: '#ef4444', fillColor: '#ef444466', fillOpacity: 0.6, weight: 1 }).addTo(nuclearLayer)
            .bindPopup(`<div class="map-popup"><span class="tag" style="background:#ef444422;color:#ef4444">NUCLEAR</span><br><b>${n.name}</b></div>`);
    });
    nuclearLayer.addTo(map);
    mapLayers.nuclear = { layer: nuclearLayer, name: 'Nuclear Sites', icon: 'circle', color: '#ef4444', on: true };

    // ── Missiles ──
    const missileLayer = L.layerGroup();
    (md.missile_systems || []).forEach(m => {
        missileLayer.addLayer(L.circle(m.origin, { radius: m.range_km * 1000, color: m.color, fillColor: m.color, fillOpacity: 0.02, weight: 1, opacity: 0.15, dashArray: '8,4', interactive: false }));
        const labelLon = m.origin[1] + (m.range_km / 111);
        missileLayer.addLayer(L.marker([m.origin[0], labelLon], { interactive: false, icon: L.divIcon({ className: '', html: `<span style="font-family:monospace;font-size:9px;color:${m.color}88;white-space:nowrap">${m.name} ${m.range_km}km</span>`, iconSize: [80, 12] }) }));
    });
    missileLayer.addTo(map);
    mapLayers.missiles = { layer: missileLayer, name: 'Missile Ranges', icon: 'circle', color: '#a855f7', on: true };
    map.on('zoomend', () => {
        if (mapLayers.missiles.on) {
            if (map.getZoom() > 5) map.removeLayer(missileLayer);
            else if (!map.hasLayer(missileLayer)) map.addLayer(missileLayer);
        }
    });

    // ── Border Disputes ──
    const disputesLayer = L.layerGroup();
    (md.border_disputes || []).forEach(bd => {
        if (bd.type === 'area') {
            L.rectangle(bd.bounds, { color: bd.color, weight: 1, fillOpacity: 0.08, opacity: 0.4, dashArray: '3,3' }).addTo(disputesLayer)
                .bindPopup(`<div class="map-popup"><span class="tag" style="background:${bd.color}22;color:${bd.color}">DISPUTED</span><br><b>${bd.name}</b></div>`);
        } else if (bd.points) {
            L.polyline(bd.points, { color: bd.color, weight: 2, opacity: 0.5, dashArray: '6,4' }).addTo(disputesLayer)
                .bindPopup(`<div class="map-popup"><b>${bd.name}</b></div>`);
        }
    });
    disputesLayer.addTo(map);
    mapLayers.disputes = { layer: disputesLayer, name: 'Border Disputes', icon: 'rect', color: '#ef4444', on: true };

    // ── Conflicts ──
    const conflictsLayer = L.layerGroup();
    (md.active_conflicts || []).forEach(c => {
        const color = c.severity === 'critical' ? '#ef4444' : c.severity === 'high' ? '#f59e0b' : '#6b7280';
        const r = c.severity === 'critical' ? 80000 : c.severity === 'high' ? 50000 : 30000;
        L.circle([c.lat, c.lon], { radius: r, color, fillColor: color, fillOpacity: 0.1, weight: 1, opacity: 0.4 }).addTo(conflictsLayer)
            .bindPopup(`<div class="map-popup"><span class="tag" style="background:${color}22;color:${color}">CONFLICT</span><br><b>${c.name}</b><br>Since: ${c.since}</div>`);
    });
    conflictsLayer.addTo(map);
    mapLayers.conflicts = { layer: conflictsLayer, name: 'Active Conflicts', icon: 'circle', color: '#ef4444', on: true };

    // ── Chokepoints ──
    const chokepointsLayer = L.layerGroup();
    (md.chokepoints || []).forEach(cp => {
        L.circleMarker([cp.lat, cp.lon], { radius: 6, color: '#f59e0b', fillColor: '#f59e0b', fillOpacity: 0.4, weight: 2 }).addTo(chokepointsLayer)
            .bindPopup(`<div class="map-popup"><span class="tag" style="background:#f59e0b22;color:#f59e0b">CHOKEPOINT</span><br><b>${cp.name}</b><br>Flow: ${cp.daily_flow}</div>`);
    });
    chokepointsLayer.addTo(map);
    mapLayers.chokepoints = { layer: chokepointsLayer, name: 'Chokepoints', icon: 'diamond', color: '#f59e0b', on: true };

    // ── Shipping Lanes ──
    const shippingLayer = L.layerGroup();
    window._shippingLanes = [];
    const commIcons = { crude_oil: 'OIL', oil_lng: 'OIL', lng: 'LNG', coal_lng: 'COAL', grain: 'GRAIN', iron_soy: 'IRON', mixed: 'TRADE' };

    (md.shipping_lanes || []).forEach((sl, idx) => {
        const tag = commIcons[sl.commodity] || 'SHIP';
        const volMatch = (sl.volume || '').match(/([\d.]+)/);
        const volNum = volMatch ? parseFloat(volMatch[1]) : 1;
        const baseWeight = sl.critical ? 3 : 2;
        const weight = Math.max(baseWeight, Math.min(baseWeight + volNum * 0.3, 6));

        const baseLine = L.polyline(sl.points, { color: sl.color, weight: weight + 1, opacity: 0.08, smoothFactor: 2, lineCap: 'round', lineJoin: 'round' });
        shippingLayer.addLayer(baseLine);

        const flowLine = L.polyline(sl.points, { color: sl.color, weight, opacity: sl.critical ? 0.45 : 0.2, smoothFactor: 2, dashArray: sl.critical ? '12,8' : '8,12', dashOffset: '0', lineCap: 'butt', className: `flow-lane flow-lane-${idx}` });
        flowLine.bindPopup(`<div class="map-popup"><span class="tag" style="background:${sl.color}22;color:${sl.color}">${tag}</span><br><b>${sl.name}</b><br>${sl.volume}<br><span style="color:#6b7280">${sl.critical ? 'Critical route' : 'Trade route'}</span></div>`);
        shippingLayer.addLayer(flowLine);

        if (sl.points.length >= 2) {
            const mid = Math.floor(sl.points.length / 2);
            shippingLayer.addLayer(L.marker(sl.points[mid], { interactive: false, icon: L.divIcon({ className: '', iconSize: [40, 14], html: `<span style="font-family:monospace;font-size:8px;color:${sl.color};background:rgba(10,10,15,0.7);padding:1px 3px;border-radius:2px;white-space:nowrap">${tag}</span>` }) }));
        }
        const arrowCount = Math.max(1, Math.floor(sl.points.length / 2));
        for (let i = 1; i < sl.points.length && i <= arrowCount + 1; i++) {
            const p1 = sl.points[i - 1], p2 = sl.points[i];
            const midPt = [(p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2];
            const angle = Math.atan2(p2[1] - p1[1], p2[0] - p1[0]) * 180 / Math.PI;
            shippingLayer.addLayer(L.marker(midPt, { interactive: false, icon: L.divIcon({ className: '', iconSize: [10, 10], iconAnchor: [5, 5], html: `<div style="color:${sl.color};opacity:0.4;font-size:10px;transform:rotate(${angle - 90}deg)">&#9650;</div>` }) }));
        }
        window._shippingLanes.push({ idx, commodity: sl.commodity, name: sl.name, flowLine, baseLine, color: sl.color, critical: sl.critical });
    });
    shippingLayer.addTo(map);
    mapLayers.shipping = { layer: shippingLayer, name: 'Shipping Lanes', icon: 'line', color: '#f59e0b', on: true };

    // ── Pipelines ──
    const pipelinesLayer = L.layerGroup();
    (md.pipelines || []).forEach(p => {
        L.polyline(p.points, { color: p.color, weight: 2, opacity: 0.25, dashArray: '8,6' }).addTo(pipelinesLayer)
            .bindPopup(`<div class="map-popup"><b>${p.name}</b><br>Status: ${p.status}</div>`);
    });
    pipelinesLayer.addTo(map);
    mapLayers.pipelines = { layer: pipelinesLayer, name: 'Pipelines', icon: 'line', color: '#06b6d4', on: true };

    // ── Submarine Cables ──
    const cablesLayer = L.layerGroup();
    (md.submarine_cables || []).forEach(c => {
        L.polyline(c.points, { color: c.color, weight: 1, opacity: 0.15, dashArray: '2,4' }).addTo(cablesLayer)
            .bindPopup(`<div class="map-popup"><span class="tag" style="background:#06b6d422;color:#06b6d4">CABLE</span><br><b>${c.name}</b></div>`);
    });
    cablesLayer.addTo(map);
    mapLayers.cables = { layer: cablesLayer, name: 'Submarine Cables', icon: 'line', color: '#06b6d4', on: true };

    // ── Belt & Road ──
    const briLayer = L.layerGroup();
    (md.belt_and_road || []).forEach(br => {
        L.polyline(br.points, { color: br.color, weight: 2, opacity: 0.15, dashArray: '10,6' }).addTo(briLayer)
            .bindPopup(`<div class="map-popup"><span class="tag" style="background:#ef444422;color:#ef4444">BRI</span><br><b>${br.name}</b></div>`);
    });
    briLayer.addTo(map);
    mapLayers.bri = { layer: briLayer, name: 'Belt & Road', icon: 'line', color: '#ef4444', on: true };

    // ── Sanctions ──
    const sanctionsLayer = L.layerGroup();
    (md.sanctions_zones || []).forEach(s => {
        const color = s.level === 'full' ? '#ef4444' : '#f59e0b';
        L.circleMarker([s.lat, s.lon], { radius: 12, color, fillColor: color, fillOpacity: 0.08, weight: 1, opacity: 0.3, dashArray: '3,3' }).addTo(sanctionsLayer)
            .bindPopup(`<div class="map-popup"><span class="tag" style="background:${color}22;color:${color}">SANCTIONS</span><br><b>${s.name}</b><br>Level: ${s.level}</div>`);
    });
    sanctionsLayer.addTo(map);
    mapLayers.sanctions = { layer: sanctionsLayer, name: 'Sanctions Zones', icon: 'circle', color: '#f59e0b', on: true };

    // ── India EEZ ──
    const eezLayer = L.layerGroup();
    if (md.eez_india) {
        L.polygon(md.eez_india.approximate_bounds, { color: '#22c55e', weight: 1, fillOpacity: 0.02, opacity: 0.15, dashArray: '4,4' }).addTo(eezLayer)
            .bindPopup('<div class="map-popup"><b>India EEZ</b><br>Exclusive Economic Zone</div>');
    }
    eezLayer.addTo(map);
    mapLayers.eez = { layer: eezLayer, name: 'India EEZ', icon: 'rect', color: '#22c55e', on: true };

    // Animate flow lanes
    let flowOffset = 0;
    setInterval(() => {
        flowOffset -= 1;
        document.querySelectorAll('[class*="flow-lane"]').forEach(el => {
            el.style.strokeDashoffset = flowOffset + 'px';
        });
    }, 50);

    // Build the layer control panel now that all layers are registered
    if (typeof buildLayerPanel === 'function') buildLayerPanel();
});

// ── Layer toggle API ──
function toggleLayer(key) {
    const entry = mapLayers[key];
    if (!entry) return;
    if (entry.on) {
        map.removeLayer(entry.layer);
        entry.on = false;
    } else {
        map.addLayer(entry.layer);
        entry.on = true;
    }
    return entry.on;
}

function soloLayer(key) {
    // Turn off everything except this one
    for (const [k, entry] of Object.entries(mapLayers)) {
        if (k === key) {
            if (!entry.on) { map.addLayer(entry.layer); entry.on = true; }
        } else {
            if (entry.on) { map.removeLayer(entry.layer); entry.on = false; }
        }
    }
}

function showAllLayers() {
    for (const entry of Object.values(mapLayers)) {
        if (!entry.on) { map.addLayer(entry.layer); entry.on = true; }
    }
}

// Global highlight helpers
function highlightShippingLanes(commodity, duration) {
    if (!window._shippingLanes) return;
    const matches = window._shippingLanes.filter(sl =>
        !commodity || sl.commodity === commodity || sl.name.toLowerCase().includes(commodity.toLowerCase())
    );
    matches.forEach(sl => {
        sl.flowLine.setStyle({ opacity: 0.7, weight: 5 });
        sl.baseLine.setStyle({ opacity: 0.2 });
    });
    if (duration) setTimeout(() => resetShippingLanes(), duration);
}

function resetShippingLanes() {
    if (!window._shippingLanes) return;
    window._shippingLanes.forEach(sl => {
        sl.flowLine.setStyle({ opacity: sl.critical ? 0.45 : 0.2, weight: sl.critical ? 3 : 2 });
        sl.baseLine.setStyle({ opacity: 0.08 });
    });
}
