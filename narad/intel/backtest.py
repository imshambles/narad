"""
Signal Backtesting Engine

Evaluates past trading signals against actual market movements.
Tracks price changes at 1h, 4h, 24h, 48h, 72h after signal generation.
Calculates hit rates per signal type and correlation rule.
"""
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func, and_

from narad.database import async_session
from narad.models import MarketDataPoint, Signal, SignalOutcome

logger = logging.getLogger(__name__)

# How long after a signal to check prices (in hours)
EVAL_WINDOWS = [1, 4, 24, 48, 72]


async def evaluate_signals() -> None:
    """Evaluate past signals that haven't been scored yet.

    Finds commodity and correlation signals older than 72h without outcomes,
    looks up market prices at trigger time and at each evaluation window,
    determines if the predicted direction was correct.
    """
    async with async_session() as session:
        now = datetime.now(timezone.utc)
        # Only evaluate signals between 72h and 30d old (need full window, skip ancient)
        cutoff_min = now - timedelta(days=30)
        cutoff_max = now - timedelta(hours=72)

        # Find unevaluated commodity signals
        unevaluated = await session.execute(
            select(Signal)
            .where(Signal.signal_type.in_(["commodity", "correlation"]))
            .where(Signal.detected_at >= cutoff_min)
            .where(Signal.detected_at <= cutoff_max)
            .where(
                ~Signal.id.in_(
                    select(SignalOutcome.signal_id)
                )
            )
            .order_by(Signal.detected_at.desc())
            .limit(50)
        )
        signals = list(unevaluated.scalars().all())

        if not signals:
            logger.debug("No signals to evaluate")
            return

        evaluated = 0
        for signal in signals:
            outcome = await _evaluate_single(session, signal)
            if outcome:
                session.add(outcome)
                evaluated += 1

        if evaluated:
            await session.commit()
            logger.info(f"Backtest: evaluated {evaluated} signals")


async def _evaluate_single(session, signal: Signal) -> SignalOutcome | None:
    """Evaluate a single signal against market data."""
    data = json.loads(signal.data_json or "{}")

    # Get tracked symbols from signal
    symbols_with_direction = _extract_symbols_and_directions(signal.signal_type, data)
    if not symbols_with_direction:
        return None

    # Get trigger prices
    trigger_prices = data.get("price_at_trigger", {})
    market_context = data.get("market_context", {})

    # If no explicit trigger prices, try to find market data near signal time
    if not trigger_prices:
        for sym, _ in symbols_with_direction:
            price_at_trigger = await _get_price_near(session, sym, signal.detected_at)
            if price_at_trigger is not None:
                trigger_prices[sym] = price_at_trigger

    if not trigger_prices:
        return None

    # Get prices at each evaluation window
    results = {}
    hits = 0
    total = 0

    for sym, expected_direction in symbols_with_direction:
        tp = trigger_prices.get(sym)
        if tp is None or tp == 0:
            continue

        sym_results = {"trigger_price": tp, "direction": expected_direction, "windows": {}}

        for hours in EVAL_WINDOWS:
            target_time = signal.detected_at + timedelta(hours=hours)
            price = await _get_price_near(session, sym, target_time)
            if price is None:
                continue

            pct_change = ((price - tp) / tp) * 100
            direction_correct = _check_direction(expected_direction, pct_change)

            sym_results["windows"][f"{hours}h"] = {
                "price": round(price, 4),
                "change_pct": round(pct_change, 2),
                "direction_correct": direction_correct,
            }

            if direction_correct is not None:
                total += 1
                if direction_correct:
                    hits += 1

        results[sym] = sym_results

    if not results:
        return None

    hit_rate = (hits / total * 100) if total > 0 else 0

    # Determine overall verdict
    if hit_rate >= 60:
        verdict = "hit"
    elif hit_rate >= 40:
        verdict = "partial"
    else:
        verdict = "miss"

    # Extract rule_id for correlations
    rule_id = data.get("rule_id", "")
    bucket_name = data.get("bucket_name", "")

    return SignalOutcome(
        signal_id=signal.id,
        signal_type=signal.signal_type,
        rule_id=rule_id or bucket_name,
        severity=signal.severity,
        detected_at=signal.detected_at,
        symbols_json=json.dumps(list(trigger_prices.keys())),
        trigger_prices_json=json.dumps(trigger_prices),
        results_json=json.dumps(results),
        hit_rate=round(hit_rate, 1),
        verdict=verdict,
        evaluated_at=datetime.now(timezone.utc),
    )


def _extract_symbols_and_directions(signal_type: str, data: dict) -> list[tuple[str, str]]:
    """Extract (symbol, expected_direction) pairs from signal data."""
    pairs = []

    if signal_type == "commodity":
        for comm in data.get("commodities", []):
            sym = comm.get("symbol")
            direction = comm.get("direction", "").lower()
            if sym and direction:
                pairs.append((sym, direction))

        # Also check market_context symbols
        if not pairs:
            for sym in data.get("market_context", {}):
                pairs.append((sym, "long"))  # default assumption for commodity signals

    elif signal_type == "correlation":
        factors = data.get("factors", [])
        for f in factors:
            if f.get("domain") == "market":
                sym = f.get("symbol")
                change = f.get("change_1d", 0)
                direction = "long" if change > 0 else "short"
                if sym:
                    pairs.append((sym, direction))

        # If no market factors, use rule-specific defaults
        rule_id = data.get("rule_id", "")
        if not pairs:
            rule_symbols = {
                "hormuz_oil": [("BZ=F", "long"), ("CL=F", "long")],
                "lac_tension_defense": [("GC=F", "long")],
                "pak_border_escalation": [("GC=F", "long")],
                "gulf_aden_shipping": [("BZ=F", "long")],
                "gold_rush_geopolitical": [("GC=F", "long")],
                "inr_pressure": [("INR=X", "long")],  # INR weakening = number goes up
                "scs_maritime": [("GC=F", "long")],
            }
            pairs = rule_symbols.get(rule_id, [])

    return pairs


async def _get_price_near(session, symbol: str, target_time: datetime, tolerance_hours: int = 2) -> float | None:
    """Get the market price closest to a target time."""
    window_start = target_time - timedelta(hours=tolerance_hours)
    window_end = target_time + timedelta(hours=tolerance_hours)

    result = await session.execute(
        select(MarketDataPoint)
        .where(MarketDataPoint.symbol == symbol)
        .where(MarketDataPoint.fetched_at >= window_start)
        .where(MarketDataPoint.fetched_at <= window_end)
        .order_by(
            func.abs(
                func.julianday(MarketDataPoint.fetched_at) - func.julianday(target_time)
            )
        )
        .limit(1)
    )
    point = result.scalar_one_or_none()
    return point.price if point else None


def _check_direction(expected: str, pct_change: float) -> bool | None:
    """Check if price moved in the expected direction.

    Returns True (correct), False (wrong), None (insignificant move).
    """
    # Minimum move threshold to count as a directional move
    if abs(pct_change) < 0.1:
        return None

    expected = expected.lower().strip()

    if expected in ("long", "positive", "already moving"):
        return pct_change > 0
    elif expected in ("short", "negative", "short-term negative"):
        return pct_change < 0
    elif expected in ("mixed",):
        return None  # Can't evaluate mixed signals
    else:
        return None


async def get_backtest_summary() -> dict:
    """Get overall backtest statistics."""
    async with async_session() as session:
        # Overall stats
        total = await session.execute(
            select(func.count(SignalOutcome.id))
        )
        total_count = total.scalar() or 0

        if total_count == 0:
            return {"total_evaluated": 0, "message": "No signals evaluated yet. Signals need 72h of market data after generation."}

        # Average hit rate
        avg_hit = await session.execute(
            select(func.avg(SignalOutcome.hit_rate))
        )
        avg_hit_rate = round(avg_hit.scalar() or 0, 1)

        # By verdict
        verdicts = {}
        for v in ["hit", "partial", "miss"]:
            count = await session.execute(
                select(func.count(SignalOutcome.id))
                .where(SignalOutcome.verdict == v)
            )
            verdicts[v] = count.scalar() or 0

        # By signal type
        by_type = {}
        for stype in ["commodity", "correlation"]:
            type_result = await session.execute(
                select(
                    func.count(SignalOutcome.id),
                    func.avg(SignalOutcome.hit_rate),
                )
                .where(SignalOutcome.signal_type == stype)
            )
            row = type_result.first()
            if row and row[0]:
                by_type[stype] = {
                    "count": row[0],
                    "avg_hit_rate": round(row[1] or 0, 1),
                }

        # By rule_id (for correlations and commodity buckets)
        rule_result = await session.execute(
            select(
                SignalOutcome.rule_id,
                SignalOutcome.signal_type,
                func.count(SignalOutcome.id),
                func.avg(SignalOutcome.hit_rate),
            )
            .where(SignalOutcome.rule_id != "")
            .group_by(SignalOutcome.rule_id, SignalOutcome.signal_type)
            .order_by(func.avg(SignalOutcome.hit_rate).desc())
        )
        by_rule = []
        for row in rule_result.all():
            by_rule.append({
                "rule_id": row[0],
                "signal_type": row[1],
                "count": row[2],
                "avg_hit_rate": round(row[3] or 0, 1),
            })

        # Recent outcomes (last 10)
        recent = await session.execute(
            select(SignalOutcome)
            .order_by(SignalOutcome.evaluated_at.desc())
            .limit(10)
        )
        recent_outcomes = []
        for o in recent.scalars().all():
            recent_outcomes.append({
                "signal_id": o.signal_id,
                "type": o.signal_type,
                "rule": o.rule_id,
                "severity": o.severity,
                "hit_rate": o.hit_rate,
                "verdict": o.verdict,
                "detected_at": o.detected_at,
                "evaluated_at": o.evaluated_at,
                "results": json.loads(o.results_json or "{}"),
            })

        return {
            "total_evaluated": total_count,
            "avg_hit_rate": avg_hit_rate,
            "verdicts": verdicts,
            "by_signal_type": by_type,
            "by_rule": by_rule,
            "recent": recent_outcomes,
        }
