from __future__ import annotations

from swingtrading.execute.validate import setup_still_valid
from swingtrading.models import Quote, Side


def test_gap_through_stop_invalidates_long(sample_plan) -> None:
    plan = sample_plan
    if plan.side is Side.BUY:
        quote = Quote(symbol=plan.symbol, open=plan.stop_price - 0.5, last=plan.stop_price - 0.5)
        ok, reason = setup_still_valid(plan, quote)
        assert ok is False
        assert "stop" in reason


def test_extension_beyond_skip_invalidates(sample_plan) -> None:
    beyond = sample_plan.skip_if.open_beyond
    if beyond is None:
        return
    if sample_plan.side is Side.BUY:
        quote = Quote(symbol=sample_plan.symbol, open=beyond + 0.25, last=beyond + 0.25)
    else:
        quote = Quote(symbol=sample_plan.symbol, open=beyond - 0.25, last=beyond - 0.25)
    ok, reason = setup_still_valid(sample_plan, quote)
    assert ok is False
    assert "extended" in reason


def test_near_entry_still_valid(sample_plan) -> None:
    quote = Quote(
        symbol=sample_plan.symbol,
        open=sample_plan.entry_price,
        last=sample_plan.entry_price,
    )
    ok, reason = setup_still_valid(sample_plan, quote)
    assert ok is True
    assert "valid" in reason


def test_already_at_target_invalidates_late_start(sample_plan) -> None:
    if sample_plan.side is Side.BUY:
        last = sample_plan.target_price + 0.01
    else:
        last = sample_plan.target_price - 0.01
    quote = Quote(symbol=sample_plan.symbol, open=sample_plan.entry_price, last=last)
    ok, _ = setup_still_valid(sample_plan, quote)
    assert ok is False


def test_missing_quote_does_not_send(sample_plan) -> None:
    ok, reason = setup_still_valid(sample_plan, Quote(symbol=sample_plan.symbol))
    assert ok is False
    assert "opening print" in reason
