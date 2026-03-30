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

QUERY_PROMPT = """You are Narad, an intelligence analyst with access to a structured geopolitical database spanning the last 30 days. A user is asking you a question. You must answer ONLY using the data provided below — do not use your general knowledge except to connect dots within the provided data.

USER QUESTION: {question}

=== DATA FROM NARAD'S DATABASE ===

RELEVANT EVENTS (last 30 days, sorted by relevance):
{events_data}

MARKET DATA (current):
{market_data}

ENTITY RELATIONSHIPS & GRAPH:
{entity_data}

ACTIVE INTELLIGENCE SIGNALS:
{signals_data}

INDIA THREAT MATRIX:
{threat_data}

CROSS-DOMAIN CORRELATIONS:
{correlation_data}

=== INSTRUCTIONS ===

Answer the user's question using the data above. Your response must be in this JSON format (no markdown, no code fences):
{{
  "answer": "Your analytical answer, 3-8 sentences. Reference specific events, dates, prices, and entities from the data. If the data doesn't contain enough information to answer, say so honestly. Identify trends across the 30-day window, not just recent events.",
  "evidence": [
    {{
      "type": "event|market|signal|entity|correlation",
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
- Look for TRENDS across the 30-day window — frequency changes, escalation patterns, relationship shifts.
- Cross-reference entity relationships with events to identify causal chains.
- Confidence: high = multiple data points support this, medium = reasonable inference, low = limited data."""


async def ask_narad(question: str) -> dict:
    """Process a natural language query against Narad's database with 30-day lookback and entity graph traversal."""
    if not settings.gemini_api_key:
        return {"answer": "Gemini API key not configured.", "evidence": [], "confidence": "low", "follow_up_questions": []}

    async with async_session() as session:
        now = datetime.now(timezone.utc)
        lookback_30d = now - timedelta(days=30)
        question_lower = question.lower()

        # 1. Gather relevant events (30-day window, keyword-boosted)
        all_events = await session.execute(
            select(Event)
            .where(Event.summary.isnot(None))
            .where(Event.last_updated_at >= lookback_30d)
            .order_by(Event.article_count.desc())
            .limit(80)
        )
        all_events_list = list(all_events.scalars().all())

        # Score events by relevance to query
        query_words = set(question_lower.split())
        scored = []
        for e in all_events_list:
            text = f"{e.title} {e.summary or ''} {e.category or ''} {e.entities_json or ''}".lower()
            relevance = sum(1 for w in query_words if len(w) > 2 and w in text)
            scored.append((relevance, e))
        scored.sort(key=lambda x: (-x[0], -x[1].article_count))
        top_events = [e for _, e in scored[:30]]

        events_data = ""
        for e in top_events:
            age_days = (now - e.last_updated_at.replace(tzinfo=timezone.utc)).days if e.last_updated_at else 0
            events_data += f"[{e.article_count}src | {e.category} | {age_days}d ago] {e.title}: {(e.summary or '')[:200]}\n"

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

        # 3. Entity relationships with graph traversal
        entity_data = ""
        # Find entities mentioned in the question
        query_entities = await session.execute(
            select(Entity).where(Entity.mention_count >= 2).order_by(Entity.mention_count.desc()).limit(50)
        )
        all_ents = list(query_entities.scalars().all())
        matched_ids = set()

        for ent in all_ents:
            if ent.canonical_name in question_lower or ent.name.lower() in question_lower:
                matched_ids.add(ent.id)
                entity_data += f"* {ent.name} ({ent.entity_type}): {ent.mention_count} mentions\n"

        # Traverse relationships from matched entities
        if matched_ids:
            for eid in list(matched_ids):
                rels = await session.execute(
                    select(EntityRelation)
                    .where(or_(EntityRelation.entity_a_id == eid, EntityRelation.entity_b_id == eid))
                    .order_by(EntityRelation.co_occurrence_count.desc())
                    .limit(10)
                )
                for rel in rels.scalars().all():
                    other_id = rel.entity_b_id if rel.entity_a_id == eid else rel.entity_a_id
                    other = await session.get(Entity, other_id)
                    if other:
                        entity_data += f"  → {other.name} ({rel.relation_type}, {rel.co_occurrence_count} co-occurrences, trend: {rel.trend})\n"

        # Also add top entities for general context
        if not entity_data:
            for ent in all_ents[:15]:
                entity_data += f"{ent.name} ({ent.entity_type}): {ent.mention_count} mentions\n"

        # 4. Gather signals (including correlations)
        signals_data = ""
        signals = await session.execute(
            select(Signal).where(Signal.is_active == True).order_by(Signal.detected_at.desc()).limit(15)
        )
        for s in signals.scalars().all():
            signals_data += f"[{s.severity}|{s.signal_type}] {s.title}: {s.description[:150]}\n"

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

        # 6. Correlation signals
        correlation_data = ""
        corr_signals = await session.execute(
            select(Signal)
            .where(Signal.signal_type == "correlation")
            .where(Signal.is_active == True)
            .order_by(Signal.detected_at.desc())
            .limit(5)
        )
        for cs in corr_signals.scalars().all():
            correlation_data += f"[{cs.severity}] {cs.title}: {cs.description[:200]}\n"

        if not correlation_data:
            correlation_data = "No active cross-domain correlations.\n"

        prompt = QUERY_PROMPT.format(
            question=question,
            events_data=events_data or "No events available yet.\n",
            market_data=market_data,
            entity_data=entity_data or "No entity data yet.\n",
            signals_data=signals_data,
            threat_data=threat_data,
            correlation_data=correlation_data,
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
