from __future__ import annotations

from swingtrading.features.compute import build_features
from swingtrading.screen.scoring import best_setup, rank_by_side, rank_setups, select_finalists
from swingtrading.models import SetupType, Side


def test_uptrend_leader_scores_continuation_long(ohlcv, constituents, as_of) -> None:
    features = build_features(ohlcv, constituents, as_of, earnings_by_symbol={})
    aaa = next(f for f in features if f.symbol == "AAA")
    setup = best_setup(aaa)
    assert setup.side is Side.BUY
    assert setup.setup_type is SetupType.CONTINUATION
    assert setup.quant_score >= 35


def test_downtrend_scores_short(ohlcv, constituents, as_of) -> None:
    features = build_features(ohlcv, constituents, as_of, earnings_by_symbol={})
    ccc = next(f for f in features if f.symbol == "CCC")
    setup = best_setup(ccc)
    assert setup.side is Side.SELL
    assert setup.quant_score >= 30


def test_rank_by_side_keeps_a_symbol_on_one_side(ohlcv, constituents, as_of) -> None:
    features = build_features(ohlcv, constituents, as_of, earnings_by_symbol={})
    longs, shorts = rank_by_side(features, min_score=1.0)
    long_symbols = {setup.features.symbol for setup in longs}
    short_symbols = {setup.features.symbol for setup in shorts}
    assert long_symbols.isdisjoint(short_symbols)
    assert "AAA" in long_symbols
    assert "CCC" in short_symbols
    assert all(setup.side is Side.BUY for setup in longs)
    assert all(setup.side is Side.SELL for setup in shorts)


def test_sector_cap_limits_finalists(ohlcv, constituents, as_of) -> None:
    features = build_features(ohlcv, constituents, as_of, earnings_by_symbol={})
    ranked = rank_setups(features, min_score=1.0)
    picked = select_finalists(ranked, limit=5, max_per_sector=2)
    tech = [s for s in picked if s.features.sector == "Information Technology"]
    assert len(tech) <= 2
    assert len(picked) <= 5
