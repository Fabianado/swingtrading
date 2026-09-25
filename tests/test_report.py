from __future__ import annotations

from swingtrading.report.render import render_markdown
from swingtrading.models import Playbook


def test_markdown_contains_exact_instructions(sample_plan, as_of) -> None:
    playbook = Playbook(
        generated_at="2024-06-28T22:00:00Z",
        as_of=as_of,
        skip_ai=True,
        universe_size=10,
        filtered_size=8,
        scored_size=7,
        ai_spent_usd=0.0,
        picks=[sample_plan],
        discarded=[],
        notes="test",
    )
    md = render_markdown(playbook)
    assert sample_plan.symbol in md
    assert f"{sample_plan.entry_price:.2f}" in md
    assert f"{sample_plan.stop_price:.2f}" in md
    assert f"{sample_plan.target_price:.2f}" in md
    assert str(sample_plan.qty) in md
    assert f"{sample_plan.qty} shares" in md
    assert "Shares to trade" in md
    assert "STP" in md or "LMT" in md
    assert "Time stop" in md
