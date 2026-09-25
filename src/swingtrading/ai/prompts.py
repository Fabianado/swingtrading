from __future__ import annotations

from datetime import date

from swingtrading.models import TradePlan


def enrichment_prompt(plan: TradePlan, as_of: date, max_posts: int) -> str:
    side = "long" if plan.side.value == "BUY" else "short"
    return f"""You are a swing-trading research assistant. Analyze {plan.symbol} ({plan.name or plan.symbol}) for the next US cash-equity session after {as_of.isoformat()}.

Quant system already chose a {side} {plan.setup_type.value} setup. Do NOT propose or change prices, entries, stops, targets, or share counts.

Use web_search at most once for the latest company news. Use x_search at most once for X/Twitter sentiment over the last 48 hours. Fetch few posts (hard cap about {max_posts}). Do not analyze images or videos.

Veto only if a material event makes a 1-5 session swing unsafe (imminent earnings, guidance, lawsuit, halt risk, merger close, etc.).

Return JSON only, no markdown:
{{
  "sentiment": <float from -1 to 1>,
  "event_risk": "<none|earnings|litigation|guidance|macro|other>",
  "veto": <true|false>,
  "veto_reason": "<short string>",
  "confidence_delta": <integer from -25 to 25>,
  "summary": "<max 400 characters>",
  "sources": ["<url>", "..."]
}}
"""


EXTRACT_PROMPT = """Extract the JSON object from the analyst notes. Return JSON only with keys:
sentiment, event_risk, veto, veto_reason, confidence_delta, summary, sources.
Do not add prices."""
