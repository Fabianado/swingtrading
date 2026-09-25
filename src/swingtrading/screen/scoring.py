from __future__ import annotations

from collections import defaultdict

from swingtrading.models import Features, ScoredSetup, SetupType, Side


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, value))


def score_continuation_long(feat: Features) -> tuple[float, str]:
    trend = 25.0 if feat.sma20 > feat.sma50 else 0.0
    trend += 15.0 * _clip01((feat.sma20 - feat.sma50) / feat.atr)
    rs = 20.0 * _clip01(feat.rs_20d / 0.05)
    vol = 15.0 * _clip01(feat.volume_ratio - 1.0)
    coil = 15.0 * _clip01(1.0 - feat.range_atr)
    span = feat.high_20 - feat.low_20
    loc = 15.0 * _clip01((feat.close - feat.low_20) / span) if span > 0 else 0.0
    score = trend + rs + vol + coil + loc
    why = (
        f"Uptrend SMA20/50, RS vs SPY {feat.rs_20d:.1%}, "
        f"vol x{feat.volume_ratio:.2f}, coiled range {feat.range_atr:.2f} ATR"
    )
    return score, why


def score_continuation_short(feat: Features) -> tuple[float, str]:
    trend = 25.0 if feat.sma20 < feat.sma50 else 0.0
    trend += 15.0 * _clip01((feat.sma50 - feat.sma20) / feat.atr)
    rs = 20.0 * _clip01((-feat.rs_20d) / 0.05)
    vol = 15.0 * _clip01(feat.volume_ratio - 1.0)
    coil = 15.0 * _clip01(1.0 - feat.range_atr)
    span = feat.high_20 - feat.low_20
    loc = 15.0 * _clip01((feat.high_20 - feat.close) / span) if span > 0 else 0.0
    score = trend + rs + vol + coil + loc
    why = (
        f"Downtrend SMA20/50, RS vs SPY {feat.rs_20d:.1%}, "
        f"vol x{feat.volume_ratio:.2f}, coiled range {feat.range_atr:.2f} ATR"
    )
    return score, why


def score_mean_reversion_long(feat: Features) -> tuple[float, str]:
    rsi_part = 40.0 * _clip01((35.0 - feat.rsi14) / 15.0) if feat.rsi14 < 35 else 0.0
    ext = 30.0 * _clip01((-feat.dist_sma20_atr) / 2.0) if feat.dist_sma20_atr < 0 else 0.0
    trend_ok = 15.0 if feat.close > feat.sma50 * 0.97 else 0.0
    vol = 15.0 * _clip01(feat.volume_ratio / 2.0)
    score = rsi_part + ext + trend_ok + vol
    why = (
        f"Oversold RSI {feat.rsi14:.1f}, {feat.dist_sma20_atr:.2f} ATR below SMA20"
    )
    return score, why


def score_mean_reversion_short(feat: Features) -> tuple[float, str]:
    rsi_part = 40.0 * _clip01((feat.rsi14 - 65.0) / 15.0) if feat.rsi14 > 65 else 0.0
    ext = 30.0 * _clip01(feat.dist_sma20_atr / 2.0) if feat.dist_sma20_atr > 0 else 0.0
    trend_ok = 15.0 if feat.close < feat.sma50 * 1.03 else 0.0
    vol = 15.0 * _clip01(feat.volume_ratio / 2.0)
    score = rsi_part + ext + trend_ok + vol
    why = (
        f"Overbought RSI {feat.rsi14:.1f}, {feat.dist_sma20_atr:.2f} ATR above SMA20"
    )
    return score, why


def best_setup(feat: Features) -> ScoredSetup:
    candidates = [
        (Side.BUY, SetupType.CONTINUATION, *score_continuation_long(feat)),
        (Side.SELL, SetupType.CONTINUATION, *score_continuation_short(feat)),
        (Side.BUY, SetupType.MEAN_REVERSION, *score_mean_reversion_long(feat)),
        (Side.SELL, SetupType.MEAN_REVERSION, *score_mean_reversion_short(feat)),
    ]
    side, setup, score, why = max(candidates, key=lambda item: item[2])
    return ScoredSetup(
        features=feat,
        side=side,
        setup_type=setup,
        quant_score=round(float(score), 2),
        rationale=why,
    )


def rank_setups(features: list[Features], min_score: float = 35.0) -> list[ScoredSetup]:
    ranked = [best_setup(f) for f in features]
    ranked = [s for s in ranked if s.quant_score >= min_score]
    ranked.sort(key=lambda s: s.quant_score, reverse=True)
    return ranked


def select_finalists(
    setups: list[ScoredSetup],
    limit: int,
    max_per_sector: int = 2,
) -> list[ScoredSetup]:
    picked: list[ScoredSetup] = []
    per_sector: dict[str, int] = defaultdict(int)
    for setup in setups:
        sector = setup.features.sector or "Unknown"
        if per_sector[sector] >= max_per_sector:
            continue
        picked.append(setup)
        per_sector[sector] += 1
        if len(picked) >= limit:
            break
    return picked
