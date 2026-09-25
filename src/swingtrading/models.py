from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class SetupType(str, Enum):
    CONTINUATION = "continuation"
    MEAN_REVERSION = "mean_reversion"


class EntryType(str, Enum):
    LMT = "LMT"
    STP = "STP"


class StopType(str, Enum):
    STP = "STP"
    STP_LMT = "STP LMT"


class Constituent(BaseModel):
    symbol: str
    yahoo_symbol: str
    name: str
    sector: str


class SkipIf(BaseModel):
    open_beyond: float | None = None
    flatten_if_open_through_stop: bool = True
    description: str


class AIOverlay(BaseModel):
    sentiment: float = Field(ge=-1.0, le=1.0)
    event_risk: str = "none"
    veto: bool = False
    veto_reason: str = ""
    confidence_delta: float = 0.0
    summary: str = ""
    sources: list[str] = Field(default_factory=list)


class Features(BaseModel):
    symbol: str
    name: str = ""
    sector: str = ""
    as_of: date
    close: float
    open: float
    high: float
    low: float
    prior_high: float
    prior_low: float
    volume: float
    atr: float
    sma20: float
    sma50: float
    rsi14: float
    dollar_volume_20: float
    ret_5d: float
    ret_20d: float
    spy_ret_20d: float
    rs_20d: float
    volume_ratio: float
    range_atr: float
    dist_sma20_atr: float
    high_20: float
    low_20: float
    earnings_date: date | None = None


class ScoredSetup(BaseModel):
    features: Features
    side: Side
    setup_type: SetupType
    quant_score: float
    rationale: str


class TradePlan(BaseModel):
    symbol: str
    side: Side
    sec_type: Literal["STK"] = "STK"
    exchange: Literal["SMART"] = "SMART"
    currency: Literal["USD"] = "USD"
    primary_exchange: str | None = None
    setup_type: SetupType
    entry_type: EntryType
    entry_price: float
    tif: Literal["DAY"] = "DAY"
    stop_type: StopType = StopType.STP
    stop_price: float
    target_price: float
    time_stop_sessions: int = 5
    skip_if: SkipIf
    qty: int
    risk_usd: float
    confidence: float
    quant_score: float
    rationale: str
    sources: list[str] = Field(default_factory=list)
    sector: str = ""
    as_of: date
    name: str = ""
    ai: AIOverlay | None = None

    def round_trip_r(self) -> float:
        risk = abs(self.entry_price - self.stop_price)
        if risk <= 0:
            return 0.0
        return abs(self.target_price - self.entry_price) / risk


class Playbook(BaseModel):
    generated_at: str
    as_of: date
    skip_ai: bool
    universe_size: int
    filtered_size: int
    scored_size: int
    ai_spent_usd: float = 0.0
    risk_usd: float | None = None
    picks: list[TradePlan]
    discarded: list[TradePlan] = Field(default_factory=list)
    notes: str = ""


class Quote(BaseModel):
    symbol: str
    last: float | None = None
    bid: float | None = None
    ask: float | None = None
    open: float | None = None


class BracketHandle(BaseModel):
    symbol: str
    parent_id: int
    take_id: int | None = None
    stop_id: int | None = None
    status: str = "working"
    order_ref: str = ""


class LedgerFill(BaseModel):
    """One program-owned parent fill. The MOC job only touches these rows."""

    symbol: str
    side: Side
    qty: int
    fill_date: date
    time_stop_sessions: int = 5
    parent_id: int | None = None
    take_id: int | None = None
    stop_id: int | None = None
    order_ref: str
    playbook_as_of: date | None = None
    status: str = "open"


class PositionLedger(BaseModel):
    fills: list[LedgerFill] = Field(default_factory=list)


class ExecutionDecision(BaseModel):
    symbol: str
    action: str
    reason: str
    qty: int = 0
    risk_usd: float | None = None


class ExecutionState(BaseModel):
    as_of: date
    risk_usd: float
    max_new_positions: int
    tickers: list[str]
    decisions: list[ExecutionDecision] = Field(default_factory=list)
    brackets: list[BracketHandle] = Field(default_factory=list)
    opened: list[str] = Field(default_factory=list)
    canceled: list[str] = Field(default_factory=list)
    invalidated: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
