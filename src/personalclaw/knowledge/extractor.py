"""Entity extraction using the LLM pool."""

import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from personalclaw.knowledge.llm_pool import LLMPool


def _extraction_prompt(chunk: str) -> str:
    """Render the entity-extraction prompt (bundled ``task-knowledge-extraction``,
    bindable in Settings → Prompts) for a text chunk."""
    from personalclaw.prompt_providers.runtime import render_use_case_prompt

    return render_use_case_prompt("knowledge_extraction", {"chunk": chunk}) or ""


EXTRACTION_TIMEOUT = 180.0

# Cap the text sent to the extraction LLM. A large document (the item's full consolidated
# text) would otherwise flood the prompt — blowing the model's context window (→ an error
# the caller turns into an empty graph) or running needlessly slow/costly. Entities live
# mostly in the leading sections (title/abstract/key content), so the head is what matters.
# Matches InsightsExtractor's 12000-char cap so both enrichment LLM calls see the same slice.
_MAX_CHARS = 12000


def _empty_result() -> dict:
    return {"title": "", "entities": [], "relations": [], "category": "document", "summary": ""}


class EntityExtractor:
    def __init__(self, pool: "LLMPool | None" = None):
        self._pool = pool

    async def extract(self, chunk: str) -> dict:
        if not self._pool or not chunk.strip():
            return _empty_result()
        try:
            prompt = _extraction_prompt(chunk[:_MAX_CHARS])
            response = await self._pool.send(prompt, timeout=EXTRACTION_TIMEOUT)
            return self._parse_response(response)
        except Exception:
            return _empty_result()

    async def extract_batch(self, chunks: list[str]) -> list[dict]:
        """Extract from multiple chunks in parallel using the pool."""
        if not self._pool or not chunks:
            return [_empty_result() for _ in chunks]
        non_empty_indices = [i for i, c in enumerate(chunks) if c.strip()]
        prompts = [_extraction_prompt(chunks[i][:_MAX_CHARS]) for i in non_empty_indices]
        try:
            responses = await self._pool.send_batch(prompts, timeout=EXTRACTION_TIMEOUT)
            results = [_empty_result() for _ in chunks]
            for idx, response in zip(non_empty_indices, responses):
                results[idx] = self._parse_response(response)
            return results
        except Exception:
            return [_empty_result() for _ in chunks]

    def _parse_response(self, response: str) -> dict:
        for text in (response, self._extract_code_block(response)):
            if text:
                try:
                    data = json.loads(text)
                    return self._validate(data)
                except (json.JSONDecodeError, ValueError):
                    pass
        m = re.search(r"\{[\s\S]*\}", response)
        if m:
            try:
                data = json.loads(m.group())
                return self._validate(data)
            except (json.JSONDecodeError, ValueError):
                pass
        return _empty_result()

    @staticmethod
    def _extract_code_block(response: str) -> str | None:
        m = re.search(r"```(?:json)?\s*\n?([\s\S]*?)```", response)
        return m.group(1).strip() if m else None

    @staticmethod
    def _normalize_entities(raw) -> list[dict]:
        """Coerce the model's entity list into well-formed dicts. The LLM sometimes
        returns bare strings (``["MongoDB", "Redis"]``) instead of objects; without
        normalization a downstream ``ent.get("name")`` raises on the string and the broad
        except drops the WHOLE item's graph (valid entities included). Bare strings become
        ``{"name": str}``; non-dict/non-str junk is dropped; unnamed dicts are dropped."""
        out: list[dict] = []
        if not isinstance(raw, list):
            return out
        for e in raw:
            if isinstance(e, str):
                name = e.strip()
                if name:
                    out.append({"name": name})
            elif isinstance(e, dict) and str(e.get("name") or "").strip():
                out.append(e)
        return out

    @staticmethod
    def _normalize_relations(raw) -> list[dict]:
        """Keep only relation dicts with a source + target (the only shape the graph
        writer can use); drop bare strings / malformed entries."""
        out: list[dict] = []
        if not isinstance(raw, list):
            return out
        for r in raw:
            if (
                isinstance(r, dict)
                and str(r.get("source") or "").strip()
                and str(r.get("target") or "").strip()
            ):
                out.append(r)
        return out

    @classmethod
    def _validate(cls, data: dict) -> dict:
        return {
            "title": data.get("title", ""),
            "entities": cls._normalize_entities(data.get("entities", [])),
            "relations": cls._normalize_relations(data.get("relations", [])),
            "category": data.get("category", "document"),
            "summary": data.get("summary", ""),
        }
