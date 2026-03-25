"""
Signal Detection Engine

Detects anomalies and patterns in entity co-occurrences,
mention frequency spikes, and sentiment shifts.
"""
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from narad.database import async_session
from narad.models import Entity, EntityMention, EntityRelation, Signal

logger = logging.getLogger(__name__)

SPIKE_THRESHOLD = 3.0  # 3x normal mention rate = spike
MIN_MENTIONS_FOR_SPIKE = 3


async def detect_signals() -> None:
    """Run all signal detection algorithms."""
    async with async_session() as session:
        signals_found = 0
        signals_found += await _detect_mention_spikes(session)
        signals_found += await _detect_new_relationships(session)
        signals_found += await _detect_sentiment_shifts(session)

        if signals_found:
            await session.commit()
            logger.info(f"Signals: detected {signals_found} new signals")


async def _detect_mention_spikes(session: AsyncSession) -> int:
    """Detect entities with unusual mention frequency (spike)."""
    now = datetime.now(timezone.utc)
    recent_window = now - timedelta(hours=6)
    baseline_window = now - timedelta(hours=48)

    # Get entities with recent mentions
    recent_stmt = (
        select(
            EntityMention.entity_id,
            func.count(EntityMention.id).label("recent_count")
        )
        .where(EntityMention.mentioned_at >= recent_window)
        .group_by(EntityMention.entity_id)
    )
    recent_result = await session.execute(recent_stmt)
    recent_counts = {row[0]: row[1] for row in recent_result.all()}

    # Get baseline (48h, normalized to 6h equivalent)
    baseline_stmt = (
        select(
            EntityMention.entity_id,
            func.count(EntityMention.id).label("baseline_count")
        )
        .where(EntityMention.mentioned_at >= baseline_window)
        .where(EntityMention.mentioned_at < recent_window)
        .group_by(EntityMention.entity_id)
    )
    baseline_result = await session.execute(baseline_stmt)
    # Normalize 42h baseline to 6h equivalent
    baseline_counts = {row[0]: row[1] / 7.0 for row in baseline_result.all()}

    signals = 0
    for entity_id, recent in recent_counts.items():
        if recent < MIN_MENTIONS_FOR_SPIKE:
            continue

        baseline = baseline_counts.get(entity_id, 0.5)  # default 0.5 for new entities
        if baseline < 0.1:
            baseline = 0.5

        ratio = recent / baseline

        if ratio >= SPIKE_THRESHOLD:
            entity = await session.get(Entity, entity_id)
            if not entity:
                continue

            # Check if we already have an active spike signal for this entity
            existing = await session.execute(
                select(Signal)
                .where(Signal.signal_type == "spike")
                .where(Signal.is_active == True)
                .where(Signal.entity_ids_json.contains(str(entity_id)))
                .where(Signal.detected_at >= now - timedelta(hours=12))
                .limit(1)
            )
            if existing.scalar_one_or_none():
                continue

            severity = "medium" if ratio < 5 else "high" if ratio < 10 else "critical"

            session.add(Signal(
                signal_type="spike",
                title=f"{entity.name} mentions surged {ratio:.1f}x",
                description=f"{entity.name} ({entity.entity_type}) was mentioned {recent} times in the last 6 hours, compared to a baseline of {baseline:.1f}. This {ratio:.1f}x increase suggests heightened activity or a developing situation.",
                severity=severity,
                entity_ids_json=json.dumps([entity_id]),
                data_json=json.dumps({"recent": recent, "baseline": round(baseline, 1), "ratio": round(ratio, 1)}),
                detected_at=now,
                is_active=True,
            ))
            signals += 1

    return signals


async def _detect_new_relationships(session: AsyncSession) -> int:
    """Detect when two entities that rarely co-occur suddenly appear together."""
    now = datetime.now(timezone.utc)
    recent_window = now - timedelta(hours=12)

    # Find recently created/updated relations
    new_rels = await session.execute(
        select(EntityRelation)
        .where(EntityRelation.last_updated_at >= recent_window)
        .where(EntityRelation.co_occurrence_count <= 2)  # new relationship
    )

    signals = 0
    for rel in new_rels.scalars().all():
        ent_a = await session.get(Entity, rel.entity_a_id)
        ent_b = await session.get(Entity, rel.entity_b_id)
        if not ent_a or not ent_b:
            continue

        # Only care about significant entities
        if ent_a.mention_count < 2 or ent_b.mention_count < 2:
            continue

        # Check if we already have this signal
        existing = await session.execute(
            select(Signal)
            .where(Signal.signal_type == "new_entity")
            .where(Signal.is_active == True)
            .where(Signal.detected_at >= recent_window)
            .where(Signal.entity_ids_json.contains(str(ent_a.id)))
            .where(Signal.entity_ids_json.contains(str(ent_b.id)))
            .limit(1)
        )
        if existing.scalar_one_or_none():
            continue

        session.add(Signal(
            signal_type="new_entity",
            title=f"New link: {ent_a.name} ↔ {ent_b.name}",
            description=f"{ent_a.name} and {ent_b.name} appeared together for the first time in recent coverage. Context: {rel.relation_type}.",
            severity="low",
            entity_ids_json=json.dumps([ent_a.id, ent_b.id]),
            data_json=json.dumps({"relation_type": rel.relation_type}),
            detected_at=now,
            is_active=True,
        ))
        signals += 1

    return signals


async def _detect_sentiment_shifts(session: AsyncSession) -> int:
    """Detect when sentiment around an entity changes significantly."""
    now = datetime.now(timezone.utc)
    recent_window = now - timedelta(hours=12)
    baseline_window = now - timedelta(hours=72)

    # Get entities with enough mentions
    entities = await session.execute(
        select(Entity)
        .where(Entity.mention_count >= 5)
        .where(Entity.entity_type == "country")
    )

    signals = 0
    for entity in entities.scalars().all():
        # Recent sentiment average
        recent_sent = await session.execute(
            select(func.avg(EntityMention.sentiment))
            .where(EntityMention.entity_id == entity.id)
            .where(EntityMention.mentioned_at >= recent_window)
        )
        recent_avg = recent_sent.scalar() or 0

        # Baseline sentiment
        baseline_sent = await session.execute(
            select(func.avg(EntityMention.sentiment))
            .where(EntityMention.entity_id == entity.id)
            .where(EntityMention.mentioned_at >= baseline_window)
            .where(EntityMention.mentioned_at < recent_window)
        )
        baseline_avg = baseline_sent.scalar() or 0

        shift = recent_avg - baseline_avg

        if abs(shift) >= 0.3:  # significant sentiment change
            direction = "negative" if shift < 0 else "positive"

            existing = await session.execute(
                select(Signal)
                .where(Signal.signal_type == "trend_shift")
                .where(Signal.entity_ids_json.contains(str(entity.id)))
                .where(Signal.detected_at >= now - timedelta(hours=12))
                .limit(1)
            )
            if existing.scalar_one_or_none():
                continue

            session.add(Signal(
                signal_type="trend_shift",
                title=f"Sentiment shift: {entity.name} trending {direction}",
                description=f"Coverage of {entity.name} has shifted {direction} (from {baseline_avg:.2f} to {recent_avg:.2f}). This may indicate a changing geopolitical posture.",
                severity="medium" if abs(shift) < 0.5 else "high",
                entity_ids_json=json.dumps([entity.id]),
                data_json=json.dumps({"recent_avg": round(recent_avg, 2), "baseline_avg": round(baseline_avg, 2), "shift": round(shift, 2)}),
                detected_at=now,
                is_active=True,
            ))
            signals += 1

    return signals
