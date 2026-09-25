from __future__ import annotations

from datetime import date

from swingtrading.execute.ledger import (
    close_qty,
    is_due,
    is_swing_ref,
    make_order_ref,
    sessions_held,
)
from swingtrading.models import LedgerFill, Side


def test_sessions_held_counts_fill_day() -> None:
    assert sessions_held(date(2024, 6, 24), date(2024, 6, 24)) == 1
    assert sessions_held(date(2024, 6, 24), date(2024, 6, 28)) == 5
    assert sessions_held(date(2024, 6, 28), date(2024, 6, 24)) == 0
    assert sessions_held(date(2024, 6, 28), date(2024, 6, 29)) == 1  # Saturday ignored


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
