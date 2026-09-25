from __future__ import annotations

from datetime import date, timedelta

import pandas as pd


def session_index(ohlcv: pd.DataFrame, benchmark: str = "SPY") -> pd.DatetimeIndex:
    spy = ohlcv.loc[ohlcv["symbol"] == benchmark, "date"]
    if spy.empty:
        dates = pd.to_datetime(ohlcv["date"]).drop_duplicates().sort_values()
        return pd.DatetimeIndex(dates)
    return pd.DatetimeIndex(pd.to_datetime(spy).sort_values().unique())


def next_sessions(sessions: pd.DatetimeIndex, as_of: date, n: int) -> list[date]:
    existing = [ts.date() for ts in sessions if ts.date() > as_of]
    if len(existing) >= n:
        return existing[:n]
    start = as_of + timedelta(days=1)
    projected = [ts.date() for ts in pd.bdate_range(start, periods=n + 5)]
    merged: list[date] = []
    for d in existing + projected:
        if d not in merged:
            merged.append(d)
    return merged[:n]


def sessions_until(sessions: pd.DatetimeIndex, as_of: date, event: date) -> int | None:
    """Trading sessions from as_of (exclusive) to event (inclusive). None if event is in the past."""
    if event < as_of:
        return None
    if event == as_of:
        return 0
    future = next_sessions(sessions, as_of, n=30)
    for i, d in enumerate(future, start=1):
        if d >= event:
            return i
    return None


def is_earnings_blackout(
    earnings: date | None,
    as_of: date,
    sessions: pd.DatetimeIndex,
    blackout: int = 2,
) -> bool:
    if earnings is None:
        return False
    until = sessions_until(sessions, as_of, earnings)
    if until is None:
        return False
    return until <= blackout


def as_of_from_bars(ohlcv: pd.DataFrame, requested: date | None) -> date:
    dates = pd.to_datetime(ohlcv["date"]).dt.date
    max_date = dates.max()
    if requested is None or requested >= max_date:
        return max_date
    eligible = dates[dates <= requested]
    if eligible.empty:
        return max_date
    return eligible.max()
