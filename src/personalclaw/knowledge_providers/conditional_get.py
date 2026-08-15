"""Conditional-GET validators carried in a source cursor (WATCHED-SOURCES §3.2).

Both network source kinds poll the same endpoint over and over, so both live or die on the
same trick: remember the ``ETag``/``Last-Modified`` the server handed back and offer them as
``If-None-Match``/``If-Modified-Since`` next time. An unchanged resource then answers 304
with no body — a few hundred bytes instead of a page — which is what makes a 15-minute
interval polite rather than abusive.

This is ONE implementation because a second copy would be one bug-fix away from the two
kinds disagreeing about what a cursor means, and the cursor is persisted state: a divergence
there is a data-shape divergence, not just a code smell. The provider keeps ownership of the
cursor's MEANING (it may store other keys alongside these two) — this module only owns the
two HTTP validators.
"""

from __future__ import annotations

import json
from typing import Any

#: The cursor keys these helpers own. A provider is free to store more (a day bucket, a
#: last-seen id) in the same JSON object; anything not named here is passed through
#: untouched by :func:`parse_validators`' callers.
ETAG_KEY = "etag"
LAST_MODIFIED_KEY = "last_modified"


def parse_validators(cursor: str) -> dict[str, str]:
    """The validators persisted in ``cursor``, or ``{}``.

    A corrupt or non-dict cursor degrades to "no validators" — one full fetch, never a lost
    source. Fail-open is right here specifically because the failure mode is *cost*, not
    correctness: the worst case is one uncached download.
    """
    try:
        data = json.loads(cursor) if cursor else {}
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: str(v) for k, v in data.items() if isinstance(v, str) and v}


def conditional_headers(state: dict[str, str], **extra: str) -> dict[str, str]:
    """Request headers offering the stored validators back to the server."""
    headers = {"Accept": "*/*"}
    headers.update({k: v for k, v in extra.items() if v})
    if state.get(ETAG_KEY):
        headers["If-None-Match"] = state[ETAG_KEY]
    if state.get(LAST_MODIFIED_KEY):
        headers["If-Modified-Since"] = state[LAST_MODIFIED_KEY]
    return headers


def validators_from(headers: Any) -> dict[str, str]:
    """The validators to persist from a response's headers (absent ones omitted).

    Case-insensitive on both spellings because ``net.fetch`` hands back whatever casing the
    server used and a plain dict lookup would silently miss ``etag`` on half the internet.
    """
    src = headers or {}
    lowered = {str(k).lower(): str(v) for k, v in dict(src).items()}
    out = {
        ETAG_KEY: lowered.get("etag", ""),
        LAST_MODIFIED_KEY: lowered.get("last-modified", ""),
    }
    return {k: v for k, v in out.items() if v}


def encode(state: dict[str, str]) -> str:
    """A cursor string from validator state. Sorted so an unchanged cursor is byte-identical
    (a cursor that churns on key order would look like progress to anything diffing it)."""
    return json.dumps(state, sort_keys=True)
