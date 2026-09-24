"""Subscription-credential sources — a provider riding an already-signed-in CLI's login.

Some model vendors bill by SUBSCRIPTION rather than per-token API key, so there is no key
for the user to paste: they already signed that vendor's own agent CLI in on this machine,
and the CLI holds a bearer token in a credential store IT owns. A model-provider app can
declare where that store is and resolve the token from it, so the provider needs no
separate key at all.

This module is that mechanism, and nothing more. It is a *credential resolution* seam:
sessions, models and catalogs still flow through the normal branded-app path, and no agent
runtime is involved.

Three contracts hold it together:

1. **Strictly read-only.** Resolving opens the declared file, parses it, and returns. It
   never writes, creates, moves, renames, chmods, refreshes or deletes another tool's
   credential store — not even to "repair" or "renew" one. An expired token is reported as
   not-signed-in and the user re-runs their own CLI's login; PersonalClaw does not touch
   the vendor's store on their behalf. ``tests/test_subscription_credentials.py`` asserts
   this structurally (no write-shaped call exists in this module's AST) and behaviourally
   (bytes, mode and mtime are identical after a resolve).

2. **Fail soft and typed — and a parse error is never "signed in".** Every failure path
   returns a :class:`SubscriptionAuth` with ``logged_in=False`` and a human ``reason``;
   nothing raises and nothing returns a partial secret. A missing file, an unreadable
   file, a *malformed or half-written* file, a missing/blank/non-string token and an
   expired token all land in the same not-signed-in outcome. That symmetry is the point:
   a truncated write must not read as authenticated.

3. **No secret is ever emitted.** ``reason`` strings are built from the source id, the
   declared path and the app's own login hint — never from file content, and never from
   the token. :class:`SubscriptionAuth` keeps ``secret`` out of its ``repr``, so logging
   or formatting a result cannot leak it. For a malformed store we deliberately report a
   fixed sentence instead of the parser's exception text, so no fragment of the file can
   ride out inside an error message.

**Core ships no vendor rows.** A :class:`SubscriptionSource` is *declared by the app* and
registered at app import time, exactly like the provider type and catalog it ships beside
(see ``sdk/provider_helpers.py``). Core knows how to read a declared store; it does not
know that any particular CLI exists, where it keeps its token, or what its login verb is
called — the app names its own login verb in ``login_hint``, the same discipline the ACP
readiness probe follows with ``login_command``.

A subscription model-provider app therefore declares two things and is done::

    from personalclaw.sdk.provider_helpers import (
        BrandedProviderSpec,
        SubscriptionSource,
        register_branded_app,
        register_subscription_source,
    )

    register_subscription_source(
        SubscriptionSource(
            id="example-cli",
            login_hint="sign in with `example login` first",
            credential_files=("~/.example/.credentials.json",),
            token_path=("oauth", "accessToken"),
            expires_at_path=("oauth", "expiresAt"),
            expires_at_unit="ms",
        )
    )

    _factory, create_provider, create_catalog = register_branded_app(
        BrandedProviderSpec(
            type="example-subscription",
            protocol="anthropic",
            default_base_url="https://api.example.invalid",
            credential_source="example-cli",  # NO api_key_env — there is no key
            default_model="example-large",
        )
    )

The app needs no ``availability()`` hook: ``providers/loader.py`` derives one from the
declared ``credential_source``, so a not-signed-in source greys the bundle out in the
extensions list with the app's own reason.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Accepted units for a declared expiry stamp. Milliseconds is the common shape for a
#: JSON OAuth record, so it is the default; seconds is the POSIX shape.
_EXPIRY_UNITS: tuple[str, ...] = ("ms", "s")


@dataclass(frozen=True)
class SubscriptionSource:
    """One agent CLI's credential store, described declaratively by the app that rides it.

    ``id`` is what a :class:`~personalclaw.sdk.provider_helpers.BrandedProviderSpec` names
    in its ``credential_source``. ``login_hint`` is the sentence a user sees when the
    source is not signed in — the APP owns that wording because the app knows its vendor's
    login verb. ``credential_files`` are ``~``-expanded paths tried in order (first one
    that yields a usable token wins), and ``token_path`` / ``expires_at_path`` are key
    walks into the parsed JSON object.

    An absent ``expires_at_path`` means "this store records no expiry" — the token is used
    as-is. It does NOT mean "assume valid forever" in any dangerous sense: a stale token
    simply fails at the wire with the vendor's own 401, which is the same outcome a stale
    API key has.
    """

    id: str
    login_hint: str
    credential_files: tuple[str, ...] = ()
    token_path: tuple[str, ...] = ()
    expires_at_path: tuple[str, ...] = ()
    expires_at_unit: str = "ms"

    def validate(self) -> list[str]:
        """Declaration errors, empty when the descriptor is usable."""
        errors: list[str] = []
        if not self.id.strip():
            errors.append("id is required")
        if not self.login_hint.strip():
            errors.append("login_hint is required (the user must be told how to sign in)")
        if not self.credential_files:
            errors.append("credential_files must name at least one path")
        if not self.token_path:
            errors.append("token_path must name at least one key")
        if self.expires_at_unit not in _EXPIRY_UNITS:
            errors.append(f"expires_at_unit must be one of {list(_EXPIRY_UNITS)}")
        return errors


@dataclass(frozen=True)
class SubscriptionAuth:
    """The outcome of one read-only probe of a declared credential store.

    ``secret`` is populated ONLY when ``logged_in`` is True, and is excluded from the
    dataclass ``repr`` (and therefore from ``str()``, ``f"{auth}"``, ``print(auth)`` and
    every logger that formats the object) so a resolved token cannot leak by accident.
    ``reason`` is always safe to display: it is composed from the source id, the declared
    path and the app's login hint, never from file content.
    """

    source: str
    logged_in: bool
    reason: str = ""
    secret: str = field(default="", repr=False)


#: source id → the descriptor an app registered. Populated at app import time by
#: :func:`register_subscription_source`; last-wins on re-registration, mirroring how
#: ``register_catalog`` and ``_REGISTERED_SPECS`` behave under a reload. Core adds no rows
#: of its own — an empty registry is the correct state for an install with no
#: subscription-credential app.
_SOURCES: dict[str, SubscriptionSource] = {}


def register_subscription_source(source: SubscriptionSource) -> None:
    """Register an app's credential-store descriptor. Raises on a broken declaration.

    A malformed descriptor is the app author's bug, not a runtime condition, so it fails
    loudly at import rather than degrading into a mystery "not signed in" later.
    """
    errors = source.validate()
    if errors:
        raise ValueError(f"invalid SubscriptionSource {source.id!r}: {'; '.join(errors)}")
    _SOURCES[source.id.strip()] = source


def _walk(payload: Any, keys: tuple[str, ...]) -> Any:
    """Follow ``keys`` through nested mappings; None when any hop is missing."""
    node: Any = payload
    for key in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _expired(payload: Any, source: SubscriptionSource, *, now: float) -> bool:
    """True when the store records an expiry that has already passed.

    An absent or unparseable stamp is NOT treated as expired — that judgement belongs to
    the vendor's endpoint, and inventing an expiry would grey out a working provider.
    """
    if not source.expires_at_path:
        return False
    raw = _walk(payload, source.expires_at_path)
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        return False
    try:
        stamp = float(raw)
    except (TypeError, ValueError):
        return False
    if source.expires_at_unit == "ms":
        stamp = stamp / 1000.0
    return stamp <= now


def resolve_subscription_credential(source_id: str) -> SubscriptionAuth:
    """Read the declared credential store for ``source_id``, read-only.

    Returns a populated :class:`SubscriptionAuth` when the source is signed in, and a
    ``logged_in=False`` one carrying a displayable ``reason`` in every other case —
    unregistered source, no file, unreadable file, malformed JSON, missing/blank token,
    expired token. It never raises and never returns a partial secret.
    """
    wanted = str(source_id or "").strip()
    if not wanted:
        return SubscriptionAuth(source="", logged_in=False, reason="no credential source declared")
    source = _SOURCES.get(wanted)
    if source is None:
        return SubscriptionAuth(
            source=wanted,
            logged_in=False,
            reason=(
                f"no {wanted!r} subscription credential source is registered "
                "(its provider app is not installed or not enabled)"
            ),
        )

    now = time.time()
    # Remember the FIRST informative failure so a source with several candidate paths
    # reports something better than "the last one was missing".
    first_failure = ""
    for raw_path in source.credential_files:
        path = Path(os.path.expanduser(os.path.expandvars(raw_path)))
        try:
            # Read-only, and the ONLY filesystem call this module makes.
            text = path.read_text(encoding="utf-8")
        except OSError:
            first_failure = first_failure or (
                f"{source.id} is not signed in on this machine — {source.login_hint}"
            )
            continue
        try:
            payload = json.loads(text)
        except (ValueError, TypeError):
            # A malformed or half-written store is NOT authenticated. The parser's message
            # is deliberately dropped: a fixed sentence cannot carry a fragment of the file.
            first_failure = first_failure or (
                f"{source.id} credential store at {raw_path} is unreadable "
                f"(malformed or half-written) — {source.login_hint}"
            )
            continue
        token = _walk(payload, source.token_path)
        if not isinstance(token, str) or not token.strip():
            first_failure = first_failure or (
                f"{source.id} credential store at {raw_path} holds no sign-in token — "
                f"{source.login_hint}"
            )
            continue
        if _expired(payload, source, now=now):
            first_failure = first_failure or (
                f"{source.id} sign-in has expired — {source.login_hint}"
            )
            continue
        return SubscriptionAuth(source=source.id, logged_in=True, secret=token.strip())

    return SubscriptionAuth(
        source=source.id,
        logged_in=False,
        reason=first_failure or f"{source.id} is not signed in — {source.login_hint}",
    )


def subscription_source_status(source_id: str) -> tuple[bool, str]:
    """The ``availability()`` shape: ``(available, reason)``.

    ``reason`` is empty when available, mirroring the extension-availability contract in
    ``providers/loader.py`` (the UI shows it verbatim when it greys a bundle out).
    """
    auth = resolve_subscription_credential(source_id)
    return (True, "") if auth.logged_in else (False, auth.reason)


__all__ = [
    "SubscriptionAuth",
    "SubscriptionSource",
    "register_subscription_source",
    "resolve_subscription_credential",
    "subscription_source_status",
]
