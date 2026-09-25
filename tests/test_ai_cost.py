from __future__ import annotations

from datetime import date

from swingtrading.ai.cost import CostGuard, estimate_ticker_cost
from swingtrading.ai.grok import NullSentimentClient, apply_overlay, parse_overlay
from swingtrading.models import AIOverlay


def test_parse_overlay_from_prose() -> None:
    text = """Here you go
    {
      "sentiment": 0.4,
      "event_risk": "none",
      "veto": false,
      "veto_reason": "",
      "confidence_delta": 5,
      "summary": "Quiet tape, no event risk.",
      "sources": ["https://example.com"]
    }
    """
    overlay = parse_overlay(text)
    assert overlay is not None
    assert overlay.sentiment == 0.4
    assert overlay.confidence_delta == 5
    assert overlay.sources[0].startswith("https://")


def test_apply_overlay_clamps_and_does_not_change_prices(sample_plan) -> None:
    overlay = AIOverlay(
        sentiment=-0.2,
        event_risk="macro",
        veto=False,
        confidence_delta=80,
        summary="boost",
    )
    updated = apply_overlay(sample_plan, overlay)
    assert updated.entry_price == sample_plan.entry_price
    assert updated.stop_price == sample_plan.stop_price
    assert updated.target_price == sample_plan.target_price
    assert updated.confidence == 100.0


def test_cost_guard_blocks_when_budget_too_small(sample_plan) -> None:
    guard = CostGuard(budget_usd=0.01)
    assert not guard.can_afford(estimate_ticker_cost(30))
    overlay = NullSentimentClient().enrich(sample_plan, date(2024, 6, 28), guard)
    assert overlay.summary.startswith("AI overlay skipped")
