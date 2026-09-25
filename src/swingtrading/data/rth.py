"""NYSE regular-hours helpers (US cash equities)."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

NYSE_TZ = ZoneInfo("America/New_York")
RTH_OPEN = time(9, 30)


def to_nyse(now: datetime, timezone: str = "America/New_York") -> datetime:
    zone = ZoneInfo(timezone)
    if now.tzinfo is None:
        return now.replace(tzinfo=zone)
    return now.astimezone(zone)


def rth_open_time(hour: int = 9, minute: int = 30) -> time:
    return time(hour=hour, minute=minute)


def rth_has_opened(
    now: datetime,
    *,
    timezone: str = "America/New_York",
    hour: int = 9,
    minute: int = 30,
) -> bool:
    """True on a weekday at or after the cash open (default 9:30 ET)."""
    et = to_nyse(now, timezone)
    if et.weekday() >= 5:
        return False
    return et.time() >= rth_open_time(hour, minute)


def next_rth_open(
    now: datetime,
    *,
    timezone: str = "America/New_York",
    hour: int = 9,
    minute: int = 30,
) -> datetime:
    zone = ZoneInfo(timezone)
    et = to_nyse(now, timezone)
    open_at = rth_open_time(hour, minute)
    candidate = et.replace(hour=open_at.hour, minute=open_at.minute, second=0, microsecond=0)
    if et.weekday() < 5 and et < candidate:
        return candidate
    day = et.date() + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return datetime.combine(day, open_at, tzinfo=zone)


def seconds_until_rth_open(
    now: datetime,
    *,
    timezone: str = "America/New_York",
    hour: int = 9,
    minute: int = 30,
) -> float:
    if rth_has_opened(now, timezone=timezone, hour=hour, minute=minute):
        return 0.0
    delta = next_rth_open(now, timezone=timezone, hour=hour, minute=minute) - to_nyse(
        now, timezone
    )
    return max(0.0, delta.total_seconds())


def moc_time(hour: int = 15, minute: int = 45) -> time:
    return time(hour=hour, minute=minute)


def seconds_until_moc(
    now: datetime,
    *,
    timezone: str = "America/New_York",
    hour: int = 15,
    minute: int = 45,
) -> float:
    """Seconds until today's MOC submit time. 0 if already at/after it (or weekend)."""
    et = to_nyse(now, timezone)
    if et.weekday() >= 5:
        return 0.0
    target = et.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if et >= target:
        return 0.0
    return (target - et).total_seconds()


def moc_window_open(
    now: datetime,
    *,
    timezone: str = "America/New_York",
    hour: int = 15,
    minute: int = 45,
    cutoff_hour: int = 15,
    cutoff_minute: int = 50,
) -> bool:
    """True on a weekday from the MOC submit time until the exchange cutoff."""
    et = to_nyse(now, timezone)
    if et.weekday() >= 5:
        return False
    stamp = et.time()
    return moc_time(hour, minute) <= stamp < moc_time(cutoff_hour, cutoff_minute)


def past_moc_cutoff(
    now: datetime,
    *,
    timezone: str = "America/New_York",
    cutoff_hour: int = 15,
    cutoff_minute: int = 50,
) -> bool:
    et = to_nyse(now, timezone)
    if et.weekday() >= 5:
        return True
    return et.time() >= moc_time(cutoff_hour, cutoff_minute)


def format_wait(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"
