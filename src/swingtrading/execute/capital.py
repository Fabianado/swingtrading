from __future__ import annotations

from swingtrading.config import Settings
from swingtrading.models import Side, TradePlan


def notional(plan: TradePlan) -> float:
    return abs(float(plan.qty) * float(plan.entry_price))


def fallback_margin(plan: TradePlan, settings: Settings) -> float:
    """Conservative cash/margin estimate when IBKR what-if is unavailable."""
    value = notional(plan)
    if plan.side is Side.SELL:
        return value * float(settings.short_margin_pct)
    return value * float(settings.long_margin_pct)


def usable_buying_power(raw: float, settings: Settings) -> float:
    reserve = min(0.95, max(0.0, float(settings.bp_reserve_pct)))
    if raw == float("inf"):
        return raw
    return max(0.0, raw * (1.0 - reserve))


def sleeve_remaining(available: float, used_side: float, used_other: float, settings: Settings) -> float:
    """Buying power left for one side.

    Each side gets half of buying power after the reserve. Margin already used by
    playbook positions on that side is subtracted. AvailableFunds is what is left
    after those positions, so their margin is added back before the split.
    """
    if available == float("inf"):
        return available
    gross = max(0.0, available) + max(0.0, used_side) + max(0.0, used_other)
    half = usable_buying_power(gross, settings) / 2.0
    return max(0.0, half - max(0.0, used_side))


def fits_buying_power(remaining: float, cost: float) -> bool:
    if cost <= 0:
        return False
    if remaining == float("inf"):
        return True
    return remaining + 1e-9 >= cost
