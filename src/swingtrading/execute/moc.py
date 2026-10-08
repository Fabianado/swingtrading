from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from swingtrading.brokers.connection import PromptFn, connect_with_prompt, recover_connection
from swingtrading.brokers.ibkr import IBKRRetailBroker
from swingtrading.brokers.protocol import Broker
from swingtrading.config import Settings
from swingtrading.data.rth import format_wait, moc_window_open, past_moc_cutoff, seconds_until_moc
from swingtrading.execute.ledger import (
    close_qty,
    due_fills,
    held_fills,
    load_ledger,
    mark_status,
    retire_flat_fills,
    save_ledger,
    session_date,
    time_stop_lines,
)
from swingtrading.models import LedgerFill, PositionLedger

logger = logging.getLogger(__name__)

NowFn = Callable[[], datetime]


def _default_now(timezone: str) -> datetime:
    return datetime.now(ZoneInfo(timezone))


def run_moc_job(
    settings: Settings,
    *,
    broker: Broker | None = None,
    prompt_fn: PromptFn | None = None,
    now_fn: NowFn | None = None,
    wait: bool = True,
    ignore_window: bool = False,
    ledger: PositionLedger | None = None,
) -> PositionLedger:
    """Flatten due ledger fills with a qty-scoped MOC. Never scans TWS positions."""
    book = ledger if ledger is not None else load_ledger(settings.out_dir)
    clock = now_fn or (lambda: _default_now(settings.rth_timezone))
    today = session_date(clock(), settings.rth_timezone)
    due = due_fills(book, today)
    if not due:
        logger.info("No program fills are due for a time-stop MOC")
        return book

    gateway = broker or IBKRRetailBroker(settings)
    ask = prompt_fn if prompt_fn is not None else input
    if not gateway.is_connected():
        connect_with_prompt(
            gateway,
            ask,
            retry_seconds=settings.tws_retry_seconds,
            host=settings.tws_host,
            port=int(settings.tws_port),
        )
    book = retire_flat_fills(book, gateway)
    save_ledger(book, settings.out_dir)
    due = [
        fill
        for fill in due_fills(book, today)
        if close_qty(fill, gateway.position_qty(fill.symbol)) > 0
    ]
    visible = book.model_copy(update={"fills": held_fills(book, gateway.position_qty)})
    for line in time_stop_lines(visible, today):
        print(line, flush=True)
    if not due:
        logger.info("No open program positions are due for a time-stop MOC")
        return book

    if wait and not ignore_window:
        if not _wait_until_moc(gateway, settings, ask, clock):
            logger.info("MOC window closed; leaving %s due fill(s) for the next session", len(due))
            save_ledger(book, settings.out_dir)
            return book
    elif not ignore_window and past_moc_cutoff(
        clock(),
        timezone=settings.rth_timezone,
        cutoff_hour=int(settings.rth_moc_cutoff_hour),
        cutoff_minute=int(settings.rth_moc_cutoff_minute),
    ):
        logger.info("MOC cutoff has passed; leaving %s due fill(s) for the next session", len(due))
        save_ledger(book, settings.out_dir)
        return book

    for fill in due:
        _ensure(gateway, settings, ask)
        book = _flatten_one(gateway, book, fill)
        save_ledger(book, settings.out_dir)
    return book


def _wait_until_moc(
    broker: Broker,
    settings: Settings,
    prompt_fn: PromptFn,
    now_fn: NowFn,
) -> bool:
    timezone = settings.rth_timezone
    hour = int(settings.rth_moc_hour)
    minute = int(settings.rth_moc_minute)
    cutoff_hour = int(settings.rth_moc_cutoff_hour)
    cutoff_minute = int(settings.rth_moc_cutoff_minute)
    if moc_window_open(
        now_fn(),
        timezone=timezone,
        hour=hour,
        minute=minute,
        cutoff_hour=cutoff_hour,
        cutoff_minute=cutoff_minute,
    ):
        return True
    if past_moc_cutoff(
        now_fn(),
        timezone=timezone,
        cutoff_hour=cutoff_hour,
        cutoff_minute=cutoff_minute,
    ):
        return False
    remaining = seconds_until_moc(now_fn(), timezone=timezone, hour=hour, minute=minute)
    logger.info(
        "Waiting %s until %02d:%02d %s to submit time-stop MOCs",
        format_wait(remaining),
        hour,
        minute,
        timezone,
    )
    while not moc_window_open(
        now_fn(),
        timezone=timezone,
        hour=hour,
        minute=minute,
        cutoff_hour=cutoff_hour,
        cutoff_minute=cutoff_minute,
    ):
        if past_moc_cutoff(
            now_fn(),
            timezone=timezone,
            cutoff_hour=cutoff_hour,
            cutoff_minute=cutoff_minute,
        ):
            return False
        _ensure(broker, settings, prompt_fn)
        remaining = seconds_until_moc(now_fn(), timezone=timezone, hour=hour, minute=minute)
        if remaining <= 0:
            break
        broker.wait(min(float(settings.rth_preopen_poll_seconds), remaining))
    return True


def _flatten_one(broker: Broker, ledger: PositionLedger, fill: LedgerFill) -> PositionLedger:
    exit_kind = broker.tagged_exit_filled(fill)
    if exit_kind == "target":
        logger.info("Skip MOC %s: tagged target already filled", fill.symbol)
        return mark_status(ledger, fill.order_ref, "targeted")
    if exit_kind == "stop":
        logger.info("Skip MOC %s: tagged stop already filled", fill.symbol)
        return mark_status(ledger, fill.order_ref, "stopped")

    held = broker.position_qty(fill.symbol)
    qty = close_qty(fill, held)
    if qty <= 0:
        logger.info(
            "Skip MOC %s: no matching program side/qty (held %s, ledger %s %s)",
            fill.symbol,
            held,
            fill.side.value,
            fill.qty,
        )
        return mark_status(ledger, fill.order_ref, "closed_elsewhere")

    broker.cancel_tagged_exits(fill)
    broker.place_moc(fill.symbol, fill.side, qty, fill.order_ref)
    logger.info(
        "Time-stop MOC %s %s x%s (ledger %s; account still holds %s)",
        fill.symbol,
        "SELL" if fill.side.value == "BUY" else "BUY",
        qty,
        fill.order_ref,
        held,
    )
    return mark_status(ledger, fill.order_ref, "flattened")


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
