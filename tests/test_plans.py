from __future__ import annotations

from swingtrading.models import EntryType, ScoredSetup, Side, SetupType
from swingtrading.plans.tradeplan import build_trade_plan, invalid_bracket_reason, round_price


def test_round_price() -> None:
    assert round_price(12.345) == 12.35
    assert round_price(0.12345) == 0.1235


def test_continuation_long_bracket(sample_setup, settings) -> None:
    plan = build_trade_plan(sample_setup, settings)
    assert plan is not None
    if plan.side is Side.BUY and plan.setup_type is SetupType.CONTINUATION:
        assert plan.entry_type is EntryType.STP
        assert plan.stop_price < plan.entry_price
        assert plan.target_price > plan.entry_price
        assert plan.qty >= 1
        assert plan.tif == "DAY"
        assert plan.sec_type == "STK"
        assert plan.exchange == "SMART"
        assert plan.time_stop_sessions == 5
        assert plan.skip_if.open_beyond is not None
        risk = plan.entry_price - plan.stop_price
        reward = plan.target_price - plan.entry_price
        assert abs(reward / risk - settings.target_r) < 0.05
    else:
        assert plan.qty >= 1
        assert abs(plan.entry_price - plan.stop_price) > 0


def test_spinoff_short_with_negative_target_is_not_a_plan(sample_setup, settings) -> None:
    feat = sample_setup.features.model_copy(
        update={"close": 11.58, "prior_low": 11.50, "prior_high": 12.0, "atr": 7.0}
    )
    setup = ScoredSetup(
        features=feat,
        side=Side.SELL,
        setup_type=SetupType.CONTINUATION,
        quant_score=90.0,
        rationale="spin-off gap",
    )
    assert build_trade_plan(setup, settings) is None
    draft = build_trade_plan(sample_setup, settings)
    assert draft is not None
    broken = draft.model_copy(
        update={"side": Side.SELL, "entry_price": 11.58, "stop_price": 20.02, "target_price": -5.2898}
    )
    reason = invalid_bracket_reason(broken)
    assert reason is not None
    assert "profit-taker" in reason
