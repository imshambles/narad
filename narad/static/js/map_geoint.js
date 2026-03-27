/* map_geoint.js — GEOINT live layers: thermal anomalies + aircraft tracking */

// Live layer groups (cleared and redrawn on each refresh)
const liveLayers = { geoint: L.layerGroup().addTo(map), aircraft: L.layerGroup().addTo(map) };
let lastGeointUpdate = 0;

// Register with layer control
if (typeof registerLiveLayer === 'function') {
    registerLiveLayer('geoint', liveLayers.geoint, 'Thermal / FIRMS', '#ef4444');
    registerLiveLayer('aircraft', liveLayers.aircraft, 'Aircraft / ADS-B', '#60a5fa');
}

async function refreshGeoint() {
    try {
        const resp = await fetch('/api/intel/geoint');
        const data = await resp.json();
        const allSignals = [...(data.thermal || []), ...(data.aircraft || [])];

        liveLayers.geoint.clearLayers();
        liveLayers.aircraft.clearLayers();

        // Thermal anomalies
        for (const t of (data.thermal || [])) {
            const d = t.data || {};
            const center = ZONE_CENTERS[d.zone];
            if (!center) continue;
            const radius = Math.min(Math.max((d.fire_count || 1) * 300, 5000), 150000);
            const color = { critical: '#ef4444', high: '#f59e0b', medium: '#3b82f6', low: '#6b7280' }[t.severity] || '#6b7280';
            L.circle(center, { radius, color, fillColor: color, fillOpacity: 0.15, weight: 1 }).addTo(liveLayers.geoint)
                .bindPopup(`<div class="map-popup"><span class="tag" style="background:${color}22;color:${color}">THERMAL</span><br><b>${d.zone_name || ''}</b><br>${d.fire_count || 0} heat signatures<br>Peak: ${d.max_frp || 0} FRP | Night: ${d.night_count || 0}<br><br>${d.interpretation || ''}</div>`, { maxWidth: 280 });
        }

        // Aircraft
        for (const a of (data.aircraft || [])) {
            const d = a.data || {};
            const center = ZONE_CENTERS[d.zone];
            if (!center) continue;
            let details = '';
            if (d.details) {
                details = '<br><br><table style="font-size:9px;width:100%"><tr style="color:#6b7280"><td>Call</td><td>Country</td><td>Alt</td></tr>';
                for (const ac of d.details.slice(0, 5)) details += `<tr><td>${ac.callsign}</td><td>${ac.country}</td><td>${ac.altitude_m}m</td></tr>`;
                details += '</table>';
            }
            L.marker(center, { icon: triIcon('#60a5fa', 12) }).addTo(liveLayers.aircraft)
                .bindPopup(`<div class="map-popup"><span class="tag" style="background:#3b82f622;color:#3b82f6">AIRCRAFT</span><br><b>${d.zone_name || ''}</b><br>${d.military_count || 0} military / ${d.total_aircraft || 0} total${details}</div>`, { maxWidth: 280 });
        }

        // Update zone rectangles for active zones
        const activeZones = new Set(allSignals.map(s => s.data?.zone));
        map.eachLayer(l => {
            if (l instanceof L.Rectangle) {
                for (const [id, z] of Object.entries(ZONES)) {
                    const b = l.getBounds();
                    if (Math.abs(b.getSouth() - z.bounds[0][0]) < 0.1) {
                        if (activeZones.has(id)) {
                            l.setStyle({ fillOpacity: 0.15, opacity: 0.6, dashArray: null });
                        }
                        break;
                    }
                }
            }
        });

        // Update sidebar GEOINT panel
        const el = document.getElementById('geoint-compact');
        if (el) {
            const zoneNames = data.zone_names || {};
            let html = '';
            for (let i = 0; i < allSignals.length && i < 6; i++) {
                const s = allSignals[i];
                const d = s.data || {};
                const icon = d.type === 'firms' ? '&#9632;' : '&#9650;';
                const c = { critical: 'text-red-400', high: 'text-amber-400', medium: 'text-blue-400', low: 'text-white/30' }[s.severity] || 'text-white/30';
                html += `<div class="border-b border-white/5">
                    <div class="flex items-center gap-1.5 py-1.5 cursor-pointer" onclick="this.nextElementSibling.classList.toggle('hidden')">
                        <span class="${c}">${icon}</span>
                        <span class="font-mono text-[10px] ${c} flex-1">${s.title}</span>
                        <span class="font-mono text-[9px] text-white/15">&#x25BE;</span>
                    </div>
                    <div class="hidden pb-2 pl-5">
                        <p class="font-mono text-[9px] text-white/25 leading-relaxed">${d.interpretation || ''}</p>
                    </div>
                </div>`;
            }
            for (const [k, v] of Object.entries(zoneNames)) {
                if (!activeZones.has(k)) {
                    html += `<div class="flex items-center gap-1.5 py-1 border-b border-white/5">
                        <span class="w-1.5 h-1.5 rounded-full bg-green-500/40"></span>
                        <span class="font-mono text-[10px] text-white/20">${v}</span>
                        <span class="font-mono text-[9px] text-green-400/30 ml-auto">clear</span>
                    </div>`;
                }
            }
            el.innerHTML = html;
        }

        lastGeointUpdate = Date.now();
        updateStatusBadge();
    } catch (e) { console.log('GEOINT refresh failed:', e); }
}

// Initial GEOINT load (also plotted by map_init's first fetch, but this handles the sidebar)
fetch('/api/intel/geoint').then(r => r.json()).then(data => {
    const allSignals = [...(data.thermal || []), ...(data.aircraft || [])];
    const activeZones = new Set(allSignals.map(s => s.data?.zone));

    // Thermal on map
    for (const t of (data.thermal || [])) {
        const d = t.data || {};
        const center = ZONE_CENTERS[d.zone];
        if (!center) continue;
        const radius = Math.min(Math.max(d.fire_count * 300, 5000), 150000);
        const color = { critical: '#ef4444', high: '#f59e0b', medium: '#3b82f6', low: '#6b7280' }[t.severity] || '#6b7280';
        L.circle(center, { radius, color, fillColor: color, fillOpacity: 0.15, weight: 1 }).addTo(map)
            .bindPopup(`<div class="map-popup"><span class="tag" style="background:${color}22;color:${color}">THERMAL</span><br><b>${d.zone_name}</b><br>${d.fire_count} heat signatures<br>Peak: ${d.max_frp || 0} FRP | Night: ${d.night_count || 0}<br><br>${d.interpretation || ''}</div>`, { maxWidth: 280 });
    }

    // Aircraft on map
    for (const a of (data.aircraft || [])) {
        const d = a.data || {};
        const center = ZONE_CENTERS[d.zone];
        if (!center) continue;
        let details = '';
        if (d.details) {
            details = '<br><br><table style="font-size:9px;width:100%"><tr style="color:#6b7280"><td>Call</td><td>Country</td><td>Alt</td></tr>';
            for (const ac of d.details.slice(0, 5)) details += `<tr><td>${ac.callsign}</td><td>${ac.country}</td><td>${ac.altitude_m}m</td></tr>`;
            details += '</table>';
        }
        L.marker(center, { icon: triIcon('#60a5fa', 12) }).addTo(map)
            .bindPopup(`<div class="map-popup"><span class="tag" style="background:#3b82f622;color:#3b82f6">AIRCRAFT</span><br><b>${d.zone_name}</b><br>${d.military_count} military-pattern / ${d.total_aircraft} total${details}</div>`, { maxWidth: 280 });
    }

    // Highlight active zones
    map.eachLayer(l => {
        if (l instanceof L.Rectangle) {
            for (const [id, z] of Object.entries(ZONES)) {
                const b = l.getBounds();
                if (Math.abs(b.getSouth() - z.bounds[0][0]) < 0.1) {
                    if (activeZones.has(id)) {
                        l.setStyle({ fillOpacity: 0.15, opacity: 0.6, dashArray: null });
                    }
                    break;
                }
            }
        }
    });
});
