from __future__ import annotations

import pandas as pd
from yfinance.utils import empty_df

from swingtrading.data.yahoo import _normalize_download


def test_failed_yahoo_symbols_do_not_abort_the_batch() -> None:
    """Empty Yahoo replies include Close and Adj Close. That used to crash to_numeric."""
    idx = pd.date_range("2025-08-04", periods=3, name="Date")
    good = pd.DataFrame(
        {
            "Open": [10.0, 11.0, 12.0],
            "High": [10.5, 11.5, 12.5],
            "Low": [9.5, 10.5, 11.5],
            "Close": [10.2, 11.2, 12.2],
            "Volume": [100, 110, 120],
        },
        index=idx,
    )
    missing = empty_df().reindex(idx)
    raw = pd.concat(
        {"SPY": good, "LVS": missing, "ADBE": missing.copy()},
        axis=1,
        names=["Ticker", "Price"],
    )
    frame = _normalize_download(raw, ["SPY", "LVS", "ADBE"])
    assert set(frame["symbol"]) == {"SPY"}
    assert len(frame) == 3
    assert frame["close"].tolist() == [10.2, 11.2, 12.2]
