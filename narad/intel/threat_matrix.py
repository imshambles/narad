"""
India Threat Matrix

Maintains a live bilateral relationship score for every country
India interacts with, derived from entity co-occurrences and event sentiment.
"""
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from narad.database import async_session
from narad.models import Entity, EntityMention, EntityRelation, Event, ThreatMatrix, ThreatMatrixHistory

logger = logging.getLogger(__name__)

INDIA_CANONICAL = "india"


async def update_threat_matrix() -> None:
    """Recalculate India's bilateral relationship scores with all countries."""
    async with async_session() as session:
        # Find India entity
        india_result = await session.execute(
            select(Entity).where(Entity.canonical_name == INDIA_CANONICAL).limit(1)
        )
        india = india_result.scalar_one_or_none()
        if not india:
            logger.info("Threat matrix: India entity not found yet")
            return

        # Get all countries (except India)
        countries_result = await session.execute(
            select(Entity)
            .where(Entity.entity_type == "country")
            .where(Entity.canonical_name != INDIA_CANONICAL)
            .order_by(Entity.mention_count.desc())
        )
        countries = list(countries_result.scalars().all())

        if not countries:
            logger.info("Threat matrix: no country entities found")
            return

        now = datetime.now(timezone.utc)
        lookback = now - timedelta(hours=72)
        updated = 0

        for country in countries:
            # Find relationship between India and this country
            a_id, b_id = sorted([india.id, country.id])
            rel_result = await session.execute(
                select(EntityRelation)
                .where(EntityRelation.entity_a_id == a_id)
                .where(EntityRelation.entity_b_id == b_id)
                .limit(1)
            )
            relation = rel_result.scalar_one_or_none()

            if not relation:
                continue  # No interaction recorded

            # Calculate scores from recent mentions
            # Get recent events where both India and this country are mentioned
            india_mentions = await session.execute(
                select(EntityMention)
                .where(EntityMention.entity_id == india.id)
                .where(EntityMention.mentioned_at >= lookback)
            )
            india_event_ids = {m.event_id for m in india_mentions.scalars().all()}

            country_mentions = await session.execute(
                select(EntityMention)
                .where(EntityMention.entity_id == country.id)
                .where(EntityMention.mentioned_at >= lookback)
            )
            country_events = list(country_mentions.scalars().all())
            country_event_ids = {m.event_id for m in country_events}

            # Co-mentioned events
            shared_event_ids = india_event_ids & country_event_ids

            if not shared_event_ids:
                continue

            # Score based on event categories and sentiment
            cooperation = 0.0
            tension = 0.0
            recent_events = []

            for event_id in list(shared_event_ids)[:20]:
                event = await session.get(Event, event_id)
                if not event:
                    continue

                category = (event.category or "").lower()
                title_lower = (event.title or "").lower()

                # Cooperation signals
                if category in ("diplomacy", "economy") or any(w in title_lower for w in ("agreement", "cooperation", "deal", "trade", "visit", "partnership", "pact")):
                    cooperation += 0.15
                if any(w in title_lower for w in ("joint", "ally", "alliance", "bilateral")):
                    cooperation += 0.1

                # Tension signals
                if category == "conflict" or any(w in title_lower for w in ("sanctions", "tension", "border", "dispute", "clash", "threat", "warning")):
                    tension += 0.15
                if any(w in title_lower for w in ("war", "attack", "strike", "hostile")):
                    tension += 0.2

                # Neutral development
                if category in ("defense",):
                    cooperation += 0.05  # defense cooperation is usually positive

                recent_events.append({
                    "event_id": event.id,
                    "title": (event.title or "")[:80],
                    "category": event.category,
                })

            # Normalize scores to 0-1 range
            cooperation = min(cooperation, 1.0)
            tension = min(tension, 1.0)

            # Determine trend
            trend = "stable"
            # Check existing matrix entry for comparison
            existing = await session.execute(
                select(ThreatMatrix)
                .where(ThreatMatrix.country_entity_id == country.id)
                .limit(1)
            )
            old_entry = existing.scalar_one_or_none()

            if old_entry:
                coop_delta = cooperation - old_entry.cooperation_score
                tension_delta = tension - old_entry.tension_score
                if coop_delta > 0.1:
                    trend = "warming"
                elif tension_delta > 0.1:
                    trend = "cooling"
                elif abs(coop_delta) > 0.1 and abs(tension_delta) > 0.1:
                    trend = "volatile"

                # Update existing
                old_entry.cooperation_score = cooperation
                old_entry.tension_score = tension
                old_entry.trend = trend
                old_entry.recent_events_json = json.dumps(recent_events[:5])
                old_entry.updated_at = now
            else:
                # Create new entry
                session.add(ThreatMatrix(
                    country_entity_id=country.id,
                    cooperation_score=cooperation,
                    tension_score=tension,
                    trend=trend,
                    recent_events_json=json.dumps(recent_events[:5]),
                    updated_at=now,
                ))

            # Store historical snapshot (one per country per hour max)
            last_snap = await session.execute(
                select(ThreatMatrixHistory)
                .where(ThreatMatrixHistory.country_entity_id == country.id)
                .order_by(ThreatMatrixHistory.snapshot_at.desc())
                .limit(1)
            )
            last = last_snap.scalar_one_or_none()
            if not last or (now - last.snapshot_at.replace(tzinfo=timezone.utc)).total_seconds() >= 3600:
                session.add(ThreatMatrixHistory(
                    country_entity_id=country.id,
                    cooperation_score=cooperation,
                    tension_score=tension,
                    trend=trend,
                    snapshot_at=now,
                ))

            updated += 1

        if updated:
            await session.commit()
            logger.info(f"Threat matrix: updated {updated} country relationships")
