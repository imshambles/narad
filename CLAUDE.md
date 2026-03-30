# NARAD — Geopolitical Intelligence Platform

## What This Is
Narad is a Palantir-style geopolitical intelligence platform focused on India. It is NOT a news app — it's a command center with a map-first interface, real-time satellite/aircraft data, commodity trading signals, and AI-powered intelligence assessments.

**Live deployment**: https://narad-337w.onrender.com
**Repo**: https://github.com/imshambles/narad

## Tech Stack
- **Backend**: Python, FastAPI, SQLAlchemy async, SQLite (aiosqlite), APScheduler
- **Frontend**: Jinja2 templates, TailwindCSS CDN, Leaflet.js (map), vanilla JS
- **LLM**: Google Gemini 2.0 Flash (via `google-genai` SDK)
- **Deployment**: Docker Compose locally, Render.com in production
- **Keep-alive**: cron-job.org pinging /health every 10 min

## Architecture

### Data Pipeline (runs automatically via APScheduler)
```
Sources (16) → Cluster → Summarize → Entity Graph → Threat Matrix → Signals → Briefing
  every 5m     10m       10m          10m            15m              15m       30m

Additional pipelines:
- Market data: every 15 min (Yahoo Finance — oil, gold, forex, indices)
- GEOINT: every 10 min (NASA FIRMS thermal + OpenSky aircraft + AIS vessels)
- Commodity signals: every 30 min (maps events to stock buckets)
- Cross-domain correlation: every 10 min (compound signal detection across GEOINT + market + entities)
- Intelligence analyst: every 30 min (Gemini cross-domain analysis, now includes correlation context)
- Entity merge: every 6 hours (fuzzy deduplication of knowledge graph)
```

### Data Sources (16 active)
- **Wire services**: AP, Reuters, AFP/France24
- **Institutional**: UN News, ReliefWeb
- **India-focused**: 6 Google News RSS feeds (India Defence, Diplomacy, Geopolitics, Reuters India, AP India, ANI Wire)
- **OSINT**: Reddit (7 subreddits), Think tanks (14 sources: RAND, Bellingcat, The Diplomat, War on the Rocks), OSINT Twitter (10 accounts via RSSHub + Nitter fallback)
- **GDELT**: India-focused geopolitical event data
- **NewsAPI**: disabled (needs key in .env)

### Key Files
```
narad/
├── app.py                    # FastAPI app, lifespan, source seeding
├── config.py                 # Settings (pydantic-settings, reads .env)
├── database.py               # SQLAlchemy async engine, init_db with auto-migrations
├── models.py                 # ALL models: Source, Article, Event, EventArticle,
│                             #   EventRelationship, Briefing, Entity, EntityRelation,
│                             #   EntityMention, ThreatMatrix, ThreatMatrixHistory,
│                             #   MarketDataPoint, Signal
├── scheduler.py              # APScheduler — all job definitions and timing
├── schemas.py                # Pydantic response schemas
│
├── sources/                  # Data source adapters
│   ├── base.py               # SourceAdapter ABC + RawArticle dataclass
│   ├── rss.py                # RSS/Atom feed parser
│   ├── gdelt.py              # GDELT API (India-focused, exponential backoff on 429)
│   ├── newsapi.py            # NewsAPI.org adapter
│   ├── reddit.py             # Reddit RSS (7 subreddits)
│   ├── thinktanks.py         # 14 think tank RSS feeds in parallel
│   └── osint_twitter.py      # Twitter via RSSHub + Nitter fallback
│
├── pipeline/                 # Processing pipeline
│   ├── normalizer.py         # Raw → normalized article (SHA256 fingerprint)
│   ├── deduplicator.py       # Fingerprint + rapidfuzz fuzzy title matching
│   ├── clusterer.py          # TF-IDF + agglomerative clustering (scikit-learn)
│   ├── summarizer.py         # Gemini: event summary + timeline + entities
│   ├── briefing.py           # Gemini: confidence-scored briefing with evidence chains
│   └── graph_builder.py      # Entity relationship edges
│
├── intel/                    # Intelligence layer
│   ├── entity_graph.py       # Entity knowledge graph + alias resolution + fuzzy merge
│   ├── threat_matrix.py      # India bilateral scores + historical trend snapshots
│   ├── signals.py            # Anomaly detection (spikes, sentiment shifts)
│   ├── correlator.py         # Cross-domain compound signal detection (7 rules)
│   ├── analyst.py            # Gemini: RAW-grade intelligence assessments
│   ├── market_data.py        # Yahoo Finance: oil, gold, forex, indices (10 symbols)
│   ├── geospatial.py         # NASA FIRMS thermal + OpenSky ADS-B + AIS vessels
│   ├── commodity.py          # Event → stock bucket mapping + Gemini trade signals
│   └── query.py              # "Ask Narad" — 30-day NL query with entity graph traversal
│
├── api/                      # REST endpoints
│   ├── articles.py           # /api/articles, /api/sources
│   ├── events.py             # /api/events, /api/events/{id}, /api/events/graph
│   └── intel.py              # /api/intel/query, /api/intel/market, /api/intel/geoint,
│                             #   /api/intel/commodity, /api/intel/entities,
│                             #   /api/intel/threat-matrix/history, /api/intel/vessels
│
├── web/                      # Template routes
│   └── views.py              # / (command center), /events/{id}, /feed, /explore,
│                             #   /admin/status (all routes + IST timezone filter)
│
├── templates/
│   ├── base.html             # Dark theme, monospace, COMMAND nav, LIVE badge
│   ├── briefing.html         # Slim orchestrator (~100 lines): includes partials + JS modules
│   ├── partials/             # Jinja2 includes for sidebar panels
│   │   ├── sidebar_ask.html      # "Ask Narad" query input
│   │   ├── sidebar_stories.html  # Stories with confidence badges, evidence chains, watch signals
│   │   ├── sidebar_markets.html  # MARKETS grid + TRADING SIGNALS container
│   │   └── sidebar_threat.html   # INDIA BILATERAL matrix + trend sparklines + compound signals
│   ├── event_detail.html     # Timeline + facts + sources (dark theme)
│   ├── events.html           # Explore all events (accessible but not in nav)
│   ├── dashboard.html        # Raw feed (accessible but not in nav)
│   ├── graph.html            # vis.js entity graph (accessible via URL)
│   ├── intel.html            # Intel assessments page (merged into main now)
│   └── status.html           # Admin pipeline status
│
└── static/
    ├── css/
    │   ├── custom.css         # Minimal Tailwind overrides
    │   └── briefing.css       # Map layout, sidebar, ticker bar, popup styles
    ├── js/
    │   ├── map_init.js        # Leaflet map setup, icons, zones, static data layers
    │   ├── map_geoint.js      # GEOINT live layers (thermal + aircraft) + refresh
    │   ├── market.js          # Market grid, ticker bar rendering + refresh
    │   ├── commodity.js       # Trading signal cards rendering + refresh
    │   └── sidebar.js         # Toggle, story expand, Ask Narad, live update scheduler
    └── map_data.json          # 200+ static map markers: bases, conflicts, disputes,
                               #   chokepoints, pipelines, shipping lanes, nuclear sites,
                               #   BRI routes, submarine cables, sanctions zones
```

### Map Data (static/map_data.json)
Contains 200+ data points:
- Military bases: India (14), China (15), Pakistan (6), US (19), Russia (9), NATO (8)
- Active conflicts: 11 zones globally
- Nuclear sites: 13
- Missile systems: 9 with range circles
- Border disputes: 13 (LAC, LOC, Crimea, Taiwan, etc.)
- Chokepoints: 10 with daily flow volumes
- Shipping lanes: 11 commodity corridors (oil, LNG, coal, grain, iron)
- Pipelines: 6 (IPI, TAPI, Nord Stream, TurkStream, BTC, CPEC)
- Belt & Road: 3 routes
- Submarine cables: 5
- Sanctions zones: 6

### Intelligence Features

**Cross-Domain Correlation Engine** (`intel/correlator.py`):
7 rules that detect compound signals when multiple domains activate simultaneously:
- `hormuz_oil`: Thermal/military at Hormuz + oil price surge
- `lac_tension_defense`: LAC GEOINT + China/India entity spikes
- `pak_border_escalation`: LOC GEOINT + Pakistan entity spikes
- `gulf_aden_shipping`: Gulf of Aden activity + commodity moves
- `gold_rush_geopolitical`: Multiple high-severity signals + gold surge
- `inr_pressure`: Oil spike + geopolitical tension + India entity activity
- `scs_maritime`: South China Sea military activity + diplomatic entity spikes

Requires 2+ cross-domain factors to trigger. Escalates severity with 3+ or 4+ factors.

**Entity Disambiguation** (`intel/entity_graph.py`):
- 25+ hardcoded aliases (PM Modi → narendra modi, US → united states, etc.)
- Title/prefix stripping (President, PM, Dr, Gen, etc.)
- Fuzzy matching at 88% threshold via rapidfuzz against same-type entities
- Periodic bulk merge job (every 6h) cleans up existing duplicates

**Threat Matrix History** (`models.py: ThreatMatrixHistory`):
- Hourly snapshots of cooperation/tension scores per country
- API endpoint: `/api/intel/threat-matrix/history?country_id=X&days=7`
- Sidebar renders hover sparklines from historical data

**Confidence-Scored Briefings** (`pipeline/briefing.py`):
- Each story includes `confidence` (high/medium/low), `confidence_reason`, `evidence_chain`
- Briefing prompt receives active signals (GEOINT, correlations, spikes) as additional context
- Stories show historical parallels and expandable watch signals in UI

**Extended Query Interface** (`intel/query.py`):
- 30-day lookback (was 72h)
- Query-relevant event scoring with keyword boost
- Entity graph traversal: finds entities mentioned in question, walks their relationships
- Includes correlation signals in context

### Database
SQLite with WAL mode. Auto-creates tables on startup. Auto-migrates missing columns.
- **DO NOT delete the DB** unless you want to lose all historical data
- DB path: `data/narad.db` (local) or `/data/narad.db` (Docker)
- Docker volume `narad_narad_data` persists the DB across rebuilds

### Environment Variables (.env)
```
GEMINI_API_KEY=<required for AI features>
FIRMS_API_KEY=<required for satellite thermal detection>
AISSTREAM_API_KEY=<optional, for live AIS vessel tracking>
NEWSAPI_KEY=<optional>
DATABASE_URL=<optional, defaults to local SQLite>
```

### Running Locally
```bash
# Development (no Docker)
uvicorn narad.app:app --reload

# Docker (persistent DB)
docker compose up -d --build

# Docker logs
docker logs narad -f

# Rebuild after code changes (keeps DB)
docker compose up -d --build

# Force a specific pipeline job
docker exec narad python -c "import asyncio; from narad.intel.commodity import generate_commodity_signals; asyncio.run(generate_commodity_signals())"
```

## User Preferences (IMPORTANT)
- **NO emojis** anywhere in the UI
- **NO news app UX** — no event lists, no article feeds as primary views
- **Dark theme only** — intelligence platform aesthetic
- **Map is the primary interface** — everything else is secondary sidebar
- **India-focused** — every feature should answer "what does this mean for India?"
- **No BBC, Al Jazeera, NPR** — user considers them biased. Only wire services + OSINT
- **IST timezone** throughout (custom Jinja2 filter `|ist`)
- **Professional, data-dense UI** — monospace fonts, minimal whitespace
- **Self-explanatory UI** — every panel needs context labels
- **Mobile-friendly** — responsive sidebar, touch targets

## Known Issues / TODO
- Gemini free tier: 15 RPM limit — some jobs may fail on first run, succeed on retry
- OSINT Twitter depends on RSSHub/Nitter availability — multiple fallbacks configured but all proxies can be unreliable
- GDELT rate-limited (429) frequently — exponential backoff handles this (2min→4min→8min→max 30min)
- AIS vessel tracking requires AISSTREAM_API_KEY — falls back to simulation without it
- Stock buckets in sidebar need visual redesign — currently text-heavy
- Render deployment sometimes 502s on cold start — cron-job.org keeps it warm
- Some map coordinates may need fine-tuning (verify when zoomed in)
- Entity merge can be aggressive at 88% fuzzy threshold — may need tuning for short entity names
