/* sidebar.js — Toggle, story expand, Ask Narad, live update scheduler */

// Responsive sidebar width
function getSidebarWidth() {
    if (window.innerWidth <= 768) return '100%';
    if (window.innerWidth <= 1024) return '320px';
    return '380px';
}

function isMobileView() {
    return window.innerWidth <= 768;
}

// Sidebar toggle
document.getElementById('sb-toggle').addEventListener('click', function () {
    const sb = document.getElementById('sidebar');
    sb.classList.toggle('collapsed');
    const isCollapsed = sb.classList.contains('collapsed');

    if (isMobileView()) {
        // Mobile: toggle is handled by CSS transform
        this.textContent = isCollapsed ? '\u25B2' : '\u25BC';
    } else {
        const w = getSidebarWidth();
        this.textContent = isCollapsed ? '\u25B6' : '\u25C0';
        this.style.right = isCollapsed ? '0px' : w;
        const ticker = document.querySelector('.ticker-bar');
        if (ticker) ticker.style.right = isCollapsed ? '0' : w;
        const alertBar = document.querySelector('.alert-bar');
        if (alertBar) alertBar.style.right = isCollapsed ? '0' : w;
    }
    setTimeout(() => map.invalidateSize(), 300);
});

// Update sidebar on resize
window.addEventListener('resize', function () {
    const sb = document.getElementById('sidebar');
    const btn = document.getElementById('sb-toggle');
    const ticker = document.querySelector('.ticker-bar');
    const isCollapsed = sb.classList.contains('collapsed');

    if (isMobileView()) {
        btn.style.right = '';
        if (ticker) ticker.style.right = '';
    } else if (!isCollapsed) {
        const w = getSidebarWidth();
        btn.style.right = w;
        if (ticker) ticker.style.right = w;
    }
    setTimeout(() => map.invalidateSize(), 100);
});

// Story expand/collapse
function toggleStory(el) {
    const expand = el.nextElementSibling;
    expand.classList.toggle('open');
}

// Ask Narad
async function askNarad() {
    const input = document.getElementById('query-input');
    const q = input.value.trim();
    if (!q) return;
    const result = document.getElementById('query-result');
    const loading = document.getElementById('query-loading');
    const answer = document.getElementById('query-answer');
    result.classList.remove('hidden');
    loading.classList.remove('hidden');
    answer.innerHTML = '';
    try {
        const resp = await fetch('/api/intel/query', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question: q }) });
        const data = await resp.json();
        loading.classList.add('hidden');
        let html = `<p class="text-[11px] text-white/50 leading-relaxed mb-1">${data.answer || 'No answer.'}</p>`;
        if (data.follow_up_questions?.length) {
            for (const fq of data.follow_up_questions.slice(0, 2))
                html += `<button onclick="document.getElementById('query-input').value='${fq.replace(/'/g, "\\'")}';askNarad()" class="block font-mono text-[10px] text-blue-400/30 hover:text-blue-400/60 mt-1">${fq}</button>`;
        }
        answer.innerHTML = html;
    } catch (e) {
        loading.classList.add('hidden');
        answer.innerHTML = `<p class="text-[10px] text-red-400/50">Failed</p>`;
    }
}

// Status badge
function updateStatusBadge() {
    const badge = document.getElementById('live-badge');
    if (badge) {
        const now = Date.now();
        const geointAge = Math.floor((now - lastGeointUpdate) / 60000);
        const marketAge = Math.floor((now - lastMarketUpdate) / 60000);
        badge.title = `GEOINT: ${geointAge}m ago | Market: ${marketAge}m ago`;
    }
}

// ── Watchlist (localStorage) ──
function getWatchlist() {
    return JSON.parse(localStorage.getItem('narad_watchlist') || '[]');
}

function saveWatchlist(list) {
    localStorage.setItem('narad_watchlist', JSON.stringify(list));
    renderWatchlist();
}

function addToWatchlist(name) {
    name = name.trim().toUpperCase();
    if (!name) return;
    const list = getWatchlist();
    if (!list.includes(name)) {
        list.push(name);
        saveWatchlist(list);
    }
}

function removeFromWatchlist(name) {
    const list = getWatchlist().filter(n => n !== name);
    saveWatchlist(list);
}

function renderWatchlist() {
    const el = document.getElementById('watchlist-items');
    const countEl = document.getElementById('watchlist-count');
    if (!el) return;
    const list = getWatchlist();
    if (countEl) countEl.textContent = list.length ? list.length + ' items' : '';
    if (!list.length) {
        el.innerHTML = '<span class="font-mono text-[9px] text-white/10">Add stocks to track (e.g. HAL, BRENT, ONGC)</span>';
        return;
    }
    el.innerHTML = list.map(name =>
        `<span class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-white/5 font-mono text-[9px] text-white/35">
            ${name}
            <button onclick="removeFromWatchlist('${name.replace(/'/g, "\\'")}')" class="text-white/15 hover:text-red-400 ml-0.5">x</button>
        </span>`
    ).join('');
}

// Check if a signal affects watchlist items
function signalMatchesWatchlist(signal) {
    const list = getWatchlist();
    if (!list.length) return false;
    const text = JSON.stringify(signal).toUpperCase();
    return list.some(name => text.includes(name));
}

// Initial render
renderWatchlist();

// Signal → Map linking: pan map and highlight related lanes
const SIGNAL_GEO = {
    'Strait of Hormuz Disruption': { center: [26.5, 56.2], zoom: 6, commodity: 'crude_oil' },
    'Oil Price Surge': { center: [26.5, 56.2], zoom: 4, commodity: 'crude_oil' },
    'India-China Border Tension': { center: [34.0, 78.0], zoom: 6 },
    'India-Pakistan Tension': { center: [34.0, 74.5], zoom: 6 },
    'Global Food Supply Disruption': { center: [46.0, 35.0], zoom: 4, commodity: 'grain' },
    'Sanctions / Trade War': { center: [35.0, 50.0], zoom: 3 },
    'Indo-Pacific Maritime Tension': { center: [13.5, 115.0], zoom: 5, commodity: 'mixed' },
    'INR Depreciation Pressure': { center: [20.5, 78.9], zoom: 5 },
};

function focusSignalOnMap(bucketName) {
    const geo = SIGNAL_GEO[bucketName];
    if (geo) {
        map.flyTo(geo.center, geo.zoom, { duration: 1 });
        if (geo.commodity && typeof highlightShippingLanes === 'function') {
            highlightShippingLanes(geo.commodity, 6000);
        }
    }
}

// Live update intervals
setInterval(refreshGeoint, 120000);
setInterval(refreshMarket, 300000);
setInterval(refreshCommodity, 600000);
setInterval(updateStatusBadge, 30000);
