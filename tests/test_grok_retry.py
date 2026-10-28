from __future__ import annotations

import pytest

from swingtrading.ai.grok import call_with_retry, is_retryable_grok_error


class _Status:
    def __init__(self, name: str) -> None:
        self.name = name


class _CapacityError(Exception):
    def code(self):
        return _Status("RESOURCE_EXHAUSTED")


class _UnavailableError(Exception):
    def code(self):
        return _Status("UNAVAILABLE")


class _BillingError(Exception):
    def code(self):
        return _Status("PERMISSION_DENIED")


def test_capacity_and_unavailable_are_retried() -> None:
    assert is_retryable_grok_error(_CapacityError("full"))
    assert is_retryable_grok_error(_UnavailableError("down"))
    assert is_retryable_grok_error(Exception("RESOURCE_EXHAUSTED: at capacity"))
    assert is_retryable_grok_error(_BillingError("no credit")) is False


def test_retry_waits_then_returns_the_later_success() -> None:
    waits: list[float] = []
    calls = {"n": 0}

    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise _CapacityError("full")
        return "ok"

    result = call_with_retry(flaky, attempts=4, wait_seconds=60, sleep_fn=waits.append)
    assert result == "ok"
    assert waits == [60, 120]


def test_retry_stops_on_a_non_capacity_error() -> None:
    waits: list[float] = []

    def billing() -> str:
        raise _BillingError("no credit")

    with pytest.raises(_BillingError):
        call_with_retry(billing, attempts=4, wait_seconds=60, sleep_fn=waits.append)
    assert waits == []


def test_retry_gives_up_after_the_last_attempt() -> None:
    waits: list[float] = []

    def always() -> str:
        raise _CapacityError("full")

    with pytest.raises(_CapacityError):
        call_with_retry(always, attempts=3, wait_seconds=60, sleep_fn=waits.append)
    assert waits == [60, 120]
