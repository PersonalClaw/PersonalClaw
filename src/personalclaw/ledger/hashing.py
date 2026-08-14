"""Canonical serialization and content hashing — the same two functions a cache key, a spill
size and an idempotency check all need.

Kept together because they share one requirement: two logically identical values must produce one
string. A hash that depends on dict insertion order is a cache that never hits, and a size computed
from a differently-serialized body is a spill boundary that moves per call.
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
