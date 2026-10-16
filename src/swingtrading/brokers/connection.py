from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from swingtrading.brokers.protocol import Broker
from swingtrading.data.rth import cash_session_over

logger = logging.getLogger(__name__)

PromptFn = Callable[[str], object]
SessionOverFn = Callable[[], bool]
SleepFn = Callable[[float], object]

UNATTENDED_RETRY_SECONDS = 60.0


def unattended_tws() -> bool:
    """True when a timer run should retry TWS without waiting for Enter."""
    return os.environ.get("SWINGTRADING_UNATTENDED", "").strip().lower() in {"1", "true", "yes"}


def session_over_checker(settings, now_fn: Callable[[], datetime] | None = None) -> SessionOverFn:
    zone = ZoneInfo(settings.rth_timezone)
    clock = now_fn or (lambda: datetime.now(zone))

    def over() -> bool:
        return cash_session_over(
            clock(),
            timezone=settings.rth_timezone,
            hour=int(settings.rth_close_hour),
            minute=int(settings.rth_close_minute),
        )

    return over


def _fatal_connect_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "event loop" in text or "handshake" in text or "accept incoming" in text


def _give_up(host: str, port: int) -> None:
    logger.error(
        "TWS is not running at %s:%s. The cash session is over; giving up.",
        host,
        port,
    )
    raise ConnectionError(f"TWS is not running at {host}:{port}. The cash session is over; giving up.")


def connect_with_prompt(
    broker: Broker,
    prompt_fn: PromptFn,
    *,
    retry_seconds: float = 5.0,
    host: str = "127.0.0.1",
    port: int = 7497,
    unattended: bool | None = None,
    session_over_fn: SessionOverFn | None = None,
    sleep_fn: SleepFn | None = None,
) -> None:
    """Connect to TWS. If it is down, tell the user to start it and retry.

    An unattended run logs the failure and retries once a minute until the
    cash session is over, then gives up.
    """
    if broker.is_connected():
        return
    auto = unattended_tws() if unattended is None else unattended
    pause = sleep_fn or time.sleep
    try:
        broker.connect()
        if broker.is_connected():
            return
    except Exception as exc:  # noqa: BLE001
        logger.warning("TWS connect failed: %s", exc)
        if _fatal_connect_error(exc):
            raise

    if auto:
        _retry_until_session_end(
            lambda: broker.connect(),
            connected=broker.is_connected,
            host=host,
            port=port,
            session_over_fn=session_over_fn,
            sleep_fn=pause,
            waiting="TWS is not running",
        )
        return

    message = (
        f"Could not connect to TWS / LYNX Gateway at {host}:{port}. "
        "Start Trader Workstation or LYNX Gateway, enable the socket API "
        "(Global Configuration → API → Settings), trust 127.0.0.1, then press Enter "
        "to retry (Ctrl-C to abort)."
    )
    while not broker.is_connected():
        prompt_fn(message)
        try:
            broker.connect()
        except Exception as exc:  # noqa: BLE001
            logger.warning("TWS connect failed: %s", exc)
            if _fatal_connect_error(exc):
                raise
            time.sleep(max(0.1, retry_seconds))
            message = "Still no TWS connection. Start TWS / LYNX Gateway, then press Enter to retry."


def recover_connection(
    broker: Broker,
    *,
    retry_seconds: float = 5.0,
    host: str = "127.0.0.1",
    port: int = 7497,
    unattended: bool | None = None,
    session_over_fn: SessionOverFn | None = None,
    sleep_fn: SleepFn | None = None,
) -> None:
    """Block until TWS is reachable again. Used after a mid-session drop."""
    if broker.is_connected():
        return
    auto = unattended_tws() if unattended is None else unattended
    if auto:
        logger.warning("TWS connection lost at %s:%s.", host, port)
        _retry_until_session_end(
            lambda: broker.reconnect(),
            connected=broker.is_connected,
            host=host,
            port=port,
            session_over_fn=session_over_fn,
            sleep_fn=sleep_fn or time.sleep,
            waiting="TWS is not running",
        )
        logger.info("TWS connection resumed")
        return

    logger.warning("TWS connection lost; waiting to resume...")
    pause = sleep_fn or time.sleep
    while not broker.is_connected():
        try:
            broker.reconnect()
        except Exception as exc:  # noqa: BLE001
            logger.warning("TWS reconnect failed: %s", exc)
            pause(max(0.1, retry_seconds))
    logger.info("TWS connection resumed")


def _retry_until_session_end(
    attempt: Callable[[], object],
    *,
    connected: Callable[[], bool],
    host: str,
    port: int,
    session_over_fn: SessionOverFn | None,
    sleep_fn: SleepFn,
    waiting: str,
) -> None:
    over = session_over_fn or _default_session_over
    while not connected():
        if over():
            _give_up(host, port)
        logger.warning("%s at %s:%s. Retrying in 60 seconds.", waiting, host, port)
        sleep_fn(UNATTENDED_RETRY_SECONDS)
        if over():
            _give_up(host, port)
        try:
            attempt()
        except Exception as exc:  # noqa: BLE001
            logger.warning("TWS connect failed: %s", exc)
            if _fatal_connect_error(exc):
                raise


def _default_session_over() -> bool:
    from swingtrading.config import Settings

    settings = Settings()
    return session_over_checker(settings)()
