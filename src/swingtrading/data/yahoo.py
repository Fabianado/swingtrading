from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta

import pandas as pd
import yfinance as yf

from swingtrading.data.cache import CacheStore
from swingtrading.models import Constituent

logger = logging.getLogger(__name__)


class YahooClient:
    """Yahoo Finance ingest for daily OHLCV and next earnings dates."""

    def __init__(self, cache: CacheStore) -> None:
        self.cache = cache

    def refresh_ohlcv(
        self,
        symbols: list[str],
        lookback_days: int = 400,
        as_of: date | None = None,
    ) -> pd.DataFrame:
        end = as_of or date.today()
        start = end - timedelta(days=lookback_days + 30)
        last = self.cache.last_cached_date()
        if last is not None and last >= end - timedelta(days=1):
            start = last - timedelta(days=5)

        tickers = sorted(set(symbols))
        logger.info("Downloading OHLCV for %s symbols from Yahoo (%s → %s)", len(tickers), start, end)
        raw = yf.download(
            tickers=tickers,
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
            auto_adjust=True,
            group_by="ticker",
            threads=True,
            progress=False,
        )
        frames = _normalize_download(raw, tickers)
        if frames.empty:
            existing = self.cache.load_ohlcv()
            if existing.empty:
                raise RuntimeError("Yahoo returned no OHLCV and cache is empty")
            logger.warning("Yahoo returned no rows; using cache")
            return existing
        return self.cache.merge_ohlcv(frames)

    def refresh_earnings(
        self,
        symbols: list[str],
        workers: int = 8,
        force: bool = False,
    ) -> pd.DataFrame:
        if not force and self.cache.earnings_fresh():
            return self.cache.load_earnings()
        rows: list[dict] = []
        fetched_at = datetime.now().isoformat(timespec="seconds")

        def one(symbol: str) -> dict:
            nxt = _next_earnings(symbol)
            return {
                "symbol": symbol,
                "earnings_date": pd.Timestamp(nxt) if nxt else pd.NaT,
                "fetched_at": fetched_at,
            }

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(one, s): s for s in symbols}
            for fut in as_completed(futs):
                try:
                    rows.append(fut.result())
                except Exception as exc:  # noqa: BLE001
                    logger.debug("earnings fetch failed for %s: %s", futs[fut], exc)
                    rows.append(
                        {
                            "symbol": futs[fut],
                            "earnings_date": pd.NaT,
                            "fetched_at": fetched_at,
                        }
                    )
        df = pd.DataFrame(rows)
        self.cache.save_earnings(df)
        return df


def constituents_frame(items: list[Constituent]) -> pd.DataFrame:
    return pd.DataFrame([c.model_dump() for c in items])


def _normalize_download(raw: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame(columns=["symbol", "date", "open", "high", "low", "close", "volume"])
    frames: list[pd.DataFrame] = []
    if isinstance(raw.columns, pd.MultiIndex):
        level0 = set(raw.columns.get_level_values(0))
        if "Close" in level0 or "close" in {str(x).title() for x in level0}:
            # group_by=column: top level is OHLCV
            for symbol in tickers:
                if symbol not in raw.columns.get_level_values(1):
                    continue
                part = raw.xs(symbol, axis=1, level=1, drop_level=True)
                frames.append(_one_ohlcv(part, symbol))
        else:
            for symbol in tickers:
                if symbol not in level0:
                    continue
                part = raw[symbol]
                frames.append(_one_ohlcv(part, symbol))
    else:
        symbol = tickers[0] if len(tickers) == 1 else "UNKNOWN"
        frames.append(_one_ohlcv(raw, symbol))
    if not frames:
        return pd.DataFrame(columns=["symbol", "date", "open", "high", "low", "close", "volume"])
    return pd.concat(frames, ignore_index=True)


def _one_ohlcv(part: pd.DataFrame, symbol: str) -> pd.DataFrame:
    df = part.copy()
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    rename = {"adj_close": "close"}
    df = df.rename(columns=rename)
    needed = {"open", "high", "low", "close", "volume"}
    if not needed.issubset(df.columns):
        return pd.DataFrame(columns=["symbol", "date", "open", "high", "low", "close", "volume"])
    out = df[list(needed)].dropna(subset=["close"]).reset_index()
    date_col = "date" if "date" in out.columns else out.columns[0]
    out = out.rename(columns={date_col: "date"})
    out["date"] = pd.to_datetime(out["date"]).dt.tz_localize(None).dt.normalize()
    out["symbol"] = symbol
    out["volume"] = pd.to_numeric(out["volume"], errors="coerce").fillna(0)
    for col in ("open", "high", "low", "close"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    return out[["symbol", "date", "open", "high", "low", "close", "volume"]].dropna(
        subset=["open", "high", "low", "close"]
    )


def _next_earnings(symbol: str) -> date | None:
    ticker = yf.Ticker(symbol)
    try:
        dates = ticker.get_earnings_dates(limit=8)
    except Exception:  # noqa: BLE001
        dates = None
    if dates is None or dates.empty:
        info = getattr(ticker, "calendar", None)
        if isinstance(info, pd.DataFrame) and not info.empty:
            for val in info.values.flatten():
                ts = pd.to_datetime(val, errors="coerce")
                if pd.notna(ts) and ts.date() >= date.today() - timedelta(days=1):
                    return ts.date()
        return None
    idx = pd.to_datetime(dates.index)
    today = pd.Timestamp(date.today())
    future = idx[idx >= today - pd.Timedelta(days=1)]
    if future.empty:
        return None
    return pd.Timestamp(future.min()).date()
