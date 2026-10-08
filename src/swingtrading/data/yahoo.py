from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta

import pandas as pd
import yfinance as yf

from swingtrading.data.cache import CacheStore
from swingtrading.models import Constituent, Quote

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
        frames = _download_symbols(tickers, start, end)
        missing = _symbols_without_rows(frames, tickers)
        if missing:
            logger.warning(
                "Yahoo returned no prices for %s; retrying individually",
                ", ".join(missing),
            )
            retried = _download_symbols(missing, start, end)
            if not retried.empty:
                frames = pd.concat([frames, retried], ignore_index=True) if not frames.empty else retried
            still_missing = _symbols_without_rows(frames, tickers)
            if still_missing:
                logger.warning(
                    "Skipping %s symbol(s) with no Yahoo prices: %s",
                    len(still_missing),
                    ", ".join(still_missing),
                )
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


def _download_symbols(tickers: list[str], start: date, end: date) -> pd.DataFrame:
    empty = pd.DataFrame(columns=["symbol", "date", "open", "high", "low", "close", "volume"])
    if not tickers:
        return empty
    try:
        raw = yf.download(
            tickers=tickers,
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
            auto_adjust=True,
            group_by="ticker",
            threads=len(tickers) > 1,
            progress=False,
        )
    except Exception:  # noqa: BLE001
        if len(tickers) == 1:
            logger.warning("Yahoo download failed for %s", tickers[0], exc_info=True)
            return empty
        mid = len(tickers) // 2
        logger.warning("Yahoo batch failed for %s symbols; splitting the request", len(tickers))
        left = _download_symbols(tickers[:mid], start, end)
        right = _download_symbols(tickers[mid:], start, end)
        parts = [frame for frame in (left, right) if not frame.empty]
        return pd.concat(parts, ignore_index=True) if parts else empty
    return _normalize_download(raw, tickers)


def _symbols_without_rows(frames: pd.DataFrame, tickers: list[str]) -> list[str]:
    if frames.empty or "symbol" not in frames.columns:
        return list(tickers)
    present = set(frames["symbol"].astype(str))
    return [symbol for symbol in tickers if symbol not in present]


def _normalize_download(raw: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    empty = pd.DataFrame(columns=["symbol", "date", "open", "high", "low", "close", "volume"])
    if raw is None or raw.empty:
        return empty
    frames: list[pd.DataFrame] = []
    if isinstance(raw.columns, pd.MultiIndex):
        level0 = set(raw.columns.get_level_values(0))
        grouped_by_column = "Close" in level0 or "close" in {str(x).title() for x in level0}
        for symbol in tickers:
            if grouped_by_column:
                if symbol not in raw.columns.get_level_values(1):
                    continue
                part = raw.xs(symbol, axis=1, level=1, drop_level=True)
            else:
                if symbol not in level0:
                    continue
                part = raw[symbol]
            frames.append(_safe_ohlcv(part, symbol))
    else:
        symbol = tickers[0] if len(tickers) == 1 else "UNKNOWN"
        frames.append(_safe_ohlcv(raw, symbol))
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        return empty
    return pd.concat(frames, ignore_index=True)


def _safe_ohlcv(part: pd.DataFrame, symbol: str) -> pd.DataFrame:
    try:
        return _one_ohlcv(part, symbol)
    except Exception:  # noqa: BLE001
        logger.warning("Skipping unreadable Yahoo frame for %s", symbol, exc_info=True)
        return pd.DataFrame(columns=["symbol", "date", "open", "high", "low", "close", "volume"])


def _one_ohlcv(part: pd.DataFrame, symbol: str) -> pd.DataFrame:
    empty = pd.DataFrame(columns=["symbol", "date", "open", "high", "low", "close", "volume"])
    df = part.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(-1)
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    if df.columns.duplicated().any():
        df = df.loc[:, ~df.columns.duplicated()].copy()
    # Failed Yahoo replies include both Close and Adj Close. Renaming both
    # to close makes a 2-d column and crashes the rest of the download.
    if "close" in df.columns and "adj_close" in df.columns:
        df = df.drop(columns=["adj_close"])
    elif "adj_close" in df.columns:
        df = df.rename(columns={"adj_close": "close"})
    needed = {"open", "high", "low", "close", "volume"}
    if not needed.issubset(set(df.columns)):
        return empty
    out = df[list(needed)].dropna(subset=["close"]).reset_index()
    if out.empty:
        return empty
    date_col = "date" if "date" in out.columns else out.columns[0]
    out = out.rename(columns={date_col: "date"})
    if isinstance(out["date"], pd.DataFrame):
        out = out.loc[:, ~out.columns.duplicated()].copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.tz_localize(None).dt.normalize()
    out["symbol"] = symbol
    volume = out["volume"]
    if isinstance(volume, pd.DataFrame):
        volume = volume.iloc[:, 0]
    out["volume"] = pd.to_numeric(volume, errors="coerce").fillna(0)
    for col in ("open", "high", "low", "close"):
        values = out[col]
        if isinstance(values, pd.DataFrame):
            values = values.iloc[:, 0]
        out[col] = pd.to_numeric(values, errors="coerce")
    return out[["symbol", "date", "open", "high", "low", "close", "volume"]].dropna(
        subset=["open", "high", "low", "close"]
    )


def yahoo_session_quote(symbol: str) -> Quote | None:
    """Today's Yahoo daily open/last when TWS has no US tape entitlement."""
    try:
        hist = yf.Ticker(symbol).history(period="5d", interval="1d", auto_adjust=True)
    except Exception:  # noqa: BLE001
        logger.warning("Yahoo quote failed for %s", symbol, exc_info=True)
        return None
    if hist is None or hist.empty:
        return None
    row = hist.iloc[-1]
    try:
        open_px = float(row["Open"])
        last_px = float(row["Close"])
    except (TypeError, ValueError, KeyError):
        return None
    if open_px != open_px or last_px != last_px:
        return None
    return Quote(symbol=symbol, open=open_px, last=last_px)


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
