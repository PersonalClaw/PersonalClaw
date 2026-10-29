"""Canonical serialization and content hashing — the same two functions a cache key, a spill
size and an idempotency check all need, and the one digest a yes is bound to.

Kept together because they share one requirement: two logically identical values must produce one
string. A hash that depends on dict insertion order is a cache that never hits, and a size computed
from a differently-serialized body is a spill boundary that moves per call.

:func:`canonical_digest` is the other form (shared contracts §2.4): compact, and text other than
ASCII written as itself. Every digest an owner's yes is sealed to is taken with it;
:func:`hash_value` is for cache keys only. ``tests/rails/test_digests_have_one_canonical_form.py``
fails on a SHA-256 of compact JSON spelled anywhere else.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def stable_json(value: Any) -> str:
    """Canonical form for hashing: sorted keys, no incidental whitespace. Two logically
    identical inputs must hash identically or the resume cache never hits."""
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def hash_value(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()[:16]


def canonical_digest(value: Any) -> str:
    """The SHA-256 hex of *value* as compact canonical JSON: keys sorted at every depth, no
    whitespace between tokens, text other than ASCII as itself in UTF-8, and ``str`` of a value
    JSON cannot hold. What a workflow version's Allow, a tool's sealed trust and a study's
    registration are bound to, so its bytes never change."""
    text = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
