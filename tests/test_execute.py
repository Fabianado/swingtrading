from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from swingtrading.data.rth import rth_has_opened
from swingtrading.execute.ledger import load_ledger, make_order_ref
from swingtrading.execute.session import execute_tickers
from swingtrading.execute.store import load_playbook, playbook_symbols, save_execution
from swingtrading.models import (
    BracketHandle,
    ExecutionState,
    Playbook,
    Quote,
    Side,
    TradePlan,
)
from swingtrading.plans.tradeplan import resize_for_risk
from swingtrading.report.render import write_playbook

ET = ZoneInfo("America/New_York")
AFTER_OPEN = datetime(2024, 6, 28, 9, 35, tzinfo=ET)
BEFORE_OPEN = datetime(2024, 6, 28, 8, 0, tzinfo=ET)


class FakeBroker:
    def __init__(self) -> None:
        self.connected = False
        self.connect_calls = 0
        self.fail_until = 0
        self.quotes: dict[str, Quote] = {}
        self.positions: dict[str, int] = {}
        self.brackets: dict[str, BracketHandle] = {}
        self.existing: dict[str, BracketHandle] = {}
        self.prompts: list[str] = []
        self.placed: list[str] = []
        self.canceled: list[str] = []
        self.fill_on_wait: list[str] = []
        self.wait_count = 0
        self.drop_before_wait = False
        self._id = 100
        self.buying_power_usd = 1e12
        self.margins: dict[str, float] = {}
        self.quote_calls = 0

    def buying_power(self) -> float:
        return float(self.buying_power_usd)

    def order_margin(self, plan: TradePlan) -> float:
        if plan.symbol in self.margins:
            return float(self.margins[plan.symbol])
        return abs(float(plan.qty) * float(plan.entry_price))

    def connect(self) -> None:
        self.connect_calls += 1
        if self.connect_calls <= self.fail_until:
            self.connected = False
            raise ConnectionError("TWS not running")
        self.connected = True

    def reconnect(self) -> None:
        self.connected = True

    def is_connected(self) -> bool:
        return self.connected

    def quote(self, symbol: str) -> Quote:
        self.quote_calls += 1
        return self.quotes.get(symbol, Quote(symbol=symbol, open=100.0, last=100.0))

    def position_qty(self, symbol: str) -> int:
        return int(self.positions.get(symbol, 0))

    def place_bracket(self, plan: TradePlan) -> BracketHandle:
        self._id += 1
        handle = BracketHandle(
            symbol=plan.symbol,
            parent_id=self._id,
            take_id=self._id + 1,
            stop_id=self._id + 2,
            status="working",
            order_ref=make_order_ref(plan.as_of, plan.symbol),
        )
        self.brackets[plan.symbol] = handle
        self.placed.append(plan.symbol)
        return handle

    def cancel_tagged_exits(self, fill) -> None:
        self.canceled.append(fill.order_ref)

    def place_moc(self, symbol: str, side: Side, qty: int, order_ref: str) -> None:
        self.placed.append(f"MOC:{symbol}:{qty}")

    def tagged_exit_filled(self, fill) -> str | None:
        return None

    def cancel_bracket(self, handle: BracketHandle) -> None:
        self.canceled.append(handle.symbol)
        self.brackets[handle.symbol] = handle.model_copy(update={"status": "cancelled"})

    def refresh_bracket(self, handle: BracketHandle) -> BracketHandle:
        return self.brackets.get(handle.symbol, handle)

    def existing_bracket(self, plan: TradePlan) -> BracketHandle | None:
        return self.existing.get(plan.symbol)

    def wait(self, seconds: float) -> None:
        self.wait_count += 1
        if self.drop_before_wait:
            self.connected = False
            self.drop_before_wait = False
        if self.fill_on_wait:
            symbol = self.fill_on_wait[0]
            current = self.brackets.get(symbol)
            if current is not None:
                self.fill_on_wait.pop(0)
                self.brackets[symbol] = current.model_copy(update={"status": "filled"})


@pytest.fixture
def playbook(sample_plan, as_of, tmp_path: Path) -> Playbook:
    book = Playbook(
        generated_at="2024-06-28T22:00:00Z",
        as_of=as_of,
        skip_ai=True,
        universe_size=10,
        filtered_size=8,
        scored_size=7,
        risk_usd=500.0,
        picks=[sample_plan],
        discarded=[],
        notes="test",
    )
    write_playbook(book, tmp_path)
    return book


def _settings(tmp_path: Path, settings):
    settings.out_dir = tmp_path
    return settings


def test_resize_uses_override_risk(sample_plan) -> None:
    wider = resize_for_risk(sample_plan, sample_plan.risk_usd * 2)
    assert wider is not None
    stop = abs(sample_plan.entry_price - sample_plan.stop_price)
    assert wider.qty == int((sample_plan.risk_usd * 2) // stop)
    assert wider.entry_price == sample_plan.entry_price


def test_load_playbook_roundtrip(playbook: Playbook, tmp_path: Path, as_of) -> None:
    loaded = load_playbook(tmp_path, as_of)
    assert loaded.picks[0].symbol == playbook.picks[0].symbol
    assert loaded.risk_usd == 500.0


def test_playbook_symbols_lists_shortlist(playbook: Playbook, sample_plan) -> None:
    extra = sample_plan.model_copy(update={"symbol": "BBB"})
    book = playbook.model_copy(update={"picks": [sample_plan, extra]})
    assert playbook_symbols(book) == [sample_plan.symbol, "BBB"]


def test_execute_skips_gapped_name_and_places_other(playbook, settings, tmp_path, sample_plan) -> None:
    other = sample_plan.model_copy(update={"symbol": "BBB", "confidence": 40.0})
    book = playbook.model_copy(update={"picks": [sample_plan, other]})
    write_playbook(book, tmp_path)
    settings = _settings(tmp_path, settings)
    broker = FakeBroker()
    beyond = sample_plan.skip_if.open_beyond
    if beyond is None:
        bad = (
            sample_plan.stop_price - 1
            if sample_plan.side is Side.BUY
            else sample_plan.stop_price + 1
        )
    elif sample_plan.side is Side.BUY:
        bad = beyond + 1
    else:
        bad = beyond - 1
    broker.quotes[sample_plan.symbol] = Quote(
        symbol=sample_plan.symbol,
        open=bad,
        last=bad,
    )
    broker.quotes["BBB"] = Quote(symbol="BBB", open=other.entry_price, last=other.entry_price)
    broker.fill_on_wait = ["BBB"]

    state = execute_tickers(
        settings,
        [sample_plan.symbol, "BBB"],
        playbook=book,
        broker=broker,
        prompt_fn=broker.prompts.append,
        monitor=True,
        now_fn=lambda: AFTER_OPEN,
    )
    assert sample_plan.symbol in state.invalidated
    assert "BBB" in broker.placed
    assert sample_plan.symbol not in broker.placed


def test_execute_reuses_playbook_risk_when_flag_omitted(playbook, settings, tmp_path, sample_plan) -> None:
    settings = _settings(tmp_path, settings)
    settings.risk_usd = 1.0  # would be too small if used
    broker = FakeBroker()
    broker.quotes[sample_plan.symbol] = Quote(
        symbol=sample_plan.symbol,
        open=sample_plan.entry_price,
        last=sample_plan.entry_price,
    )
    broker.fill_on_wait = [sample_plan.symbol]
    state = execute_tickers(
        settings,
        [sample_plan.symbol],
        risk_usd=None,
        playbook=playbook,
        broker=broker,
        prompt_fn=broker.prompts.append,
        now_fn=lambda: AFTER_OPEN,
    )
    assert state.risk_usd == 500.0
    assert any(d.qty == sample_plan.qty for d in state.decisions if d.action == "placed")


def test_execute_risk_override_changes_qty(playbook, settings, tmp_path, sample_plan) -> None:
    settings = _settings(tmp_path, settings)
    broker = FakeBroker()
    broker.quotes[sample_plan.symbol] = Quote(
        symbol=sample_plan.symbol,
        open=sample_plan.entry_price,
        last=sample_plan.entry_price,
    )
    broker.fill_on_wait = [sample_plan.symbol]
    doubled = sample_plan.risk_usd * 2
    state = execute_tickers(
        settings,
        [sample_plan.symbol],
        risk_usd=doubled,
        playbook=playbook,
        broker=broker,
        prompt_fn=broker.prompts.append,
        now_fn=lambda: AFTER_OPEN,
    )
    expected = resize_for_risk(sample_plan, doubled)
    assert expected is not None
    assert state.risk_usd == doubled
    assert any(d.qty == expected.qty for d in state.decisions if d.action == "placed")


def test_max_new_positions_does_not_transmit_rest(playbook, settings, tmp_path, sample_plan) -> None:
    second = sample_plan.model_copy(update={"symbol": "BBB", "confidence": 50.0})
    third = sample_plan.model_copy(update={"symbol": "CCC", "confidence": 40.0})
    book = playbook.model_copy(update={"picks": [sample_plan, second, third]})
    write_playbook(book, tmp_path)
    settings = _settings(tmp_path, settings)
    broker = FakeBroker()
    for symbol in (sample_plan.symbol, "BBB", "CCC"):
        broker.quotes[symbol] = Quote(symbol=symbol, open=sample_plan.entry_price, last=sample_plan.entry_price)
    broker.fill_on_wait = [sample_plan.symbol]

    state = execute_tickers(
        settings,
        [sample_plan.symbol, "BBB", "CCC"],
        max_new_positions=1,
        playbook=book,
        broker=broker,
        prompt_fn=broker.prompts.append,
        now_fn=lambda: AFTER_OPEN,
    )
    assert sample_plan.symbol in state.opened
    assert broker.placed == [sample_plan.symbol]
    assert set(state.skipped) >= {"BBB", "CCC"}
    assert "BBB" not in broker.canceled
    assert "CCC" not in broker.canceled


def test_prompts_when_tws_is_down(playbook, settings, tmp_path, sample_plan) -> None:
    settings = _settings(tmp_path, settings)
    broker = FakeBroker()
    broker.fail_until = 1
    broker.quotes[sample_plan.symbol] = Quote(
        symbol=sample_plan.symbol,
        open=sample_plan.entry_price,
        last=sample_plan.entry_price,
    )
    broker.fill_on_wait = [sample_plan.symbol]
    execute_tickers(
        settings,
        [sample_plan.symbol],
        playbook=playbook,
        broker=broker,
        prompt_fn=broker.prompts.append,
        now_fn=lambda: AFTER_OPEN,
    )
    assert broker.prompts
    assert "TWS" in broker.prompts[0]
    assert broker.connected is True


def test_recovers_after_disconnect(playbook, settings, tmp_path, sample_plan) -> None:
    settings = _settings(tmp_path, settings)
    broker = FakeBroker()
    broker.quotes[sample_plan.symbol] = Quote(
        symbol=sample_plan.symbol,
        open=sample_plan.entry_price,
        last=sample_plan.entry_price,
    )
    broker.fill_on_wait = [sample_plan.symbol]
    broker.drop_before_wait = True
    state = execute_tickers(
        settings,
        [sample_plan.symbol],
        playbook=playbook,
        broker=broker,
        prompt_fn=broker.prompts.append,
        now_fn=lambda: AFTER_OPEN,
    )
    assert sample_plan.symbol in state.opened
    assert broker.connected is True


def test_skips_existing_position(playbook, settings, tmp_path, sample_plan) -> None:
    settings = _settings(tmp_path, settings)
    broker = FakeBroker()
    broker.positions[sample_plan.symbol] = 10
    state = execute_tickers(
        settings,
        [sample_plan.symbol],
        playbook=playbook,
        broker=broker,
        prompt_fn=broker.prompts.append,
        now_fn=lambda: AFTER_OPEN,
    )
    assert sample_plan.symbol in state.invalidated
    assert broker.placed == []


def test_unknown_ticker_raises(playbook, settings, tmp_path) -> None:
    settings = _settings(tmp_path, settings)
    with pytest.raises(KeyError, match="ZZZZ"):
        execute_tickers(
            settings,
            ["ZZZZ"],
            playbook=playbook,
            broker=FakeBroker(),
            prompt_fn=lambda _m: None,
            monitor=False,
            now_fn=lambda: AFTER_OPEN,
        )


def test_execution_report_written(tmp_path: Path, as_of) -> None:
    state = ExecutionState(
        as_of=as_of,
        risk_usd=500,
        max_new_positions=2,
        tickers=["AAA"],
        opened=["AAA"],
    )
    json_path, md_path = save_execution(state, tmp_path)
    assert json_path.exists()
    assert "Opened: AAA" in md_path.read_text(encoding="utf-8")


def test_parent_fill_is_written_to_ledger(playbook, settings, tmp_path, sample_plan) -> None:
    settings = _settings(tmp_path, settings)
    broker = FakeBroker()
    broker.quotes[sample_plan.symbol] = Quote(
        symbol=sample_plan.symbol,
        open=sample_plan.entry_price,
        last=sample_plan.entry_price,
    )
    broker.fill_on_wait = [sample_plan.symbol]
    execute_tickers(
        settings,
        [sample_plan.symbol],
        playbook=playbook,
        broker=broker,
        prompt_fn=broker.prompts.append,
        now_fn=lambda: AFTER_OPEN,
        wait_for_moc=False,
    )
    book = load_ledger(tmp_path)
    assert len(book.fills) == 1
    row = book.fills[0]
    assert row.symbol == sample_plan.symbol
    assert row.qty == sample_plan.qty
    assert row.side is sample_plan.side
    assert row.fill_date == AFTER_OPEN.date()
    assert row.status == "open"
    assert row.order_ref == make_order_ref(sample_plan.as_of, sample_plan.symbol)


class _Clock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now


def test_waits_for_open_then_sends(playbook, settings, tmp_path, sample_plan) -> None:
    settings = _settings(tmp_path, settings)
    settings.rth_preopen_poll_seconds = 10_000
    clock = _Clock(BEFORE_OPEN)
    broker = FakeBroker()
    broker.quotes[sample_plan.symbol] = Quote(symbol=sample_plan.symbol)
    placed_at: list[datetime] = []

    def wait(seconds: float) -> None:
        clock.now = clock.now + timedelta(seconds=seconds)
        if rth_has_opened(clock.now):
            broker.quotes[sample_plan.symbol] = Quote(
                symbol=sample_plan.symbol,
                open=sample_plan.entry_price,
                last=sample_plan.entry_price,
            )
        FakeBroker.wait(broker, seconds)

    orig_place = broker.place_bracket

    def place(plan: TradePlan):
        placed_at.append(clock.now)
        return orig_place(plan)

    broker.wait = wait  # type: ignore[method-assign]
    broker.place_bracket = place  # type: ignore[method-assign]
    broker.fill_on_wait = [sample_plan.symbol]

    state = execute_tickers(
        settings,
        [sample_plan.symbol],
        playbook=playbook,
        broker=broker,
        prompt_fn=broker.prompts.append,
        now_fn=clock,
        wait_for_open=True,
    )
    assert sample_plan.symbol in broker.placed
    assert placed_at
    assert rth_has_opened(placed_at[0])
    assert any(d.action == "placed" for d in state.decisions)


def test_waits_for_open_then_skips_gap(playbook, settings, tmp_path, sample_plan) -> None:
    settings = _settings(tmp_path, settings)
    settings.rth_preopen_poll_seconds = 10_000
    clock = _Clock(BEFORE_OPEN)
    broker = FakeBroker()
    broker.quotes[sample_plan.symbol] = Quote(symbol=sample_plan.symbol)
    beyond = sample_plan.skip_if.open_beyond
    if beyond is None:
        bad = (
            sample_plan.stop_price - 1
            if sample_plan.side is Side.BUY
            else sample_plan.stop_price + 1
        )
    elif sample_plan.side is Side.BUY:
        bad = beyond + 1
    else:
        bad = beyond - 1

    def wait(seconds: float) -> None:
        clock.now = clock.now + timedelta(seconds=seconds)
        if rth_has_opened(clock.now):
            broker.quotes[sample_plan.symbol] = Quote(
                symbol=sample_plan.symbol,
                open=bad,
                last=bad,
            )
        FakeBroker.wait(broker, seconds)

    broker.wait = wait  # type: ignore[method-assign]

    state = execute_tickers(
        settings,
        [sample_plan.symbol],
        playbook=playbook,
        broker=broker,
        prompt_fn=broker.prompts.append,
        now_fn=clock,
        wait_for_open=True,
        monitor=False,
    )
    assert sample_plan.symbol in state.invalidated
    assert broker.placed == []
    assert rth_has_opened(clock.now)


def test_negative_target_is_not_transmitted(playbook, settings, tmp_path, sample_plan) -> None:
    bad = sample_plan.model_copy(
        update={
            "symbol": "CTVA",
            "side": Side.SELL,
            "entry_price": 11.58,
            "stop_price": 20.02,
            "target_price": -5.2898,
        }
    )
    book = playbook.model_copy(update={"picks": [bad]})
    write_playbook(book, tmp_path)
    settings = _settings(tmp_path, settings)
    broker = FakeBroker()
    broker.quotes["CTVA"] = Quote(symbol="CTVA", open=11.58, last=11.58)
    state = execute_tickers(
        settings,
        ["CTVA"],
        playbook=book,
        broker=broker,
        prompt_fn=broker.prompts.append,
        now_fn=lambda: AFTER_OPEN,
        wait_for_open=True,
        wait_for_moc=False,
        monitor=False,
    )
    assert "CTVA" in state.invalidated
    assert broker.placed == []
    assert any("profit-taker" in d.reason for d in state.decisions)


def test_no_opening_print_does_not_send(playbook, settings, tmp_path, sample_plan) -> None:
    settings = _settings(tmp_path, settings)
    settings.rth_quote_timeout_seconds = 0
    broker = FakeBroker()
    broker.quotes[sample_plan.symbol] = Quote(symbol=sample_plan.symbol)
    state = execute_tickers(
        settings,
        [sample_plan.symbol],
        playbook=playbook,
        broker=broker,
        prompt_fn=broker.prompts.append,
        now_fn=lambda: AFTER_OPEN,
        wait_for_open=True,
        monitor=False,
    )
    assert sample_plan.symbol in state.invalidated
    assert broker.placed == []
    assert any("opening print" in d.reason for d in state.decisions)
    assert broker.quote_calls == 1
    assert broker.wait_count == 0


def test_yahoo_fallback_used_when_tws_has_no_tape(
    playbook, settings, tmp_path, sample_plan
) -> None:
    settings = _settings(tmp_path, settings)
    broker = FakeBroker()
    broker.quotes[sample_plan.symbol] = Quote(symbol=sample_plan.symbol)
    broker.fill_on_wait = [sample_plan.symbol]

    def fallback(_symbol: str) -> Quote:
        return Quote(
            symbol=sample_plan.symbol,
            open=sample_plan.entry_price,
            last=sample_plan.entry_price,
        )

    state = execute_tickers(
        settings,
        [sample_plan.symbol],
        playbook=playbook,
        broker=broker,
        prompt_fn=broker.prompts.append,
        now_fn=lambda: AFTER_OPEN,
        wait_for_moc=False,
        fallback_quote=fallback,
    )
    assert sample_plan.symbol in broker.placed
    assert any(d.action == "placed" for d in state.decisions)


def test_now_flag_skips_open_wait(playbook, settings, tmp_path, sample_plan) -> None:
    settings = _settings(tmp_path, settings)
    clock = _Clock(BEFORE_OPEN)
    broker = FakeBroker()
    broker.quotes[sample_plan.symbol] = Quote(
        symbol=sample_plan.symbol,
        open=sample_plan.entry_price,
        last=sample_plan.entry_price,
    )
    broker.fill_on_wait = [sample_plan.symbol]
    execute_tickers(
        settings,
        [sample_plan.symbol],
        playbook=playbook,
        broker=broker,
        prompt_fn=broker.prompts.append,
        now_fn=clock,
        wait_for_open=False,
    )
    assert sample_plan.symbol in broker.placed
    assert clock.now == BEFORE_OPEN


def _valid_quote(plan: TradePlan) -> Quote:
    return Quote(symbol=plan.symbol, open=plan.entry_price, last=plan.entry_price)


def test_promotes_next_after_gap_skip(playbook, settings, tmp_path, sample_plan) -> None:
    settings = _settings(tmp_path, settings)
    settings.bp_reserve_pct = 0.0
    cheap = sample_plan.model_copy(update={"symbol": "BBB", "confidence": 40.0})
    book = playbook.model_copy(update={"picks": [sample_plan, cheap]})
    write_playbook(book, tmp_path)
    broker = FakeBroker()
    broker.buying_power_usd = 10_000
    broker.margins[sample_plan.symbol] = 8_000
    broker.margins["BBB"] = 8_000
    beyond = sample_plan.skip_if.open_beyond
    bad = (beyond + 1) if beyond is not None and sample_plan.side is Side.BUY else (
        (beyond - 1) if beyond is not None else sample_plan.stop_price
    )
    if sample_plan.side is Side.SELL and beyond is not None:
        bad = beyond - 1
    broker.quotes[sample_plan.symbol] = Quote(symbol=sample_plan.symbol, open=bad, last=bad)
    broker.quotes["BBB"] = _valid_quote(cheap)
    broker.fill_on_wait = ["BBB"]
    state = execute_tickers(
        settings,
        [sample_plan.symbol, "BBB"],
        playbook=book,
        broker=broker,
        prompt_fn=broker.prompts.append,
        now_fn=lambda: AFTER_OPEN,
        wait_for_moc=False,
    )
    assert sample_plan.symbol in state.invalidated
    assert sample_plan.symbol not in broker.placed
    assert "BBB" in broker.placed


def test_skips_expensive_name_and_places_next_that_fits(
    playbook, settings, tmp_path, sample_plan
) -> None:
    settings = _settings(tmp_path, settings)
    settings.bp_reserve_pct = 0.0
    cheap = sample_plan.model_copy(update={"symbol": "BBB", "confidence": 40.0})
    book = playbook.model_copy(update={"picks": [sample_plan, cheap]})
    write_playbook(book, tmp_path)
    broker = FakeBroker()
    broker.buying_power_usd = 6_000
    broker.margins[sample_plan.symbol] = 20_000
    broker.margins["BBB"] = 5_000
    broker.quotes[sample_plan.symbol] = _valid_quote(sample_plan)
    broker.quotes["BBB"] = _valid_quote(cheap)
    broker.fill_on_wait = ["BBB"]
    state = execute_tickers(
        settings,
        [sample_plan.symbol, "BBB"],
        playbook=book,
        broker=broker,
        prompt_fn=broker.prompts.append,
        now_fn=lambda: AFTER_OPEN,
        wait_for_moc=False,
    )
    assert sample_plan.symbol in state.skipped
    assert sample_plan.symbol not in broker.placed
    assert "BBB" in broker.placed


def test_does_not_transmit_more_than_buying_power(
    playbook, settings, tmp_path, sample_plan
) -> None:
    settings = _settings(tmp_path, settings)
    settings.bp_reserve_pct = 0.0
    second = sample_plan.model_copy(update={"symbol": "BBB", "confidence": 50.0})
    third = sample_plan.model_copy(update={"symbol": "CCC", "confidence": 40.0})
    book = playbook.model_copy(update={"picks": [sample_plan, second, third]})
    write_playbook(book, tmp_path)
    broker = FakeBroker()
    broker.buying_power_usd = 10_000
    broker.margins[sample_plan.symbol] = 6_000
    broker.margins["BBB"] = 6_000
    broker.margins["CCC"] = 3_000
    for symbol, plan in ((sample_plan.symbol, sample_plan), ("BBB", second), ("CCC", third)):
        broker.quotes[symbol] = _valid_quote(plan)
    broker.fill_on_wait = [sample_plan.symbol, "CCC"]
    state = execute_tickers(
        settings,
        [sample_plan.symbol, "BBB", "CCC"],
        playbook=book,
        broker=broker,
        prompt_fn=broker.prompts.append,
        now_fn=lambda: AFTER_OPEN,
        wait_for_moc=False,
    )
    assert sample_plan.symbol in broker.placed
    assert "BBB" not in broker.placed
    assert "CCC" in broker.placed
    assert "BBB" in state.skipped


def test_position_cap_does_not_transmit_lower_confidence(
    playbook, settings, tmp_path, sample_plan
) -> None:
    settings = _settings(tmp_path, settings)
    second = sample_plan.model_copy(update={"symbol": "BBB", "confidence": 50.0})
    third = sample_plan.model_copy(update={"symbol": "CCC", "confidence": 40.0})
    book = playbook.model_copy(update={"picks": [sample_plan, second, third]})
    write_playbook(book, tmp_path)
    broker = FakeBroker()
    for symbol, plan in ((sample_plan.symbol, sample_plan), ("BBB", second), ("CCC", third)):
        broker.quotes[symbol] = _valid_quote(plan)
    broker.fill_on_wait = [sample_plan.symbol]
    state = execute_tickers(
        settings,
        [sample_plan.symbol, "BBB", "CCC"],
        max_new_positions=1,
        playbook=book,
        broker=broker,
        prompt_fn=broker.prompts.append,
        now_fn=lambda: AFTER_OPEN,
        wait_for_moc=False,
    )
    assert broker.placed == [sample_plan.symbol]
    assert set(state.skipped) >= {"BBB", "CCC"}
