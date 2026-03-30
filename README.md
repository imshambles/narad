# NARAD

**Geopolitical Intelligence Platform for India**

Narad is a Palantir-style command center that ingests 16+ real-time data sources, clusters events, tracks entities, monitors satellites and aircraft, correlates market movements with geopolitical shifts, and produces AI-powered intelligence briefings -- all through a map-first dark interface.

**Live**: https://narad-337w.onrender.com

---

## What It Does

Narad runs a continuous intelligence pipeline that transforms raw open-source data into actionable analysis:

```
Sources (16)  -->  Cluster  -->  Summarize  -->  Entity Graph  -->  Threat Matrix  -->  Signals  -->  Briefing
  every 5m         10m          10m             10m               15m                15m           30m

Cross-Domain:
  Market data      every 15m    Yahoo Finance (oil, gold, forex, indices)
  GEOINT           every 10m    NASA FIRMS thermal + OpenSky ADS-B + AIS vessels
  Commodity intel   every 30m    Event-to-stock-bucket mapping + trade signals
  Correlation       every 10m    Cross-domain compound signal detection
  Analyst           every 30m    Gemini-powered RAW-grade assessments
  Entity merge      every 6h     Fuzzy deduplication of knowledge graph
```

### Intelligence Capabilities

- **Event Clustering**: TF-IDF + agglomerative clustering groups articles into coherent events across 16 sources
- **Entity Knowledge Graph**: Persistent graph of countries, leaders, organizations with co-occurrence tracking, sentiment, and relationship evolution
- **Entity Disambiguation**: Alias resolution (25+ known aliases) + fuzzy matching (rapidfuzz at 88% threshold) to prevent duplicate entities like "Modi" / "PM Modi" / "Narendra Modi"
- **India Threat Matrix**: Live bilateral relationship scores (cooperation vs tension) for every country India interacts with, with historical trend snapshots stored hourly
- **Signal Detection**: Mention spikes (3x baseline), new entity relationships, sentiment shifts
- **Cross-Domain Correlation Engine**: 7 rules that detect compound signals across GEOINT + market + entity domains (e.g., "Hormuz thermal anomaly + Brent crude spike = critical alert")
- **Confidence-Scored Briefings**: 5-7 stories ranked by severity with confidence levels, evidence chains, historical parallels, scenarios (likely/best/worst), and watch signals
- **"Ask Narad"**: Natural language queries against 30 days of data with entity graph traversal and cross-domain context
- **GEOINT**: NASA FIRMS thermal anomalies across 8 monitored zones, OpenSky military aircraft tracking, AIS vessel positions (live via AISStream or simulated)
- **Market Intelligence**: 10 symbols tracked (Brent, WTI, Gold, Natural Gas, Wheat, INR, CNY, EUR/INR, Nifty 50, Sensex) with 1d/7d/30d deltas
- **Commodity Signals**: Events mapped to stock buckets (defense, oil, FMCG, infrastructure) with Gemini-generated trade signal analysis

### Data Sources

| Category | Sources |
|----------|---------|
| Wire services | AP, Reuters, AFP/France24 |
| Institutional | UN News, ReliefWeb |
| India-focused | 6 Google News RSS feeds (Defence, Diplomacy, Geopolitics, Reuters India, AP India, ANI Wire) |
| OSINT social | Reddit (7 subreddits), OSINT Twitter/X (10 accounts via RSSHub + Nitter fallback) |
| Think tanks | 14 sources (RAND, Bellingcat, The Diplomat, War on the Rocks, etc.) |
| Event data | GDELT (India-focused, with exponential backoff on rate limits) |
| Market | Yahoo Finance (10 symbols) |
| Satellite | NASA FIRMS (8 zones), OpenSky ADS-B |
| Maritime | AISStream WebSocket (optional, falls back to simulation) |

### Map Data

200+ static data points rendered on Leaflet.js:
- Military bases: India (14), China (15), Pakistan (6), US (19), Russia (9), NATO (8)
- Active conflicts (11), nuclear sites (13), missile systems (9 with range circles)
- Border disputes (13): LAC, LOC, Crimea, Taiwan, etc.
- Chokepoints (10) with daily flow volumes
- Shipping lanes (11), pipelines (6), Belt & Road routes (3)
- Submarine cables (5), sanctions zones (6)

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python, FastAPI, SQLAlchemy async, SQLite (aiosqlite), APScheduler |
| Frontend | Jinja2 templates, TailwindCSS CDN, Leaflet.js, vanilla JS |
| LLM | Google Gemini 2.0 Flash (via `google-genai` SDK) |
| NLP | scikit-learn (TF-IDF, clustering), rapidfuzz (dedup + entity merge) |
| Deployment | Docker Compose (local), Render.com (production) |

---

## Setup

### Prerequisites
- Python 3.11+
- A Gemini API key (free tier works, 15 RPM limit)

### Environment Variables

Create a `.env` file:
```
GEMINI_API_KEY=<required for AI features>
FIRMS_API_KEY=<required for satellite thermal detection>
AISSTREAM_API_KEY=<optional, for live vessel tracking>
NEWSAPI_KEY=<optional>
DATABASE_URL=<optional, defaults to local SQLite>
```

### Run Locally

```bash
# Install dependencies
pip install -r requirements.txt

# Development
uvicorn narad.app:app --reload

# Open http://localhost:8000
```

### Run with Docker

```bash
# Start (persistent DB via volume)
docker compose up -d --build

# Logs
docker logs narad -f

# Rebuild after code changes (keeps DB)
docker compose up -d --build

# Force a specific pipeline job
docker exec narad python -c "import asyncio; from narad.intel.correlator import run_correlations; asyncio.run(run_correlations())"
```

---

## Architecture

```
narad/
├── app.py                    # FastAPI app, lifespan, source seeding
├── config.py                 # Settings (pydantic-settings, reads .env)
├── database.py               # SQLAlchemy async engine, init_db with auto-migrations
├── models.py                 # All 14 models (Source, Article, Event, Entity,
│                             #   ThreatMatrix, ThreatMatrixHistory, Signal, etc.)
├── scheduler.py              # APScheduler — all job definitions and timing
│
├── sources/                  # Data source adapters
│   ├── base.py               # SourceAdapter ABC + RawArticle dataclass
│   ├── rss.py                # RSS/Atom feed parser
│   ├── gdelt.py              # GDELT API (exponential backoff on 429)
│   ├── reddit.py             # Reddit RSS (7 subreddits)
│   ├── thinktanks.py         # 14 think tank RSS feeds in parallel
│   └── osint_twitter.py      # Twitter via RSSHub + Nitter fallback
│
├── pipeline/                 # Processing pipeline
│   ├── normalizer.py         # Raw -> normalized article (SHA256 fingerprint)
│   ├── deduplicator.py       # Fingerprint + rapidfuzz fuzzy title matching
│   ├── clusterer.py          # TF-IDF + agglomerative clustering
│   ├── summarizer.py         # Gemini: event summary + timeline + entities
│   ├── briefing.py           # Gemini: confidence-scored briefing with evidence chains
│   └── graph_builder.py      # Entity relationship edges
│
├── intel/                    # Intelligence layer
│   ├── entity_graph.py       # Entity knowledge graph + alias resolution + fuzzy merge
│   ├── threat_matrix.py      # India bilateral scores + historical snapshots
│   ├── signals.py            # Anomaly detection (spikes, sentiment shifts)
│   ├── correlator.py         # Cross-domain compound signal detection (7 rules)
│   ├── analyst.py            # Gemini: RAW-grade intelligence assessments
│   ├── market_data.py        # Yahoo Finance: 10 symbols
│   ├── geospatial.py         # NASA FIRMS + OpenSky ADS-B + AIS vessels
│   ├── commodity.py          # Event-to-stock mapping + trade signals
│   └── query.py              # "Ask Narad" — 30-day NL query with graph traversal
│
├── api/                      # REST endpoints
│   ├── articles.py           # /api/articles, /api/sources
│   ├── events.py             # /api/events, /api/events/{id}, /api/events/graph
│   └── intel.py              # /api/intel/query, /api/intel/market, /api/intel/geoint,
│                             #   /api/intel/threat-matrix/history, /api/intel/vessels, etc.
│
├── web/views.py              # Template routes (/, /explore, /events/{id}, /intel, etc.)
│
├── templates/
│   ├── base.html             # Dark theme, monospace, COMMAND nav, LIVE badge
│   ├── briefing.html         # Main command center (map + sidebar)
│   ├── partials/             # Sidebar panels (stories, markets, threat matrix, ask)
│   └── ...                   # Event detail, explore, graph, status pages
│
└── static/
    ├── css/                  # briefing.css, custom.css
    ├── js/                   # map_init, map_geoint, map_ships, market, commodity, sidebar, alerts
    └── map_data.json         # 200+ static geopolitical markers
```

### Database

SQLite with WAL mode. 14 tables. Auto-creates on startup, auto-migrates missing columns.

- DB path: `data/narad.db` (local) or `/data/narad.db` (Docker)
- Docker volume `narad_narad_data` persists across rebuilds
- **Do not delete the DB** unless you want to lose all historical data (threat matrix trends, entity graph, briefing history)

### API Endpoints

| Endpoint | Description |
|----------|-------------|
| `POST /api/intel/query` | "Ask Narad" — natural language query |
| `GET /api/intel/market` | Latest commodity/forex/index prices |
| `GET /api/intel/market/history?symbol=BZ=F` | Price history for sparklines |
| `GET /api/intel/commodity` | Active trading signals with live price deltas |
| `GET /api/intel/geoint` | Thermal + aircraft signal summary |
| `GET /api/intel/vessels` | Live AIS or simulated vessel positions |
| `GET /api/intel/threat-matrix` | Current bilateral scores |
| `GET /api/intel/threat-matrix/history?days=7` | Historical trend data |
| `GET /api/intel/signals` | All active intelligence signals |
| `GET /api/intel/entities` | Entity list with mention counts |
| `GET /api/intel/entity-graph` | Nodes + edges for visualization |
| `GET /api/events` | Event list with filters |
| `GET /api/events/{id}` | Event detail with articles |

---

## Key Design Decisions

- **Map-first**: The map is the primary interface. Everything else is a sidebar panel.
- **India-focused**: Every feature answers "what does this mean for India?"
- **No news app UX**: No article feeds or event lists as primary views. Briefings and signals.
- **Dark theme only**: Intelligence platform aesthetic. Monospace fonts, minimal whitespace.
- **Wire services + OSINT only**: No BBC, Al Jazeera, NPR. AP, Reuters, AFP + open-source intelligence.
- **IST timezone**: All times displayed in Indian Standard Time.
- **Cross-domain correlation**: The moat is connecting GEOINT + market + entity + event data, not just aggregating sources.

---

## Known Issues

- Gemini free tier: 15 RPM limit -- some jobs may fail on first run, succeed on retry
- OSINT Twitter depends on RSSHub/Nitter availability (unreliable, multiple fallbacks configured)
- GDELT rate-limited (429) frequently -- exponential backoff handles this gracefully
- Render deployment sometimes 502s on cold start -- cron-job.org pings /health every 10 min
- Stock bucket cards in sidebar are text-heavy (visual redesign pending)
- Some map coordinates may need fine-tuning when zoomed in
