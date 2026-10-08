from __future__ import annotations

from datetime import date

from swingtrading.execute.ledger import (
    close_qty,
    held_fills,
    is_due,
    is_swing_ref,
    make_order_ref,
    retire_flat_fills,
    sessions_held,
    sessions_remaining,
    time_stop_lines,
)
from swingtrading.models import LedgerFill, PositionLedger, Side


def test_sessions_held_counts_fill_day() -> None:
    assert sessions_held(date(2024, 6, 24), date(2024, 6, 24)) == 1
    assert sessions_held(date(2024, 6, 24), date(2024, 6, 28)) == 5
    assert sessions_held(date(2024, 6, 28), date(2024, 6, 24)) == 0
    assert sessions_held(date(2024, 6, 28), date(2024, 6, 29)) == 1  # Saturday ignored


def test_time_stop_lines_count_sessions_left() -> None:
    fill = LedgerFill(
        symbol="CTVA",
        side=Side.SELL,
        qty=59,
        fill_date=date(2024, 6, 24),
        time_stop_sessions=5,
        order_ref=make_order_ref(date(2024, 6, 21), "CTVA"),
    )
    assert sessions_remaining(fill, date(2024, 6, 24)) == 5
    assert sessions_remaining(fill, date(2024, 6, 26)) == 3
    assert sessions_remaining(fill, date(2024, 6, 28)) == 1
    assert sessions_remaining(fill, date(2024, 6, 29)) == 1
    lines = time_stop_lines(PositionLedger(fills=[fill]), date(2024, 6, 26))
    assert lines == [
        "CTVA short 59 sh: session 3 of 5, 3 trading sessions left, including today "
        "if neither target nor stop is hit"
    ]
    assert time_stop_lines(PositionLedger(), date(2024, 6, 26)) == [
        "No open program positions. Nothing is on a time stop."
    ]


class _FlatBook:
    def __init__(self, qty: int = 0, working: bool = False) -> None:
        self.qty = qty
        self.working = working

    def position_qty(self, _symbol: str) -> int:
        return self.qty

    def has_working_exit(self, _fill: LedgerFill) -> bool:
        return self.working


def test_flat_fill_without_exits_is_retired() -> None:
    fill = LedgerFill(
        symbol="MCO",
        side=Side.SELL,
        qty=37,
        fill_date=date(2024, 6, 24),
        time_stop_sessions=5,
        order_ref=make_order_ref(date(2024, 6, 21), "MCO"),
    )
    book = PositionLedger(fills=[fill])
    retired = retire_flat_fills(book, _FlatBook())
    assert retired.fills[0].status == "closed_elsewhere"
    assert held_fills(retired, lambda _symbol: 0) == []
    assert time_stop_lines(PositionLedger(fills=held_fills(retired, lambda _symbol: 0)), date(2024, 6, 27)) == [
        "No open program positions. Nothing is on a time stop."
    ]
    kept = retire_flat_fills(book, _FlatBook(working=True))
    assert kept.fills[0].status == "open"
    still_short = retire_flat_fills(book, _FlatBook(qty=-37))
    assert still_short.fills[0].status == "open"


def test_is_due_on_session_five() -> None:
    fill = LedgerFill(
        symbol="MCD",
        side=Side.SELL,
        qty=24,
        fill_date=date(2024, 6, 24),
        time_stop_sessions=5,
        order_ref=make_order_ref(date(2024, 6, 21), "MCD"),
    )
    assert is_due(fill, date(2024, 6, 27)) is False
    assert is_due(fill, date(2024, 6, 28)) is True


def test_close_qty_never_uses_whole_net() -> None:
    long_fill = LedgerFill(
        symbol="MCD",
        side=Side.BUY,
        qty=24,
        fill_date=date(2024, 6, 24),
        order_ref="swing:2024-06-21:MCD",
    )
    assert close_qty(long_fill, 124) == 24
    assert close_qty(long_fill, 10) == 10
    assert close_qty(long_fill, 0) == 0
    assert close_qty(long_fill, -50) == 0

    short_fill = long_fill.model_copy(update={"side": Side.SELL})
    assert close_qty(short_fill, -124) == 24
    assert close_qty(short_fill, 80) == 0


def test_order_ref_tag() -> None:
    ref = make_order_ref(date(2024, 6, 21), "mcd")
    assert ref == "swing:2024-06-21:MCD"
    assert is_swing_ref(ref) is True
    assert is_swing_ref("manual-order") is False
