from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from swingtrading.data.rth import (
    format_wait,
    moc_window_open,
    next_rth_open,
    past_moc_cutoff,
    rth_has_opened,
    seconds_until_moc,
    seconds_until_rth_open,
)

ET = ZoneInfo("America/New_York")


def test_weekday_before_open_is_closed() -> None:
    now = datetime(2024, 6, 28, 8, 0, tzinfo=ET)  # Friday
    assert rth_has_opened(now) is False
    nxt = next_rth_open(now)
    assert nxt == datetime(2024, 6, 28, 9, 30, tzinfo=ET)
    assert seconds_until_rth_open(now) == 90 * 60


def test_weekday_at_open_is_open() -> None:
    now = datetime(2024, 6, 28, 9, 30, tzinfo=ET)
    assert rth_has_opened(now) is True
    assert seconds_until_rth_open(now) == 0


def test_friday_afternoon_counts_as_open() -> None:
    now = datetime(2024, 6, 28, 16, 0, tzinfo=ET)
    assert rth_has_opened(now) is True


def test_saturday_waits_until_monday() -> None:
    now = datetime(2024, 6, 29, 10, 0, tzinfo=ET)
    assert rth_has_opened(now) is False
    assert next_rth_open(now) == datetime(2024, 7, 1, 9, 30, tzinfo=ET)


def test_naive_datetime_treated_as_et() -> None:
    now = datetime(2024, 6, 28, 9, 29)
    assert rth_has_opened(now) is False
    assert rth_has_opened(datetime(2024, 6, 28, 9, 30)) is True


def test_format_wait() -> None:
    assert format_wait(90 * 60) == "1h 30m"
    assert format_wait(45) == "45s"
    assert format_wait(125) == "2m 5s"


def test_moc_window() -> None:
    assert moc_window_open(datetime(2024, 6, 28, 15, 44, tzinfo=ET)) is False
    assert moc_window_open(datetime(2024, 6, 28, 15, 45, tzinfo=ET)) is True
    assert moc_window_open(datetime(2024, 6, 28, 15, 49, tzinfo=ET)) is True
    assert moc_window_open(datetime(2024, 6, 28, 15, 50, tzinfo=ET)) is False
    assert past_moc_cutoff(datetime(2024, 6, 28, 15, 50, tzinfo=ET)) is True
    assert seconds_until_moc(datetime(2024, 6, 28, 14, 45, tzinfo=ET)) == 3600
    assert seconds_until_moc(datetime(2024, 6, 28, 15, 45, tzinfo=ET)) == 0
