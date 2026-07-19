"""Search use-case truth store — which search provider serves each use-case.

Mirrors the Model use-case store (``providers/use_cases.py``) for the Search entity.
There is ONE store: ``~/.personalclaw/active_search_providers.json``, mapping a
use-case to the active provider name(s). The Settings → Search picker and what
:func:`resolve_search_provider_for_use_case` resolves both read it, so they never
disagree.

A search provider ref is a bare provider name (``"tavily"``) — unlike a model ref
there is no ``provider:model_id`` sub-selection, because a search provider IS the
unit. An unbound use-case falls back to ``search-general``.
"""

import json
import logging
from pathlib import Path

from personalclaw.atomic_write import atomic_write

logger = logging.getLogger(__name__)


# ── Canonical Search use-case vocabulary ─────────────────────────────────────
#   search-general    — default web search
#   search-news       — recency-biased (prefers a supports_recency provider)
#   search-financial  — domain/source-biased (a future specialist)
#   fetch-article     — single-URL content extraction (prefers a supports_fetch
#                       provider, else the native fetch pipeline §4 handles it)
# Extensible exactly like Model's use-cases (stt/tts/ocr grew the same way).
SEARCH_USE_CASES: tuple[str, ...] = (
    "search-general",
    "search-news",
    "search-financial",
    "fetch-article",
)
VALID_SEARCH_USE_CASES = frozenset(SEARCH_USE_CASES)

# The use-case every other one falls back to when unbound.
DEFAULT_SEARCH_USE_CASE = "search-general"


# ── Active-provider store (active_search_providers.json) ──────────────────────


def _active_path() -> Path:
    from personalclaw.config.loader import config_dir
    return config_dir() / "active_search_providers.json"


def load_active_search_providers() -> dict[str, list[str]]:
    """Active search-provider selection(s) per use-case. ``{}`` when none set."""
    path = _active_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    # Normalize every value to a list[str] (a single name may be stored bare).
    out: dict[str, list[str]] = {}
    for use_case, refs in data.items():
        if isinstance(refs, str):
            out[use_case] = [refs]
        elif isinstance(refs, list):
            out[use_case] = [str(r) for r in refs if r]
    return out


def save_active_search_providers(active: dict[str, list[str]]) -> None:
    path = _active_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(active, indent=2) + "\n")


def active_search_provider_names(use_case: str) -> list[str]:
    """Active provider name(s) for ``use_case``, applying the general fallback.

    An unbound non-general use-case borrows the ``search-general`` binding.
    Returns ``[]`` when nothing is bound (the resolver then falls back to any
    available provider).
    """
    active = load_active_search_providers()
    refs = active.get(use_case)
    if not refs and use_case != DEFAULT_SEARCH_USE_CASE:
        refs = active.get(DEFAULT_SEARCH_USE_CASE)
    return list(refs) if isinstance(refs, list) else []


def set_active_search_provider(use_case: str, provider_name: str) -> None:
    """Bind a provider to a use-case (single active provider per use-case)."""
    if use_case not in VALID_SEARCH_USE_CASES:
        raise ValueError(f"Invalid search use case: {use_case!r}")
    active = load_active_search_providers()
    if provider_name:
        active[use_case] = [provider_name]
    else:
        active.pop(use_case, None)
    save_active_search_providers(active)
