from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from swingtrading.ai.cost import CostGuard
from swingtrading.ai.grok import (
    GrokClient,
    NullSentimentClient,
    SentimentClient,
    apply_overlay,
)
from swingtrading.config import Settings
from swingtrading.data.cache import CacheStore
from swingtrading.data.calendar import as_of_from_bars, session_index
from swingtrading.data.wikipedia import fetch_sp500
from swingtrading.data.yahoo import YahooClient, constituents_frame
from swingtrading.features.compute import build_features
from swingtrading.models import Constituent, Playbook, TradePlan
from swingtrading.plans.tradeplan import build_trade_plan
from swingtrading.report.render import write_playbook
from swingtrading.screen.filters import apply_hard_filters
from swingtrading.screen.scoring import rank_setups, select_finalists

logger = logging.getLogger(__name__)


def ingest(
    settings: Settings,
    *,
    as_of: date | None = None,
    refresh: bool = True,
) -> tuple[list[Constituent], pd.DataFrame, dict[str, date | None]]:
    cache = CacheStore(settings.cache_dir)
    constituents = _load_constituents(cache, refresh=refresh)
    yahoo_symbols = [c.yahoo_symbol for c in constituents] + [settings.benchmark_symbol]
    client = YahooClient(cache)
    if refresh:
        ohlcv = client.refresh_ohlcv(yahoo_symbols, lookback_days=settings.lookback_days, as_of=as_of)
        earnings_df = client.refresh_earnings([c.yahoo_symbol for c in constituents])
    else:
        ohlcv = cache.load_ohlcv()
        earnings_df = cache.load_earnings()
        if ohlcv.empty:
            raise RuntimeError("Cache is empty. Run without --offline first.")
    earnings = _earnings_map(earnings_df)
    return constituents, ohlcv, earnings


def run_playbook(
    settings: Settings,
    *,
    as_of: date | None = None,
    skip_ai: bool = False,
    refresh: bool = True,
    constituents: list[Constituent] | None = None,
    ohlcv: pd.DataFrame | None = None,
    earnings: dict[str, date | None] | None = None,
    sentiment: SentimentClient | None = None,
) -> Playbook:
    if constituents is None or ohlcv is None:
        constituents, ohlcv, fetched_earnings = ingest(settings, as_of=as_of, refresh=refresh)
        earnings = earnings if earnings is not None else fetched_earnings
    earnings = earnings or {}

    as_of_date = as_of_from_bars(ohlcv, as_of)
    ohlcv = ohlcv.loc[pd.to_datetime(ohlcv["date"]).dt.date <= as_of_date].copy()
    sessions = session_index(ohlcv, settings.benchmark_symbol)
    features = build_features(
        ohlcv,
        constituents,
        as_of_date,
        benchmark=settings.benchmark_symbol,
        atr_period=settings.atr_period,
        earnings_by_symbol=earnings,
    )
    filtered = apply_hard_filters(
        features,
        sessions,
        min_dollar_volume=settings.min_dollar_volume,
        earnings_blackout_sessions=settings.earnings_blackout_sessions,
    )
    ranked = rank_setups(filtered)
    shortlist = select_finalists(ranked, settings.pre_shortlist, settings.max_per_sector)

    plans: list[TradePlan] = []
    for setup in shortlist:
        plan = build_trade_plan(setup, settings)
        if plan is not None:
            plans.append(plan)

    guard = CostGuard(settings.ai_budget_usd)
    client = sentiment if sentiment is not None else _sentiment_client(settings, skip_ai)
    enriched: list[TradePlan] = []
    discarded: list[TradePlan] = []
    for plan in plans:
        overlay = client.enrich(plan, as_of_date, guard)
        updated = apply_overlay(plan, overlay)
        if overlay.veto:
            discarded.append(updated)
            continue
        enriched.append(updated)

    enriched.sort(key=lambda p: p.confidence, reverse=True)
    picks = _cap_plans(enriched, settings.max_picks, settings.max_per_sector)
    leftover = [p for p in enriched if p.symbol not in {x.symbol for x in picks}]
    discarded.extend(leftover)

    notes = (
        "Quant screen on the full S&P 500; Grok news/X overlay only on the shortlist. "
        "Entry/stop/target are ATR math, not model-invented prices."
    )
    if guard.skipped:
        notes += f" {guard.skipped} name(s) skipped Grok because the ${settings.ai_budget_usd:.2f} budget was exhausted."

    return Playbook(
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        as_of=as_of_date,
        skip_ai=skip_ai or isinstance(client, NullSentimentClient),
        universe_size=len(features),
        filtered_size=len(filtered),
        scored_size=len(ranked),
        ai_spent_usd=round(guard.spent_usd, 4),
        risk_usd=settings.risk_usd,
        picks=picks,
        discarded=discarded,
        notes=notes,
    )


def run_and_write(
    settings: Settings,
    *,
    as_of: date | None = None,
    skip_ai: bool = False,
    refresh: bool = True,
    out_dir: Path | None = None,
) -> tuple[Playbook, Path, Path]:
    playbook = run_playbook(settings, as_of=as_of, skip_ai=skip_ai, refresh=refresh)
    json_path, md_path = write_playbook(playbook, out_dir or settings.out_dir)
    return playbook, json_path, md_path


def _sentiment_client(settings: Settings, skip_ai: bool) -> SentimentClient:
    if skip_ai:
        return NullSentimentClient()
    return GrokClient(settings)


def _load_constituents(cache: CacheStore, refresh: bool) -> list[Constituent]:
    if refresh:
        try:
            items = fetch_sp500()
            cache.save_constituents(constituents_frame(items))
            return items
        except Exception as exc:  # noqa: BLE001
            logger.warning("Wikipedia constituents failed (%s); using cache if present", exc)
    frame = cache.load_constituents()
    if frame.empty:
        raise RuntimeError("No S&P 500 constituents. Need a Wikipedia fetch or a cached list.")
    return [Constituent(**row) for row in frame.to_dict(orient="records")]


def _earnings_map(df: pd.DataFrame) -> dict[str, date | None]:
    if df is None or df.empty:
        return {}
    out: dict[str, date | None] = {}
    for _, row in df.iterrows():
        symbol = str(row["symbol"])
        raw = row.get("earnings_date")
        ts = pd.to_datetime(raw, errors="coerce")
        out[symbol] = None if pd.isna(ts) else ts.date()
    return out


def _cap_plans(plans: list[TradePlan], limit: int, max_per_sector: int) -> list[TradePlan]:
    picked: list[TradePlan] = []
    per_sector: dict[str, int] = {}
    for plan in plans:
        sector = plan.sector or "Unknown"
        if per_sector.get(sector, 0) >= max_per_sector:
            continue
        picked.append(plan)
        per_sector[sector] = per_sector.get(sector, 0) + 1
        if len(picked) >= limit:
            break
    return picked
