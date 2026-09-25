from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from swingtrading.models import BracketHandle, LedgerFill, PositionLedger, Side, TradePlan

LEDGER_NAME = "positions.json"


def session_date(now: datetime, timezone: str = "America/New_York") -> date:
    if now.tzinfo is None:
        return now.date()
    return now.astimezone(ZoneInfo(timezone)).date()


def make_order_ref(as_of: date, symbol: str) -> str:
    return f"swing:{as_of.isoformat()}:{symbol.strip().upper()}"


def is_swing_ref(order_ref: str | None) -> bool:
    return str(order_ref or "").startswith("swing:")


def ledger_path(out_dir: Path) -> Path:
    return Path(out_dir) / LEDGER_NAME


def load_ledger(out_dir: Path) -> PositionLedger:
    path = ledger_path(out_dir)
    if not path.exists():
        return PositionLedger()
    return PositionLedger.model_validate_json(path.read_text(encoding="utf-8"))


def save_ledger(ledger: PositionLedger, out_dir: Path) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = ledger_path(out_dir)
    path.write_text(ledger.model_dump_json(indent=2), encoding="utf-8")
    return path


def sessions_held(fill_date: date, today: date) -> int:
    """Inclusive weekday count from fill date through today (session 1 = fill day)."""
    if today < fill_date:
        return 0
    count = 0
    cursor = fill_date
    while cursor <= today:
        if cursor.weekday() < 5:
            count += 1
        cursor += timedelta(days=1)
    return count


def is_due(fill: LedgerFill, today: date) -> bool:
    if fill.status != "open":
        return False
    return sessions_held(fill.fill_date, today) >= int(fill.time_stop_sessions)


def due_fills(ledger: PositionLedger, today: date) -> list[LedgerFill]:
    return [row for row in ledger.fills if is_due(row, today)]


def open_fills(ledger: PositionLedger) -> list[LedgerFill]:
    return [row for row in ledger.fills if row.status == "open"]


def close_qty(fill: LedgerFill, held: int) -> int:
    """Shares this program may flatten. Never the whole account net."""
    if fill.qty <= 0:
        return 0
    if fill.side is Side.BUY:
        if held <= 0:
            return 0
        return min(fill.qty, held)
    if held >= 0:
        return 0
    return min(fill.qty, abs(held))


def fill_from_plan(
    plan: TradePlan,
    handle: BracketHandle,
    fill_date: date,
) -> LedgerFill:
    ref = handle.order_ref or make_order_ref(plan.as_of, plan.symbol)
    return LedgerFill(
        symbol=plan.symbol,
        side=plan.side,
        qty=plan.qty,
        fill_date=fill_date,
        time_stop_sessions=plan.time_stop_sessions,
        parent_id=handle.parent_id,
        take_id=handle.take_id,
        stop_id=handle.stop_id,
        order_ref=ref,
        playbook_as_of=plan.as_of,
        status="open",
    )


def upsert_fill(ledger: PositionLedger, fill: LedgerFill) -> PositionLedger:
    rows: list[LedgerFill] = []
    replaced = False
    for row in ledger.fills:
        if row.order_ref == fill.order_ref:
            rows.append(fill)
            replaced = True
        else:
            rows.append(row)
    if not replaced:
        rows.append(fill)
    return ledger.model_copy(update={"fills": rows})


def mark_status(ledger: PositionLedger, order_ref: str, status: str) -> PositionLedger:
    rows = [
        row.model_copy(update={"status": status}) if row.order_ref == order_ref else row
        for row in ledger.fills
    ]
    return ledger.model_copy(update={"fills": rows})
