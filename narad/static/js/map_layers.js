/* map_layers.js — Collapsible layer control panel with legend + toggles */

// Also register live layers (geoint, aircraft, vessels) — called by their respective scripts
function registerLiveLayer(key, layer, name, color) {
    mapLayers[key] = { layer, name, icon: 'live', color, on: true };
    // Re-render panel if it exists
    const panel = document.getElementById('layer-panel-items');
    if (panel) buildLayerPanel();
}

function buildLayerPanel() {
    const container = document.getElementById('layer-panel-items');
    if (!container) return;

    // Icon SVG helpers
    function iconSvg(type, color) {
        const c = color || '#9ca3af';
        switch (type) {
            case 'circle': return `<svg width="10" height="10"><circle cx="5" cy="5" r="4" fill="${c}" opacity="0.7"/></svg>`;
            case 'diamond': return `<svg width="10" height="10"><rect x="2" y="2" width="6" height="6" fill="${c}" opacity="0.7" transform="rotate(45 5 5)"/></svg>`;
            case 'rect': return `<svg width="12" height="8"><rect x="1" y="1" width="10" height="6" fill="none" stroke="${c}" stroke-width="1" opacity="0.5" stroke-dasharray="2,1"/></svg>`;
            case 'line': return `<svg width="14" height="6"><line x1="0" y1="3" x2="14" y2="3" stroke="${c}" stroke-width="2" opacity="0.5" stroke-dasharray="3,2"/></svg>`;
            case 'triangle': return `<svg width="10" height="10"><polygon points="5,1 9,9 1,9" fill="${c}" opacity="0.7"/></svg>`;
            case 'live': return `<span class="w-1.5 h-1.5 rounded-full pulse-dot inline-block" style="background:${c}"></span>`;
            default: return `<span class="w-2 h-2 rounded-full inline-block" style="background:${c}"></span>`;
        }
    }

    // Group layers
    const groups = [
        { label: 'MILITARY', keys: ['bases', 'nuclear', 'missiles', 'conflicts'] },
        { label: 'BORDERS', keys: ['disputes', 'zones', 'sanctions', 'eez'] },
        { label: 'TRADE', keys: ['shipping', 'chokepoints', 'pipelines', 'cables', 'bri'] },
        { label: 'LIVE', keys: ['geoint', 'aircraft', 'vessels', 'stories'] },
    ];

    let html = '';
    for (const g of groups) {
        const validKeys = g.keys.filter(k => mapLayers[k]);
        if (!validKeys.length) continue;

        html += `<div class="mt-1.5 first:mt-0">
            <p class="font-mono text-[7px] text-white/10 mb-0.5">${g.label}</p>`;
        for (const key of validKeys) {
            const entry = mapLayers[key];
            const checked = entry.on ? 'checked' : '';
            html += `<div class="flex items-center gap-1.5 py-0.5 group">
                <label class="flex items-center gap-1.5 flex-1 cursor-pointer">
                    <input type="checkbox" ${checked} onchange="onLayerToggle('${key}', this.checked)"
                        class="w-2.5 h-2.5 rounded-sm border border-white/15 bg-transparent appearance-none cursor-pointer checked:bg-current checked:border-current"
                        style="color:${entry.color}; accent-color:${entry.color}">
                    ${iconSvg(entry.icon, entry.color)}
                    <span class="font-mono text-[9px] ${entry.on ? 'text-white/45' : 'text-white/15'}">${entry.name}</span>
                </label>
                <button onclick="onLayerSolo('${key}')" class="font-mono text-[7px] text-white/10 hover:text-white/40 opacity-0 group-hover:opacity-100" title="Solo this layer">SOLO</button>
            </div>`;
        }
        html += '</div>';
    }

    // Show all / Hide all buttons
    html += `<div class="mt-2 pt-1.5 border-t border-white/5 flex items-center gap-3">
        <button onclick="onShowAll()" class="font-mono text-[8px] text-white/20 hover:text-white/40">SHOW ALL</button>
        <button onclick="onHideAll()" class="font-mono text-[8px] text-white/20 hover:text-white/40">HIDE ALL</button>
    </div>`;

    container.innerHTML = html;
}

// Event handlers
function onLayerToggle(key, checked) {
    const entry = mapLayers[key];
    if (!entry) return;
    if (checked && !entry.on) { map.addLayer(entry.layer); entry.on = true; }
    else if (!checked && entry.on) { map.removeLayer(entry.layer); entry.on = false; }
    // Update label opacity
    buildLayerPanel();
}

function onLayerSolo(key) {
    soloLayer(key);
    buildLayerPanel();
}

function onShowAll() {
    showAllLayers();
    buildLayerPanel();
}

function onHideAll() {
    hideAllLayers();
    buildLayerPanel();
}

// Toggle panel visibility
function toggleLayerPanel() {
    const panel = document.getElementById('layer-panel');
    panel.classList.toggle('collapsed');
}
