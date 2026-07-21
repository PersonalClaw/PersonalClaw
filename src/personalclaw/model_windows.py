"""Shared model → context-window lookup (``model_tokens.json``).

ONE reader for the model-context-window table that the provider adapters
(anthropic/openai/bedrock) each also load. Used by the adaptive memory-injection
budget (mem-adaptive-budget) to scale per-section caps to the resolved model's
window instead of hardcoding, and resolvable standalone (no provider instance).
"""

from __future__ import annotations

import json
from pathlib import Path

# Absent-model fallback — the conservative floor the provider adapters also use.
DEFAULT_CONTEXT_WINDOW = 200_000

_TOKENS_FILE = Path(__file__).resolve().parent / "model_tokens.json"
_WINDOWS: dict[str, int] | None = None


def _load() -> dict[str, int]:
    global _WINDOWS
    if _WINDOWS is None:
        try:
            with open(_TOKENS_FILE, encoding="utf-8") as fp:
                _WINDOWS = {
                    k: int(v)
                    for k, v in json.load(fp).items()
                    if not k.startswith("_") and isinstance(v, (int, float))
                }
        except (OSError, ValueError, json.JSONDecodeError):
            _WINDOWS = {}
    return _WINDOWS


def model_context_window(model_id: str | None, default: int = DEFAULT_CONTEXT_WINDOW) -> int:
    """Context window (tokens) for ``model_id`` → its entry, else a suffix/prefix
    match (handles provider-prefixed ids like ``Bedrock:global.anthropic.claude-
    opus-4-8`` and dated variants), else ``default``. ``default`` lets a provider
    keep its own absent-model fallback (OpenAI 128k vs Anthropic/Bedrock 200k)."""
    if not model_id:
        return default
    windows = _load()
    mid = model_id.strip()
    if mid in windows:
        return windows[mid]
    # Strip a "Provider:" / "Provider/" qualifier and re-check the bare id.
    for sep in (":", "/"):
        if sep in mid:
            bare = mid.split(sep, 1)[1]
            if bare in windows:
                return windows[bare]
            mid = bare
    # Loose containment match (a dated/suffixed id contains a catalog key, e.g.
    # "global.anthropic.claude-opus-4-8" ⊃ "claude-opus-4.8"-ish). Normalize dots
    # vs dashes so "4-8" and "4.8" reconcile. Longest key wins (most specific).
    norm = mid.replace(".", "-").lower()
    best = 0
    for k, v in windows.items():
        kn = k.replace(".", "-").lower()
        if (kn in norm or norm in kn) and len(kn) > best:
            best = len(kn)
            match = v
    return match if best else default


def active_chat_model_window() -> int:
    """The context window of the model bound to the ``chat`` use-case (Settings →
    Models), or the default. Lets a context builder scale its budget to the model
    actually in use without threading the id through every call site."""
    try:
        from personalclaw.providers.use_cases import active_model_refs

        refs = active_model_refs("chat")
        if refs:
            return model_context_window(refs[0])
    except Exception:
        pass
    return DEFAULT_CONTEXT_WINDOW
