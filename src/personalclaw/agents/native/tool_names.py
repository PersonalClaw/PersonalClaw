"""Healing the one tool-name hop the runtime does not control: a provider's model-safe rewrite.

Model-name-constraint sanitizer (provider-agnostic). Several providers must rewrite tool names to
satisfy a naming constraint before sending them to the model — e.g. Bedrock Converse rejects "/"
and caps names at 64 chars, matching the common OpenAI ``^[a-zA-Z0-9_-]{1,64}$`` shape. Providers
reverse-map the name the model returns back to the real tool id, but if that round-trip ever fails
(a name not in the turn's reverse map, or the model echoing the rewritten form) the sanitized name
reaches dispatch and every exact-key lookup misses. The native runtime keeps a sanitized(real)->real
fallback built here, so it can heal ANY provider. The wire map is
``docs/architecture/tool-name-wire.md``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_TOOL_NAME_SANITIZE_RE = re.compile(r"[^a-zA-Z0-9_-]")
_TOOL_NAME_SANITIZE_MAX = 64


def _sanitized_tool_key(name: str) -> str:
    """Return the common model-safe form of ``name`` (illegal chars -> ``_``,
    capped at 64). Mirrors the constraint providers like Bedrock apply so the
    runtime can recognize a rewritten name and map it back to the real tool."""
    safe = _TOOL_NAME_SANITIZE_RE.sub("_", name or "")[:_TOOL_NAME_SANITIZE_MAX]
    return safe or "tool"


def build_sanitized_index(names: Iterable[str]) -> tuple[dict[str, str], dict[str, list[str]]]:
    """The sanitized(real)->real healing map for a tool census, plus its losses.

    For each real name a provider WOULD have to rewrite, remember the rewritten
    form -> real name, but ONLY when that sanitized form is unique across the
    census (an ambiguous collision is dropped so we never dispatch the wrong
    tool) and only when it differs from the real name (an already-legal name
    needs no fallback). A key that would shadow a REAL exact name is likewise
    never remapped.

    Returns ``(healing_map, collisions)`` where ``collisions`` maps each dropped
    sanitized key to the real names that fought over it — the ONE lossy spot in
    the whole name wire (see docs/architecture/tool-name-wire.md), surfaced so a
    caller can report it instead of losing tools silently. SM-12's census rail
    (tests/test_tool_name_wire_fidelity.py) keeps the live census collision-free,
    which is what makes every transform on the wire reversible in practice.
    """
    census = set(names)
    healing: dict[str, str] = {}
    collisions: dict[str, list[str]] = {}
    for real in census:
        key = _sanitized_tool_key(real)
        if key == real or key in census:
            # Legal already, or would shadow a real exact name — never remap.
            continue
        if key in collisions:
            collisions[key].append(real)
            continue
        if key in healing and healing[key] != real:
            collisions[key] = [healing.pop(key), real]
            continue
        healing[key] = real
    return healing, {k: sorted(v) for k, v in collisions.items()}
