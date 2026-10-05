from __future__ import annotations

from swingtrading.config import Settings
from swingtrading.models import (
    EntryType,
    ScoredSetup,
    SetupType,
    Side,
    SkipIf,
    StopType,
    TradePlan,
)


def invalid_bracket_reason(plan: TradePlan) -> str | None:
    """Why this bracket must not be sent. None when entry, stop, and target are usable."""
    entry = float(plan.entry_price)
    stop = float(plan.stop_price)
    target = float(plan.target_price)
    if entry <= 0 or stop <= 0 or target <= 0:
        return (
            f"target {target:.4f} is not a valid profit-taker "
            f"(entry {entry:.2f}, stop {stop:.2f}); not sending"
        )
    if plan.side is Side.BUY and not (stop < entry < target):
        return (
            f"long prices out of order (stop {stop:.2f}, entry {entry:.2f}, "
            f"target {target:.2f}); not sending"
        )
    if plan.side is Side.SELL and not (target < entry < stop):
        return (
            f"short prices out of order (target {target:.2f}, entry {entry:.2f}, "
            f"stop {stop:.2f}); not sending"
        )
    return None


def round_price(price: float) -> float:
    if price >= 1:
        return round(price + 1e-12, 2)
    return round(price + 1e-12, 4)


def resize_for_risk(plan: TradePlan, risk_usd: float) -> TradePlan | None:
    """Whole-share size so a stop is about risk_usd. None if that is under 1 share."""
    stop_dist = abs(plan.entry_price - plan.stop_price)
    if stop_dist <= 0 or risk_usd <= 0:
        return None
    qty = int(risk_usd // stop_dist)
    if qty < 1:
        return None
    return plan.model_copy(update={"qty": qty, "risk_usd": risk_usd})


def build_trade_plan(setup: ScoredSetup, settings: Settings) -> TradePlan | None:
    if setup.setup_type == SetupType.CONTINUATION:
        plan = _continuation(setup, settings)
    else:
        plan = _mean_reversion(setup, settings)
    if plan is None:
        return None
    risk = abs(plan.entry_price - plan.stop_price)
    if risk <= 0:
        return None
    qty = int(settings.risk_usd // risk)
    if qty < 1:
        return None
    sized = plan.model_copy(
        update={
            "qty": qty,
            "risk_usd": settings.risk_usd,
            "entry_price": round_price(plan.entry_price),
            "stop_price": round_price(plan.stop_price),
            "target_price": round_price(plan.target_price),
            "skip_if": plan.skip_if.model_copy(
                update={
                    "open_beyond": round_price(plan.skip_if.open_beyond)
                    if plan.skip_if.open_beyond is not None
                    else None
                }
            ),
        }
    )
    if invalid_bracket_reason(sized):
        return None
    return sized


def _continuation(setup: ScoredSetup, settings: Settings) -> TradePlan | None:
    feat = setup.features
    atr = feat.atr
    tick = 0.01 if feat.close >= 1 else 0.0001
    if setup.side is Side.BUY:
        entry = max(feat.prior_high, feat.close + settings.continuation_buffer_atr * atr)
        entry = max(entry, feat.close + tick)
        stop = entry - settings.stop_atr * atr
        target = entry + settings.target_r * (entry - stop)
        skip_beyond = entry + settings.skip_gap_atr * atr
        skip = SkipIf(
            open_beyond=skip_beyond,
            flatten_if_open_through_stop=True,
            description=(
                f"Skip if the next regular-session open is above {round_price(skip_beyond):.2f} "
                f"(already extended) or at/below the stop {round_price(stop):.2f}."
            ),
        )
        entry_type = EntryType.STP
    else:
        entry = min(feat.prior_low, feat.close - settings.continuation_buffer_atr * atr)
        entry = min(entry, feat.close - tick)
        stop = entry + settings.stop_atr * atr
        target = entry - settings.target_r * (stop - entry)
        skip_beyond = entry - settings.skip_gap_atr * atr
        skip = SkipIf(
            open_beyond=skip_beyond,
            flatten_if_open_through_stop=True,
            description=(
                f"Skip if the next regular-session open is below {round_price(skip_beyond):.2f} "
                f"(already extended) or at/above the stop {round_price(stop):.2f}."
            ),
        )
        entry_type = EntryType.STP
    return _base(setup, settings, entry_type, entry, stop, target, skip)


def _mean_reversion(setup: ScoredSetup, settings: Settings) -> TradePlan | None:
    feat = setup.features
    atr = feat.atr
    if setup.side is Side.BUY:
        entry = feat.close - settings.mean_reversion_limit_atr * atr
        stop = entry - settings.stop_atr * atr
        target = entry + settings.target_r * (entry - stop)
        skip_beyond = stop
        skip = SkipIf(
            open_beyond=skip_beyond,
            flatten_if_open_through_stop=True,
            description=(
                f"Skip if the next regular-session open gaps through the stop "
                f"at {round_price(stop):.2f}; do not chase a limit fill below the stop."
            ),
        )
    else:
        entry = feat.close + settings.mean_reversion_limit_atr * atr
        stop = entry + settings.stop_atr * atr
        target = entry - settings.target_r * (stop - entry)
        skip_beyond = stop
        skip = SkipIf(
            open_beyond=skip_beyond,
            flatten_if_open_through_stop=True,
            description=(
                f"Skip if the next regular-session open gaps through the stop "
                f"at {round_price(stop):.2f}; do not chase a limit fill above the stop."
            ),
        )
    return _base(setup, settings, EntryType.LMT, entry, stop, target, skip)


def _base(
    setup: ScoredSetup,
    settings: Settings,
    entry_type: EntryType,
    entry: float,
    stop: float,
    target: float,
    skip: SkipIf,
) -> TradePlan:
    feat = setup.features
    return TradePlan(
        symbol=feat.symbol,
        side=setup.side,
        setup_type=setup.setup_type,
        entry_type=entry_type,
        entry_price=entry,
        stop_type=StopType.STP,
        stop_price=stop,
        target_price=target,
        time_stop_sessions=settings.time_stop_sessions,
        skip_if=skip,
        qty=0,
        risk_usd=settings.risk_usd,
        confidence=setup.quant_score,
        quant_score=setup.quant_score,
        rationale=setup.rationale,
        sector=feat.sector,
        as_of=feat.as_of,
        name=feat.name,
    )
