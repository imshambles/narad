/* market.js — Market grid, ticker bar, and refresh logic */

const MKT_SHORT = { 'BZ=F':'BRENT','CL=F':'WTI','GC=F':'GOLD','NG=F':'NATGAS','ZW=F':'WHEAT','INR=X':'USD/INR','CNY=X':'USD/CNY','EURINR=X':'EUR/INR','^NSEI':'NIFTY','^BSESN':'SENSEX' };
const MKT_ORDER = ['BZ=F','CL=F','GC=F','NG=F','ZW=F','INR=X','CNY=X','EURINR=X','^NSEI','^BSESN'];
const TICKER_ORDER = ['BZ=F','CL=F','GC=F','INR=X','^NSEI','NG=F','ZW=F','^BSESN','CNY=X','EURINR=X'];

function renderMarketGrid(data) {
    const grid = document.getElementById('market-grid');
    const updEl = document.getElementById('market-updated');
    if (!grid || !Object.keys(data).length) return;

    let html = '';
    const groups = [
        { label: 'COMMODITIES', syms: ['BZ=F','CL=F','GC=F','NG=F','ZW=F'] },
        { label: 'FOREX', syms: ['INR=X','CNY=X','EURINR=X'] },
        { label: 'INDICES', syms: ['^NSEI','^BSESN'] },
    ];
    for (const g of groups) {
        html += `<div class="col-span-2 font-mono text-[8px] text-white/10 mt-1 first:mt-0 border-b border-white/5 pb-0.5">${g.label}</div>`;
        for (const sym of g.syms) {
            const d = data[sym]; if (!d) continue;
            const c1d = d.change_1d >= 0 ? 'text-green-400/60' : 'text-red-400/60';
            const c7d = d.change_7d >= 0 ? 'text-green-400/40' : 'text-red-400/40';
            const a1 = d.change_1d >= 0 ? '+' : '';
            const a7 = d.change_7d >= 0 ? '+' : '';
            html += `<div class="flex items-center justify-between py-0.5">
                <span class="font-mono text-[9px] text-white/30 w-12 truncate">${MKT_SHORT[sym]}</span>
                <span class="font-mono text-[10px] text-white/55">${d.price.toLocaleString(undefined,{maximumFractionDigits:sym.includes('=X')?2:1})}</span>
            </div>
            <div class="flex items-center justify-end gap-2 py-0.5">
                <span class="font-mono text-[9px] ${c1d}">${a1}${d.change_1d.toFixed(1)}%</span>
                <span class="font-mono text-[8px] ${c7d}">${a7}${d.change_7d.toFixed(1)}%<span class="text-white/10">w</span></span>
            </div>`;
        }
    }
    grid.innerHTML = html;
    if (updEl) {
        const ts = Object.values(data).find(d => d.fetched_at);
        if (ts) updEl.textContent = new Date(ts.fetched_at).toLocaleTimeString('en-IN',{hour:'2-digit',minute:'2-digit',timeZone:'Asia/Kolkata'}) + ' IST';
    }
}

function renderTicker(data) {
    const el = document.getElementById('market-bar');
    if (!el || !Object.keys(data).length) return;
    let html = '';
    for (const sym of TICKER_ORDER) {
        const d = data[sym]; if (!d) continue;
        const c = d.change_1d >= 0 ? 'text-green-400/70' : 'text-red-400/70';
        const c7 = d.change_7d >= 0 ? 'text-green-400/40' : 'text-red-400/40';
        const a = d.change_1d >= 0 ? '&#9650;' : '&#9660;';
        html += `<div class="flex items-center gap-1 flex-shrink-0">
            <span class="font-mono text-[9px] text-white/20">${MKT_SHORT[sym]}</span>
            <span class="font-mono text-[10px] text-white/50">${d.price.toLocaleString(undefined,{maximumFractionDigits:1})}</span>
            <span class="font-mono text-[9px] ${c}">${a}${Math.abs(d.change_1d).toFixed(1)}%</span>
            <span class="font-mono text-[8px] ${c7}">${d.change_7d>=0?'+':''}${d.change_7d.toFixed(1)}%w</span>
        </div>`;
    }
    el.innerHTML = html;
}

let lastMarketUpdate = 0;

async function refreshMarket() {
    try {
        const resp = await fetch('/api/intel/market');
        const data = await resp.json();
        if (!Object.keys(data).length) return;
        renderMarketGrid(data);
        renderTicker(data);
        lastMarketUpdate = Date.now();
        updateStatusBadge();
    } catch (e) { console.log('Market refresh failed:', e); }
}

// Initial load
fetch('/api/intel/market').then(r => r.json()).then(data => {
    renderMarketGrid(data);
    renderTicker(data);
});
