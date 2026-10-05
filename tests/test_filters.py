from __future__ import annotations

from datetime import timedelta

from swingtrading.data.calendar import is_earnings_blackout, next_sessions, session_index
from swingtrading.data.wikipedia import to_yahoo_symbol
from swingtrading.features.compute import build_features
from swingtrading.screen.filters import apply_hard_filters


def test_yahoo_symbol_dot_to_dash() -> None:
    assert to_yahoo_symbol("BRK.B") == "BRK-B"
    assert to_yahoo_symbol("BF.B") == "BF-B"


def test_illiquid_and_earnings_are_filtered(ohlcv, constituents, earnings, as_of, settings) -> None:
    sessions = session_index(ohlcv, "SPY")
    features = build_features(ohlcv, constituents, as_of, earnings_by_symbol=earnings)
    kept = apply_hard_filters(
        features,
        sessions,
        min_dollar_volume=settings.min_dollar_volume,
        earnings_blackout_sessions=settings.earnings_blackout_sessions,
    )
    symbols = {f.symbol for f in kept}
    assert "EEE" not in symbols
    assert "FFF" not in symbols
    assert "AAA" in symbols


def test_spinoff_gap_is_filtered(ohlcv, constituents, earnings, as_of, settings) -> None:
    crashed = ohlcv.copy()
    aaa = crashed["symbol"] == "AAA"
    last_date = crashed.loc[aaa, "date"].max()
    prev = crashed.loc[aaa & (crashed["date"] < last_date), "close"].iloc[-1]
    spun = float(prev) * 0.16
    last = aaa & (crashed["date"] == last_date)
    crashed.loc[last, ["open", "high", "low", "close"]] = spun
    sessions = session_index(crashed, "SPY")
    features = build_features(crashed, constituents, as_of, earnings_by_symbol=earnings)
    aaa_feat = next(f for f in features if f.symbol == "AAA")
    assert aaa_feat.max_tr_pct > settings.max_bar_move_pct
    kept = apply_hard_filters(
        features,
        sessions,
        min_dollar_volume=settings.min_dollar_volume,
        earnings_blackout_sessions=settings.earnings_blackout_sessions,
        max_atr_pct=settings.max_atr_pct,
        max_bar_move_pct=settings.max_bar_move_pct,
    )
    symbols = {f.symbol for f in kept}
    assert "AAA" not in symbols
    assert "BBB" in symbols


def test_earnings_blackout_counts_sessions(sessions, as_of) -> None:
    nxt = next_sessions(sessions, as_of, n=1)[0]
    assert is_earnings_blackout(nxt, as_of, sessions, blackout=2)
    far = as_of + timedelta(days=30)
    assert not is_earnings_blackout(far, as_of, sessions, blackout=2)
    assert not is_earnings_blackout(None, as_of, sessions, blackout=2)
