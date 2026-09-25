from __future__ import annotations

from swingtrading.models import Quote, Side, TradePlan


def _finite(price: float | None) -> float | None:
    if price is None:
        return None
    value = float(price)
    if value != value:  # NaN
        return None
    return value


def reference_prices(quote: Quote) -> tuple[float | None, float | None]:
    """(gap_price, last_price). Gap uses the session open when known."""
    last = (
        _finite(quote.last)
        or _finite(quote.ask)
        or _finite(quote.bid)
        or _finite(quote.open)
    )
    gap = _finite(quote.open) or last
    return gap, last


def has_gap_quote(quote: Quote) -> bool:
    """True when the quote has an open or last we can use for the gap check."""
    gap, last = reference_prices(quote)
    return gap is not None or last is not None


def setup_still_valid(plan: TradePlan, quote: Quote) -> tuple[bool, str]:
    """Return whether a saved overnight plan can still be worked now.

    Uses the session open for gap/invalidation rules and last/bid/ask when
    the job is started after the open. Does not send without a print.
    """
    gap, last = reference_prices(quote)
    if gap is None and last is None:
        return False, "no opening print; not sending"

    px = gap if gap is not None else last
    assert px is not None

    if plan.skip_if.flatten_if_open_through_stop:
        if plan.side is Side.BUY and px <= plan.stop_price:
            return False, f"price {px:.2f} is at/through the stop {plan.stop_price:.2f}"
        if plan.side is Side.SELL and px >= plan.stop_price:
            return False, f"price {px:.2f} is at/through the stop {plan.stop_price:.2f}"

    beyond = plan.skip_if.open_beyond
    if beyond is not None:
        if plan.side is Side.BUY and px >= beyond:
            return False, f"open/last {px:.2f} already extended through {beyond:.2f}"
        if plan.side is Side.SELL and px <= beyond:
            return False, f"open/last {px:.2f} already extended through {beyond:.2f}"

    if last is not None:
        if plan.side is Side.BUY and last >= plan.target_price:
            return False, f"last {last:.2f} already at/through the target {plan.target_price:.2f}"
        if plan.side is Side.SELL and last <= plan.target_price:
            return False, f"last {last:.2f} already at/through the target {plan.target_price:.2f}"

    return True, "setup still valid"
