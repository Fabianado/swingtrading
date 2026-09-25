from __future__ import annotations

import numpy as np
import pandas as pd

from swingtrading.features.indicators import atr, rsi, sma


def test_sma_and_rsi_known_series() -> None:
    close = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0])
    mean = sma(close, 5)
    assert np.isclose(mean.iloc[-1], 8.0)
    # Strictly rising closes → RSI near 100 after the window fills
    values = rsi(close, window=5)
    assert values.iloc[-1] > 90


def test_atr_positive_on_range() -> None:
    high = pd.Series([11.0, 12.0, 13.5, 13.0, 14.0, 15.0, 16.0, 15.5, 17.0, 18.0, 19.0, 20.0, 21.0, 22.0, 23.0])
    low = high - 1.0
    close = high - 0.4
    values = atr(high, low, close, window=5)
    assert values.iloc[-1] >= 1.0
