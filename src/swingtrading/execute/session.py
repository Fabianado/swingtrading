from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from swingtrading.brokers.connection import PromptFn, connect_with_prompt, recover_connection
from swingtrading.brokers.ibkr import IBKRRetailBroker
from swingtrading.brokers.protocol import Broker
from swingtrading.config import Settings
from swingtrading.data.rth import format_wait, rth_has_opened, seconds_until_rth_open
from swingtrading.execute.capital import fits_buying_power, usable_buying_power
from swingtrading.execute.ledger import (
    due_fills,
    fill_from_plan,
    held_fills,
    load_ledger,
    retire_flat_fills,
    save_ledger,
    session_date,
    time_stop_lines,
    upsert_fill,
)
from swingtrading.execute.moc import run_moc_job
from swingtrading.execute.store import (
    load_playbook,
    match_plan,
    playbook_risk_usd,
    save_execution,
)
from swingtrading.execute.validate import has_gap_quote, setup_still_valid
from swingtrading.models import (
    BracketHandle,
    ExecutionDecision,
    ExecutionState,
    Playbook,
    Quote,
    TradePlan,
)
from swingtrading.plans.tradeplan import invalid_bracket_reason, resize_for_risk

logger = logging.getLogger(__name__)

NowFn = Callable[[], datetime]
QuoteFallback = Callable[[str], Quote | None]


def _default_now(timezone: str) -> datetime:
    return datetime.now(ZoneInfo(timezone))


def _say(message: str) -> None:
    logger.info("%s", message)
    print(message, flush=True)


def execute_tickers(
    settings: Settings,
    tickers: list[str],
    *,
    risk_usd: float | None = None,
    max_new_positions: int | None = None,
    as_of: date | None = None,
    broker: Broker | None = None,
    prompt_fn: PromptFn | None = None,
    playbook: Playbook | None = None,
    monitor: bool = True,
    wait_for_open: bool = True,
    wait_for_moc: bool = True,
    now_fn: NowFn | None = None,
    fallback_quote: QuoteFallback | None = None,
) -> ExecutionState:
    """Automate saved shortlist plans for the given tickers via TWS.

    By default the job waits for the NYSE 9:30 ET cash open, checks each
    name against the opening print, then transmits only setups that survive.
    """
    book = playbook or load_playbook(settings.out_dir, as_of)
    used_risk = float(risk_usd) if risk_usd is not None else playbook_risk_usd(book, settings.risk_usd)
    symbols = [t.strip().upper() for t in tickers if t.strip()]
    if not symbols:
        raise ValueError("Provide at least one shortlist ticker to execute")

    plans: list[TradePlan] = []
    for ticker in symbols:
        plan = match_plan(book, ticker)
        resized = resize_for_risk(plan, used_risk)
        if resized is None:
            raise ValueError(
                f"{plan.symbol}: risk ${used_risk:.0f} is smaller than the stop distance "
                f"{abs(plan.entry_price - plan.stop_price):.2f}"
            )
        plans.append(resized)
    plans.sort(key=lambda p: p.confidence, reverse=True)

    cap = max_new_positions if max_new_positions is not None else len(plans)
    if cap < 1:
        raise ValueError("--max-new-positions must be at least 1")

    state = ExecutionState(
        as_of=book.as_of,
        risk_usd=used_risk,
        max_new_positions=cap,
        tickers=[p.symbol for p in plans],
    )
    gateway = broker or IBKRRetailBroker(settings)
    ask = prompt_fn if prompt_fn is not None else input
    clock = now_fn or (lambda: _default_now(settings.rth_timezone))
    _say(
        f"Connecting to TWS at {settings.tws_host}:{settings.tws_port} "
        f"(clientId {settings.tws_client_id}, {settings.tws_connect_timeout:.0f}s handshake) …"
    )
    connect_with_prompt(
        gateway,
        ask,
        retry_seconds=settings.tws_retry_seconds,
        host=settings.tws_host,
        port=int(settings.tws_port),
    )
    _say("TWS connected")
    today = session_date(clock(), settings.rth_timezone)
    book = retire_flat_fills(load_ledger(settings.out_dir), gateway)
    save_ledger(book, settings.out_dir)
    visible = book.model_copy(update={"fills": held_fills(book, gateway.position_qty)})
    for line in time_stop_lines(visible, today):
        print(line, flush=True)

    if wait_for_open:
        _wait_until_rth(gateway, settings, ask, clock)

    handles: dict[str, BracketHandle] = {}
    plans_by_symbol = {plan.symbol: plan for plan in plans}
    remaining_bp = usable_buying_power(gateway.buying_power(), settings)
    _say(f"Usable buying power ${remaining_bp:.0f} (after reserve)")
    placed = 0
    session_open = rth_has_opened(
        clock(),
        timezone=settings.rth_timezone,
        hour=int(settings.rth_open_hour),
        minute=int(settings.rth_open_minute),
    )
    for plan in plans:
        _ensure(gateway, settings, ask)
        _say(f"Checking {plan.symbol} …")
        quote = _quote_for_gap(
            gateway,
            plan.symbol,
            settings,
            ask,
            clock,
            wait=wait_for_open and not session_open,
        )
        if not has_gap_quote(quote) and fallback_quote is not None:
            fallback = fallback_quote(plan.symbol)
            if fallback is not None and has_gap_quote(fallback):
                _say(f"{plan.symbol}: no TWS tape; using Yahoo open/last")
                quote = fallback
        decision, remaining_bp, committed = _prepare_one(
            gateway,
            plan,
            handles,
            quote,
            remaining_bp=remaining_bp,
            slots_left=cap - placed,
        )
        state.decisions.append(decision)
        extra = f" {decision.qty} sh" if decision.qty else ""
        _say(f"- {decision.symbol} {decision.action}{extra}: {decision.reason}")
        if decision.action == "invalidated":
            state.invalidated.append(plan.symbol)
        elif decision.action == "skipped":
            state.skipped.append(plan.symbol)
        if committed:
            placed += 1
        save_execution(state, settings.out_dir)

    _say(
        f"Done placing: opened {len(state.opened)}, "
        f"invalidated {len(state.invalidated)}, skipped {len(state.skipped)}, "
        f"working {len(handles)}"
    )
    if monitor and handles:
        _say("Watching working entries for fills (leave TWS running; Ctrl-C keeps them working)")
        _monitor(gateway, settings, ask, state, handles, plans_by_symbol, clock)

    state.brackets = list(handles.values())
    save_execution(state, settings.out_dir)

    book = load_ledger(settings.out_dir)
    if wait_for_moc and due_fills(book, session_date(clock(), settings.rth_timezone)):
        run_moc_job(
            settings,
            broker=gateway,
            prompt_fn=ask,
            now_fn=clock,
            wait=True,
            ignore_window=False,
            ledger=book,
        )
    return state


def _wait_until_rth(
    broker: Broker,
    settings: Settings,
    prompt_fn: PromptFn,
    now_fn: NowFn,
) -> None:
    timezone = settings.rth_timezone
    hour = int(settings.rth_open_hour)
    minute = int(settings.rth_open_minute)
    if rth_has_opened(now_fn(), timezone=timezone, hour=hour, minute=minute):
        return
    remaining = seconds_until_rth_open(now_fn(), timezone=timezone, hour=hour, minute=minute)
    logger.info(
        "NYSE cash open is %02d:%02d %s; waiting %s before gap check",
        hour,
        minute,
        timezone,
        format_wait(remaining),
    )
    while not rth_has_opened(now_fn(), timezone=timezone, hour=hour, minute=minute):
        _ensure(broker, settings, prompt_fn)
        remaining = seconds_until_rth_open(now_fn(), timezone=timezone, hour=hour, minute=minute)
        if remaining <= 0:
            break
        broker.wait(min(float(settings.rth_preopen_poll_seconds), remaining))
    logger.info("NYSE cash session is open; checking opening prints")


def _quote_for_gap(
    broker: Broker,
    symbol: str,
    settings: Settings,
    prompt_fn: PromptFn,
    now_fn: NowFn,
    *,
    wait: bool,
) -> Quote:
    quote = broker.quote(symbol)
    if has_gap_quote(quote) or not wait:
        return quote
    timeout = float(settings.rth_quote_timeout_seconds)
    waited = 0.0
    deadline = now_fn() + timedelta(seconds=timeout)
    while waited < timeout and now_fn() < deadline:
        _ensure(broker, settings, prompt_fn)
        quote = broker.quote(symbol)
        if has_gap_quote(quote):
            return quote
        step = float(settings.rth_poll_seconds)
        broker.wait(step)
        waited += step
    return quote


def _prepare_one(
    broker: Broker,
    plan: TradePlan,
    handles: dict[str, BracketHandle],
    quote: Quote,
    *,
    remaining_bp: float,
    slots_left: int,
) -> tuple[ExecutionDecision, float, bool]:
    broken = invalid_bracket_reason(plan)
    if broken:
        logger.info("Skip %s: %s", plan.symbol, broken)
        return (
            ExecutionDecision(
                symbol=plan.symbol,
                action="invalidated",
                reason=broken,
                qty=plan.qty,
                risk_usd=plan.risk_usd,
            ),
            remaining_bp,
            False,
        )
    held = broker.position_qty(plan.symbol)
    if held != 0:
        return (
            ExecutionDecision(
                symbol=plan.symbol,
                action="invalidated",
                reason=f"already have a position ({held} shares); will not double-enter",
                qty=plan.qty,
                risk_usd=plan.risk_usd,
            ),
            remaining_bp,
            False,
        )
    ok, reason = setup_still_valid(plan, quote)
    if not ok:
        logger.info("Skip %s: %s", plan.symbol, reason)
        return (
            ExecutionDecision(
                symbol=plan.symbol,
                action="invalidated",
                reason=reason,
                qty=plan.qty,
                risk_usd=plan.risk_usd,
            ),
            remaining_bp,
            False,
        )
    existing = broker.existing_bracket(plan)
    logger.info("Estimating margin for %s", plan.symbol)
    cost = broker.order_margin(plan)
    if existing is not None:
        handles[plan.symbol] = existing
        next_bp = remaining_bp - cost if remaining_bp != float("inf") else remaining_bp
        return (
            ExecutionDecision(
                symbol=plan.symbol,
                action="reused",
                reason=f"working TWS order {existing.parent_id} already exists",
                qty=plan.qty,
                risk_usd=plan.risk_usd,
            ),
            next_bp,
            True,
        )
    if slots_left <= 0:
        logger.info("Skip %s: position cap already allocated to higher-confidence names", plan.symbol)
        return (
            ExecutionDecision(
                symbol=plan.symbol,
                action="skipped",
                reason="not transmitted; higher-confidence names already filled the position cap",
                qty=plan.qty,
                risk_usd=plan.risk_usd,
            ),
            remaining_bp,
            False,
        )
    if not fits_buying_power(remaining_bp, cost):
        logger.info(
            "Skip %s: needs ~$%.0f, $%.0f buying power left; trying next name",
            plan.symbol,
            cost,
            remaining_bp,
        )
        return (
            ExecutionDecision(
                symbol=plan.symbol,
                action="skipped",
                reason=(
                    f"not transmitted; needs ~${cost:.0f} vs ${remaining_bp:.0f} buying power left"
                ),
                qty=plan.qty,
                risk_usd=plan.risk_usd,
            ),
            remaining_bp,
            False,
        )
    handle = broker.place_bracket(plan)
    handles[plan.symbol] = handle
    next_bp = remaining_bp - cost if remaining_bp != float("inf") else remaining_bp
    logger.info(
        "Placed %s %s %s x%s @ %s (parent %s, ~$%.0f margin, $%.0f left)",
        plan.symbol,
        plan.side.value,
        plan.entry_type.value,
        plan.qty,
        plan.entry_price,
        handle.parent_id,
        cost,
        next_bp,
    )
    return (
        ExecutionDecision(
            symbol=plan.symbol,
            action="placed",
            reason=(
                f"{plan.entry_type.value} {plan.side.value} {plan.qty} @ {plan.entry_price:.2f} "
                f"stop {plan.stop_price:.2f} target {plan.target_price:.2f}"
            ),
            qty=plan.qty,
            risk_usd=plan.risk_usd,
        ),
        next_bp,
        True,
    )


def _monitor(
    broker: Broker,
    settings: Settings,
    prompt_fn: PromptFn,
    state: ExecutionState,
    handles: dict[str, BracketHandle],
    plans_by_symbol: dict[str, TradePlan],
    now_fn: NowFn,
) -> None:
    if not handles:
        return
    while True:
        _ensure(broker, settings, prompt_fn)
        opened: list[str] = []
        working: list[str] = []
        for symbol, handle in list(handles.items()):
            _ensure(broker, settings, prompt_fn)
            updated = broker.refresh_bracket(handle)
            handles[symbol] = updated
            if updated.status == "filled":
                if symbol not in opened:
                    opened.append(symbol)
                if symbol not in state.opened:
                    plan = plans_by_symbol.get(symbol)
                    if plan is not None:
                        _record_fill(settings, plan, updated, now_fn)
            elif updated.status == "working":
                working.append(symbol)
        state.opened = list(dict.fromkeys([*state.opened, *opened]))
        if len(state.opened) >= state.max_new_positions and working:
            logger.info(
                "Max new positions (%s) reached; canceling remaining entries %s",
                state.max_new_positions,
                ", ".join(working),
            )
            for symbol in working:
                _ensure(broker, settings, prompt_fn)
                broker.cancel_bracket(handles[symbol])
                handles[symbol] = handles[symbol].model_copy(update={"status": "cancelled"})
                if symbol not in state.canceled:
                    state.canceled.append(symbol)
                state.decisions.append(
                    ExecutionDecision(
                        symbol=symbol,
                        action="canceled",
                        reason=f"canceled after {state.max_new_positions} new position(s) opened",
                    )
                )
            save_execution(state, settings.out_dir)
            return
        if not working:
            save_execution(state, settings.out_dir)
            return
        save_execution(state, settings.out_dir)
        broker.wait(1.0)


def _record_fill(
    settings: Settings,
    plan: TradePlan,
    handle: BracketHandle,
    now_fn: NowFn,
) -> None:
    fill_date = session_date(now_fn(), settings.rth_timezone)
    book = upsert_fill(load_ledger(settings.out_dir), fill_from_plan(plan, handle, fill_date))
    save_ledger(book, settings.out_dir)
    logger.info(
        "Recorded program fill %s %s x%s on %s (%s)",
        plan.symbol,
        plan.side.value,
        plan.qty,
        fill_date.isoformat(),
        handle.order_ref or "swing",
    )


def _ensure(broker: Broker, settings: Settings, prompt_fn: PromptFn) -> None:
    if broker.is_connected():
        return
    recover_connection(broker, retry_seconds=settings.tws_retry_seconds)
    if not broker.is_connected():
        connect_with_prompt(
            broker,
            prompt_fn,
            retry_seconds=settings.tws_retry_seconds,
            host=settings.tws_host,
            port=int(settings.tws_port),
        )
