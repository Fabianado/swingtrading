from __future__ import annotations

from datetime import date

import pandas as pd

from swingtrading.features.indicators import atr, rsi, sma
from swingtrading.models import Constituent, Features


def bars_for(ohlcv: pd.DataFrame, symbol: str, as_of: date) -> pd.DataFrame:
    df = ohlcv.loc[ohlcv["symbol"] == symbol].copy()
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    cutoff = pd.Timestamp(as_of)
    df = df.loc[df["date"] <= cutoff].sort_values("date")
    return df.reset_index(drop=True)


def build_features(
    ohlcv: pd.DataFrame,
    constituents: list[Constituent],
    as_of: date,
    benchmark: str = "SPY",
    atr_period: int = 14,
    earnings_by_symbol: dict[str, date | None] | None = None,
) -> list[Features]:
    earnings_by_symbol = earnings_by_symbol or {}
    spy = bars_for(ohlcv, benchmark, as_of)
    spy_ret_20 = _return(spy["close"], 20) if len(spy) else float("nan")
    by_yahoo = {c.yahoo_symbol: c for c in constituents}
    out: list[Features] = []
    symbols = sorted(set(ohlcv["symbol"].unique()) - {benchmark})
    for symbol in symbols:
        hist = bars_for(ohlcv, symbol, as_of)
        if len(hist) < max(60, atr_period + 5):
            continue
        feat = _last_features(
            hist,
            symbol=symbol,
            constituent=by_yahoo.get(symbol),
            as_of=as_of,
            spy_ret_20=spy_ret_20,
            atr_period=atr_period,
            earnings=earnings_by_symbol.get(symbol),
        )
        if feat is not None:
            out.append(feat)
    return out


def _return(close: pd.Series, window: int) -> float:
    if len(close) <= window:
        return float("nan")
    prev = float(close.iloc[-1 - window])
    last = float(close.iloc[-1])
    if prev == 0:
        return float("nan")
    return last / prev - 1.0


def _last_features(
    hist: pd.DataFrame,
    symbol: str,
    constituent: Constituent | None,
    as_of: date,
    spy_ret_20: float,
    atr_period: int,
    earnings: date | None,
) -> Features | None:
    close = hist["close"]
    high = hist["high"]
    low = hist["low"]
    volume = hist["volume"]
    atr_s = atr(high, low, close, atr_period)
    sma20_s = sma(close, 20)
    sma50_s = sma(close, 50)
    rsi_s = rsi(close, 14)
    dv = (close * volume).rolling(20, min_periods=20).mean()
    vol_ma = volume.rolling(20, min_periods=20).mean()
    row = hist.iloc[-1]
    prev = hist.iloc[-2] if len(hist) >= 2 else row
    atr_val = float(atr_s.iloc[-1])
    if not pd.notna(atr_val) or atr_val <= 0:
        return None
    sma20_val = float(sma20_s.iloc[-1])
    sma50_val = float(sma50_s.iloc[-1])
    rsi_val = float(rsi_s.iloc[-1])
    if not all(pd.notna(x) for x in (sma20_val, sma50_val, rsi_val)):
        return None
    last_vol = float(row["volume"])
    vol_ma_val = float(vol_ma.iloc[-1]) if pd.notna(vol_ma.iloc[-1]) else 0.0
    volume_ratio = last_vol / vol_ma_val if vol_ma_val > 0 else 1.0
    last_range = float(row["high"]) - float(row["low"])
    high_20 = float(high.tail(20).max())
    low_20 = float(low.tail(20).min())
    ret_20 = _return(close, 20)
    rs_20 = ret_20 - spy_ret_20 if pd.notna(ret_20) and pd.notna(spy_ret_20) else 0.0
    as_of_bar = pd.Timestamp(row["date"]).date()
    return Features(
        symbol=symbol,
        name=constituent.name if constituent else symbol,
        sector=constituent.sector if constituent else "Unknown",
        as_of=as_of_bar,
        close=float(row["close"]),
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        prior_high=float(prev["high"]),
        prior_low=float(prev["low"]),
        volume=last_vol,
        atr=atr_val,
        sma20=sma20_val,
        sma50=sma50_val,
        rsi14=rsi_val,
        dollar_volume_20=float(dv.iloc[-1]) if pd.notna(dv.iloc[-1]) else 0.0,
        ret_5d=_return(close, 5),
        ret_20d=ret_20,
        spy_ret_20d=float(spy_ret_20) if pd.notna(spy_ret_20) else 0.0,
        rs_20d=float(rs_20),
        volume_ratio=float(volume_ratio),
        range_atr=last_range / atr_val,
        dist_sma20_atr=(float(row["close"]) - sma20_val) / atr_val,
        high_20=high_20,
        low_20=low_20,
        earnings_date=earnings,
    )
