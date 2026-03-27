/* map_ships.js — Live vessel tracking on map */

const vesselLayer = L.layerGroup().addTo(map);
if (typeof registerLiveLayer === 'function') {
    registerLiveLayer('vessels', vesselLayer, 'Vessels / AIS', '#f59e0b');
}

const VESSEL_COLORS = {
    tanker: '#f59e0b',
    cargo: '#3b82f6',
    passenger: '#22c55e',
    military: '#ef4444',
    fishing: '#6b7280',
    other: '#9ca3af',
};

const VESSEL_LABELS = {
    tanker: 'TANKER',
    cargo: 'CARGO',
    passenger: 'PAX',
    military: 'MILITARY',
    fishing: 'FISH',
    other: 'VESSEL',
};

function vesselIcon(type, heading) {
    const color = VESSEL_COLORS[type] || '#9ca3af';
    const rot = (heading || 0);
    // Triangle pointing in heading direction
    return L.divIcon({
        className: '',
        iconSize: [10, 10],
        iconAnchor: [5, 5],
        html: `<div style="width:0;height:0;border-left:4px solid transparent;border-right:4px solid transparent;border-bottom:10px solid ${color};transform:rotate(${rot}deg);opacity:0.8;filter:drop-shadow(0 0 2px ${color}44);"></div>`
    });
}

async function refreshVessels() {
    try {
        const resp = await fetch('/api/intel/vessels');
        const zones = await resp.json();

        vesselLayer.clearLayers();

        let totalVessels = 0;
        for (const zone of zones) {
            const vessels = zone.vessels || [];
            totalVessels += vessels.length;

            for (const v of vessels) {
                if (!v.lat || !v.lon) continue;

                const color = VESSEL_COLORS[v.type] || '#9ca3af';
                const label = VESSEL_LABELS[v.type] || 'VESSEL';
                const marker = L.marker([v.lat, v.lon], {
                    icon: vesselIcon(v.type, v.heading)
                }).addTo(vesselLayer);

                let popup = `<div class="map-popup">`;
                popup += `<span class="tag" style="background:${color}22;color:${color}">${label}</span>`;
                popup += `<br><b>${v.name || 'Unknown'}</b>`;
                if (v.country) popup += `<br>Flag: ${v.country}`;
                if (v.speed) popup += `<br>Speed: ${v.speed.toFixed(1)} kn`;
                if (v.destination) popup += `<br>Dest: ${v.destination}`;
                popup += `<br><span style="color:#6b7280">${zone.zone_name}</span>`;
                popup += '</div>';
                marker.bindPopup(popup);
            }

            // Zone summary label
            if (vessels.length > 0) {
                const tc = zone.type_counts || {};
                const summary = Object.entries(tc)
                    .sort((a, b) => b[1] - a[1])
                    .slice(0, 3)
                    .map(([t, c]) => `${c} ${t}`)
                    .join(', ');

                // Place summary at zone center
                const center = ZONE_CENTERS[zone.zone];
                if (center) {
                    const summaryMarker = L.marker([center[0] - 1, center[1]], {
                        interactive: false,
                        icon: L.divIcon({
                            className: '',
                            iconSize: [120, 14],
                            html: `<span style="font-family:monospace;font-size:8px;color:rgba(255,255,255,0.25);background:rgba(10,10,15,0.7);padding:1px 4px;border-radius:2px;white-space:nowrap">${vessels.length} vessels: ${summary}</span>`
                        })
                    });
                    vesselLayer.addLayer(summaryMarker);
                }
            }
        }

        // Update sidebar vessel count if element exists
        const el = document.getElementById('vessel-count');
        if (el) el.textContent = totalVessels ? `${totalVessels} tracked` : '';

    } catch (e) { console.log('Vessel refresh failed:', e); }
}

// Initial load + schedule
refreshVessels();
setInterval(refreshVessels, 120000); // every 2 min
