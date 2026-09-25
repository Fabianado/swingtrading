from __future__ import annotations

from swingtrading.models import EntryType, Side, SetupType
from swingtrading.plans.tradeplan import build_trade_plan, round_price


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
