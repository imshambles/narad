"""
Intelligence Analyst Engine

Uses Gemini to produce actual intelligence assessments from
the entity graph, threat matrix, and recent events.
This is NOT entity extraction — this is analytical reasoning.
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from narad.config import settings
from narad.database import async_session
from narad.models import (
    Entity, EntityMention, EntityRelation, Event, EventArticle,
    Signal, ThreatMatrix, Article,
)

logger = logging.getLogger(__name__)

ANALYST_PROMPT = """You are a senior intelligence analyst at India's Research and Analysis Wing (RAW). Your job is to analyze raw geopolitical data and produce actionable intelligence assessments that a cabinet secretary or NSA would find useful.

You are NOT summarizing news. You are looking for:
1. NON-OBVIOUS patterns and connections
2. Contradictions between what different actors are saying vs doing
3. Shifts in relationships that signal changing strategic postures
4. Anomalies — things that deviate from expected behavior
5. Strategic implications for India that aren't immediately apparent
6. Historical pattern matches that predict what comes next

Here is the current intelligence picture:

=== RECENT EVENTS (last 48 hours) ===
{events_data}

=== ENTITY ACTIVITY ===
{entity_data}

=== INDIA'S BILATERAL RELATIONSHIPS ===
{relationship_data}

Produce an intelligence assessment in this exact JSON format (no markdown, no code fences, just raw JSON):
{{
  "assessments": [
    {{
      "title": "Sharp analytical headline — what the assessment reveals (not a news headline)",
      "classification": "strategic_shift|anomaly|contradiction|threat|opportunity|pattern",
      "severity": "critical|high|medium|low",
      "analysis": "3-5 sentences of analytical reasoning. Connect dots that aren't obvious. Explain WHY this matters, not just WHAT happened. Reference specific events, actors, and dates. If there's a historical parallel, name it specifically.",
      "india_implication": "2-3 sentences on what this means for India's strategic position. Be specific about which ministry, policy, or relationship is affected.",
      "recommended_watch": ["Specific observable indicator #1", "Specific observable indicator #2"],
      "confidence": "high|medium|low",
      "time_horizon": "immediate (24-48h)|short-term (1-2 weeks)|medium-term (1-3 months)"
    }}
  ],
  "relationship_insights": [
    {{
      "countries": ["Country A", "Country B"],
      "insight": "One sentence on what's changing in this bilateral relationship and why India should care",
      "direction": "warming|cooling|volatile|contradictory"
    }}
  ],
  "strategic_warning": "One paragraph: the single most important thing India's national security apparatus should be paying attention to right now, synthesized from all the data above. This should read like a brief to the PM."
}}

Rules:
- Produce 3-5 assessments. Quality over quantity. Each must contain NON-OBVIOUS analysis.
- DO NOT report obvious facts like "India's defense minister discusses defense" — that's noise.
- DO look for: frequency changes, behavioral anomalies, contradictions, timing coincidences, structural shifts.
- Relationship insights: only include relationships where something is CHANGING or CONTRADICTORY.
- The strategic warning should be something a policymaker would act on, not a news summary.
- Confidence: "high" = multiple data points support this, "medium" = reasonable inference, "low" = speculative but worth monitoring.
- Be analytical, not sensational. Write like an intelligence professional, not a journalist."""


async def run_intelligence_analysis() -> None:
    """Run Gemini-powered intelligence analysis on current data."""
    if not settings.gemini_api_key:
        return

    async with async_session() as session:
        now = datetime.now(timezone.utc)
        lookback = now - timedelta(hours=48)

        # Check if we already ran recently (within 25 min)
        recent_signal = await session.execute(
            select(Signal)
            .where(Signal.signal_type == "assessment")
            .where(Signal.detected_at >= now - timedelta(minutes=25))
            .limit(1)
        )
        if recent_signal.scalar_one_or_none():
            logger.info("Intel analyst: recent assessment exists, skipping")
            return

        # Gather events data
        events_stmt = (
            select(Event)
            .where(Event.is_active == True)
            .where(Event.summary.isnot(None))
            .where(Event.last_updated_at >= lookback)
            .order_by(Event.article_count.desc())
            .limit(30)
        )
        events_result = await session.execute(events_stmt)
        events = list(events_result.scalars().all())

        if len(events) < 3:
            logger.info("Intel analyst: not enough analyzed events yet")
            return

        events_data = ""
        for e in events:
            entities = e.entities_json or "[]"
            events_data += (
                f"[{e.article_count} sources | {e.category or '?'}] {e.title}\n"
                f"Summary: {(e.summary or '')[:200]}\n"
                f"Entities: {entities}\n"
                f"Time: {e.first_seen_at}\n\n"
            )

        # Gather entity activity
        top_entities = await session.execute(
            select(Entity)
            .where(Entity.mention_count >= 2)
            .order_by(Entity.mention_count.desc())
            .limit(20)
        )
        entity_data = ""
        for ent in top_entities.scalars().all():
            entity_data += f"{ent.name} ({ent.entity_type}): {ent.mention_count} mentions, last seen {ent.last_seen_at}\n"

        # Gather India's bilateral relationships
        india_entity = await session.execute(
            select(Entity).where(Entity.canonical_name == "india").limit(1)
        )
        india = india_entity.scalar_one_or_none()

        relationship_data = ""
        if india:
            tm_result = await session.execute(
                select(ThreatMatrix).order_by(desc(ThreatMatrix.cooperation_score + ThreatMatrix.tension_score))
            )
            for tm in tm_result.scalars().all():
                country = await session.get(Entity, tm.country_entity_id)
                if country:
                    recent = json.loads(tm.recent_events_json or "[]")
                    recent_titles = "; ".join([e.get("title", "")[:60] for e in recent[:3]])
                    relationship_data += (
                        f"India ↔ {country.name}: cooperation={tm.cooperation_score:.2f}, "
                        f"tension={tm.tension_score:.2f}, trend={tm.trend}\n"
                        f"  Recent: {recent_titles}\n\n"
                    )

        if not relationship_data:
            relationship_data = "No bilateral relationship data available yet.\n"

        prompt = ANALYST_PROMPT.format(
            events_data=events_data,
            entity_data=entity_data or "No entity data available yet.\n",
            relationship_data=relationship_data,
        )

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
            logger.error(f"Intel analysis failed: {e}")
            return

        # Store assessments as high-value signals
        assessments = data.get("assessments", [])
        for a in assessments:
            session.add(Signal(
                signal_type="assessment",
                title=a.get("title", "Untitled assessment"),
                description=a.get("analysis", ""),
                severity=a.get("severity", "medium"),
                entity_ids_json=json.dumps([]),
                data_json=json.dumps({
                    "classification": a.get("classification"),
                    "india_implication": a.get("india_implication"),
                    "recommended_watch": a.get("recommended_watch", []),
                    "confidence": a.get("confidence"),
                    "time_horizon": a.get("time_horizon"),
                    "relationship_insights": data.get("relationship_insights", []),
                    "strategic_warning": data.get("strategic_warning"),
                }),
                detected_at=now,
                is_active=True,
            ))

        # Deactivate old assessment signals (keep last batch only)
        old_assessments = await session.execute(
            select(Signal)
            .where(Signal.signal_type == "assessment")
            .where(Signal.detected_at < now - timedelta(minutes=1))
            .where(Signal.is_active == True)
        )
        for old in old_assessments.scalars().all():
            old.is_active = False

        await session.commit()
        logger.info(f"Intel analyst: produced {len(assessments)} assessments")
