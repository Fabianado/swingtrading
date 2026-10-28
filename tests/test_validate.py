from __future__ import annotations

from swingtrading.execute.validate import setup_still_valid
from swingtrading.models import Quote, SetupType, Side


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


def _plan(sample_plan, *, side, setup, entry, stop, target, beyond):
    return sample_plan.model_copy(
        update={
            "side": side,
            "setup_type": setup,
            "entry_price": entry,
            "stop_price": stop,
            "target_price": target,
            "skip_if": sample_plan.skip_if.model_copy(update={"open_beyond": beyond}),
        }
    )


def test_gap_check_for_each_side_and_setup(sample_plan) -> None:
    cases = [
        (
            "continuation long extended",
            _plan(
                sample_plan,
                side=Side.BUY,
                setup=SetupType.CONTINUATION,
                entry=100,
                stop=90,
                target=120,
                beyond=105,
            ),
            106,
            False,
        ),
        (
            "continuation long between entry and skip",
            _plan(
                sample_plan,
                side=Side.BUY,
                setup=SetupType.CONTINUATION,
                entry=100,
                stop=90,
                target=120,
                beyond=105,
            ),
            102,
            True,
        ),
        (
            "continuation long through the stop",
            _plan(
                sample_plan,
                side=Side.BUY,
                setup=SetupType.CONTINUATION,
                entry=100,
                stop=90,
                target=120,
                beyond=105,
            ),
            90,
            False,
        ),
        (
            "continuation short extended",
            _plan(
                sample_plan,
                side=Side.SELL,
                setup=SetupType.CONTINUATION,
                entry=100,
                stop=110,
                target=80,
                beyond=95,
            ),
            94,
            False,
        ),
        (
            "continuation short between entry and skip",
            _plan(
                sample_plan,
                side=Side.SELL,
                setup=SetupType.CONTINUATION,
                entry=100,
                stop=110,
                target=80,
                beyond=95,
            ),
            98,
            True,
        ),
        (
            "continuation short through the stop",
            _plan(
                sample_plan,
                side=Side.SELL,
                setup=SetupType.CONTINUATION,
                entry=100,
                stop=110,
                target=80,
                beyond=95,
            ),
            110,
            False,
        ),
        (
            "mean-reversion long open above the stop",
            _plan(
                sample_plan,
                side=Side.BUY,
                setup=SetupType.MEAN_REVERSION,
                entry=1229.69,
                stop=1108.85,
                target=1471.37,
                beyond=1108.85,
            ),
            1269.57,
            True,
        ),
        (
            "mean-reversion long between the limit and the stop",
            _plan(
                sample_plan,
                side=Side.BUY,
                setup=SetupType.MEAN_REVERSION,
                entry=1229.69,
                stop=1108.85,
                target=1471.37,
                beyond=1108.85,
            ),
            1150,
            True,
        ),
        (
            "mean-reversion long through the stop",
            _plan(
                sample_plan,
                side=Side.BUY,
                setup=SetupType.MEAN_REVERSION,
                entry=1229.69,
                stop=1108.85,
                target=1471.37,
                beyond=1108.85,
            ),
            1108.85,
            False,
        ),
        (
            "mean-reversion short open below the stop",
            _plan(
                sample_plan,
                side=Side.SELL,
                setup=SetupType.MEAN_REVERSION,
                entry=100,
                stop=110,
                target=80,
                beyond=110,
            ),
            98,
            True,
        ),
        (
            "mean-reversion short between the limit and the stop",
            _plan(
                sample_plan,
                side=Side.SELL,
                setup=SetupType.MEAN_REVERSION,
                entry=100,
                stop=110,
                target=80,
                beyond=110,
            ),
            105,
            True,
        ),
        (
            "mean-reversion short through the stop",
            _plan(
                sample_plan,
                side=Side.SELL,
                setup=SetupType.MEAN_REVERSION,
                entry=100,
                stop=110,
                target=80,
                beyond=110,
            ),
            110,
            False,
        ),
    ]
    for name, plan, price, expect_ok in cases:
        quote = Quote(symbol=plan.symbol, open=price, last=price)
        ok, reason = setup_still_valid(plan, quote)
        assert ok is expect_ok, f"{name}: {reason}"


def test_missing_quote_does_not_send(sample_plan) -> None:
    ok, reason = setup_still_valid(sample_plan, Quote(symbol=sample_plan.symbol))
    assert ok is False
    assert "opening print" in reason
