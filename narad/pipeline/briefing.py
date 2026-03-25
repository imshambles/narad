import asyncio
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from narad.config import settings
from narad.database import async_session
from narad.models import Briefing, Event

logger = logging.getLogger(__name__)

BRIEFING_PROMPT = """You are Narad, an AI geopolitical intelligence analyst preparing a daily briefing for an Indian professional. Your job is to select the most significant stories and explain their India impact.

Here are today's {n} geopolitical events. Each has an ID, title, summary, article count, source count, and category:

{events_list}

Produce a briefing in this exact JSON format (no markdown, no code fences, just raw JSON):
{{
  "stories": [
    {{
      "event_id": <id from the list above>,
      "headline": "Crisp headline, max 12 words",
      "summary": "2-3 neutral sentences synthesizing the event",
      "india_impact": "1-2 sentences on why this matters for India specifically — trade, security, diplomacy, economy, diaspora, or strategic interests. If no direct India impact, say 'No direct India impact, but worth monitoring.'",
      "severity": "critical or developing or monitoring",
      "source_count": <n>,
      "category": "<category>"
    }}
  ],
  "connections": [
    {{
      "from_event_id": <id>,
      "to_event_id": <id>,
      "narrative": "One sentence explaining how these two stories are linked"
    }}
  ]
}}

Rules:
- Select exactly 5-7 stories maximum. Quality over quantity. Pick the ones that MATTER.
- Rank by: (1) global significance, (2) relevance to India, (3) number of sources covering it
- "critical" = active conflict, major diplomatic shift, direct India impact
- "developing" = evolving situation, indirect India impact
- "monitoring" = worth watching, no immediate India impact
- Connections: only include genuine links — shared countries/people, causal chains, same conflict. Do NOT force connections where none exist. It's fine to have 0 connections.
- Be factual, neutral, no speculation beyond what sources report.
- The india_impact must be specific, not generic. Reference actual trade routes, treaties, diplomatic relationships, or economic ties."""


async def generate_briefing() -> None:
    """Generate a daily briefing from summarized events."""
    if not settings.gemini_api_key:
        logger.debug("Gemini API key not configured, skipping briefing")
        return

    async with async_session() as session:
        # Get all active events with summaries (prioritize multi-source events)
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
            summary = (e.summary or e.title)[:200]
            events_text += f"ID: {e.id} | Title: {e.title} | Summary: {summary} | Articles: {e.article_count} | Sources: {e.source_count} | Category: {e.category or 'unknown'}\n\n"

        prompt = BRIEFING_PROMPT.format(n=len(events), events_list=events_text)

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
            # Strip markdown code fences
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

        if not stories:
            logger.warning("Briefing: Gemini returned no stories")
            return

        # Mark all existing briefings as not current
        await session.execute(
            update(Briefing).where(Briefing.is_current == True).values(is_current=False)
        )

        # Create new briefing
        briefing = Briefing(
            generated_at=datetime.now(timezone.utc),
            stories_json=json.dumps(stories),
            connections_json=json.dumps(connections),
            is_current=True,
        )
        session.add(briefing)
        await session.commit()

        logger.info(f"Briefing generated: {len(stories)} stories, {len(connections)} connections")
