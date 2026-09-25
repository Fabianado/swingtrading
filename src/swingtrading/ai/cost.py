from __future__ import annotations

# grok-4.3 list prices (USD / 1M tokens) and xAI server-side tool fees.
INPUT_PER_MILLION = 1.25
OUTPUT_PER_MILLION = 2.50
TOOL_CALL_USD = 0.005  # $5 / 1k calls
POST_USD = 0.005  # $5 / 1k posts after 21 Sep 2026
ESTIMATE_INPUT_TOKENS = 8_000
ESTIMATE_OUTPUT_TOKENS = 2_500


def estimate_ticker_cost(max_posts: int = 30) -> float:
    tools = 2 * TOOL_CALL_USD
    posts = max_posts * POST_USD
    tokens = (
        ESTIMATE_INPUT_TOKENS / 1_000_000 * INPUT_PER_MILLION
        + ESTIMATE_OUTPUT_TOKENS / 1_000_000 * OUTPUT_PER_MILLION
    )
    return tools + posts + tokens


def usage_cost(input_tokens: int, output_tokens: int, tool_calls: int = 0, posts: int = 0) -> float:
    return (
        input_tokens / 1_000_000 * INPUT_PER_MILLION
        + output_tokens / 1_000_000 * OUTPUT_PER_MILLION
        + tool_calls * TOOL_CALL_USD
        + posts * POST_USD
    )


class CostGuard:
    def __init__(self, budget_usd: float) -> None:
        self.budget_usd = budget_usd
        self.spent_usd = 0.0
        self.skipped = 0

    def can_afford(self, estimate: float) -> bool:
        return self.spent_usd + estimate <= self.budget_usd + 1e-9

    def add(self, amount: float) -> None:
        self.spent_usd += max(0.0, amount)
