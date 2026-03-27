/* commodity.js — Trading signals rendering */

// Mini sparkline SVG generator
function sparkline(prices, width, height, color) {
    if (!prices || prices.length < 2) return '';
    const min = Math.min(...prices);
    const max = Math.max(...prices);
    const range = max - min || 1;
    const step = width / (prices.length - 1);
    const points = prices.map((p, i) => `${i * step},${height - ((p - min) / range) * height}`).join(' ');
    return `<svg width="${width}" height="${height}" style="display:inline-block;vertical-align:middle;margin-left:4px"><polyline points="${points}" fill="none" stroke="${color}" stroke-width="1" opacity="0.7"/></svg>`;
}

// Fetch sparkline data for a symbol and cache it
const _sparkCache = {};
async function getSparkline(sym) {
    if (_sparkCache[sym]) return _sparkCache[sym];
    try {
        const resp = await fetch(`/api/intel/market/history?symbol=${encodeURIComponent(sym)}&limit=24`);
        const data = await resp.json();
        const prices = data.map(d => d.price);
        _sparkCache[sym] = prices;
        setTimeout(() => delete _sparkCache[sym], 300000); // cache 5min
        return prices;
    } catch { return []; }
}

function parseTrade(t) {
    const m = t.match(/^(.+?):\s*(long|short|positive|negative|mixed|neutral|buy|sell|already moving|short-term negative)[^\w]*[---]*\s*(.*)/i);
    if (m) return { name: m[1].trim(), dir: m[2].trim().toLowerCase(), reason: m[3].trim() };
    return { name: t, dir: '', reason: '' };
}

function dirBadge(dir) {
    if (!dir) return '';
    const up = ['long','positive','buy'];
    const down = ['short','negative','sell','short-term negative'];
    if (up.some(u => dir.includes(u))) return '<span class="font-mono text-[8px] px-1 py-0 rounded bg-green-500/15 text-green-400/70">LONG</span>';
    if (down.some(d => dir.includes(d))) return '<span class="font-mono text-[8px] px-1 py-0 rounded bg-red-500/15 text-red-400/70">SHORT</span>';
    return '<span class="font-mono text-[8px] px-1 py-0 rounded bg-white/5 text-white/30">MIXED</span>';
}

function renderTradeList(trades) {
    let html = '';
    for (const t of trades) {
        if (typeof t === 'string') {
            const p = parseTrade(t);
            html += `<div class="flex items-center gap-1.5 py-0.5">
                ${dirBadge(p.dir)}
                <span class="font-mono text-[9px] text-white/45">${p.name}</span>
                ${p.reason ? `<span class="text-[8px] text-white/20 ml-auto truncate max-w-[140px]">${p.reason}</span>` : ''}
            </div>`;
        } else {
            html += `<div class="flex items-center gap-1.5 py-0.5">
                ${dirBadge(t.direction)}
                <span class="font-mono text-[9px] text-white/45">${t.name}</span>
                <span class="text-[8px] text-white/20 ml-auto truncate max-w-[140px]">${t.reason||''}</span>
            </div>`;
        }
    }
    return html;
}

async function refreshCommodity() {
    try {
        const resp = await fetch('/api/intel/commodity');
        const signals = await resp.json();
        const el = document.getElementById('commodity-panel');
        const countEl = document.getElementById('signal-count');
        if (!el) return;
        if (!signals.length) {
            el.innerHTML = '<span class="font-mono text-[9px] text-white/10">No active signals</span>';
            if (countEl) countEl.textContent = '';
            return;
        }
        if (countEl) countEl.textContent = signals.length + ' active';

        let html = '';
        for (const s of signals) {
            const d = s.data || {};
            const conv = d.conviction || s.severity || 'medium';
            const convColor = conv === 'high' ? 'bg-red-500/20 text-red-400' : conv === 'medium' ? 'bg-amber-500/15 text-amber-400' : 'bg-white/5 text-white/30';
            const barColor = conv === 'high' ? 'bg-red-500' : conv === 'medium' ? 'bg-amber-500' : 'bg-white/20';
            const tf = d.timeframe || '';
            const isWatchlisted = typeof signalMatchesWatchlist === 'function' && signalMatchesWatchlist(s);
            const watchBorder = isWatchlisted ? 'border-l-2 border-blue-400/30' : '';

            html += `<div class="border-b border-white/5 last:border-0 ${watchBorder}">
                <div class="h-0.5 ${barColor} opacity-40"></div>
                <div class="py-2 cursor-pointer" onclick="this.nextElementSibling.classList.toggle('hidden')">
                    <div class="flex items-center gap-1.5 mb-1">
                        ${isWatchlisted ? '<span class="font-mono text-[7px] px-1 py-0 rounded bg-blue-500/15 text-blue-400/60">WATCH</span>' : ''}
                        <span class="font-mono text-[8px] px-1.5 py-0 rounded ${convColor}">${conv.toUpperCase()}</span>
                        ${tf ? `<span class="font-mono text-[8px] px-1 py-0 rounded bg-white/5 text-white/20">${tf}</span>` : ''}
                        <span class="font-mono text-[9px] text-white/15 ml-auto">&#x25BE;</span>
                    </div>
                    <p class="font-mono text-[10px] text-white/60 leading-snug">${s.title}</p>
                    <p class="text-[10px] text-white/30 leading-snug mt-0.5">${s.description}</p>
                </div>
                <div class="hidden pb-2 px-1">
                <button class="font-mono text-[8px] text-blue-400/40 hover:text-blue-400/70 mb-1.5 block" onclick="focusSignalOnMap('${(d.bucket_name||'').replace(/'/g,"\\'")}')">SHOW ON MAP</button>`;

            // Market context with price deltas since trigger
            const deltas = d.price_deltas || {};
            if (d.market_context && Object.keys(d.market_context).length) {
                html += '<div class="mb-2">';
                for (const [sym, mc] of Object.entries(d.market_context)) {
                    const name = MKT_SHORT[sym] || sym;
                    const c = (mc.change_1d||0) >= 0 ? 'text-green-400/60' : 'text-red-400/60';
                    const delta = deltas[sym];
                    let deltaHtml = '';
                    if (delta) {
                        const dc = delta.delta_pct >= 0 ? 'text-green-400' : 'text-red-400';
                        deltaHtml = `<span class="font-mono text-[8px] ${dc} ml-auto">${delta.delta_pct>=0?'+':''}${delta.delta_pct.toFixed(2)}% since signal</span>`;
                    }
                    html += `<div class="flex items-center gap-2 py-0.5" data-spark-sym="${sym}">
                        <span class="font-mono text-[9px] text-white/25 w-10">${name}</span>
                        <span class="font-mono text-[9px] text-white/40">${mc.price?.toLocaleString(undefined,{maximumFractionDigits:1})}</span>
                        <span class="font-mono text-[8px] ${c}">${(mc.change_1d||0)>=0?'+':''}${(mc.change_1d||0).toFixed(1)}%</span>
                        <span class="spark-slot"></span>
                        ${deltaHtml}
                    </div>`;
                }
                html += '</div>';
            }

            // Indian trades
            const indTrades = d.top_indian_trades || d.stocks_india || [];
            if (indTrades.length) {
                html += '<p class="font-mono text-[8px] text-white/15 mb-0.5 mt-1">INDIA</p><div class="space-y-0.5">';
                html += renderTradeList(indTrades);
                html += '</div>';
            }

            // Global trades
            const glbTrades = d.top_global_trades || d.stocks_global || [];
            if (glbTrades.length) {
                html += '<p class="font-mono text-[8px] text-white/15 mb-0.5 mt-2">GLOBAL</p><div class="space-y-0.5">';
                html += renderTradeList(glbTrades);
                html += '</div>';
            }

            // Triggering events
            if (d.triggering_events?.length) {
                html += '<p class="font-mono text-[8px] text-white/10 mt-2 mb-0.5">TRIGGERS</p>';
                for (const ev of d.triggering_events.slice(0,3)) {
                    html += `<p class="text-[8px] text-white/15 py-0.5 leading-snug">${ev.title} <span class="text-white/8">${ev.articles||''}src</span></p>`;
                }
            }

            // Historical precedents
            if (d.precedents?.length) {
                html += '<p class="font-mono text-[8px] text-amber-400/40 mt-2 mb-0.5">PRECEDENT</p>';
                for (const p of d.precedents.slice(0, 2)) {
                    html += `<div class="px-1.5 py-1 mb-1 rounded bg-amber-500/5 border-l border-amber-500/20">
                        <p class="font-mono text-[8px] text-amber-300/50">${p.event} <span class="text-white/15">${p.date}</span></p>`;
                    if (p.impacts?.length) {
                        html += '<div class="flex flex-wrap gap-x-3 gap-y-0 mt-0.5">';
                        for (const imp of p.impacts.slice(0, 4)) {
                            const ic = imp.change.startsWith('+') ? 'text-green-400/50' : imp.change.startsWith('-') ? 'text-red-400/50' : 'text-white/25';
                            html += `<span class="text-[8px]"><span class="text-white/30">${imp.name}</span> <span class="${ic}">${imp.change}</span> <span class="text-white/10">${imp.period}</span></span>`;
                        }
                        html += '</div>';
                    }
                    html += '</div>';
                }
            }
            // LLM precedent summary (from Gemini)
            if (d.precedent) {
                html += `<div class="px-1.5 py-1 mt-1 rounded bg-amber-500/5 border-l border-amber-500/20"><p class="font-mono text-[8px] text-amber-300/40">${d.precedent}</p></div>`;
            }

            // Risk callout
            if (d.risk) html += `<div class="mt-2 px-1.5 py-1 rounded bg-red-500/5 border-l border-red-500/20"><p class="font-mono text-[8px] text-red-400/40">RISK: ${d.risk}</p></div>`;

            html += '</div></div>';
        }
        el.innerHTML = html;

        // Trigger alerts for high-conviction signals
        if (typeof checkForAlerts === 'function') checkForAlerts(signals);

        // Load sparklines async for market context rows
        document.querySelectorAll('[data-spark-sym]').forEach(async row => {
            const sym = row.dataset.sparkSym;
            const prices = await getSparkline(sym);
            const slot = row.querySelector('.spark-slot');
            if (slot && prices.length >= 2) {
                const color = prices[prices.length-1] >= prices[0] ? '#22c55e' : '#ef4444';
                slot.innerHTML = sparkline(prices, 40, 12, color);
            }
        });
    } catch(e) { console.log('Commodity refresh failed:', e); }
}

// Initial load
refreshCommodity();
