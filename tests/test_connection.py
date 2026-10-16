from __future__ import annotations

import pytest

from swingtrading.brokers.connection import connect_with_prompt, recover_connection


class _DownBroker:
    def __init__(self, *, fail_times: int) -> None:
        self.fail_times = fail_times
        self.calls = 0
        self.connected = False

    def is_connected(self) -> bool:
        return self.connected

    def connect(self) -> None:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ConnectionError("Nothing is accepting sockets at 127.0.0.1:7496")
        self.connected = True

    def reconnect(self) -> None:
        self.connect()


def test_unattended_retries_once_a_minute_until_tws_accepts() -> None:
    broker = _DownBroker(fail_times=1)
    slept: list[float] = []
    connect_with_prompt(
        broker,
        lambda _message: None,
        unattended=True,
        session_over_fn=lambda: False,
        sleep_fn=slept.append,
        host="127.0.0.1",
        port=7496,
    )
    assert slept == [60.0]
    assert broker.connected is True
    assert broker.calls == 2


def test_unattended_gives_up_when_the_cash_session_is_already_over() -> None:
    broker = _DownBroker(fail_times=99)
    slept: list[float] = []
    with pytest.raises(ConnectionError, match="giving up"):
        connect_with_prompt(
            broker,
            lambda _message: None,
            unattended=True,
            session_over_fn=lambda: True,
            sleep_fn=slept.append,
            host="127.0.0.1",
            port=7496,
        )
    assert slept == []
    assert broker.calls == 1


def test_unattended_gives_up_if_the_close_arrives_while_waiting() -> None:
    broker = _DownBroker(fail_times=99)
    slept: list[float] = []
    open_session = True

    def sleep(seconds: float) -> None:
        nonlocal open_session
        slept.append(seconds)
        open_session = False

    with pytest.raises(ConnectionError, match="giving up"):
        connect_with_prompt(
            broker,
            lambda _message: None,
            unattended=True,
            session_over_fn=lambda: not open_session,
            sleep_fn=sleep,
            host="127.0.0.1",
            port=7496,
        )
    assert slept == [60.0]
    assert broker.calls == 1


def test_unattended_reconnect_uses_the_same_minute_interval() -> None:
    broker = _DownBroker(fail_times=0)
    slept: list[float] = []
    recover_connection(
        broker,
        unattended=True,
        session_over_fn=lambda: False,
        sleep_fn=slept.append,
        host="127.0.0.1",
        port=7496,
    )
    assert slept == [60.0]
    assert broker.connected is True
