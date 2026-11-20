"""The scope vocabulary of an automation client's session: what a scoped pairing may name.

Core, so the pairing-code store (``auth/pairing.py``) can check a code's scope
without importing the HTTP surface. What each scope lets a session reach, and the refusal for
everything else, are ``personalclaw.dashboard.device_scopes``'s.
"""

from __future__ import annotations

from typing import Any

#: The vocabulary, fixed. Order is the order the pairing form lists them in.
SCOPES: tuple[str, ...] = ("status", "loops", "approvals", "loop_create", "loop_land", "connector")

#: The scope of a browser connector's session: the connector's own routes and nothing else. Never a
#: pairing code's: ``pair/complete`` gives it to a device that redeemed an unscoped code from an
#: extension's own origin (``connector_origin``), so no form or code names it.
CONNECTOR_SCOPE = "connector"


def normalize_scope(value: Any) -> tuple[str, ...]:
    """*value* (a list of scope names) as a scope: sorted, each named once.

    Raises ``ValueError`` for anything else: an empty list, a name outside :data:`SCOPES`, a
    name that isn't a string, a bare string. Nothing is dropped quietly, so a scope is never
    narrower or wider than what was asked for.
    """
    if not isinstance(value, list | tuple) or not value:
        raise ValueError("a scope is a non-empty list of scope names")
    names: set[str] = set()
    for item in value:
        if not isinstance(item, str) or item not in SCOPES:
            raise ValueError(f"unknown scope {item!r}")
        names.add(item)
    return tuple(sorted(names))
