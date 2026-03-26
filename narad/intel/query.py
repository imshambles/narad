"""
Query Interface — "Ask Narad"

Lets users ask natural language questions. The system searches its own
data (events, entities, market data, signals) and uses Gemini to
synthesize an answer with citations.

This is what turns Narad from a report generator into an intelligence tool.
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from narad.config import settings
from narad.database import async_session
from narad.models import (
    Article, Entity, EntityRelation, Event, EventArticle,
    MarketDataPoint, Signal, ThreatMatrix,
)

logger = logging.getLogger(__name__)

QUERY_PROMPT = """You are Narad, an intelligence analyst with access to a structured geopolitical database. A user is asking you a question. You must answer ONLY using the data provided below — do not use your general knowledge except to connect dots within the provided data.

USER QUESTION: {question}

=== DATA FROM NARAD'S DATABASE ===

RELEVANT EVENTS (last 72 hours):
{events_data}

MARKET DATA (current):
{market_data}

ENTITY RELATIONSHIPS:
{entity_data}

ACTIVE INTELLIGENCE SIGNALS:
{signals_data}

INDIA THREAT MATRIX:
{threat_data}

=== INSTRUCTIONS ===

Answer the user's question using the data above. Your response must be in this JSON format (no markdown, no code fences):
{{
  "answer": "Your analytical answer, 3-8 sentences. Reference specific events, dates, prices, and entities from the data. If the data doesn't contain enough information to answer, say so honestly.",
  "evidence": [
    {{
      "type": "event|market|signal|entity",
      "reference": "The specific data point you're citing",
      "relevance": "Why this supports your answer"
    }}
  ],
  "confidence": "high|medium|low",
  "follow_up_questions": ["A suggested follow-up question the user might want to ask", "Another one"]
}}

Rules:
- ONLY use the provided data. Don't make up events or prices.
- If the data is insufficient, say "Based on available data..." and note what's missing.
- Be analytical, not narrative. Answer like an intelligence professional.
- Cite specific evidence for every claim.
- Confidence: high = multiple data points support this, medium = reasonable inference, low = limited data."""


async def ask_narad(question: str) -> dict:
    """Process a natural language query against Narad's database."""
    if not settings.gemini_api_key:
        return {"answer": "Gemini API key not configured.", "evidence": [], "confidence": "low", "follow_up_questions": []}

    async with async_session() as session:
        now = datetime.now(timezone.utc)
        lookback = now - timedelta(hours=72)

        # 1. Gather relevant events
        events = await session.execute(
            select(Event)
            .where(Event.is_active == True)
            .where(Event.summary.isnot(None))
            .order_by(Event.article_count.desc())
            .limit(30)
        )
        events_data = ""
        for e in events.scalars().all():
            events_data += f"[{e.article_count}src | {e.category}] {e.title}: {(e.summary or '')[:200]}\n"

        # 2. Gather market data
        market_data = ""
        for symbol in ["CL=F", "BZ=F", "GC=F", "NG=F", "INR=X", "^NSEI"]:
            point = await session.execute(
                select(MarketDataPoint)
                .where(MarketDataPoint.symbol == symbol)
                .order_by(MarketDataPoint.fetched_at.desc())
                .limit(1)
            )
            p = point.scalar_one_or_none()
            if p:
                market_data += f"{p.name}: ${p.price:.2f} (1d: {p.change_1d:+.1f}%, 7d: {p.change_7d:+.1f}%, 30d: {p.change_30d:+.1f}%)\n"

        if not market_data:
            market_data = "No market data available yet.\n"

        # 3. Gather entity relationships
        entity_data = ""
        top_entities = await session.execute(
            select(Entity).where(Entity.mention_count >= 2).order_by(Entity.mention_count.desc()).limit(15)
        )
        for ent in top_entities.scalars().all():
            entity_data += f"{ent.name} ({ent.entity_type}): {ent.mention_count} mentions\n"

        # 4. Gather signals
        signals_data = ""
        signals = await session.execute(
            select(Signal).where(Signal.is_active == True).order_by(Signal.detected_at.desc()).limit(10)
        )
        for s in signals.scalars().all():
            signals_data += f"[{s.severity}] {s.title}: {s.description[:150]}\n"

        if not signals_data:
            signals_data = "No active signals.\n"

        # 5. Threat matrix
        threat_data = ""
        tm_entries = await session.execute(select(ThreatMatrix))
        for tm in tm_entries.scalars().all():
            country = await session.get(Entity, tm.country_entity_id)
            if country:
                threat_data += f"India ↔ {country.name}: cooperation={tm.cooperation_score:.2f}, tension={tm.tension_score:.2f}, trend={tm.trend}\n"

        if not threat_data:
            threat_data = "No threat matrix data yet.\n"

        prompt = QUERY_PROMPT.format(
            question=question,
            events_data=events_data or "No events available yet.\n",
            market_data=market_data,
            entity_data=entity_data or "No entity data yet.\n",
            signals_data=signals_data,
            threat_data=threat_data,
        )

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

            return json.loads(text)
        except Exception as e:
            logger.error(f"Query failed: {e}")
            return {
                "answer": f"Query processing failed: {str(e)[:100]}",
                "evidence": [],
                "confidence": "low",
                "follow_up_questions": [],
            }
