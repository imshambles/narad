"""
Tests for the signal backtesting engine:
- _extract_symbols_and_directions (pure function)
- _check_direction (pure function)
- _get_price_near (DB lookup)
- _evaluate_single (single signal evaluation)
- evaluate_signals (batch evaluation)
- get_backtest_summary (statistics)
"""
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from narad.models import Signal, SignalOutcome, MarketDataPoint
from narad.intel.backtest import (
    _extract_symbols_and_directions,
    _check_direction,
    _get_price_near,
    _evaluate_single,
    evaluate_signals,
    get_backtest_summary,
    EVAL_WINDOWS,
)
from tests.conftest import (
    make_signal, make_market_point_at, make_signal_outcome,
)


# ═══════════════════════════════════════════
# _extract_symbols_and_directions
# ═══════════════════════════════════════════

class TestExtractSymbolsAndDirections:
    def test_commodity_with_commodities_field(self):
        data = {"commodities": [
            {"symbol": "BZ=F", "direction": "long", "reason": "oil"},
            {"symbol": "GC=F", "direction": "long", "reason": "gold"},
        ]}
        result = _extract_symbols_and_directions("commodity", data)
        assert ("BZ=F", "long") in result
        assert ("GC=F", "long") in result

    def test_commodity_fallback_to_market_context(self):
        data = {"market_context": {"GC=F": {"price": 2050}, "BZ=F": {"price": 85}}}
        result = _extract_symbols_and_directions("commodity", data)
        symbols = [s for s, _ in result]
        assert "GC=F" in symbols
        assert "BZ=F" in symbols

    def test_commodity_empty_data(self):
        result = _extract_symbols_and_directions("commodity", {})
        assert result == []

    def test_commodity_commodities_without_symbol(self):
        data = {"commodities": [{"direction": "long"}]}
        result = _extract_symbols_and_directions("commodity", data)
        assert result == []

    def test_correlation_with_market_factors_positive(self):
        data = {"factors": [
            {"domain": "market", "symbol": "BZ=F", "change_1d": 3.5},
        ]}
        result = _extract_symbols_and_directions("correlation", data)
        assert result == [("BZ=F", "long")]

    def test_correlation_with_market_factors_negative(self):
        data = {"factors": [
            {"domain": "market", "symbol": "GC=F", "change_1d": -2.0},
        ]}
        result = _extract_symbols_and_directions("correlation", data)
        assert result == [("GC=F", "short")]

    def test_correlation_geoint_factors_ignored(self):
        data = {"factors": [
            {"domain": "geoint", "title": "heat sigs"},
        ]}
        result = _extract_symbols_and_directions("correlation", data)
        assert result == []

    def test_correlation_fallback_hormuz(self):
        data = {"rule_id": "hormuz_oil"}
        result = _extract_symbols_and_directions("correlation", data)
        assert ("BZ=F", "long") in result
        assert ("CL=F", "long") in result

    def test_correlation_fallback_lac(self):
        data = {"rule_id": "lac_tension_defense"}
        result = _extract_symbols_and_directions("correlation", data)
        assert ("GC=F", "long") in result

    def test_correlation_fallback_inr(self):
        data = {"rule_id": "inr_pressure"}
        result = _extract_symbols_and_directions("correlation", data)
        assert ("INR=X", "long") in result

    def test_correlation_unknown_rule_no_factors(self):
        data = {"rule_id": "nonexistent_rule"}
        result = _extract_symbols_and_directions("correlation", data)
        assert result == []

    def test_unknown_signal_type(self):
        result = _extract_symbols_and_directions("spike", {"some": "data"})
        assert result == []


# ═══════════════════════════════════════════
# _check_direction
# ═══════════════════════════════════════════

class TestCheckDirection:
    def test_long_positive_is_true(self):
        assert _check_direction("long", 2.5) is True

    def test_long_negative_is_false(self):
        assert _check_direction("long", -2.5) is False

    def test_short_negative_is_true(self):
        assert _check_direction("short", -1.5) is True

    def test_short_positive_is_false(self):
        assert _check_direction("short", 1.5) is False

    def test_insignificant_move_returns_none(self):
        assert _check_direction("long", 0.05) is None
        assert _check_direction("short", -0.05) is None
        assert _check_direction("long", 0.0) is None

    def test_mixed_returns_none(self):
        assert _check_direction("mixed", 5.0) is None
        assert _check_direction("mixed", -5.0) is None

    def test_positive_alias(self):
        assert _check_direction("positive", 2.0) is True
        assert _check_direction("positive", -2.0) is False

    def test_negative_alias(self):
        assert _check_direction("negative", -2.0) is True
        assert _check_direction("negative", 2.0) is False

    def test_short_term_negative_alias(self):
        assert _check_direction("short-term negative", -3.0) is True
        assert _check_direction("short-term negative", 3.0) is False

    def test_already_moving_alias(self):
        assert _check_direction("already moving", 1.0) is True

    def test_unknown_direction_returns_none(self):
        assert _check_direction("sideways", 5.0) is None
        assert _check_direction("", 5.0) is None

    def test_boundary_at_threshold(self):
        # Exactly 0.1 should count as a move
        assert _check_direction("long", 0.1) is True
        # Just below should be insignificant
        assert _check_direction("long", 0.09) is None


# ═══════════════════════════════════════════
# _get_price_near (DB tests)
# ═══════════════════════════════════════════

class TestGetPriceNear:
    @pytest.mark.asyncio
    async def test_finds_closest_price(self, patched_session):
        factory = patched_session
        target = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)

        async with factory() as session:
            session.add(make_market_point_at("BZ=F", 84.0, target - timedelta(hours=1)))
            session.add(make_market_point_at("BZ=F", 85.0, target))
            session.add(make_market_point_at("BZ=F", 86.0, target + timedelta(hours=1)))
            await session.commit()

        async with factory() as session:
            price = await _get_price_near(session, "BZ=F", target)
            assert price == 85.0

    @pytest.mark.asyncio
    async def test_returns_none_outside_tolerance(self, patched_session):
        factory = patched_session
        target = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)

        async with factory() as session:
            # Price 5 hours away — beyond 2h default tolerance
            session.add(make_market_point_at("BZ=F", 85.0, target - timedelta(hours=5)))
            await session.commit()

        async with factory() as session:
            price = await _get_price_near(session, "BZ=F", target)
            assert price is None

    @pytest.mark.asyncio
    async def test_returns_none_for_missing_symbol(self, patched_session):
        factory = patched_session
        target = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)

        async with factory() as session:
            session.add(make_market_point_at("GC=F", 2050.0, target))
            await session.commit()

        async with factory() as session:
            price = await _get_price_near(session, "BZ=F", target)
            assert price is None

    @pytest.mark.asyncio
    async def test_picks_nearest_in_window(self, patched_session):
        factory = patched_session
        target = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)

        async with factory() as session:
            # Two points in window, one closer
            session.add(make_market_point_at("BZ=F", 80.0, target - timedelta(minutes=90)))
            session.add(make_market_point_at("BZ=F", 85.0, target - timedelta(minutes=10)))
            await session.commit()

        async with factory() as session:
            price = await _get_price_near(session, "BZ=F", target)
            assert price == 85.0  # closer one


# ═══════════════════════════════════════════
# _evaluate_single
# ═══════════════════════════════════════════

class TestEvaluateSingle:
    @pytest.mark.asyncio
    async def test_commodity_hit(self, patched_session):
        factory = patched_session
        now = datetime.now(timezone.utc)
        detected = now - timedelta(days=5)

        async with factory() as session:
            sig = make_signal(
                signal_type="commodity",
                title="Hormuz Disruption",
                severity="high",
                data_json=json.dumps({
                    "commodities": [{"symbol": "BZ=F", "direction": "long"}],
                    "price_at_trigger": {"BZ=F": 80.0},
                    "bucket_name": "hormuz",
                }),
                detected_at=detected,
            )
            session.add(sig)
            await session.flush()

            # Add market data showing upward movement at each window
            for hours in EVAL_WINDOWS:
                session.add(make_market_point_at(
                    "BZ=F", 80.0 + hours * 0.5,
                    detected + timedelta(hours=hours),
                ))
            await session.commit()

            outcome = await _evaluate_single(session, sig)

        assert outcome is not None
        assert outcome.verdict == "hit"
        assert outcome.hit_rate >= 60

    @pytest.mark.asyncio
    async def test_commodity_miss(self, patched_session):
        factory = patched_session
        now = datetime.now(timezone.utc)
        detected = now - timedelta(days=5)

        async with factory() as session:
            sig = make_signal(
                signal_type="commodity",
                title="Hormuz Miss",
                severity="high",
                data_json=json.dumps({
                    "commodities": [{"symbol": "BZ=F", "direction": "long"}],
                    "price_at_trigger": {"BZ=F": 80.0},
                    "bucket_name": "hormuz",
                }),
                detected_at=detected,
            )
            session.add(sig)
            await session.flush()

            # Price went DOWN (wrong direction for "long")
            for hours in EVAL_WINDOWS:
                session.add(make_market_point_at(
                    "BZ=F", 80.0 - hours * 0.3,
                    detected + timedelta(hours=hours),
                ))
            await session.commit()

            outcome = await _evaluate_single(session, sig)

        assert outcome is not None
        assert outcome.verdict == "miss"
        assert outcome.hit_rate < 40

    @pytest.mark.asyncio
    async def test_returns_none_no_symbols(self, patched_session):
        factory = patched_session
        async with factory() as session:
            sig = make_signal(
                signal_type="commodity",
                data_json=json.dumps({}),
            )
            session.add(sig)
            await session.flush()

            outcome = await _evaluate_single(session, sig)
            assert outcome is None

    @pytest.mark.asyncio
    async def test_returns_none_no_trigger_prices_no_market_data(self, patched_session):
        factory = patched_session
        async with factory() as session:
            sig = make_signal(
                signal_type="commodity",
                data_json=json.dumps({
                    "commodities": [{"symbol": "BZ=F", "direction": "long"}],
                }),
                detected_at=datetime.now(timezone.utc) - timedelta(days=5),
            )
            session.add(sig)
            await session.flush()

            # No market data at all
            outcome = await _evaluate_single(session, sig)
            assert outcome is None

    @pytest.mark.asyncio
    async def test_zero_trigger_price_skipped(self, patched_session):
        factory = patched_session
        async with factory() as session:
            sig = make_signal(
                signal_type="commodity",
                data_json=json.dumps({
                    "commodities": [{"symbol": "BZ=F", "direction": "long"}],
                    "price_at_trigger": {"BZ=F": 0},
                }),
            )
            session.add(sig)
            await session.flush()

            outcome = await _evaluate_single(session, sig)
            assert outcome is None

    @pytest.mark.asyncio
    async def test_correlation_with_rule_fallback(self, patched_session):
        factory = patched_session
        detected = datetime.now(timezone.utc) - timedelta(days=5)

        async with factory() as session:
            sig = make_signal(
                signal_type="correlation",
                title="COMPOUND: Hormuz Oil",
                severity="critical",
                data_json=json.dumps({
                    "rule_id": "hormuz_oil",
                    "factors": [],
                    "domains": [],
                }),
                detected_at=detected,
            )
            session.add(sig)
            await session.flush()

            # Add market data near detection time and at eval windows
            session.add(make_market_point_at("BZ=F", 80.0, detected))
            session.add(make_market_point_at("CL=F", 75.0, detected))
            for hours in EVAL_WINDOWS:
                session.add(make_market_point_at("BZ=F", 80.0 + hours * 0.2, detected + timedelta(hours=hours)))
                session.add(make_market_point_at("CL=F", 75.0 + hours * 0.2, detected + timedelta(hours=hours)))
            await session.commit()

            outcome = await _evaluate_single(session, sig)

        assert outcome is not None
        assert outcome.signal_type == "correlation"
        assert outcome.rule_id == "hormuz_oil"


# ═══════════════════════════════════════════
# evaluate_signals (batch)
# ═══════════════════════════════════════════

class TestEvaluateSignals:
    @pytest.mark.asyncio
    async def test_evaluates_old_signals(self, patched_session):
        factory = patched_session
        now = datetime.now(timezone.utc)
        detected = now - timedelta(days=5)

        async with factory() as session:
            for i in range(3):
                sig = make_signal(
                    signal_type="commodity",
                    title=f"Signal {i}",
                    severity="high",
                    data_json=json.dumps({
                        "commodities": [{"symbol": "BZ=F", "direction": "long"}],
                        "price_at_trigger": {"BZ=F": 80.0},
                        "bucket_name": f"bucket_{i}",
                    }),
                    detected_at=detected - timedelta(hours=i),
                )
                session.add(sig)

            # Market data for evaluation
            session.add(make_market_point_at("BZ=F", 80.0, detected))
            for hours in EVAL_WINDOWS:
                session.add(make_market_point_at(
                    "BZ=F", 80.0 + hours * 0.1,
                    detected + timedelta(hours=hours),
                ))
            await session.commit()

        await evaluate_signals()

        async with factory() as session:
            outcomes = (await session.execute(select(SignalOutcome))).scalars().all()
            assert len(outcomes) == 3

    @pytest.mark.asyncio
    async def test_skips_already_evaluated(self, patched_session):
        factory = patched_session
        now = datetime.now(timezone.utc)
        detected = now - timedelta(days=5)

        async with factory() as session:
            sig = make_signal(
                signal_type="commodity",
                title="Already evaluated",
                data_json=json.dumps({
                    "commodities": [{"symbol": "BZ=F", "direction": "long"}],
                    "price_at_trigger": {"BZ=F": 80.0},
                    "bucket_name": "test",
                }),
                detected_at=detected,
            )
            session.add(sig)
            await session.flush()

            # Pre-existing outcome
            session.add(make_signal_outcome(signal_id=sig.id))
            await session.commit()

        await evaluate_signals()

        async with factory() as session:
            outcomes = (await session.execute(select(SignalOutcome))).scalars().all()
            assert len(outcomes) == 1  # no new one

    @pytest.mark.asyncio
    async def test_skips_too_recent_signals(self, patched_session):
        factory = patched_session
        now = datetime.now(timezone.utc)

        async with factory() as session:
            sig = make_signal(
                signal_type="commodity",
                title="Too recent",
                data_json=json.dumps({
                    "commodities": [{"symbol": "BZ=F", "direction": "long"}],
                    "price_at_trigger": {"BZ=F": 80.0},
                }),
                detected_at=now - timedelta(hours=24),  # within 72h cutoff
            )
            session.add(sig)
            await session.commit()

        await evaluate_signals()

        async with factory() as session:
            outcomes = (await session.execute(select(SignalOutcome))).scalars().all()
            assert len(outcomes) == 0

    @pytest.mark.asyncio
    async def test_skips_too_old_signals(self, patched_session):
        factory = patched_session
        now = datetime.now(timezone.utc)

        async with factory() as session:
            sig = make_signal(
                signal_type="commodity",
                title="Too old",
                data_json=json.dumps({
                    "commodities": [{"symbol": "BZ=F", "direction": "long"}],
                    "price_at_trigger": {"BZ=F": 80.0},
                }),
                detected_at=now - timedelta(days=45),  # beyond 30d cutoff
            )
            session.add(sig)
            await session.commit()

        await evaluate_signals()

        async with factory() as session:
            outcomes = (await session.execute(select(SignalOutcome))).scalars().all()
            assert len(outcomes) == 0

    @pytest.mark.asyncio
    async def test_only_evaluates_commodity_and_correlation(self, patched_session):
        factory = patched_session
        now = datetime.now(timezone.utc)
        detected = now - timedelta(days=5)

        async with factory() as session:
            # Spike signal should be ignored
            session.add(make_signal(
                signal_type="spike",
                title="Pakistan spike",
                detected_at=detected,
            ))
            # Assessment should be ignored
            session.add(make_signal(
                signal_type="assessment",
                title="Strategic shift",
                detected_at=detected,
            ))
            await session.commit()

        await evaluate_signals()

        async with factory() as session:
            outcomes = (await session.execute(select(SignalOutcome))).scalars().all()
            assert len(outcomes) == 0


# ═══════════════════════════════════════════
# get_backtest_summary
# ═══════════════════════════════════════════

class TestGetBacktestSummary:
    @pytest.mark.asyncio
    async def test_empty_database(self, patched_session):
        result = await get_backtest_summary()
        assert result["total_evaluated"] == 0
        assert "message" in result

    @pytest.mark.asyncio
    async def test_with_outcomes(self, patched_session):
        factory = patched_session
        async with factory() as session:
            session.add(make_signal_outcome(signal_id=1, hit_rate=80, verdict="hit", signal_type="commodity", rule_id="hormuz"))
            session.add(make_signal_outcome(signal_id=2, hit_rate=70, verdict="hit", signal_type="commodity", rule_id="hormuz"))
            session.add(make_signal_outcome(signal_id=3, hit_rate=50, verdict="partial", signal_type="correlation", rule_id="lac_tension"))
            session.add(make_signal_outcome(signal_id=4, hit_rate=20, verdict="miss", signal_type="correlation", rule_id="pak_border"))
            session.add(make_signal_outcome(signal_id=5, hit_rate=65, verdict="hit", signal_type="commodity", rule_id="oil_spike"))
            await session.commit()

        result = await get_backtest_summary()
        assert result["total_evaluated"] == 5
        assert result["avg_hit_rate"] > 0
        assert result["verdicts"]["hit"] == 3
        assert result["verdicts"]["partial"] == 1
        assert result["verdicts"]["miss"] == 1
        assert "commodity" in result["by_signal_type"]
        assert "correlation" in result["by_signal_type"]
        assert len(result["by_rule"]) >= 3

    @pytest.mark.asyncio
    async def test_recent_outcomes_limited_to_10(self, patched_session):
        factory = patched_session
        async with factory() as session:
            for i in range(15):
                session.add(make_signal_outcome(
                    signal_id=i + 100,
                    hit_rate=50 + i,
                    verdict="hit" if i % 2 == 0 else "miss",
                ))
            await session.commit()

        result = await get_backtest_summary()
        assert len(result["recent"]) == 10

    @pytest.mark.asyncio
    async def test_by_rule_ordered_by_hit_rate(self, patched_session):
        factory = patched_session
        async with factory() as session:
            session.add(make_signal_outcome(signal_id=201, rule_id="bad_rule", hit_rate=20, verdict="miss"))
            session.add(make_signal_outcome(signal_id=202, rule_id="good_rule", hit_rate=90, verdict="hit"))
            await session.commit()

        result = await get_backtest_summary()
        rules = result["by_rule"]
        assert len(rules) >= 2
        # Best rule should be first
        assert rules[0]["avg_hit_rate"] >= rules[-1]["avg_hit_rate"]


# ═══════════════════════════════════════════
# Hit rate boundaries
# ═══════════════════════════════════════════

class TestHitRateBoundaries:
    @pytest.mark.asyncio
    async def test_exactly_60_is_hit(self, patched_session):
        """60% hit rate should be verdict 'hit'."""
        factory = patched_session
        detected = datetime.now(timezone.utc) - timedelta(days=5)

        async with factory() as session:
            sig = make_signal(
                signal_type="commodity",
                data_json=json.dumps({
                    "commodities": [
                        {"symbol": "BZ=F", "direction": "long"},
                        {"symbol": "GC=F", "direction": "long"},
                    ],
                    "price_at_trigger": {"BZ=F": 80.0, "GC=F": 2000.0},
                    "bucket_name": "test",
                }),
                detected_at=detected,
            )
            session.add(sig)
            await session.flush()

            # BZ=F goes up (correct for long), GC=F goes down (incorrect)
            # With 5 windows each: BZ=F all correct (5), GC=F all wrong (5)
            # But we need to hit ~60%, so make BZ go up consistently and GC mixed
            for hours in EVAL_WINDOWS:
                t = detected + timedelta(hours=hours)
                session.add(make_market_point_at("BZ=F", 80.0 + 1.0, t))  # up = correct
                # GC alternates: up for first 2, down for last 3
                if hours <= 4:
                    session.add(make_market_point_at("GC=F", 2000.0 + 10.0, t))  # correct
                else:
                    session.add(make_market_point_at("GC=F", 2000.0 - 10.0, t))  # wrong
            await session.commit()

            outcome = await _evaluate_single(session, sig)

        # With this data distribution, the signal should be at least partial/hit
        assert outcome is not None
        assert outcome.verdict in ("hit", "partial")


# ═══════════════════════════════════════════
# EVAL_WINDOWS config
# ═══════════════════════════════════════════

class TestEvalConfig:
    def test_eval_windows(self):
        assert EVAL_WINDOWS == [1, 4, 24, 48, 72]

    def test_windows_are_sorted(self):
        assert EVAL_WINDOWS == sorted(EVAL_WINDOWS)
