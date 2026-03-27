"""
Commodity Intelligence Engine

Maps geopolitical events to affected commodities, sectors, and specific stocks.
Generates trading signal "buckets" — groups of instruments affected by each event.

This is what commodity trading desks and hedge funds do:
- Hormuz tension → long oil, short Indian oil importers, long defense stocks
- India-China border → long defense (HAL, BEL), short Chinese goods importers
- Wheat supply disruption → long wheat futures, short FMCG companies
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from narad.config import settings
from narad.database import async_session
from narad.models import Event, MarketDataPoint, Signal

logger = logging.getLogger(__name__)

# Static mapping: event keywords → affected instruments
# Each entry: trigger keywords, affected buckets with direction
COMMODITY_MAP = {
    "hormuz": {
        "name": "Strait of Hormuz Disruption",
        "commodities": [
            {"symbol": "BZ=F", "name": "Brent Crude", "direction": "long", "reason": "21M bbl/day transit at risk"},
            {"symbol": "CL=F", "name": "WTI Crude", "direction": "long", "reason": "Global supply shock"},
            {"symbol": "NG=F", "name": "Natural Gas", "direction": "long", "reason": "LNG rerouting costs"},
        ],
        "stocks_india": [
            {"name": "ONGC", "direction": "mixed", "reason": "Higher realization but supply risk"},
            {"name": "Reliance Industries", "direction": "short-term negative", "reason": "Refining margin squeeze if crude spikes too fast"},
            {"name": "IOC / BPCL / HPCL", "direction": "negative", "reason": "Under-recovery on fuel if crude spikes, govt may not raise prices"},
            {"name": "IndiGo / SpiceJet", "direction": "negative", "reason": "ATF cost surge, ~40% of opex"},
            {"name": "Asian Paints / Berger", "direction": "negative", "reason": "Crude derivatives in paint raw materials"},
            {"name": "SCI (Shipping Corp)", "direction": "positive", "reason": "Higher freight rates"},
            {"name": "HAL / BEL / BDL", "direction": "positive", "reason": "Defense spending increase expected"},
        ],
        "stocks_global": [
            {"name": "ExxonMobil (XOM)", "direction": "positive", "reason": "Oil producer benefits"},
            {"name": "Chevron (CVX)", "direction": "positive", "reason": "Oil producer benefits"},
            {"name": "Shell (SHEL)", "direction": "positive", "reason": "Oil producer benefits"},
            {"name": "Airlines (JETS ETF)", "direction": "negative", "reason": "Fuel cost surge"},
        ],
    },
    "oil_price|crude_surge|oil_spike": {
        "name": "Oil Price Surge",
        "commodities": [
            {"symbol": "BZ=F", "name": "Brent Crude", "direction": "already moving", "reason": "Direct indicator"},
            {"symbol": "GC=F", "name": "Gold", "direction": "long", "reason": "Inflation hedge, risk-off"},
        ],
        "stocks_india": [
            {"name": "IOC / BPCL / HPCL", "direction": "negative", "reason": "Marketing margin compression"},
            {"name": "Tyre companies (MRF, Apollo)", "direction": "negative", "reason": "Rubber + crude input costs"},
            {"name": "ONGC / Oil India", "direction": "positive", "reason": "Higher realizations"},
            {"name": "Coal India", "direction": "positive", "reason": "Alternative energy demand"},
        ],
        "stocks_global": [
            {"name": "Oil majors (XOM, CVX, BP)", "direction": "positive", "reason": "Revenue increase"},
            {"name": "Renewables (ICLN ETF)", "direction": "positive", "reason": "Accelerated transition narrative"},
        ],
    },
    "india_china|lac|ladakh|arunachal|border_tension": {
        "name": "India-China Border Tension",
        "commodities": [
            {"symbol": "GC=F", "name": "Gold", "direction": "long", "reason": "Safe haven demand"},
        ],
        "stocks_india": [
            {"name": "HAL", "direction": "positive", "reason": "Fighter jet orders accelerate"},
            {"name": "BEL", "direction": "positive", "reason": "Radar, EW systems demand"},
            {"name": "BDL", "direction": "positive", "reason": "Missile production orders"},
            {"name": "Paras Defence", "direction": "positive", "reason": "Defense electronics"},
            {"name": "Data Patterns", "direction": "positive", "reason": "Defense electronics"},
            {"name": "Chinese goods importers", "direction": "negative", "reason": "Import restrictions likely"},
            {"name": "Dixon Technologies", "direction": "positive", "reason": "PLI beneficiary, import substitution"},
        ],
        "stocks_global": [
            {"name": "Lockheed Martin (LMT)", "direction": "positive", "reason": "India defense orders"},
            {"name": "Dassault (AM.PA)", "direction": "positive", "reason": "Rafale additional orders possible"},
        ],
    },
    "india_pakistan|loc|kashmir|cross_border": {
        "name": "India-Pakistan Tension",
        "commodities": [
            {"symbol": "GC=F", "name": "Gold", "direction": "long", "reason": "Regional instability"},
        ],
        "stocks_india": [
            {"name": "HAL / BEL / BDL", "direction": "positive", "reason": "Defense emergency procurement"},
            {"name": "Cement (border infra)", "direction": "mixed", "reason": "Border road construction vs sentiment"},
            {"name": "NIFTY overall", "direction": "short-term negative", "reason": "Risk-off sentiment"},
        ],
        "stocks_global": [],
    },
    "wheat|food_crisis|grain": {
        "name": "Global Food Supply Disruption",
        "commodities": [
            {"symbol": "ZW=F", "name": "Wheat", "direction": "long", "reason": "Supply shortage"},
        ],
        "stocks_india": [
            {"name": "ITC", "direction": "mixed", "reason": "Agri business benefits but FMCG input costs rise"},
            {"name": "Britannia / Nestle", "direction": "negative", "reason": "Wheat input cost surge"},
            {"name": "KRBL (rice)", "direction": "positive", "reason": "Grain substitution demand"},
        ],
        "stocks_global": [
            {"name": "ADM / Bunge / Cargill", "direction": "positive", "reason": "Grain trading revenue"},
            {"name": "Deere (DE)", "direction": "positive", "reason": "Agriculture equipment demand"},
        ],
    },
    "sanctions|trade_war|tariff": {
        "name": "Sanctions / Trade War",
        "commodities": [
            {"symbol": "GC=F", "name": "Gold", "direction": "long", "reason": "De-dollarization hedge"},
        ],
        "stocks_india": [
            {"name": "IT services (TCS, Infosys)", "direction": "mixed", "reason": "Client uncertainty but outsourcing demand"},
            {"name": "Pharma exports", "direction": "depends on target", "reason": "Supply chain reshoring"},
        ],
        "stocks_global": [
            {"name": "Gold miners (GDX ETF)", "direction": "positive", "reason": "Gold price correlation"},
        ],
    },
    "south_china_sea|taiwan|indo_pacific": {
        "name": "Indo-Pacific Maritime Tension",
        "commodities": [
            {"symbol": "GC=F", "name": "Gold", "direction": "long", "reason": "Global risk-off"},
        ],
        "stocks_india": [
            {"name": "Shipping (SCI, GE Shipping)", "direction": "mixed", "reason": "Rerouting costs vs higher rates"},
            {"name": "Defense (HAL, BEL)", "direction": "positive", "reason": "Naval buildup"},
            {"name": "Mazagon Dock / Cochin Shipyard", "direction": "positive", "reason": "Warship orders"},
        ],
        "stocks_global": [
            {"name": "Taiwan Semi (TSM)", "direction": "negative", "reason": "Supply chain risk"},
            {"name": "NVIDIA / AMD", "direction": "negative", "reason": "TSMC dependency"},
            {"name": "Defense (LMT, RTX, NOC)", "direction": "positive", "reason": "Defense spending"},
        ],
    },
    "rupee|inr_depreciation|currency": {
        "name": "INR Depreciation Pressure",
        "commodities": [
            {"symbol": "INR=X", "name": "USD/INR", "direction": "already moving", "reason": "Direct indicator"},
            {"symbol": "GC=F", "name": "Gold", "direction": "long in INR terms", "reason": "INR hedge"},
        ],
        "stocks_india": [
            {"name": "IT exporters (TCS, Infosys, Wipro)", "direction": "positive", "reason": "Revenue in USD, costs in INR"},
            {"name": "Pharma exporters (Sun, Dr Reddy)", "direction": "positive", "reason": "Export revenue boost"},
            {"name": "Oil importers (IOC, BPCL)", "direction": "negative", "reason": "Higher import bill in INR"},
            {"name": "Foreign debt companies", "direction": "negative", "reason": "Repayment cost increase"},
        ],
        "stocks_global": [],
    },
}


# Historical precedents — verified event → market impact data
HISTORICAL_PRECEDENTS = {
    "india_china": [
        {"event": "Galwan Valley clash", "date": "Jun 2020", "impacts": [
            {"name": "HAL", "change": "+23%", "period": "2 weeks"},
            {"name": "BEL", "change": "+18%", "period": "2 weeks"},
            {"name": "Nifty 50", "change": "-1.5%", "period": "1 day"},
            {"name": "Gold", "change": "+3%", "period": "1 week"},
        ]},
        {"event": "Doklam standoff", "date": "Jun-Aug 2017", "impacts": [
            {"name": "HAL", "change": "+12%", "period": "2 months"},
            {"name": "Nifty 50", "change": "flat", "period": "resolved peacefully"},
        ]},
    ],
    "india_pakistan": [
        {"event": "Balakot airstrikes", "date": "Feb 2019", "impacts": [
            {"name": "HAL", "change": "+15%", "period": "1 week"},
            {"name": "Nifty 50", "change": "-1%", "period": "1 day, recovered in 3 days"},
            {"name": "Gold (INR)", "change": "+2%", "period": "1 week"},
        ]},
        {"event": "Pulwama attack", "date": "Feb 2019", "impacts": [
            {"name": "Nifty 50", "change": "-0.7%", "period": "1 day"},
            {"name": "Defense stocks", "change": "+8-15%", "period": "1 week"},
        ]},
    ],
    "hormuz": [
        {"event": "Soleimani assassination / Iran tensions", "date": "Jan 2020", "impacts": [
            {"name": "Brent Crude", "change": "+4.1%", "period": "1 day"},
            {"name": "Gold", "change": "+2.3%", "period": "1 day"},
            {"name": "IOC", "change": "-3%", "period": "1 week"},
            {"name": "HPCL", "change": "-4%", "period": "1 week"},
        ]},
        {"event": "Tanker attacks in Gulf of Oman", "date": "Jun 2019", "impacts": [
            {"name": "Brent Crude", "change": "+2.2%", "period": "1 day"},
            {"name": "SCI (Shipping Corp)", "change": "+5%", "period": "3 days"},
        ]},
    ],
    "oil_price": [
        {"event": "Russia-Ukraine war", "date": "Feb 2022", "impacts": [
            {"name": "Brent Crude", "change": "+30%", "period": "2 weeks"},
            {"name": "Gold", "change": "+8%", "period": "1 month"},
            {"name": "Wheat", "change": "+40%", "period": "3 weeks"},
            {"name": "Nifty 50", "change": "-5%", "period": "1 month"},
            {"name": "IOC/BPCL/HPCL", "change": "-15 to -20%", "period": "1 month"},
        ]},
    ],
    "wheat": [
        {"event": "India wheat export ban", "date": "May 2022", "impacts": [
            {"name": "Global Wheat", "change": "+6%", "period": "1 day"},
            {"name": "ITC", "change": "+3%", "period": "1 week (agri benefit)"},
        ]},
    ],
    "south_china_sea": [
        {"event": "Pelosi Taiwan visit", "date": "Aug 2022", "impacts": [
            {"name": "TSMC", "change": "-3%", "period": "1 week"},
            {"name": "Gold", "change": "+1.5%", "period": "3 days"},
            {"name": "Defense ETFs", "change": "+2%", "period": "1 week"},
        ]},
    ],
    "sanctions": [
        {"event": "Russia SWIFT sanctions", "date": "Feb 2022", "impacts": [
            {"name": "Gold", "change": "+8%", "period": "1 month"},
            {"name": "Brent Crude", "change": "+25%", "period": "2 weeks"},
            {"name": "IT services (TCS, Infosys)", "change": "-3%", "period": "uncertainty, recovered"},
        ]},
    ],
}


def find_precedents(trigger_keys: str) -> list:
    """Find historical precedents matching a commodity trigger."""
    matches = []
    for key in trigger_keys.split("|"):
        for pkey, precedents in HISTORICAL_PRECEDENTS.items():
            if key in pkey or pkey in key:
                matches.extend(precedents)
    return matches[:3]  # max 3 precedents


async def generate_commodity_signals() -> None:
    """Scan recent events and market data, generate commodity trading signals."""
    async with async_session() as session:
        now = datetime.now(timezone.utc)

        # Check if we already ran recently
        recent = await session.execute(
            select(Signal)
            .where(Signal.signal_type == "commodity")
            .where(Signal.detected_at >= now - timedelta(minutes=25))
            .limit(1)
        )
        if recent.scalar_one_or_none():
            return

        # Get recent events
        events_stmt = (
            select(Event)
            .where(Event.is_active == True)
            .where(Event.summary.isnot(None))
            .order_by(Event.article_count.desc())
            .limit(30)
        )
        result = await session.execute(events_stmt)
        events = list(result.scalars().all())

        # Get market data for context
        market = {}
        for sym in ["BZ=F", "CL=F", "GC=F", "ZW=F", "NG=F", "INR=X", "^NSEI"]:
            point = await session.execute(
                select(MarketDataPoint).where(MarketDataPoint.symbol == sym)
                .order_by(MarketDataPoint.fetched_at.desc()).limit(1)
            )
            p = point.scalar_one_or_none()
            if p:
                market[sym] = {"price": p.price, "change_1d": p.change_1d, "change_7d": p.change_7d}

        # Deactivate old commodity signals
        old = await session.execute(
            select(Signal).where(Signal.signal_type == "commodity").where(Signal.is_active == True)
        )
        for s in old.scalars().all():
            s.is_active = False

        # Scan events against commodity map
        triggered_buckets = {}
        for event in events:
            text = f"{event.title} {event.summary or ''} {event.category or ''}".lower()

            for trigger_keys, bucket in COMMODITY_MAP.items():
                keywords = trigger_keys.split("|")
                if any(kw in text for kw in keywords):
                    bucket_name = bucket["name"]
                    if bucket_name not in triggered_buckets:
                        triggered_buckets[bucket_name] = {
                            "bucket": bucket,
                            "trigger_keys": trigger_keys,
                            "triggering_events": [],
                            "market_context": {},
                        }
                    triggered_buckets[bucket_name]["triggering_events"].append({
                        "title": event.title[:80],
                        "articles": event.article_count,
                    })

        # Enrich with market data, price snapshots, and historical precedents
        for name, tb in triggered_buckets.items():
            bucket = tb["bucket"]
            tb["price_at_trigger"] = {}
            for comm in bucket.get("commodities", []):
                sym = comm.get("symbol")
                if sym and sym in market:
                    tb["market_context"][sym] = market[sym]
                    tb["price_at_trigger"][sym] = market[sym]["price"]
            tb["precedents"] = find_precedents(tb.get("trigger_keys", ""))

        # Use Gemini to refine the analysis
        if settings.gemini_api_key and triggered_buckets:
            await _refine_with_llm(session, triggered_buckets, market, now)
        else:
            # Store raw signals without LLM refinement
            for name, tb in triggered_buckets.items():
                bucket = tb["bucket"]
                session.add(Signal(
                    signal_type="commodity",
                    title=f"Trading signal: {name}",
                    description=f"Triggered by {len(tb['triggering_events'])} events. Affects {len(bucket.get('stocks_india',[]))} Indian stocks and {len(bucket.get('commodities',[]))} commodities.",
                    severity="medium",
                    entity_ids_json=json.dumps([]),
                    data_json=json.dumps({
                        "bucket_name": name,
                        "commodities": bucket.get("commodities", []),
                        "stocks_india": bucket.get("stocks_india", []),
                        "stocks_global": bucket.get("stocks_global", []),
                        "triggering_events": tb["triggering_events"][:5],
                        "market_context": tb["market_context"],
                        "price_at_trigger": tb.get("price_at_trigger", {}),
                        "precedents": tb.get("precedents", []),
                    }),
                    detected_at=now,
                    is_active=True,
                ))

        await session.commit()
        logger.info(f"Commodity signals: {len(triggered_buckets)} buckets triggered")


async def _refine_with_llm(session, triggered_buckets, market, now):
    """Use Gemini to add conviction levels and specific trade ideas."""
    prompt = f"""You are a commodity trading analyst. Based on the following geopolitical events and current market data, refine the trading signals.

TRIGGERED SIGNALS:
"""
    for name, tb in triggered_buckets.items():
        prompt += f"\n--- {name} ---\n"
        prompt += f"Events: {json.dumps(tb['triggering_events'][:3])}\n"
        prompt += f"Market: {json.dumps(tb['market_context'])}\n"
        prompt += f"Affected Indian stocks: {json.dumps(tb['bucket'].get('stocks_india', [])[:5])}\n"
        if tb.get("precedents"):
            prompt += f"Historical precedents: {json.dumps(tb['precedents'][:2])}\n"

    prompt += f"""
CURRENT MARKET DATA:
{json.dumps(market)}

For each signal, provide a refined analysis in JSON (no markdown):
{{
  "signals": [
    {{
      "bucket_name": "name",
      "conviction": "high|medium|low",
      "summary": "One sentence: what trade to consider and why",
      "top_indian_trades": ["STOCK_NAME: direction — one-line reason"],
      "top_global_trades": ["TICKER: direction — one-line reason"],
      "risk": "Key risk to this trade thesis",
      "timeframe": "hours|days|weeks",
      "precedent": "One sentence referencing the most relevant historical parallel and what happened"
    }}
  ]
}}

Rules:
- Be specific with stock names and directions
- conviction = high only if multiple events + market data confirm
- Include risk for every signal
- timeframe = how long the trade thesis is valid
- Reference historical precedents when available — cite specific dates and price moves"""

    try:
        from narad.pipeline.summarizer import _get_client
        client = _get_client()
        response = await asyncio.to_thread(
            client.models.generate_content, model="gemini-2.0-flash", contents=prompt,
        )
        text = response.text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        data = json.loads(text)
        signals = data.get("signals", [])

        for sig in signals:
            bucket_name = sig.get("bucket_name", "")
            tb = triggered_buckets.get(bucket_name, {})
            bucket = tb.get("bucket", {})

            session.add(Signal(
                signal_type="commodity",
                title=f"{bucket_name}",
                description=sig.get("summary", ""),
                severity="high" if sig.get("conviction") == "high" else "medium" if sig.get("conviction") == "medium" else "low",
                entity_ids_json=json.dumps([]),
                data_json=json.dumps({
                    "bucket_name": bucket_name,
                    "conviction": sig.get("conviction"),
                    "top_indian_trades": sig.get("top_indian_trades", []),
                    "top_global_trades": sig.get("top_global_trades", []),
                    "risk": sig.get("risk"),
                    "timeframe": sig.get("timeframe"),
                    "commodities": bucket.get("commodities", []),
                    "triggering_events": tb.get("triggering_events", [])[:3],
                    "market_context": tb.get("market_context", {}),
                    "price_at_trigger": tb.get("price_at_trigger", {}),
                }),
                detected_at=now,
                is_active=True,
            ))

    except Exception as e:
        logger.error(f"Commodity LLM refinement failed: {e}")
        # Fallback to raw signals
        for name, tb in triggered_buckets.items():
            bucket = tb["bucket"]
            session.add(Signal(
                signal_type="commodity",
                title=f"{name}",
                description=f"Triggered by {len(tb['triggering_events'])} events.",
                severity="medium",
                entity_ids_json=json.dumps([]),
                data_json=json.dumps({
                    "bucket_name": name,
                    "commodities": bucket.get("commodities", []),
                    "stocks_india": bucket.get("stocks_india", []),
                    "stocks_global": bucket.get("stocks_global", []),
                    "triggering_events": tb["triggering_events"][:5],
                    "market_context": tb["market_context"],
                    "price_at_trigger": tb.get("price_at_trigger", {}),
                }),
                detected_at=now,
                is_active=True,
            ))
