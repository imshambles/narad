import asyncio
import json
import logging
from datetime import datetime, timezone

from google import genai
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from narad.config import settings
from narad.database import async_session
from narad.models import Article, Event, EventArticle

logger = logging.getLogger(__name__)

_semaphore = asyncio.Semaphore(3)
_client = None


def _get_client():
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


SUMMARIZE_PROMPT = """You are a geopolitical news analyst. Given these {n} news articles about the same event from different sources, produce a structured analysis with a chronological timeline.

ARTICLES (with timestamps):
{articles_text}

Respond in this exact JSON format (no markdown, no code fences, just raw JSON):
{{
  "title": "A concise neutral headline for this event (max 15 words)",
  "summary": "A 2-3 sentence neutral summary synthesizing all sources",
  "key_facts": ["fact 1 agreed upon by multiple sources", "fact 2"],
  "disagreements": ["Source A says X while Source B says Y"],
  "category": "one of: conflict, diplomacy, economy, defense, humanitarian, politics, technology, environment, other",
  "entities": [
    {{"name": "entity name", "type": "country"}},
    {{"name": "entity name", "type": "person"}},
    {{"name": "entity name", "type": "organization"}}
  ],
  "timeline": [
    {{
      "time": "ISO 8601 timestamp from the articles",
      "title": "Short milestone headline (max 10 words)",
      "description": "One sentence describing what happened at this point",
      "significance": "origin or escalation or development or response"
    }}
  ]
}}

Rules:
- Be neutral and factual
- Only include facts that appear in the provided articles
- If sources disagree, note it in disagreements (empty list if no disagreements)
- Extract ALL named countries, people, and organizations mentioned
- Entity type must be one of: country, person, organization, location
- Timeline: reconstruct the chronological story from the article timestamps
- Timeline: 3-8 milestones max — only genuinely distinct developments, not restatements
- Timeline: first milestone = origin/trigger, last milestone = most recent development
- Timeline significance: origin = how it started, escalation = got worse, development = neutral new info, response = reaction from another party"""


async def summarize_events() -> None:
    """Find events needing summarization and call Gemini."""
    if not settings.gemini_api_key:
        logger.debug("Gemini API key not configured, skipping summarization")
        return

    async with async_session() as session:
        stmt = (
            select(Event)
            .where(Event.is_active == True)
            .where(Event.article_count >= 3)
            .where(Event.source_count >= 2)
            .options(joinedload(Event.articles).joinedload(EventArticle.article).joinedload(Article.source))
        )
        result = await session.execute(stmt)
        events = list(result.scalars().unique().all())

        summarized = 0
        for event in events:
            if event.summarized_at:
                newest_article = max(
                    (ea.assigned_at for ea in event.articles),
                    default=datetime.min.replace(tzinfo=timezone.utc),
                )
                if event.summarized_at.tzinfo is None:
                    check_time = event.summarized_at.replace(tzinfo=timezone.utc)
                else:
                    check_time = event.summarized_at
                if newest_article.tzinfo is None:
                    newest_article = newest_article.replace(tzinfo=timezone.utc)
                if newest_article <= check_time:
                    continue

            try:
                await _summarize_event(session, event)
                summarized += 1
            except Exception as e:
                logger.error(f"Failed to summarize event {event.id}: {e}")

        if summarized:
            await session.commit()
            logger.info(f"Summarized {summarized} events")


async def _summarize_event(session: AsyncSession, event: Event) -> None:
    """Call Gemini to summarize a single event."""
    # Sort articles chronologically for timeline reconstruction
    sorted_articles = sorted(
        [ea.article for ea in event.articles if ea.article],
        key=lambda a: a.published_at,
    )
    articles_text = ""
    for a in sorted_articles:
        source_name = a.source.name if a.source else "Unknown"
        pub_time = a.published_at.isoformat() if a.published_at else "unknown"
        articles_text += f"[{pub_time}] [Source: {source_name}] {a.title}\n"
        if a.summary:
            articles_text += f"{a.summary}\n"
        articles_text += "---\n"

    prompt = SUMMARIZE_PROMPT.format(n=event.article_count, articles_text=articles_text)

    async with _semaphore:
        try:
            client = _get_client()
            response = await asyncio.to_thread(
                client.models.generate_content,
                model="gemini-2.0-flash",
                contents=prompt,
            )
            text = response.text.strip()
            # Strip markdown code fences if present
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()

            data = json.loads(text)
        except Exception as e:
            logger.error(f"Gemini API error for event {event.id}: {e}")
            await asyncio.sleep(10)
            try:
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
            except Exception as e2:
                logger.error(f"Gemini retry failed for event {event.id}: {e2}")
                return

    event.title = data.get("title", event.title)
    event.summary = data.get("summary")
    event.key_facts = json.dumps(data.get("key_facts", []))
    event.disagreements = json.dumps(data.get("disagreements", []))
    event.category = data.get("category")
    event.entities_json = json.dumps(data.get("entities", []))
    event.timeline_json = json.dumps(data.get("timeline", []))
    event.summarized_at = datetime.now(timezone.utc)

    logger.info(f"Summarized event {event.id}: {event.title}")
