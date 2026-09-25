from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Protocol

from swingtrading.ai.cost import CostGuard, estimate_ticker_cost, usage_cost
from swingtrading.ai.prompts import EXTRACT_PROMPT, enrichment_prompt
from swingtrading.config import Settings
from swingtrading.models import AIOverlay, TradePlan

logger = logging.getLogger(__name__)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


class SentimentClient(Protocol):
    def enrich(self, plan: TradePlan, as_of: date, guard: CostGuard) -> AIOverlay: ...


class NullSentimentClient:
    def enrich(self, plan: TradePlan, as_of: date, guard: CostGuard) -> AIOverlay:
        return AIOverlay(
            sentiment=0.0,
            event_risk="none",
            veto=False,
            summary="AI overlay skipped.",
        )


class GrokClient:
    def __init__(self, settings: Settings) -> None:
        if not settings.xai_api_key:
            raise RuntimeError("XAI_API_KEY is required unless you pass --skip-ai")
        self.settings = settings
        self._client = None

    def _sdk(self):
        if self._client is None:
            from xai_sdk import Client

            self._client = Client(api_key=self.settings.xai_api_key)
        return self._client

    def enrich(self, plan: TradePlan, as_of: date, guard: CostGuard) -> AIOverlay:
        estimate = estimate_ticker_cost(self.settings.x_search_max_posts)
        if not guard.can_afford(estimate):
            guard.skipped += 1
            logger.warning("AI budget exhausted; skipping Grok for %s", plan.symbol)
            return AIOverlay(
                sentiment=0.0,
                event_risk="none",
                veto=False,
                summary="AI skipped: remaining budget too small.",
            )
        guard.add(estimate)
        prompt = enrichment_prompt(plan, as_of, self.settings.x_search_max_posts)
        text, in_tok, out_tok, tools = self._search_call(prompt)
        overlay = parse_overlay(text)
        if overlay is None:
            text2, in2, out2, tools2 = self._extract_call(text)
            in_tok += in2
            out_tok += out2
            tools += tools2
            overlay = parse_overlay(text2)
        if overlay is None:
            overlay = AIOverlay(
                sentiment=0.0,
                event_risk="other",
                veto=False,
                summary="Grok response was not valid JSON; overlay ignored.",
            )
        actual = usage_cost(in_tok, out_tok, tool_calls=tools, posts=self.settings.x_search_max_posts)
        if actual > estimate:
            guard.add(actual - estimate)
        return overlay

    def _search_call(self, prompt: str) -> tuple[str, int, int, int]:
        from xai_sdk.chat import user
        from xai_sdk.tools import web_search

        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=self.settings.x_search_lookback_hours)
        tools = [web_search(), _x_search_tool(start, end)]
        chat = self._sdk().chat.create(model=self.settings.grok_model, tools=tools)
        chat.append(user(prompt))
        response = chat.sample()
        text = _content(response)
        in_tok, out_tok = _tokens(response)
        return text, in_tok, out_tok, 2

    def _extract_call(self, notes: str) -> tuple[str, int, int, int]:
        from xai_sdk.chat import user

        chat = self._sdk().chat.create(model=self.settings.grok_model)
        chat.append(user(f"{EXTRACT_PROMPT}\n\nNOTES:\n{notes[:12000]}"))
        response = chat.sample()
        return _content(response), *_tokens(response), 0


def parse_overlay(text: str) -> AIOverlay | None:
    if not text:
        return None
    match = _JSON_RE.search(text.strip())
    if not match:
        return None
    try:
        raw = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    try:
        sentiment = float(raw.get("sentiment", 0.0))
        delta = float(raw.get("confidence_delta", 0.0))
        sources = raw.get("sources") or []
        if not isinstance(sources, list):
            sources = [str(sources)]
        return AIOverlay(
            sentiment=max(-1.0, min(1.0, sentiment)),
            event_risk=str(raw.get("event_risk", "none"))[:40],
            veto=bool(raw.get("veto", False)),
            veto_reason=str(raw.get("veto_reason", ""))[:400],
            confidence_delta=max(-25.0, min(25.0, delta)),
            summary=str(raw.get("summary", ""))[:400],
            sources=[str(s) for s in sources][:8],
        )
    except (TypeError, ValueError):
        return None


def apply_overlay(plan: TradePlan, overlay: AIOverlay) -> TradePlan:
    confidence = max(0.0, min(100.0, plan.quant_score + overlay.confidence_delta))
    rationale = plan.rationale
    if overlay.summary:
        rationale = f"{plan.rationale} | AI: {overlay.summary}"
    if overlay.veto and overlay.veto_reason:
        rationale = f"{rationale} | VETO: {overlay.veto_reason}"
    sources = list(plan.sources) + [s for s in overlay.sources if s not in plan.sources]
    return plan.model_copy(
        update={
            "confidence": round(confidence, 2),
            "rationale": rationale,
            "sources": sources,
            "ai": overlay,
        }
    )


def _content(response: object) -> str:
    for attr in ("content", "text"):
        value = getattr(response, attr, None)
        if isinstance(value, str) and value.strip():
            return value
    return str(response)


def _tokens(response: object) -> tuple[int, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return ESTIMATE_FALLBACK
    in_tok = int(getattr(usage, "prompt_tokens", None) or getattr(usage, "input_tokens", 0) or 0)
    out_tok = int(
        getattr(usage, "completion_tokens", None)
        or getattr(usage, "output_tokens", 0)
        or 0
    )
    return in_tok, out_tok


ESTIMATE_FALLBACK = (8000, 2500)


def _x_search_tool(start: datetime, end: datetime):
    from xai_sdk.tools import x_search

    for kwargs in (
        {
            "from_date": start,
            "to_date": end,
            "enable_image_understanding": False,
            "enable_video_understanding": False,
        },
        {"from_date": start, "to_date": end},
        {},
    ):
        try:
            return x_search(**kwargs)
        except TypeError:
            continue
    return x_search()
