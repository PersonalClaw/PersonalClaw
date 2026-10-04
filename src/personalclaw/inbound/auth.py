"""Inbound surface authentication.

Two independent gates, both of which must pass: the caller presents a valid
bearer token for this surface, AND the connection comes from an allowed peer.

They're separate on purpose. A token alone would make the surface reachable from
anywhere the port is; a peer check alone is not authentication at all — local port
forwarders (``socat``, ``ssh -R``) make remote traffic arrive as 127.0.0.1, which
is precisely why the dashboard's own middleware refuses to treat loopback as
proof of anything.
"""

from __future__ import annotations

import hmac
import logging
import os
import secrets
import sys
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from personalclaw.inbound.tokens import SurfaceToken

logger = logging.getLogger(__name__)

# A token shorter than this is refused outright rather than "working but weak" —
# an inbound surface credential is machine-generated, so there is no reason to
# accept a hand-typed short one.
MIN_TOKEN_BYTES = 32

_LOOPBACK = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost"})

#: The control bridge. Named rather than spelled inline because its rule (loopback
#: forever, `allow_remote` ignored) is a *behavioural exception*, and an exception
#: keyed on a bare string literal is one rename away from silently not applying.
BRIDGE_SURFACE = "bridge"


def surfaces() -> tuple[str, ...]:
    """The five inbound surfaces. Re-exported from the config loader, which owns the
    single declaration, so this module cannot drift into its own shorter list — which
    is exactly what `_SURFACES = ("mcp",)` was before EA-1 widened the seam."""
    from personalclaw.config.external_access import EXTERNAL_ACCESS_SURFACES

    return EXTERNAL_ACCESS_SURFACES


def client_surfaces() -> tuple[str, ...]:
    """The surfaces a registered client may be bound to: the five, and the webhook."""
    from personalclaw.config.external_access import CLIENT_SURFACES

    return CLIENT_SURFACES


def token_env_key(surface: str) -> str:
    """The credential key a surface's token is stored under (EXTERNAL-ACCESS §1.1).

    One spelling, derived, imported by every reader — the CLI, the mount check and
    the settings surface. A hand-built ``f"PERSONALCLAW_INBOUND_{s}_TOKEN"`` at three
    call sites is three chances to disagree about the casing.
    """
    return f"PERSONALCLAW_INBOUND_{surface.upper()}_TOKEN"


def load_surface_token(surface: str) -> str | None:
    """The configured token for ``surface``, or None.

    Reads through the CREDENTIAL STORE (`get_credential`), which consults the
    keychain and then ``.env`` — so a token follows whichever backend the owner has
    active instead of living in a bespoke dotfile this module invented. Environment
    is checked first so a container can inject one without any persistence at all.

    **Unless the environment's value was replaced.** A process copies the store's tokens into
    its environment when it starts, and ``personalclaw inbound token create --rotate`` runs in
    another process: it writes the store and records the old token as replaced
    (``inbound/tokens.py``). An environment value that record names is this process's stale
    copy, so the store's newer value is the configured one, and it is copied into the
    environment as it is read — the running gateway honours a rotation at once, and the old
    token stops at once. The store is read only then, never on the ordinary path.
    """
    env_key = token_env_key(surface)
    from_env = (os.environ.get(env_key) or "").strip()
    if from_env:
        newer = _stored_after_rotation(env_key, from_env)
        if newer:
            os.environ[env_key] = newer
            return newer
        return from_env
    try:
        from personalclaw.config.credentials import get_credential

        return (get_credential(env_key) or "").strip() or None
    except Exception:  # noqa: BLE001 — an unreadable store means "no token", i.e. no mount
        logger.debug("inbound: credential read failed for %s", surface, exc_info=True)
        return None


def _stored_after_rotation(env_key: str, from_env: str) -> str:
    """The store's value for *env_key* when *from_env* is a token recorded as replaced and the
    store holds a different one; otherwise ``""``."""
    from personalclaw.inbound import tokens

    if not tokens.was_replaced(from_env):
        return ""
    try:
        from personalclaw.config.credentials import get_credential

        stored = (get_credential(env_key) or "").strip()
    except Exception:  # noqa: BLE001 — unreadable: the replaced value stays, and is refused
        logger.debug("inbound: credential read failed for %s", env_key, exc_info=True)
        return ""
    return stored if stored and stored != from_env else ""


def create_surface_token(surface: str, ttl_secs: int | None = None, *, actor: str = "owner") -> str:
    """Mint, persist and return a fresh token for ``surface``, working for *ttl_secs*.

    Persisted through `save_credential`, so it lands in the active credential
    backend (keychain, else ``.env`` at 0600) and is mirrored into ``os.environ`` —
    the running gateway therefore honours a freshly created token without a restart.
    Its lifetime is recorded beside it (``inbound/tokens.py``): at most 90 days, the limit
    for a long-lived credential, which is also the default. A longer *ttl_secs* raises
    ``ValueError`` whose message is the sentence to show whoever asked.

    Rotation is just calling this again: the previous value is overwritten, which is
    what makes `--rotate` meaningful, and whatever still presents it is told it was replaced.
    """
    from personalclaw.auth.lifetimes import MAX_LIFETIME_SECS, integration_too_long
    from personalclaw.inbound import tokens

    lifetime = tokens.INTEGRATION_TTL_SECS if ttl_secs is None else int(ttl_secs)
    if lifetime > MAX_LIFETIME_SECS:
        raise ValueError(integration_too_long(lifetime))
    token = secrets.token_urlsafe(48)  # ~64 chars, well past MIN_TOKEN_BYTES
    from personalclaw.config.credentials import save_credential

    save_credential(token_env_key(surface), token)
    tokens.issue_surface_token(surface, token, lifetime, actor=actor)
    return token


def _forbidden_token_values(surface: str = "") -> set[str]:
    """Credentials this surface must NEVER accept as its own token.

    Reusing the dashboard token or the internal secret would silently extend those
    credentials to a new network surface — a caller who obtained one for a
    different purpose would suddenly have inbound access too. Both are the SAME file
    (`.local_secret`: the dashboard session secret and `mcp_core._internal_secret`
    read it), so one read covers the pair.

    Since EA-1 this also refuses **another surface's token**. Five surfaces sharing
    one bearer would collapse five independently revocable credentials into one, so
    turning off the capture proxy would not stop a capture client from reaching the
    MCP surface with the same string.
    """
    values: set[str] = set()
    try:
        from personalclaw.config.loader import config_dir

        home = config_dir()
        for name in (".local_secret",):
            try:
                raw = (home / name).read_text(encoding="utf-8").strip()
                if raw:
                    values.add(raw)
            except (FileNotFoundError, OSError):
                continue
    except Exception:  # noqa: BLE001 — an unreadable home must not weaken the check
        logger.debug("inbound: could not read reserved secrets", exc_info=True)
    if surface:
        for other in surfaces():
            if other == surface:
                continue
            try:
                peer_token = load_surface_token(other)
            except Exception:  # noqa: BLE001
                continue
            if peer_token:
                values.add(peer_token)
    return values


def token_problem(surface: str) -> str | None:
    """Why ``surface``'s token is unusable, or None when it's fine.

    Returns a REASON rather than a bool so the mount refusal can name the failing
    condition in one log line — "inbound disabled" with no cause is the kind of
    message that costs an hour.
    """
    token = load_surface_token(surface)
    if not token:
        return f"no token configured (run: personalclaw inbound token create {surface})"
    return token_strength_problem(token, surface)


def token_strength_problem(token: str, surface: str) -> str | None:
    """Why *token* cannot be *surface*'s credential, or None: shorter than :data:`MIN_TOKEN_BYTES`,
    or the same as a credential that opens something else (:func:`_forbidden_token_values`)."""
    if len(token.encode("utf-8")) < MIN_TOKEN_BYTES:
        return f"token shorter than {MIN_TOKEN_BYTES} bytes"
    if token in _forbidden_token_values(surface):
        return "token must not equal the dashboard/internal secret or another surface's token"
    return None


def verify_bearer(surface: str, presented: str) -> bool:
    """Constant-time bearer check. False for any unusable token configuration, and for a
    token past its lifetime or revoked (``inbound/tokens.py``) — whose holder is then told
    which, in the refusal's sentence (``tokens.refusal``)."""
    if token_problem(surface) is not None:
        return False
    expected = load_surface_token(surface) or ""
    if not presented:
        return False
    if not hmac.compare_digest(presented, expected):
        return False
    from personalclaw.inbound import tokens

    if not tokens.surface_usable(surface, expected):
        return False
    tokens.note_surface_use(surface, expected)
    return True


def _peer_host(request) -> str:
    """The peer address from the TRANSPORT, never from a header.

    `X-Forwarded-For` and friends are attacker-settable on a directly-reachable
    port, so they cannot participate in an access decision.
    """
    try:
        peer = request.transport.get_extra_info("peername")
        if peer:
            return str(peer[0])
    except Exception:  # noqa: BLE001
        pass
    try:
        return str(request.remote or "")
    except Exception:  # noqa: BLE001
        return ""


def is_loopback(request) -> bool:
    return _peer_host(request) in _LOOPBACK


#: Where each inbound surface on the dashboard's port answers: an exact path, or a prefix ending
#: in ``/``. The control bridge has none; it listens on a port of its own.
_SURFACE_PATHS: tuple[tuple[str, str], ...] = (
    ("/mcp", "mcp"),
    ("/v1/", "openai"),
    ("/a2a/", "a2a"),
    ("/capture/", "capture"),
)


def surface_of_path(path: str) -> str | None:
    """The inbound surface a request to *path* on the dashboard's port is for, or None."""
    for route, surface in _SURFACE_PATHS:
        if path == route or (route.endswith("/") and path.startswith(route)):
            return surface
    from personalclaw.inbound import webhook

    return webhook.SURFACE if webhook.is_door(path) else None


def off_machine_refusal(request) -> str:
    """What a program on another machine is told when it calls an inbound surface, or ``""`` when
    the request is not that.

    Such a request carries no browser origin, and the dashboard refuses a state-changing request
    from another address that carries none before any surface reads it — so the surfaces take
    their requests only from programs on the machine PersonalClaw runs on, whatever
    ``allow_remote`` says. The sentence says that, which address the request came from, and the
    two things that do reach it: a client on that machine, or an SSH tunnel to its loopback. It
    says the same whether the surface is on or off, so it tells a prober nothing a 404 hides.
    """
    surface = surface_of_path(str(getattr(request, "path", "") or ""))
    if surface is None or is_loopback(request):
        return ""
    headers = getattr(request, "headers", {}) or {}
    if headers.get("Origin") or headers.get("Referer"):
        return ""
    return off_machine_sentence(request, surface)


def off_machine_sentence(request, surface: str) -> str:
    """What a program calling *surface* from another address is told: that the surface takes
    requests only from programs on PersonalClaw's machine, which address this one came from, and
    the two ways that do reach it. The words of :func:`off_machine_refusal`, for a surface whose
    own door asks where a request came from (the webhook, ``inbound.webhook``)."""
    from dataclasses import fields

    from personalclaw.config.external_access import ExternalAccessConfig

    label = next(
        (
            f.metadata.get("label", surface)
            for f in fields(ExternalAccessConfig)
            if f.name == surface
        ),
        surface,
    )
    peer = _peer_host(request) or "another address"
    return (
        f"PersonalClaw's {label} takes requests only from programs on the machine PersonalClaw "
        f"runs on; this one came from {peer}. To reach it, run your client on PersonalClaw's "
        "machine and connect to 127.0.0.1, or forward a port to that machine's 127.0.0.1 over "
        "SSH and connect through the tunnel."
    )


def peer_allowed(request, surface: str = "mcp") -> tuple[bool, str]:
    """Whether this peer may reach the surface. Returns ``(ok, reason)``.

    Loopback always passes. A non-loopback peer passes ONLY when the owner both
    opted into remote access for this surface and declared the public URL, and the
    request's Host matches it exactly — a declared URL is how the owner states
    which name this instance answers to, so an unmatched Host is a
    misconfiguration or a probe either way.
    """
    if is_loopback(request):
        return True, ""
    if surface == BRIDGE_SURFACE:
        # The control bridge ignores `allow_remote` ENTIRELY — loopback-only
        # forever, by construction. Checked before the config read so no combination
        # of settings can widen it; it drives FE semantic actions, so a remote caller
        # reaching it is a full control-plane compromise, not a data read.
        return False, "the control bridge is loopback-only by construction"
    try:
        from personalclaw.config.loader import AppConfig

        cfg = AppConfig.load()
        allow_remote = bool(
            getattr(getattr(cfg.external_access, surface, None), "allow_remote", False)
        )
        public_url = str(getattr(cfg.external_access, "public_url", "") or "")
    except Exception:  # noqa: BLE001 — unreadable config ⇒ refuse (fail-closed)
        logger.debug("inbound: config unreadable during peer check", exc_info=True)
        return False, "config unreadable"
    if not allow_remote:
        return False, "non-loopback peer and allow_remote is off"
    if not public_url:
        return False, "allow_remote is on but external_access.public_url is unset"
    host = str(request.headers.get("Host", "") or "")
    expected_host = public_url.split("://", 1)[-1].rstrip("/")
    if host != expected_host:
        return False, f"Host {host!r} does not match external_access.public_url"
    return True, ""


# ── CLI ─────────────────────────────────────────────────────────────────────


def _show_token(surface: str, key: str, current: SurfaceToken | None) -> int:
    """``inbound token show``: whether ``surface`` has a token that works, and until when.

    The answer is the output, so it is on stdout either way, and the exit status says which:
    1 when no token works, the way ``security verify`` reports a tampered log.
    """
    from personalclaw.auth.lifetimes import until_words, when_words
    from personalclaw.inbound import tokens

    problem = token_problem(surface)
    if problem:
        print(f"❌ {surface}: {problem}")
        return 1
    ended = tokens.ending(surface, load_surface_token(surface) or "")
    if ended is not None:
        print(f"❌ {surface}: {ended.sentence}")
        return 1
    print(f"✅ {surface}: a valid token is configured ({key}, credential store)")
    if current is not None:
        started = "first seen" if current.found else "created"
        print(
            f"   {started.capitalize()} {when_words(current.issued_at)}; it works until "
            f"{until_words(current.expires_at)}."
        )
    print("   The value is intentionally not printed — rotate if you've lost it.")
    return 0


def inbound_cmd(args) -> int:
    """``personalclaw inbound token create|show|revoke <surface> [--rotate]``.

    The token is printed ONCE at creation. There is no "show me the token" that
    reveals it: a bearer credential you can re-read from the CLI is one an
    unattended process can also exfiltrate, and rotation is cheap.

    There is no `confirm` verb: a control-bridge action waits for you in PersonalClaw's Inbox,
    and a command the agent that asked can run as you would let it confirm its own. The parser
    refuses a bare ``inbound`` and any action but these three.

    ``personalclaw inbound webhook …`` makes, lists and revokes a webhook automation's sender
    tokens (``inbound.webhook.webhook_cmd``).
    """
    if getattr(args, "inbound_command", "") == "webhook":
        from personalclaw.inbound import webhook

        return webhook.webhook_cmd(args)
    known = surfaces()
    surface = str(getattr(args, "surface", "") or "mcp").lower()
    if surface not in known:
        print(f"❌ Unknown surface {surface!r}. Known: {', '.join(known)}", file=sys.stderr)
        return 1

    sub = str(getattr(args, "token_action", "") or "create")
    key = token_env_key(surface)

    from personalclaw.auth.lifetimes import (
        duration_words,
        integration_too_long,
        lifetime_seconds,
        unreadable,
        until_words,
    )
    from personalclaw.inbound import tokens

    current = None
    try:
        current = tokens.surface_token(surface, load_surface_token(surface))
    except tokens.RegistryUnavailable as exc:
        print(
            f"❌ {surface}: the token registry is unreadable, so every surface token refuses: {exc}",
            file=sys.stderr,
        )
        return 1

    if sub == "show":
        return _show_token(surface, key, current)

    if sub == "revoke":
        if current is None:
            print(f"❌ No token is configured for {surface}.", file=sys.stderr)
            return 1
        if not tokens.revoke_surface_token(surface, load_surface_token(surface), actor="cli"):
            print(f"❌ The {surface} token is already revoked.", file=sys.stderr)
            return 1
        print(f"✅ Revoked the {surface} inbound token. Anything still using it is refused, and")
        print(f"   told it was revoked. Create a new one with: {tokens.create_command(surface)}")
        return 0

    ttl_text = str(getattr(args, "ttl", "") or "90d")
    ttl = lifetime_seconds(ttl_text)
    if ttl is None:
        print(f"❌ {unreadable(ttl_text)}", file=sys.stderr)
        return 1
    if ttl > tokens.INTEGRATION_TTL_SECS:
        print(f"❌ {integration_too_long(ttl)}", file=sys.stderr)
        return 1

    rotate = bool(getattr(args, "rotate", False))
    # A token that expired or was revoked is not one worth protecting from a plain `create`.
    live = current is not None and current.usable()
    if live and not rotate:
        # Existence is asked of the CREDENTIAL STORE, not of a file: with the keychain
        # backend active there is no path to stat, so a file check would report "no
        # token" and silently clobber a live one on the next `create`.
        print(f"❌ A token already exists for {surface} ({key}).", file=sys.stderr)
        print(
            "   Re-run with --rotate to replace it (the old token stops working).", file=sys.stderr
        )
        return 1

    replacing = current is not None
    token = create_surface_token(surface, ttl, actor="cli")
    print(f"✅ {'Rotated' if replacing else 'Created'} the {surface} inbound token.")
    print(f"🔑 stored as {key} in the credential store (keychain, else .env at 0600)")
    print(
        f"⏱  It works for {duration_words(ttl)}, until {until_words(time.time() + ttl)}; then "
        "create a new one. Settings → Devices lists it, and revokes it."
    )
    print()
    print("Copy it into your client now — it is not shown again:")
    print()
    print(f"    Authorization: Bearer {token}")
    print()
    print("Then enable the surface (BOTH switches — the master gate is separate):")
    print("    personalclaw config set external_access.enabled true")
    print(f"    personalclaw config set external_access.{surface}.enabled true")
    if surface == BRIDGE_SURFACE:
        # Its own listener, started with the gateway: the switches reach it at the next start.
        print(
            "The control bridge starts listening the next time PersonalClaw starts, and only "
            "programs on this machine can reach it."
        )
    else:
        # The truth whatever `allow_remote` says: the dashboard refuses a program's request from
        # another address before the surface reads it (`off_machine_refusal`).
        print(
            "It takes requests only from programs on this machine. From another one, forward a "
            "port to this machine's 127.0.0.1 over SSH and connect through the tunnel."
        )
    return 0
