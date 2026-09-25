from __future__ import annotations

import asyncio
import logging
import math
import time

from swingtrading.config import Settings
from swingtrading.execute.capital import fallback_margin
from swingtrading.execute.ledger import is_swing_ref, make_order_ref
from swingtrading.models import BracketHandle, EntryType, LedgerFill, Quote, Side, TradePlan

logger = logging.getLogger(__name__)

_FILLED = {"Filled"}
_CANCELLED = {"Cancelled", "ApiCancelled", "Inactive"}
_WORKING = {
    "PendingSubmit",
    "PreSubmitted",
    "Submitted",
    "ApiPending",
    "PendingCancel",
}


def ensure_asyncio_loop() -> asyncio.AbstractEventLoop:
    """ib_insync needs a current loop; Python 3.12+ no longer creates one."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        pass
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
        return loop
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


def _account_usd(rows: list, tag: str) -> float | None:
    for row in rows:
        if str(getattr(row, "tag", "")) != tag:
            continue
        currency = str(getattr(row, "currency", "") or "USD")
        if currency not in {"USD", ""}:
            continue
        parsed = _px(getattr(row, "value", None))
        if parsed is not None:
            return parsed
    return None


def _px(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    if math.isnan(number):
        return None
    return number


class IBKRRetailBroker:
    """Lynx / IBKR retail adapter via TWS or LYNX Gateway (ib_insync)."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._ib = None

    @property
    def ib(self):
        if self._ib is None:
            from ib_insync import IB

            ensure_asyncio_loop()
            self._ib = IB()
        return self._ib

    def connect(self) -> None:
        ensure_asyncio_loop()
        if self.ib.isConnected():
            return
        self.ib.connect(
            self.settings.tws_host,
            int(self.settings.tws_port),
            clientId=int(self.settings.tws_client_id),
            timeout=float(self.settings.tws_connect_timeout),
            readonly=False,
        )
        # Delayed quotes if no paid US tape; order prices remain the source of truth.
        try:
            self.ib.reqMarketDataType(3)
        except Exception:  # noqa: BLE001
            logger.debug("reqMarketDataType(3) failed", exc_info=True)
        try:
            self.ib.reqAllOpenOrders()
        except Exception:  # noqa: BLE001
            logger.debug("reqAllOpenOrders failed", exc_info=True)
        try:
            self.ib.reqAccountUpdates()
        except Exception:  # noqa: BLE001
            logger.debug("reqAccountUpdates failed", exc_info=True)

    def reconnect(self) -> None:
        ensure_asyncio_loop()
        try:
            if self._ib is not None and self.ib.isConnected():
                self.ib.disconnect()
        except Exception:  # noqa: BLE001
            logger.debug("disconnect during reconnect failed", exc_info=True)
        self._ib = None
        self.connect()

    def is_connected(self) -> bool:
        try:
            return bool(self._ib is not None and self.ib.isConnected())
        except Exception:  # noqa: BLE001
            return False

    def quote(self, symbol: str) -> Quote:
        from ib_insync import Stock

        contract = Stock(symbol, "SMART", "USD")
        qualified = self.ib.qualifyContracts(contract)
        if qualified:
            contract = qualified[0]
        ticker = self.ib.reqMktData(contract, "", snapshot=True, regulatorySnapshot=False)
        self.ib.sleep(1.5)
        quote = Quote(
            symbol=symbol,
            last=_px(getattr(ticker, "last", None)) or _px(getattr(ticker, "delayedLast", None)),
            bid=_px(getattr(ticker, "bid", None)) or _px(getattr(ticker, "delayedBid", None)),
            ask=_px(getattr(ticker, "ask", None)) or _px(getattr(ticker, "delayedAsk", None)),
            open=_px(getattr(ticker, "open", None)) or _px(getattr(ticker, "delayedOpen", None)),
        )
        try:
            self.ib.cancelMktData(contract)
        except Exception:  # noqa: BLE001
            pass
        return quote

    def buying_power(self) -> float:
        """USD AvailableFunds, else BuyingPower. inf if TWS has not published either."""
        rows = list(self.ib.accountValues())
        funds = _account_usd(rows, "AvailableFunds")
        if funds is None:
            funds = _account_usd(rows, "BuyingPower")
        if funds is None:
            logger.warning("No AvailableFunds/BuyingPower from TWS; not gating on cash")
            return float("inf")
        return funds

    def order_margin(self, plan: TradePlan) -> float:
        """Initial margin for the parent entry; fallback is conservative notional."""
        fallback = fallback_margin(plan, self.settings)
        try:
            from ib_insync import Order, Stock

            contract = Stock(plan.symbol, plan.exchange, plan.currency)
            if plan.primary_exchange:
                contract.primaryExchange = plan.primary_exchange
            qualified = self.ib.qualifyContracts(contract)
            if qualified:
                contract = qualified[0]
            order = Order()
            order.action = plan.side.value
            order.orderType = plan.entry_type.value
            order.totalQuantity = plan.qty
            order.tif = "DAY"
            order.outsideRth = False
            if plan.entry_type is EntryType.LMT:
                order.lmtPrice = plan.entry_price
            else:
                order.auxPrice = plan.entry_price
            state = self.ib.whatIfOrder(contract, order)
            change = _px(getattr(state, "initMarginChange", None))
            if change is None:
                change = _px(str(getattr(state, "initMarginChange", "") or "").replace(",", ""))
            if change is not None and change > 0:
                return change
        except Exception:  # noqa: BLE001
            logger.debug("whatIfOrder margin failed for %s", plan.symbol, exc_info=True)
        return fallback

    def position_qty(self, symbol: str) -> int:
        total = 0
        for pos in self.ib.positions():
            contract = getattr(pos, "contract", None)
            if contract is None:
                continue
            if str(getattr(contract, "symbol", "")).upper() != symbol.upper():
                continue
            total += int(getattr(pos, "position", 0) or 0)
        return total

    def place_bracket(self, plan: TradePlan) -> BracketHandle:
        from ib_insync import Order, Stock

        contract = Stock(plan.symbol, plan.exchange, plan.currency)
        if plan.primary_exchange:
            contract.primaryExchange = plan.primary_exchange
        qualified = self.ib.qualifyContracts(contract)
        if qualified:
            contract = qualified[0]

        parent_id = int(self.ib.client.getReqId())
        order_ref = make_order_ref(plan.as_of, plan.symbol)
        parent = Order()
        parent.orderId = parent_id
        parent.action = plan.side.value
        parent.orderType = plan.entry_type.value
        parent.totalQuantity = plan.qty
        parent.tif = "DAY"
        parent.transmit = False
        parent.outsideRth = False
        parent.orderRef = order_ref
        if plan.entry_type is EntryType.LMT:
            parent.lmtPrice = plan.entry_price
        else:
            parent.auxPrice = plan.entry_price

        exit_action = "SELL" if plan.side is Side.BUY else "BUY"
        take = Order()
        take.orderId = parent_id + 1
        take.parentId = parent_id
        take.action = exit_action
        take.orderType = "LMT"
        take.lmtPrice = plan.target_price
        take.totalQuantity = plan.qty
        take.tif = "GTC"
        take.transmit = False
        take.outsideRth = False
        take.orderRef = order_ref

        stop = Order()
        stop.orderId = parent_id + 2
        stop.parentId = parent_id
        stop.action = exit_action
        stop.orderType = "STP"
        stop.auxPrice = plan.stop_price
        stop.totalQuantity = plan.qty
        stop.tif = "GTC"
        stop.transmit = True
        stop.outsideRth = False
        stop.orderRef = order_ref

        self.ib.placeOrder(contract, parent)
        self.ib.placeOrder(contract, take)
        self.ib.placeOrder(contract, stop)
        self.ib.sleep(0.4)
        return BracketHandle(
            symbol=plan.symbol,
            parent_id=parent_id,
            take_id=parent_id + 1,
            stop_id=parent_id + 2,
            status="working",
            order_ref=order_ref,
        )

    def cancel_bracket(self, handle: BracketHandle) -> None:
        for trade in list(self.ib.trades()):
            order = trade.order
            if int(getattr(order, "orderId", -1)) != handle.parent_id:
                continue
            status = str(getattr(trade.orderStatus, "status", ""))
            if status in _FILLED:
                return
            self.ib.cancelOrder(order)
            return
        for trade in list(self.ib.openTrades()):
            if int(getattr(trade.order, "orderId", -1)) == handle.parent_id:
                self.ib.cancelOrder(trade.order)
                return

    def refresh_bracket(self, handle: BracketHandle) -> BracketHandle:
        status = handle.status
        for trade in self.ib.trades():
            if int(getattr(trade.order, "orderId", -1)) != handle.parent_id:
                continue
            raw = str(getattr(trade.orderStatus, "status", "") or "")
            filled = float(getattr(trade.orderStatus, "filled", 0) or 0)
            if raw in _FILLED or filled > 0:
                status = "filled"
            elif raw in _CANCELLED:
                status = "cancelled"
            elif raw in _WORKING:
                status = "working"
            else:
                status = raw.lower() or status
            break
        return handle.model_copy(update={"status": status})

    def existing_bracket(self, plan: TradePlan) -> BracketHandle | None:
        for trade in self.ib.openTrades():
            contract = trade.contract
            order = trade.order
            if str(getattr(contract, "symbol", "")).upper() != plan.symbol.upper():
                continue
            if int(getattr(order, "parentId", 0) or 0) != 0:
                continue
            if str(getattr(order, "action", "")) != plan.side.value:
                continue
            if not is_swing_ref(getattr(order, "orderRef", "")):
                continue
            status = str(getattr(trade.orderStatus, "status", ""))
            if status in _CANCELLED or status in _FILLED:
                continue
            return BracketHandle(
                symbol=plan.symbol,
                parent_id=int(order.orderId),
                status="working",
                order_ref=str(getattr(order, "orderRef", "") or ""),
            )
        return None

    def wait(self, seconds: float) -> None:
        if self.is_connected():
            self.ib.sleep(max(0.0, seconds))
        else:
            time.sleep(max(0.0, seconds))

    def cancel_tagged_exits(self, fill: LedgerFill) -> None:
        """Cancel only this fill's GTC stop/target. Leave manual orders alone."""
        wanted_ids = {i for i in (fill.take_id, fill.stop_id) if i is not None}
        for trade in list(self.ib.openTrades()) + list(self.ib.trades()):
            order = trade.order
            oid = int(getattr(order, "orderId", -1) or -1)
            ref = str(getattr(order, "orderRef", "") or "")
            parent = int(getattr(order, "parentId", 0) or 0)
            tagged = oid in wanted_ids or (
                ref == fill.order_ref and parent != 0 and oid != int(fill.parent_id or -1)
            )
            if not tagged:
                continue
            status = str(getattr(trade.orderStatus, "status", ""))
            if status in _FILLED or status in _CANCELLED:
                continue
            try:
                self.ib.cancelOrder(order)
            except Exception:  # noqa: BLE001
                logger.debug("cancel tagged exit %s failed", oid, exc_info=True)

    def place_moc(self, symbol: str, side: Side, qty: int, order_ref: str) -> None:
        from ib_insync import Order, Stock

        if qty <= 0:
            return
        contract = Stock(symbol, "SMART", "USD")
        qualified = self.ib.qualifyContracts(contract)
        if qualified:
            contract = qualified[0]
        order = Order()
        order.action = "SELL" if side is Side.BUY else "BUY"
        order.orderType = "MOC"
        order.totalQuantity = int(qty)
        order.tif = "DAY"
        order.outsideRth = False
        order.transmit = True
        order.orderRef = f"{order_ref}:moc"
        self.ib.placeOrder(contract, order)
        self.ib.sleep(0.3)

    def tagged_exit_filled(self, fill: LedgerFill) -> str | None:
        """Return 'target' or 'stop' if that tagged child already filled."""
        for trade in list(self.ib.trades()):
            order = trade.order
            oid = int(getattr(order, "orderId", -1) or -1)
            ref = str(getattr(order, "orderRef", "") or "")
            if ref != fill.order_ref and oid not in {fill.take_id, fill.stop_id}:
                continue
            raw = str(getattr(trade.orderStatus, "status", "") or "")
            filled = float(getattr(trade.orderStatus, "filled", 0) or 0)
            if raw not in _FILLED and filled <= 0:
                continue
            otype = str(getattr(order, "orderType", "") or "").upper()
            if oid == fill.take_id or otype == "LMT":
                return "target"
            if oid == fill.stop_id or otype in {"STP", "STP LMT"}:
                return "stop"
        return None
