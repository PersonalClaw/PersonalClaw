"""Shared prompt blocks — the conventions library templates reference instead of duplicating.

Six templates independently defined the Finding record three times. That is the shape of the
problem: a convention repeated by hand drifts silently, and drift in a Finding record breaks
things far from the text — a gate predicate like "no open Critical" stops being meaningful once
one stage grades on a different ladder, and the Run Ledger stops being minable by the flywheel.

So a template writes `{{block:finding-record}}` and this module substitutes the real text at
DEFINITION time, next to macro expansion and for the same reasons: what is stored, what is
validated and what the engine runs are one tree, and no run-time component needs to know blocks
exist.

**An unknown block is an ERROR, not a passthrough.** A typo'd `{{block:finding-recrod}}` left
verbatim would reach a model as literal braces, and the model would either ignore it or invent
what it thought was meant — a convention silently not applied, which is worse than one loudly
missing. The reference syntax deliberately mirrors the binding syntax so an author reads both the
same way, but blocks resolve at authoring and bindings resolve at run.

Blocks live in `bundled/shared/<name>.md` and ship in the package, so they are versioned with the
templates that cite them rather than living in a user's home where an upgrade would fight an edit.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SHARED_PKG = "personalclaw.workflows.bundled.shared"

#: `{{block:name}}`. Mirrors the binding syntax on purpose — an author reads both the same way —
#: but with a distinct `block:` prefix so the two resolvers can never contend for a reference.
BLOCK_REF = re.compile(r"\{\{\s*block:([a-z0-9][a-z0-9-]*)\s*\}\}")


class BlockError(ValueError):
    """A block reference that cannot be resolved. ValueError so the save path's existing
    "unusable spec" channel reports it without a new error taxonomy."""


def shared_root() -> Path:
    """On-disk path of the shared block directory, resolved the same way as bundled templates."""
    return Path(str(resources.files(_SHARED_PKG)))


def block_names() -> list[str]:
    """Every available block name, sorted — for the manifest and an author's error message."""
    root = shared_root()
    if not root.is_dir():
        return []
    return sorted(p.stem for p in root.glob("*.md"))


@lru_cache(maxsize=32)
def _read_cached(name: str, mtime_ns: int) -> str:
    path = shared_root() / f"{name}.md"
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        logger.warning("shared block %s: unreadable", name)
        return ""


def read_block(name: str) -> str:
    """The text of one block, or "" if it does not exist.

    Keyed by mtime so editing a block during development takes effect without a restart; cached
    because expanding six templates re-reads the same three blocks on every listing.
    """
    path = shared_root() / f"{name}.md"
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        return ""
    return _read_cached(name, mtime_ns)


def refs_in(value: Any) -> set[str]:
    """Every block name referenced anywhere in a value (recursing dicts and lists)."""
    found: set[str] = set()

    def walk(v: Any) -> None:
        if isinstance(v, str):
            found.update(BLOCK_REF.findall(v))
        elif isinstance(v, dict):
            for item in v.values():
                walk(item)
        elif isinstance(v, list):
            for item in v:
                walk(item)

    walk(value)
    return found


def resolve_text(text: str) -> str:
    """Substitute every block reference in one string.

    Raises :class:`BlockError` on an unknown name rather than leaving the reference in place: a
    literal `{{block:…}}` reaching a model is a convention silently not applied, and the model
    will either ignore it or invent what it guessed was meant.
    """

    def sub(match: re.Match[str]) -> str:
        name = match.group(1)
        body = read_block(name)
        if not body:
            raise BlockError(
                f"unknown shared block {name!r} — available: {', '.join(block_names()) or 'none'}"
            )
        return body

    return BLOCK_REF.sub(sub, text)


def resolve(value: Any) -> Any:
    """Recursively resolve block references in any spec fragment.

    Returns new containers rather than mutating: the caller may hold the author's original, and
    the same reasoning as macro expansion applies.
    """
    if isinstance(value, str):
        return resolve_text(value) if "{{block:" in value else value
    if isinstance(value, dict):
        return {k: resolve(v) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v) for v in value]
    return value


def resolve_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """Resolve every block reference in a whole spec."""
    out = resolve(spec)
    return out if isinstance(out, dict) else spec


def has_refs(spec: Any) -> bool:
    """True when a spec still contains an unresolved block reference.

    Used by a test asserting that what reaches the engine never does — the same invariant macro
    expansion has.
    """
    return bool(refs_in(spec))
