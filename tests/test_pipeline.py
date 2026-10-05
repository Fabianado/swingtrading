from __future__ import annotations

import pytest

from swingtrading.ai.cost import CostGuard
from swingtrading.ai.grok import AIOverlay
from swingtrading.brokers.ibkr import IBKRRetailBroker, ensure_asyncio_loop
from swingtrading.pipeline import run_playbook


class VetoAAA:
    def enrich(self, plan, as_of, guard: CostGuard) -> AIOverlay:
        guard.add(0.01)
        if plan.symbol == "AAA":
            return AIOverlay(
                sentiment=-0.8,
                event_risk="litigation",
                veto=True,
                veto_reason="headline lawsuit",
                confidence_delta=-20,
                summary="Lawsuit headline.",
            )
        return AIOverlay(sentiment=0.1, event_risk="none", veto=False, confidence_delta=3, summary="Clean.")


def test_pipeline_skip_ai_writes_ranked_picks(ohlcv, constituents, earnings, settings) -> None:
    playbook = run_playbook(
        settings,
        skip_ai=True,
        refresh=False,
        constituents=constituents,
        ohlcv=ohlcv,
        earnings=earnings,
    )
    assert playbook.skip_ai is True
    assert 1 <= len(playbook.picks) <= 5
    sectors: dict[str, int] = {}
    for plan in playbook.picks:
        assert plan.qty >= 1
        assert plan.entry_price > 0
        assert plan.stop_price != plan.entry_price
        sectors[plan.sector] = sectors.get(plan.sector, 0) + 1
        assert sectors[plan.sector] <= 2
    symbols = [p.symbol for p in playbook.picks]
    assert "EEE" not in symbols
    assert "FFF" not in symbols
    conf = [p.confidence for p in playbook.picks]
    assert conf == sorted(conf, reverse=True)


def test_pipeline_honors_ai_veto(ohlcv, constituents, earnings, settings) -> None:
    playbook = run_playbook(
        settings,
        skip_ai=False,
        refresh=False,
        constituents=constituents,
        ohlcv=ohlcv,
        earnings=earnings,
        sentiment=VetoAAA(),
    )
    assert all(p.symbol != "AAA" for p in playbook.picks)
    assert any(p.symbol == "AAA" and p.ai and p.ai.veto for p in playbook.discarded)


def test_place_bracket_refuses_negative_target(settings, sample_plan) -> None:
    from swingtrading.models import Side

    bad = sample_plan.model_copy(
        update={
            "side": Side.SELL,
            "entry_price": 11.58,
            "stop_price": 20.02,
            "target_price": -5.2898,
        }
    )
    broker = IBKRRetailBroker(settings)
    with pytest.raises(ValueError, match="profit-taker"):
        broker.place_bracket(bad)


def test_ibkr_broker_starts_disconnected(settings) -> None:
    broker = IBKRRetailBroker(settings)
    assert broker.is_connected() is False


def test_ensure_asyncio_loop_is_current() -> None:
    import asyncio

    loop = ensure_asyncio_loop()
    assert loop is asyncio.get_event_loop()
    assert loop is ensure_asyncio_loop()
    assert not loop.is_closed()
    # ib_insync's getLoop() is what failed on Python 3.12+ without this.
    from ib_insync.util import getLoop

    assert getLoop() is loop


def test_probe_tws_fails_fast_on_closed_port() -> None:
    from swingtrading.brokers.ibkr import probe_tws

    with pytest.raises(ConnectionError, match="Nothing is accepting sockets"):
        probe_tws("127.0.0.1", 1, timeout=0.3)


def test_connect_fails_fast_when_tws_is_down(settings) -> None:
    settings.tws_host = "127.0.0.1"
    settings.tws_port = 1
    settings.tws_connect_timeout = 1.0
    broker = IBKRRetailBroker(settings)
    with pytest.raises(ConnectionError, match="Nothing is accepting sockets"):
        broker.connect()
