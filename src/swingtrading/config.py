from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    xai_api_key: str | None = None
    tiingo_api_key: str | None = None
    fmp_api_key: str | None = None

    cache_dir: Path = Path("data/cache")
    out_dir: Path = Path("out")

    lookback_days: int = 400
    min_history: int = 60
    min_dollar_volume: float = 20_000_000.0
    earnings_blackout_sessions: int = 2
    max_atr_pct: float = 0.20
    max_bar_move_pct: float = 0.35

    pre_shortlist: int = 8
    max_picks: int = 5
    max_per_sector: int = 2

    risk_usd: float = 500.0
    atr_period: int = 14
    stop_atr: float = 1.25
    target_r: float = 2.0
    time_stop_sessions: int = 5
    continuation_buffer_atr: float = 0.05
    mean_reversion_limit_atr: float = 0.15
    skip_gap_atr: float = 0.5

    grok_model: str = "grok-4.3"
    ai_budget_usd: float = 2.0
    x_search_max_posts: int = 30
    x_search_lookback_hours: int = 48

    benchmark_symbol: str = "SPY"

    tws_host: str = "127.0.0.1"
    tws_port: int = 7497
    tws_client_id: int = 17
    tws_connect_timeout: float = 20.0
    tws_retry_seconds: float = 5.0

    rth_timezone: str = "America/New_York"
    rth_open_hour: int = 9
    rth_open_minute: int = 30
    rth_preopen_poll_seconds: float = 30.0
    rth_poll_seconds: float = 5.0
    rth_quote_timeout_seconds: float = 900.0
    rth_moc_hour: int = 15
    rth_moc_minute: int = 45
    rth_moc_cutoff_hour: int = 15
    rth_moc_cutoff_minute: int = 50

    bp_reserve_pct: float = 0.05
    long_margin_pct: float = 1.0
    short_margin_pct: float = 1.0


DEFAULTS = Settings()
