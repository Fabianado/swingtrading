from __future__ import annotations

from swingtrading.execute.capital import (
    fallback_margin,
    fits_buying_power,
    sleeve_remaining,
    usable_buying_power,
)
from swingtrading.models import Side


def test_usable_buying_power_applies_reserve(settings) -> None:
    settings.bp_reserve_pct = 0.05
    assert usable_buying_power(10_000, settings) == 9_500
    assert usable_buying_power(float("inf"), settings) == float("inf")


def test_each_side_gets_half_after_the_buffer(settings) -> None:
    settings.bp_reserve_pct = 0.05
    assert sleeve_remaining(10_000, 0, 0, settings) == 4_750
    assert sleeve_remaining(10_000, 3_000, 1_000, settings) == 3_650
    assert sleeve_remaining(float("inf"), 1_000, 1_000, settings) == float("inf")


def test_fits_buying_power() -> None:
    assert fits_buying_power(5_000, 4_999) is True
    assert fits_buying_power(5_000, 5_001) is False
    assert fits_buying_power(float("inf"), 1_000_000) is True
    assert fits_buying_power(5_000, 0) is False


def test_short_margin_uses_setting(settings, sample_plan) -> None:
    settings.short_margin_pct = 0.5
    settings.long_margin_pct = 1.0
    short = sample_plan.model_copy(update={"side": Side.SELL})
    assert fallback_margin(sample_plan, settings) == abs(sample_plan.qty * sample_plan.entry_price)
    assert fallback_margin(short, settings) == 0.5 * abs(short.qty * short.entry_price)
