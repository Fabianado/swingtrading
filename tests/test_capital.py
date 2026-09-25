from __future__ import annotations

from swingtrading.execute.capital import fallback_margin, fits_buying_power, usable_buying_power
from swingtrading.models import Side


def test_usable_buying_power_applies_reserve(settings) -> None:
    settings.bp_reserve_pct = 0.05
    assert usable_buying_power(10_000, settings) == 9_500
    assert usable_buying_power(float("inf"), settings) == float("inf")


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
