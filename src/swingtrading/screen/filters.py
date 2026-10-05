from __future__ import annotations

from datetime import date

import pandas as pd

from swingtrading.data.calendar import is_earnings_blackout
from swingtrading.models import Features


def apply_hard_filters(
    features: list[Features],
    sessions: pd.DatetimeIndex,
    min_dollar_volume: float,
    earnings_blackout_sessions: int = 2,
    max_atr_pct: float = 0.20,
    max_bar_move_pct: float = 0.35,
) -> list[Features]:
    kept: list[Features] = []
    for feat in features:
        if feat.dollar_volume_20 < min_dollar_volume:
            continue
        if feat.close <= 0 or feat.atr <= 0:
            continue
        if feat.atr / feat.close > max_atr_pct:
            continue
        if feat.max_tr_pct > max_bar_move_pct:
            continue
        if is_earnings_blackout(
            feat.earnings_date,
            feat.as_of,
            sessions,
            blackout=earnings_blackout_sessions,
        ):
            continue
        kept.append(feat)
    return kept
