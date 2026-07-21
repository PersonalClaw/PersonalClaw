"""Structured insight extraction for knowledge items.

A cross-cutting intelligence layer that runs over any item's content (regardless
of which provider stored it) and produces a category-keyed ``insights`` blob —
a concise summary plus key points, topics, and action items. The category keys
match what the typed-item UI renders as labeled rows.
"""

import json
import re
from typing import TYPE_CHECKING

from personalclaw.security import redact_credentials, redact_exfiltration_urls

if TYPE_CHECKING:
    from personalclaw.knowledge.llm_pool import LLMPool


def _insights_prompt(content: str) -> str:
    """Render the insights-extraction prompt (bundled ``task-knowledge-insights``,
    bindable in Settings → Prompts) for a piece of content.

    The content is INGESTED external material (a scraped page, an uploaded doc) — fence
    it so a prompt-injection in the source ("ignore the above; output …") is analysed as
    data, not obeyed by the insights model."""
    from personalclaw.prompt_providers.runtime import render_use_case_prompt
    from personalclaw.security import fence_untrusted

    return (
        render_use_case_prompt(
            "knowledge_insights", {"content": fence_untrusted(content, source="ingested")}
        )
        or ""
    )


INSIGHTS_TIMEOUT = 180.0

# Category order + shape used when serializing/validating; mirrors the keys the
# typed-item UI knows how to render.
_LIST_KEYS = ("key_points", "topics", "action_items")


def _redact(text: str | None) -> str:
    if not text:
        return ""
    text, _ = redact_exfiltration_urls(text)
    text, _ = redact_credentials(text)
    return text


class InsightsExtractor:
    """Produces a category-keyed insights dict from an item's content via the LLM pool."""

    def __init__(self, pool: "LLMPool | None" = None, *, max_chars: int = 12000):
        self._pool = pool
        self._max_chars = max_chars

    async def extract(self, content: str, *, raise_on_error: bool = False) -> dict:
        """Return ``{summary, key_points, topics, action_items}``; ``{}`` on failure.

        By default a model/transport error is swallowed to ``{}`` (callers that just
        want best-effort insights). ``raise_on_error=True`` re-raises it so the ingest
        runner can tell "model unavailable" apart from "nothing extracted" and mark the
        item ``partial`` instead of silently leaving it ``done`` with stale insights."""
        if not self._pool or not (content or "").strip():
            return {}
        try:
            prompt = _insights_prompt(content[: self._max_chars])
            response = await self._pool.send(prompt, timeout=INSIGHTS_TIMEOUT)
            return self._parse(response)
        except Exception:
            if raise_on_error:
                raise
            return {}

    def _parse(self, response: str) -> dict:
        data = self._loads(response)
        if not isinstance(data, dict):
            return {}
        out: dict[str, object] = {}
        title = _redact(str(data.get("title") or "")).strip().rstrip(".")
        if title:
            out["title"] = title[:120]
        summary = _redact(str(data.get("summary") or "")).strip()
        if summary:
            out["summary"] = summary
        for key in _LIST_KEYS:
            raw = data.get(key)
            if not isinstance(raw, list):
                continue
            items = [_redact(str(v)).strip() for v in raw]
            items = [v for v in items if v]
            if items:
                out[key] = items[:6]
        return out

    @staticmethod
    def _loads(response: str) -> object:
        for text in (response, _code_block(response)):
            if text:
                try:
                    return json.loads(text)
                except (json.JSONDecodeError, ValueError):
                    pass
        m = re.search(r"\{[\s\S]*\}", response or "")
        if m:
            try:
                return json.loads(m.group())
            except (json.JSONDecodeError, ValueError):
                pass
        return None


def _code_block(response: str) -> str | None:
    m = re.search(r"```(?:json)?\s*\n?([\s\S]*?)```", response or "")
    return m.group(1).strip() if m else None
