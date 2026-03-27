/* alerts.js — High-conviction signal alert bar */

const _dismissedAlerts = new Set(JSON.parse(sessionStorage.getItem('narad_dismissed') || '[]'));
let _alertQueue = [];
let _alertVisible = false;

function createAlertBar() {
    if (document.getElementById('alert-bar')) return;
    const bar = document.createElement('div');
    bar.id = 'alert-bar';
    bar.className = 'alert-bar';
    bar.innerHTML = `
        <div class="px-4 py-2 flex items-center gap-3 cursor-pointer" onclick="dismissAlert()">
            <span class="w-2 h-2 rounded-full bg-red-500 pulse-dot flex-shrink-0"></span>
            <span id="alert-badge" class="font-mono text-[8px] px-1.5 py-0 rounded bg-red-500/20 text-red-400 flex-shrink-0">HIGH</span>
            <span id="alert-text" class="font-mono text-[11px] text-white/70 flex-1 truncate"></span>
            <span id="alert-action" class="font-mono text-[9px] text-white/20 flex-shrink-0"></span>
            <span class="font-mono text-[9px] text-white/15 flex-shrink-0">DISMISS</span>
        </div>
    `;
    bar.style.background = 'rgba(10, 10, 15, 0.95)';
    bar.style.borderBottom = '1px solid rgba(239, 68, 68, 0.2)';
    bar.style.backdropFilter = 'blur(8px)';
    document.body.appendChild(bar);
}

function showAlert(signal) {
    createAlertBar();
    const bar = document.getElementById('alert-bar');
    const text = document.getElementById('alert-text');
    const action = document.getElementById('alert-action');
    const badge = document.getElementById('alert-badge');

    const d = signal.data || {};
    const conv = d.conviction || signal.severity || 'high';
    badge.textContent = conv.toUpperCase();
    badge.className = conv === 'high'
        ? 'font-mono text-[8px] px-1.5 py-0 rounded bg-red-500/20 text-red-400 flex-shrink-0'
        : 'font-mono text-[8px] px-1.5 py-0 rounded bg-amber-500/15 text-amber-400 flex-shrink-0';

    text.textContent = signal.title + (signal.description ? ' — ' + signal.description : '');

    // Show top trade action if available
    const trades = d.top_indian_trades || [];
    if (trades.length) {
        const first = typeof trades[0] === 'string' ? trades[0] : `${trades[0].name}: ${trades[0].direction}`;
        action.textContent = first;
    } else {
        action.textContent = '';
    }

    bar.classList.add('visible');
    _alertVisible = true;

    // Highlight related shipping lanes
    if (d.commodities?.length) {
        const comms = d.commodities.map(c => c.symbol).filter(Boolean);
        if (typeof highlightShippingLanes === 'function') {
            highlightShippingLanes(null, 8000); // highlight all for 8s on high conviction
        }
    }

    // Auto-dismiss after 20s
    setTimeout(() => {
        if (_alertVisible) dismissAlert();
    }, 20000);
}

function dismissAlert() {
    const bar = document.getElementById('alert-bar');
    if (bar) bar.classList.remove('visible');
    _alertVisible = false;

    // Mark current alert as dismissed
    if (_alertQueue.length) {
        const dismissed = _alertQueue.shift();
        _dismissedAlerts.add(dismissed.title);
        sessionStorage.setItem('narad_dismissed', JSON.stringify([..._dismissedAlerts]));
    }

    // Show next in queue
    if (_alertQueue.length) {
        setTimeout(() => showAlert(_alertQueue[0]), 500);
    }
}

// Called by commodity.js after refresh — check for new high-conviction signals
function checkForAlerts(signals) {
    for (const s of signals) {
        const d = s.data || {};
        const conv = d.conviction || s.severity || 'medium';
        if (conv !== 'high') continue;
        if (_dismissedAlerts.has(s.title)) continue;

        _alertQueue.push(s);
    }
    if (_alertQueue.length && !_alertVisible) {
        showAlert(_alertQueue[0]);
    }
}
