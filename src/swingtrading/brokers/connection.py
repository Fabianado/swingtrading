from __future__ import annotations

import logging
import time
from collections.abc import Callable

from swingtrading.brokers.protocol import Broker

logger = logging.getLogger(__name__)

PromptFn = Callable[[str], object]


def connect_with_prompt(
    broker: Broker,
    prompt_fn: PromptFn,
    *,
    retry_seconds: float = 5.0,
    host: str = "127.0.0.1",
    port: int = 7497,
) -> None:
    """Connect to TWS. If it is down, tell the user to start it and retry."""
    if broker.is_connected():
        return
    try:
        broker.connect()
        if broker.is_connected():
            return
    except Exception as exc:  # noqa: BLE001
        logger.warning("TWS connect failed: %s", exc)
        if "event loop" in str(exc).lower():
            raise

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
            time.sleep(max(0.1, retry_seconds))
            message = "Still no TWS connection. Start TWS / LYNX Gateway, then press Enter to retry."


def recover_connection(broker: Broker, *, retry_seconds: float = 5.0) -> None:
    """Block until TWS is reachable again. Used after a mid-session drop."""
    if broker.is_connected():
        return
    logger.warning("TWS connection lost; waiting to resume...")
    while not broker.is_connected():
        try:
            broker.reconnect()
        except Exception as exc:  # noqa: BLE001
            logger.warning("TWS reconnect failed: %s", exc)
            time.sleep(max(0.1, retry_seconds))
    logger.info("TWS connection resumed")
