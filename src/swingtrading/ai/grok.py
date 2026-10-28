from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from typing import Protocol, TypeVar

from swingtrading.ai.cost import CostGuard, estimate_ticker_cost, usage_cost
from swingtrading.ai.prompts import EXTRACT_PROMPT, enrichment_prompt
from swingtrading.config import Settings
from swingtrading.models import AIOverlay, TradePlan

logger = logging.getLogger(__name__)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
_RETRYABLE_STATUSES = {"RESOURCE_EXHAUSTED", "UNAVAILABLE"}
T = TypeVar("T")


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
        try:
            text, in_tok, out_tok, tools = self._search_call(prompt)
        except Exception as exc:
            if not is_retryable_grok_error(exc):
                raise
            logger.error(
                "Grok still unavailable for %s after %s attempts (%s). Overlay skipped.",
                plan.symbol,
                self.settings.grok_retry_attempts,
                grok_status_name(exc),
            )
            return AIOverlay(
                sentiment=0.0,
                event_risk="none",
                veto=False,
                summary="Grok unavailable after retries; overlay skipped.",
            )
        overlay = parse_overlay(text)
        if overlay is None:
            try:
                text2, in2, out2, tools2 = self._extract_call(text)
            except Exception as exc:
                if not is_retryable_grok_error(exc):
                    raise
                logger.error(
                    "Grok still unavailable for %s after %s attempts (%s). Overlay skipped.",
                    plan.symbol,
                    self.settings.grok_retry_attempts,
                    grok_status_name(exc),
                )
                return AIOverlay(
                    sentiment=0.0,
                    event_risk="none",
                    veto=False,
                    summary="Grok unavailable after retries; overlay skipped.",
                )
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
        def once() -> tuple[str, int, int, int]:
            chat = self._sdk().chat.create(model=self.settings.grok_model, tools=tools)
            chat.append(user(prompt))
            response = chat.sample()
            return _content(response), *_tokens(response), 2

        return call_with_retry(
            once,
            attempts=int(self.settings.grok_retry_attempts),
            wait_seconds=float(self.settings.grok_retry_seconds),
        )

    def _extract_call(self, notes: str) -> tuple[str, int, int, int]:
        from xai_sdk.chat import user

        def once() -> tuple[str, int, int, int]:
            chat = self._sdk().chat.create(model=self.settings.grok_model)
            chat.append(user(f"{EXTRACT_PROMPT}\n\nNOTES:\n{notes[:12000]}"))
            response = chat.sample()
            return _content(response), *_tokens(response), 0

        return call_with_retry(
            once,
            attempts=int(self.settings.grok_retry_attempts),
            wait_seconds=float(self.settings.grok_retry_seconds),
        )


def grok_status_name(exc: BaseException) -> str:
    code = getattr(exc, "code", None)
    if callable(code):
        try:
            status = code()
            name = getattr(status, "name", None)
            if name:
                return str(name)
        except Exception:  # noqa: BLE001
            pass
    text = str(exc).upper()
    for name in _RETRYABLE_STATUSES:
        if name in text:
            return name
    return exc.__class__.__name__


def is_retryable_grok_error(exc: BaseException) -> bool:
    """True for model capacity and transient unavailability, not for billing or bad requests."""
    return grok_status_name(exc) in _RETRYABLE_STATUSES


def call_with_retry(
    fn: Callable[[], T],
    *,
    attempts: int = 4,
    wait_seconds: float = 60.0,
    sleep_fn: Callable[[float], object] = time.sleep,
) -> T:
    """Call fn again after capacity or rate-limit errors. The wait grows by one minute each time."""
    tries = max(1, int(attempts))
    last: BaseException | None = None
    for attempt in range(1, tries + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            if not is_retryable_grok_error(exc) or attempt == tries:
                raise
            last = exc
            delay = float(wait_seconds) * attempt
            logger.warning(
                "Grok call refused (%s). Retry %s/%s in %.0f seconds.",
                grok_status_name(exc),
                attempt,
                tries - 1,
                delay,
            )
            sleep_fn(delay)
    assert last is not None
    raise last


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
