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


def fits_buying_power(remaining: float, cost: float) -> bool:
    if cost <= 0:
        return False
    if remaining == float("inf"):
        return True
    return remaining + 1e-9 >= cost
