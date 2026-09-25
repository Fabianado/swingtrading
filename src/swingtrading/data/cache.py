from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

OHLCV_COLUMNS = ["symbol", "date", "open", "high", "low", "close", "volume"]


class CacheStore:
    """Local Parquet cache for daily bars, constituents, and earnings dates."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @property
    def ohlcv_path(self) -> Path:
        return self.root / "ohlcv.parquet"

    @property
    def meta_path(self) -> Path:
        return self.root / "constituents.parquet"

    @property
    def earnings_path(self) -> Path:
        return self.root / "earnings.parquet"

    def load_ohlcv(self) -> pd.DataFrame:
        if not self.ohlcv_path.exists():
            return pd.DataFrame(columns=OHLCV_COLUMNS)
        df = pd.read_parquet(self.ohlcv_path)
        df["date"] = pd.to_datetime(df["date"]).dt.normalize()
        return df

    def save_ohlcv(self, df: pd.DataFrame) -> None:
        out = df.copy()
        out["date"] = pd.to_datetime(out["date"]).dt.normalize()
        out = out[OHLCV_COLUMNS].drop_duplicates(subset=["symbol", "date"], keep="last")
        out.sort_values(["symbol", "date"]).to_parquet(self.ohlcv_path, index=False)

    def merge_ohlcv(self, incoming: pd.DataFrame) -> pd.DataFrame:
        existing = self.load_ohlcv()
        merged = pd.concat([existing, incoming], ignore_index=True)
        self.save_ohlcv(merged)
        return self.load_ohlcv()

    def last_cached_date(self) -> date | None:
        df = self.load_ohlcv()
        if df.empty:
            return None
        return pd.Timestamp(df["date"].max()).date()

    def save_constituents(self, df: pd.DataFrame) -> None:
        df.to_parquet(self.meta_path, index=False)

    def load_constituents(self) -> pd.DataFrame:
        if not self.meta_path.exists():
            return pd.DataFrame(columns=["symbol", "yahoo_symbol", "name", "sector"])
        return pd.read_parquet(self.meta_path)

    def save_earnings(self, df: pd.DataFrame) -> None:
        out = df.copy()
        if "earnings_date" in out.columns:
            out["earnings_date"] = pd.to_datetime(out["earnings_date"], errors="coerce")
        out.to_parquet(self.earnings_path, index=False)

    def load_earnings(self) -> pd.DataFrame:
        if not self.earnings_path.exists():
            return pd.DataFrame(columns=["symbol", "earnings_date", "fetched_at"])
        df = pd.read_parquet(self.earnings_path)
        if "earnings_date" in df.columns:
            df["earnings_date"] = pd.to_datetime(df["earnings_date"], errors="coerce")
        return df

    def earnings_fresh(self, max_age: timedelta = timedelta(hours=20)) -> bool:
        df = self.load_earnings()
        if df.empty or "fetched_at" not in df.columns:
            return False
        fetched = pd.to_datetime(df["fetched_at"], errors="coerce").max()
        if pd.isna(fetched):
            return False
        return datetime.now() - fetched.to_pydatetime() < max_age
