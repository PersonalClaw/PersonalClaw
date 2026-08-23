"""Per-client inbound identity and bindings (EXTERNAL-ACCESS §1.2, §10).

A request to an inbound surface authenticates as a **client**, not merely as a
surface. The surface token says "you may speak to this dialect"; the client record
says *who* is speaking and therefore *what they may reach*. That distinction is the
whole reason this module exists: with only a surface token, every holder of it is
the same principal, so revoking one integration means rotating the credential of
every other.

**Bindings are pins, not suggestions.** A client bound to ``agent: "researcher"``
cannot select another agent by putting one in the request; a client bound to
``tools: [memory_recall]`` gets exactly that in ``tools/list``. A request argument
that disagrees with a binding is a **403, SEL-logged** — never a silent
substitution and never an override. This is the account-scope rule: the binding is
the authority, the argument is an assertion, and an assertion never wins.

**The store** is ``~/.personalclaw/inbound_clients.json``, written with
``atomic_write(..., mode=0o600)``. It holds token *hashes* only (sha256), never a
token: a registry that could hand back the credential would make "show me the
clients" equivalent to "exfiltrate every integration's bearer".

**Lookup is constant-time** in the comparison, via ``hmac.compare_digest`` against
every candidate hash. A short-circuiting ``==`` over a dict of hashes leaks, by
timing, how many leading bytes of a guess were right — which for a hash is a
practical oracle when the attacker also controls the guess.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_FILE = "inbound_clients.json"

#: The bindings a request argument may never override. Named as data rather than
#: spelled out at each check, so a sixth binding cannot be added to the record shape
#: while quietly staying un-enforced — `test_external_access_seam.py` asserts every
#: member of this tuple actually produces a 403.
PINNED_BINDINGS: tuple[str, ...] = ("agent", "tools", "scope")


def clients_path() -> Path:
    from personalclaw.config.loader import config_dir

    home = Path(os.environ.get("PERSONALCLAW_HOME", config_dir()))
    return home / _FILE


def hash_token(token: str) -> str:
    """The stored form of a bearer token: ``sha256`` hex, never the token itself."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass
class InboundClient:
    """One external integration's identity and its pinned bindings (§1.2)."""

    client_id: str = ""
    label: str = ""
    token_hash: str = ""
    surfaces: list[str] = field(default_factory=list)
    agent: str = ""
    tools: list[str] = field(default_factory=list)
    scope: dict[str, Any] = field(default_factory=dict)
    rate_overrides: dict[str, Any] = field(default_factory=dict)
    disabled: bool = False
    created_at: str = ""
    last_seen_at: str = ""

    def may_use(self, surface: str) -> bool:
        """Whether this client is bound to ``surface``.

        An EMPTY `surfaces` list means "none", not "all". Stated explicitly because
        the permissive reading is the tempting one and it is wrong here: a record
        that lost its surface list during an edit would otherwise silently gain
        access to all five, which is the opposite of what losing data should do.
        """
        return surface in self.surfaces


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_raw() -> dict[str, dict]:
    """The raw registry, or ``{}``. A corrupt file reads as EMPTY, i.e. no client
    authenticates — fail-closed, matching the rest of this layer."""
    try:
        data = json.loads(clients_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError):
        return {}
    except json.JSONDecodeError:
        logger.warning(
            "inbound: %s is corrupt — every client refuses until it is fixed "
            "(fail-closed; an inbound registry that degrades to 'allow' is a breach)",
            clients_path(),
        )
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, dict)}


def load_clients() -> dict[str, InboundClient]:
    """Every registered client, keyed by ``client_id``."""
    out: dict[str, InboundClient] = {}
    for client_id, row in _read_raw().items():
        try:
            out[client_id] = InboundClient(
                client_id=client_id,
                label=str(row.get("label", "") or ""),
                token_hash=str(row.get("token_hash", "") or ""),
                surfaces=[str(s) for s in (row.get("surfaces") or []) if isinstance(s, str)],
                agent=str(row.get("agent", "") or ""),
                tools=[str(t) for t in (row.get("tools") or []) if isinstance(t, str)],
                scope=dict(row.get("scope") or {}),
                rate_overrides=dict(row.get("rate_overrides") or {}),
                # `disabled` is read with plain `bool`, NOT `_expose_flag`: this flag's
                # True is the CLOSED position, so an unparseable value must read as
                # disabled-ish, not as enabled. `bool("false")` is True — which here
                # means "stays disabled", the safe direction. Do not "fix" this to
                # `_expose_flag`, which would turn the string "false" into False and
                # re-enable a client the owner had switched off.
                disabled=bool(row.get("disabled", False)),
                created_at=str(row.get("created_at", "") or ""),
                last_seen_at=str(row.get("last_seen_at", "") or ""),
            )
        except Exception:  # noqa: BLE001 — one bad row must not hide the others
            logger.debug("inbound: skipping unreadable client row %r", client_id, exc_info=True)
    return out


def save_clients(clients: dict[str, InboundClient]) -> None:
    """Persist the registry: ``atomic_write``, mode 0600.

    0600 is passed to `atomic_write` rather than chmod-ed afterwards. `mkstemp`
    creates the temp file at 0600 and the rename preserves it, so the record is
    never briefly group/world-readable — the creation-window TOCTOU that a
    write-then-chmod has by construction.
    """
    payload: dict[str, dict] = {}
    for client_id, client in clients.items():
        row = asdict(client)
        row.pop("client_id", None)  # it is the key; storing it twice invites drift
        payload[client_id] = row
    from personalclaw.atomic_write import atomic_write

    path = clients_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(payload, indent=2, sort_keys=True) + "\n", mode=0o600)


def create_client(
    label: str,
    *,
    surfaces: list[str],
    agent: str = "",
    tools: list[str] | None = None,
    scope: dict[str, Any] | None = None,
    rate_overrides: dict[str, Any] | None = None,
) -> tuple[InboundClient, str]:
    """Register a client and return ``(record, token)``.

    The token is returned ONCE, to be shown once. Only its hash is persisted, so
    there is deliberately no way to recover it later — rotation is cheap and a
    re-readable bearer credential is one an unattended process can also read.
    """
    token = secrets.token_urlsafe(48)  # ~64 chars, comfortably past MIN_TOKEN_BYTES
    client = InboundClient(
        client_id=secrets.token_hex(8),
        label=label,
        token_hash=hash_token(token),
        surfaces=list(surfaces),
        agent=agent,
        tools=list(tools or []),
        scope=dict(scope or {}),
        rate_overrides=dict(rate_overrides or {}),
        disabled=False,
        created_at=_now(),
    )
    clients = load_clients()
    clients[client.client_id] = client
    save_clients(clients)
    _sel_event("inbound_client_created", client.client_id, f"surfaces={','.join(surfaces)}")
    return client, token


def revoke_client(client_id: str) -> bool:
    """Delete a client's record. The token dies with it. False when unknown."""
    clients = load_clients()
    if client_id not in clients:
        return False
    del clients[client_id]
    save_clients(clients)
    _sel_event("inbound_client_revoked", client_id, "record deleted")
    return True


def set_disabled(client_id: str, disabled: bool, *, reason: str = "") -> bool:
    """Flip a client's own kill switch (§1.1 layer c). False when unknown."""
    clients = load_clients()
    client = clients.get(client_id)
    if client is None:
        return False
    if client.disabled == disabled:
        return True
    client.disabled = disabled
    save_clients(clients)
    _sel_event(
        "inbound_client_disabled" if disabled else "inbound_client_enabled",
        client_id,
        reason or "operator action",
    )
    return True


def lookup_by_token(token: str, surface: str) -> tuple[InboundClient | None, str]:
    """Resolve a bearer to a client for ``surface``. Returns ``(client, reason)``.

    ``reason`` is non-empty on refusal and names the failing condition, so the audit
    line can say *why* rather than logging an anonymous 401. Comparison is
    constant-time against every candidate: a short-circuit would leak, by timing,
    how much of a guessed hash was correct.
    """
    if not token:
        return None, "no bearer token presented"
    presented = hash_token(token)
    matched: InboundClient | None = None
    for client in load_clients().values():
        # Every candidate is compared, and the loop does not break on a hit: an early
        # exit reintroduces the timing signal that `compare_digest` exists to remove.
        if client.token_hash and hmac.compare_digest(client.token_hash, presented):
            matched = client
    if matched is None:
        return None, "bearer token matches no registered client"
    if matched.disabled:
        return None, f"client {matched.client_id} is disabled"
    if not matched.may_use(surface):
        return None, f"client {matched.client_id} is not bound to surface {surface!r}"
    return matched, ""


def check_bindings(client: InboundClient, arguments: dict[str, Any]) -> str:
    """``""`` when the request respects every pin, else the violation to 403 on.

    This is the enforcement half of "bindings are pins". It refuses on DISAGREEMENT,
    not on presence: a client bound to ``agent: "researcher"`` that asks for
    ``"researcher"`` is fine, and one that asks for ``"writer"`` is a 403. Silently
    substituting the bound value would be worse than either — the caller would
    believe it had reached the agent it named.
    """
    requested_agent = arguments.get("agent") or arguments.get("model")
    if client.agent and requested_agent and str(requested_agent) != client.agent:
        return (
            f"client {client.client_id} is pinned to agent {client.agent!r}; "
            f"request asked for {str(requested_agent)!r}"
        )
    requested_tools = arguments.get("tools")
    if client.tools and isinstance(requested_tools, list):
        extra = sorted({str(t) for t in requested_tools} - set(client.tools))
        if extra:
            return (
                f"client {client.client_id} is pinned to tools {sorted(client.tools)}; "
                f"request asked for un-bound {extra}"
            )
    requested_scope = arguments.get("scope")
    if client.scope and isinstance(requested_scope, dict):
        for key, value in requested_scope.items():
            if key in client.scope and client.scope[key] != value:
                return (
                    f"client {client.client_id} is pinned to scope {key}="
                    f"{client.scope[key]!r}; request asked for {value!r}"
                )
    return ""


def allowed_tools(client: InboundClient, available: list[str]) -> list[str]:
    """The tool names this client may see. A binding NARROWS; it never widens.

    Intersected with ``available`` rather than returned as-is, so a stale binding
    naming a retired tool cannot resurrect it in `tools/list`.
    """
    if not client.tools:
        return list(available)
    return [name for name in available if name in client.tools]


def touch_last_seen(client_id: str) -> None:
    """Record that a client just made a request. Never raises — a bookkeeping
    failure must not fail the caller's request."""
    try:
        clients = load_clients()
        client = clients.get(client_id)
        if client is None:
            return
        client.last_seen_at = _now()
        save_clients(clients)
    except Exception:  # noqa: BLE001
        logger.debug("inbound: last_seen update failed for %s", client_id, exc_info=True)


# ── auto-disable on repeat cap breach (§1.3) ────────────────────────────────
# Breach timestamps live in memory: the trigger is "N breaches within an hour", a
# question about the CURRENT hour, and persisting it would make a restart inherit an
# hour-old grudge. The resulting `disabled` flag IS persisted — the consequence
# outlives the process, the tally does not.
_breaches: dict[str, list[float]] = {}
_BREACH_WINDOW_S = 3600.0


def record_breach(client_id: str, *, limit: int, reason: str = "cap breach") -> bool:
    """Record one cap breach; return whether it auto-disabled the client.

    ``limit <= 0`` disables the mechanism (the owner's "never auto-disable" setting),
    which is why the guard is here and not at the call site: a caller that forgot to
    check would otherwise re-enable auto-disable for its own surface only.
    """
    if not client_id or limit <= 0:
        return False
    now = time.monotonic()
    window = [t for t in _breaches.get(client_id, []) if now - t < _BREACH_WINDOW_S]
    window.append(now)
    _breaches[client_id] = window
    if len(window) < limit:
        return False
    _breaches[client_id] = []
    set_disabled(client_id, True, reason=f"auto-disabled after {len(window)} breaches ({reason})")
    _notify_auto_disabled(client_id, len(window), reason)
    return True


def _notify_auto_disabled(client_id: str, count: int, reason: str) -> None:
    """Surface the auto-disable as a needs-input notification — the inbound twin of
    the guardrails `_maybe_autopause`. A client that silently stopped working is a
    support call; one that says why is a decision the owner can make."""
    try:
        from personalclaw import notification_kinds
        from personalclaw.action_providers.services import get_action_services

        services = get_action_services()
        state = getattr(services, "state", None) if services is not None else None
        if state is None:
            return
        state.notify(
            # `WARNING`, a REGISTERED wire kind — not a new `needs_input` string. An
            # unregistered kind resolves to `generic` and loses its row in the rules
            # matrix, so the owner could not route or mute it.
            notification_kinds.WARNING,
            "Inbound client auto-disabled",
            (
                f"Client {client_id} tripped inbound caps {count} times in an hour "
                f"({reason}) and was disabled. Re-enable it in Settings → External Access."
            ),
            meta={"client_id": client_id, "breaches": count, "reason": reason},
        )
    except Exception:  # noqa: BLE001 — notification is best-effort
        logger.debug("inbound: auto-disable notify failed", exc_info=True)


def reset_for_tests() -> None:
    """Clear the in-memory breach tallies (process-global, so order-dependent)."""
    _breaches.clear()


def _sel_event(operation: str, client_id: str, detail: str) -> None:
    """Log a client-lifecycle or binding-violation event to the SEL."""
    try:
        from personalclaw.sel import sel

        sel().log_api_access(
            caller=f"inbound:client:{client_id}",
            operation=operation,
            outcome="denied" if "violation" in operation or "disabled" in operation else "ok",
            source="inbound",
            resources=detail[:200],
        )
    except Exception:  # noqa: BLE001
        logger.debug("inbound: SEL event %s failed", operation, exc_info=True)


def log_binding_violation(client_id: str, violation: str) -> None:
    """A binding violation is a security event, not a validation error (§1.2)."""
    _sel_event("inbound_binding_violation", client_id, violation)
