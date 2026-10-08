from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from swingtrading.execute.ledger import make_order_ref, save_ledger
from swingtrading.execute.moc import run_moc_job
from swingtrading.models import LedgerFill, PositionLedger, Side

ET = ZoneInfo("America/New_York")
MOC_TIME = datetime(2024, 6, 28, 15, 45, tzinfo=ET)
BEFORE_MOC = datetime(2024, 6, 28, 10, 0, tzinfo=ET)


class FakeMocBroker:
    def __init__(self) -> None:
        self.connected = True
        self.positions: dict[str, int] = {}
        self.exit_filled: dict[str, str] = {}
        self.canceled_refs: list[str] = []
        self.moc: list[tuple[str, str, int, str]] = []
        self.wait_count = 0

    def connect(self) -> None:
        self.connected = True

    def reconnect(self) -> None:
        self.connected = True

    def is_connected(self) -> bool:
        return self.connected

    def position_qty(self, symbol: str) -> int:
        return int(self.positions.get(symbol, 0))

    def wait(self, seconds: float) -> None:
        self.wait_count += 1

    def cancel_tagged_exits(self, fill: LedgerFill) -> None:
        self.canceled_refs.append(fill.order_ref)

    def place_moc(self, symbol: str, side: Side, qty: int, order_ref: str) -> None:
        self.moc.append((symbol, side.value, qty, order_ref))
        if side is Side.BUY:
            self.positions[symbol] = self.positions.get(symbol, 0) - qty
        else:
            self.positions[symbol] = self.positions.get(symbol, 0) + qty

    def tagged_exit_filled(self, fill: LedgerFill) -> str | None:
        return self.exit_filled.get(fill.symbol)

    def has_working_exit(self, fill: LedgerFill) -> bool:
        return False


def _due_fill(symbol: str = "MCD", side: Side = Side.SELL, qty: int = 24) -> LedgerFill:
    return LedgerFill(
        symbol=symbol,
        side=side,
        qty=qty,
        fill_date=date(2024, 6, 24),
        time_stop_sessions=5,
        parent_id=10,
        take_id=11,
        stop_id=12,
        order_ref=make_order_ref(date(2024, 6, 21), symbol),
    )


def test_moc_only_closes_ledger_qty(settings, tmp_path: Path) -> None:
    settings.out_dir = tmp_path
    fill = _due_fill()
    save_ledger(PositionLedger(fills=[fill]), tmp_path)
    broker = FakeMocBroker()
    broker.positions["MCD"] = -124  # 24 program short + 100 manual short
    broker.positions["AAPL"] = 500  # manual, not in ledger

    book = run_moc_job(
        settings,
        broker=broker,
        prompt_fn=lambda _m: None,
        now_fn=lambda: MOC_TIME,
        wait=False,
        ignore_window=True,
    )
    assert broker.moc == [("MCD", "SELL", 24, fill.order_ref)]
    assert broker.canceled_refs == [fill.order_ref]
    assert broker.positions["MCD"] == -100
    assert broker.positions["AAPL"] == 500
    assert book.fills[0].status == "flattened"


def test_moc_skips_manual_only_symbol(settings, tmp_path: Path) -> None:
    settings.out_dir = tmp_path
    save_ledger(PositionLedger(fills=[]), tmp_path)
    broker = FakeMocBroker()
    broker.positions["IBM"] = 80
    book = run_moc_job(
        settings,
        broker=broker,
        prompt_fn=lambda _m: None,
        now_fn=lambda: MOC_TIME,
        wait=False,
        ignore_window=True,
    )
    assert broker.moc == []
    assert book.fills == []


def test_moc_skips_when_tagged_stop_filled(settings, tmp_path: Path) -> None:
    settings.out_dir = tmp_path
    fill = _due_fill()
    save_ledger(PositionLedger(fills=[fill]), tmp_path)
    broker = FakeMocBroker()
    broker.positions["MCD"] = -24
    broker.exit_filled["MCD"] = "stop"
    book = run_moc_job(
        settings,
        broker=broker,
        prompt_fn=lambda _m: None,
        now_fn=lambda: MOC_TIME,
        wait=False,
        ignore_window=True,
    )
    assert broker.moc == []
    assert book.fills[0].status == "stopped"


def test_moc_skips_when_position_already_flat(settings, tmp_path: Path) -> None:
    settings.out_dir = tmp_path
    fill = _due_fill(side=Side.BUY, qty=24)
    save_ledger(PositionLedger(fills=[fill]), tmp_path)
    broker = FakeMocBroker()
    broker.positions["MCD"] = 0
    book = run_moc_job(
        settings,
        broker=broker,
        prompt_fn=lambda _m: None,
        now_fn=lambda: MOC_TIME,
        wait=False,
        ignore_window=True,
    )
    assert broker.moc == []
    assert book.fills[0].status == "closed_elsewhere"


class _Clock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now


def test_moc_waits_until_1545(settings, tmp_path: Path) -> None:
    settings.out_dir = tmp_path
    settings.rth_preopen_poll_seconds = 10_000
    save_ledger(PositionLedger(fills=[_due_fill()]), tmp_path)
    clock = _Clock(BEFORE_MOC)
    broker = FakeMocBroker()
    broker.positions["MCD"] = -24

    def wait(seconds: float) -> None:
        clock.now = clock.now + timedelta(seconds=seconds)
        FakeMocBroker.wait(broker, seconds)

    broker.wait = wait  # type: ignore[method-assign]
    run_moc_job(
        settings,
        broker=broker,
        prompt_fn=lambda _m: None,
        now_fn=clock,
        wait=True,
        ignore_window=False,
    )
    assert clock.now.hour == 15
    assert clock.now.minute >= 45
    assert broker.moc
