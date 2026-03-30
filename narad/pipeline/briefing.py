import asyncio
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from narad.config import settings
from narad.database import async_session
from narad.models import Briefing, Event, MarketDataPoint, Signal

logger = logging.getLogger(__name__)

BRIEFING_PROMPT = """You are Narad, a senior geopolitical intelligence analyst preparing a daily briefing for an Indian strategist. You don't just report news — you analyze what it means, what could happen next, and what signals to watch for.

Think like a combination of:
- A RAW/CIA analyst writing a President's Daily Brief
- Professor Jiang's predictive history methodology (pattern-matching current events to historical precedents)
- A Palantir-style scenario planner

Here are today's {n} geopolitical events:

{events_list}

Produce a briefing in this exact JSON format (no markdown, no code fences, just raw JSON):
{{
  "stories": [
    {{
      "event_id": <id from the list above>,
      "headline": "Crisp headline, max 12 words",
      "summary": "2-3 neutral sentences synthesizing the event",
      "india_impact": "1-2 specific sentences on why this matters for India — reference actual trade volumes, treaties, borders, supply routes, diplomatic history, or economic ties",
      "severity": "critical or developing or monitoring",
      "source_count": <n>,
      "category": "<category>",
      "scenarios": {{
        "likely": "What will most probably happen next (60-70% chance). Be specific with timeframes — days, weeks, or months.",
        "best_case": "The optimistic but realistic outcome for India",
        "worst_case": "The pessimistic but plausible outcome for India",
        "historical_parallel": "Name ONE specific historical event that mirrors this situation and what happened then (e.g., 'Similar to the 1973 Oil Crisis when Arab states embargoed oil exports, leading to...')"
      }},
      "confidence": "high|medium|low",
      "confidence_reason": "Brief explanation: e.g., '4 wire service sources + GEOINT confirmation' or 'Single source, unverified'",
      "evidence_chain": ["Source: AP/Reuters/etc report confirms X", "GEOINT: thermal/aircraft data supports Y", "Market: oil moved Z% confirming pressure"],
      "watch_signals": ["Specific observable indicator to watch for, e.g., 'Oil futures crossing $120/barrel'", "Second signal", "Third signal"]
    }}
  ],
  "connections": [
    {{
      "from_event_id": <id>,
      "to_event_id": <id>,
      "narrative": "One sentence explaining how these stories are causally linked"
    }}
  ],
  "outlook": {{
    "next_24h": "What to expect in the next 24 hours across all stories. Be specific.",
    "next_week": "How the situation is likely to evolve over the coming week. Identify the key decision points.",
    "india_strategic_assessment": "2-3 sentences: What should an Indian strategist, investor, or policymaker be thinking about right now based on all of today's events combined?",
    "wildcard": "One low-probability but high-impact scenario that could change everything (a black swan to keep in mind)"
  }}
}}

Rules:
- Select exactly 5-7 stories maximum. Quality over quantity. Pick the ones that MATTER.
- Rank by: (1) global significance, (2) relevance to India, (3) source count
- Severity: "critical" = active conflict, major diplomatic shift, direct India impact. "developing" = evolving, indirect impact. "monitoring" = worth watching.
- Scenarios: be SPECIFIC with timeframes, not vague. "Within 48 hours" not "soon". Name actual countries, leaders, institutions.
- Historical parallels: pick the BEST match from history, not a generic comparison. Explain what happened then in 1 sentence.
- Watch signals: these must be OBSERVABLE — things someone can actually check. Not opinions, but facts that would confirm a scenario is playing out.
- Confidence: "high" = 3+ wire sources agree + corroborating data (GEOINT/market). "medium" = 2 sources or reasonable inference. "low" = single source or speculative.
- Evidence chain: list the specific data points backing each story — source reports, GEOINT signals, market data, entity activity. Be specific.
- Connections: only genuine causal or entity links. Don't force them.
- India impact: reference SPECIFIC numbers, routes, treaties, or relationships. Not "India may be affected" but "India imports 85% of its crude oil, 40% through the Strait of Hormuz."
- Outlook: the strategic assessment should read like advice to a cabinet secretary, not a news anchor.
- Be factual and analytical, not sensational. Acknowledge uncertainty where it exists."""


async def generate_briefing() -> None:
    """Generate a daily briefing with predictive intelligence from summarized events."""
    if not settings.gemini_api_key:
        logger.debug("Gemini API key not configured, skipping briefing")
        return

    async with async_session() as session:
        stmt = (
            select(Event)
            .where(Event.is_active == True)
            .where(Event.article_count >= 2)
            .order_by(Event.article_count.desc())
            .limit(50)
        )
        result = await session.execute(stmt)
        events = list(result.scalars().all())

        if len(events) < 3:
            logger.info("Briefing: not enough events yet (need at least 3)")
            return

        # Check if events changed since last briefing
        last_briefing_stmt = select(Briefing).where(Briefing.is_current == True).limit(1)
        last_result = await session.execute(last_briefing_stmt)
        last_briefing = last_result.scalar_one_or_none()

        if last_briefing:
            newest_event_update = max(e.last_updated_at for e in events)
            if last_briefing.generated_at.tzinfo is None:
                gen_time = last_briefing.generated_at.replace(tzinfo=timezone.utc)
            else:
                gen_time = last_briefing.generated_at
            if newest_event_update.tzinfo is None:
                newest_event_update = newest_event_update.replace(tzinfo=timezone.utc)
            if newest_event_update <= gen_time:
                logger.info("Briefing: no new events since last briefing, skipping")
                return

        # Build event list for prompt
        events_text = ""
        for e in events:
            summary = (e.summary or e.title)[:300]
            entities = e.entities_json or "[]"
            events_text += (
                f"ID: {e.id} | Title: {e.title}\n"
                f"Summary: {summary}\n"
                f"Articles: {e.article_count} | Sources: {e.source_count} | Category: {e.category or 'unknown'}\n"
                f"Entities: {entities}\n\n"
            )

        # Gather market data for context
        market_text = "\n=== CURRENT MARKET DATA ===\n"
        for symbol in ["BZ=F", "CL=F", "GC=F", "NG=F", "INR=X", "^NSEI"]:
            point_result = await session.execute(
                select(MarketDataPoint)
                .where(MarketDataPoint.symbol == symbol)
                .order_by(MarketDataPoint.fetched_at.desc())
                .limit(1)
            )
            p = point_result.scalar_one_or_none()
            if p:
                market_text += f"{p.name}: ${p.price:.2f} (1d: {p.change_1d:+.1f}%, 7d: {p.change_7d:+.1f}%, 30d: {p.change_30d:+.1f}%)\n"

        # Gather active intelligence signals for context
        signals_text = "\n=== ACTIVE INTELLIGENCE SIGNALS ===\n"
        active_signals = await session.execute(
            select(Signal)
            .where(Signal.is_active == True)
            .where(Signal.signal_type.in_(["correlation", "thermal_anomaly", "aircraft_activity", "spike", "trend_shift"]))
            .order_by(Signal.severity.desc(), Signal.detected_at.desc())
            .limit(10)
        )
        for sig in active_signals.scalars().all():
            signals_text += f"[{sig.severity}|{sig.signal_type}] {sig.title}: {sig.description[:150]}\n"

        prompt = BRIEFING_PROMPT.format(n=len(events), events_list=events_text + market_text + signals_text)

        # Call Gemini
        try:
            from narad.pipeline.summarizer import _get_client
            client = _get_client()
            response = await asyncio.to_thread(
                client.models.generate_content,
                model="gemini-2.0-flash",
                contents=prompt,
            )
            text = response.text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()

            data = json.loads(text)
        except Exception as e:
            logger.error(f"Briefing generation failed: {e}")
            return

        stories = data.get("stories", [])
        connections = data.get("connections", [])
        outlook = data.get("outlook", {})

        if not stories:
            logger.warning("Briefing: Gemini returned no stories")
            return

        # Mark old briefings as not current
        await session.execute(
            update(Briefing).where(Briefing.is_current == True).values(is_current=False)
        )

        briefing = Briefing(
            generated_at=datetime.now(timezone.utc),
            stories_json=json.dumps(stories),
            connections_json=json.dumps(connections),
            outlook_json=json.dumps(outlook),
            is_current=True,
        )
        session.add(briefing)
        await session.commit()

        logger.info(f"Briefing generated: {len(stories)} stories, {len(connections)} connections, outlook included")
