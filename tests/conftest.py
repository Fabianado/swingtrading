from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from swingtrading.config import Settings
from swingtrading.models import Constituent, ScoredSetup
from swingtrading.plans.tradeplan import build_trade_plan


def _settings(**overrides) -> Settings:
    data = dict(
        min_dollar_volume=1_000_000.0,
        pre_shortlist=8,
        max_picks=5,
        max_per_sector=2,
        risk_usd=500.0,
        lookback_days=400,
        ai_budget_usd=2.0,
    )
    data.update(overrides)
    return Settings(**data)


@pytest.fixture
def settings() -> Settings:
    return _settings()


@pytest.fixture
def as_of() -> date:
    return date(2024, 6, 28)


@pytest.fixture
def sessions(as_of: date) -> pd.DatetimeIndex:
    start = as_of - timedelta(days=220)
    return pd.bdate_range(start, as_of)


def _frame(symbol: str, dates: pd.DatetimeIndex, closes: np.ndarray, volume: float, last_range: float = 0.4) -> pd.DataFrame:
    rows = []
    for i, (d, close) in enumerate(zip(dates, closes)):
        rng = last_range if i == len(closes) - 1 else max(0.8, abs(close) * 0.012)
        high = close + rng / 2
        low = close - rng / 2
        open_ = closes[i - 1] if i else close
        vol = volume * (2.2 if i == len(closes) - 1 else 1.0)
        rows.append(
            {
                "symbol": symbol,
                "date": pd.Timestamp(d),
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": vol,
            }
        )
    return pd.DataFrame(rows)


@pytest.fixture
def constituents() -> list[Constituent]:
    return [
        Constituent(symbol="AAA", yahoo_symbol="AAA", name="Alpha Corp", sector="Information Technology"),
        Constituent(symbol="BBB", yahoo_symbol="BBB", name="Beta Corp", sector="Health Care"),
        Constituent(symbol="CCC", yahoo_symbol="CCC", name="Gamma Corp", sector="Energy"),
        Constituent(symbol="DDD", yahoo_symbol="DDD", name="Delta Corp", sector="Financials"),
        Constituent(symbol="EEE", yahoo_symbol="EEE", name="Thin Corp", sector="Utilities"),
        Constituent(symbol="FFF", yahoo_symbol="FFF", name="Earns Corp", sector="Consumer Staples"),
        Constituent(symbol="GGG", yahoo_symbol="GGG", name="Tech Two", sector="Information Technology"),
        Constituent(symbol="HHH", yahoo_symbol="HHH", name="Tech Three", sector="Information Technology"),
        Constituent(symbol="III", yahoo_symbol="III", name="Industrials Co", sector="Industrials"),
        Constituent(symbol="JJJ", yahoo_symbol="JJJ", name="Materials Co", sector="Materials"),
    ]


@pytest.fixture
def ohlcv(sessions: pd.DatetimeIndex) -> pd.DataFrame:
    n = len(sessions)
    spy = 400 + 0.05 * np.arange(n)
    aaa = 80 + 0.35 * np.arange(n)  # strong uptrend vs SPY
    bbb = 120 + 0.15 * np.arange(n)
    bbb[-12:] = bbb[-13] - 2.8 * np.arange(1, 13)  # dump → oversold
    ccc = 90 - 0.28 * np.arange(n)  # downtrend
    ddd = 50 + 0.12 * np.arange(n)
    ddd[-8:] = ddd[-9] + 2.4 * np.arange(1, 9)  # spike → overbought
    eee = 25 + 0.02 * np.arange(n)
    fff = 60 + 0.2 * np.arange(n)
    ggg = 70 + 0.3 * np.arange(n)
    hhh = 55 + 0.32 * np.arange(n)
    iii = 40 + 0.22 * np.arange(n)
    jjj = 33 + 0.18 * np.arange(n)
    frames = [
        _frame("SPY", sessions, spy, 80_000_000, last_range=1.5),
        _frame("AAA", sessions, aaa, 8_000_000, last_range=0.25),
        _frame("BBB", sessions, bbb, 6_000_000, last_range=1.8),
        _frame("CCC", sessions, ccc, 7_000_000, last_range=0.3),
        _frame("DDD", sessions, ddd, 5_000_000, last_range=1.2),
        _frame("EEE", sessions, eee, 5_000, last_range=0.2),  # illiquid
        _frame("FFF", sessions, fff, 4_000_000, last_range=0.4),
        _frame("GGG", sessions, ggg, 5_000_000, last_range=0.28),
        _frame("HHH", sessions, hhh, 5_000_000, last_range=0.26),
        _frame("III", sessions, iii, 4_500_000, last_range=0.3),
        _frame("JJJ", sessions, jjj, 4_000_000, last_range=0.35),
    ]
    return pd.concat(frames, ignore_index=True)


@pytest.fixture
def earnings(as_of: date) -> dict[str, date | None]:
    return {
        "FFF": as_of + timedelta(days=1),  # next session-ish; calendar uses business days
        "AAA": None,
        "BBB": None,
        "CCC": None,
        "DDD": None,
        "EEE": None,
        "GGG": None,
        "HHH": None,
        "III": None,
        "JJJ": None,
    }


@pytest.fixture
def sample_setup(ohlcv: pd.DataFrame, as_of: date) -> ScoredSetup:
    from swingtrading.features.compute import build_features
    from swingtrading.screen.scoring import best_setup

    feats = build_features(
        ohlcv,
        [Constituent(symbol="AAA", yahoo_symbol="AAA", name="Alpha Corp", sector="Information Technology")],
        as_of,
        earnings_by_symbol={},
    )
    aaa = next(f for f in feats if f.symbol == "AAA")
    return best_setup(aaa)


@pytest.fixture
def sample_plan(sample_setup: ScoredSetup, settings: Settings):
    plan = build_trade_plan(sample_setup, settings)
    assert plan is not None
    return plan
