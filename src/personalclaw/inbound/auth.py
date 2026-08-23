"""Inbound surface authentication (MCP-READONLY-INBOUND §C1).

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
from pathlib import Path

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
    from personalclaw.config.loader import EXTERNAL_ACCESS_SURFACES

    return EXTERNAL_ACCESS_SURFACES


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
    """
    env_key = token_env_key(surface)
    from_env = (os.environ.get(env_key) or "").strip()
    if from_env:
        return from_env
    try:
        from personalclaw.config.loader import get_credential

        return (get_credential(env_key) or "").strip() or None
    except Exception:  # noqa: BLE001 — an unreadable store means "no token", i.e. no mount
        logger.debug("inbound: credential read failed for %s", surface, exc_info=True)
        return None


def create_surface_token(surface: str) -> str:
    """Mint, persist and return a fresh token for ``surface``.

    Persisted through `save_credential`, so it lands in the active credential
    backend (keychain, else ``.env`` at 0600) and is mirrored into ``os.environ`` —
    the running gateway therefore honours a freshly created token without a restart.

    Rotation is just calling this again: the previous value is overwritten, which is
    what makes `--rotate` meaningful.
    """
    token = secrets.token_urlsafe(48)  # ~64 chars, well past MIN_TOKEN_BYTES
    from personalclaw.config.loader import save_credential

    save_credential(token_env_key(surface), token)
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

        home = Path(os.environ.get("PERSONALCLAW_HOME", config_dir()))
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
    if len(token.encode("utf-8")) < MIN_TOKEN_BYTES:
        return f"token shorter than {MIN_TOKEN_BYTES} bytes"
    if token in _forbidden_token_values(surface):
        return "token must not equal the dashboard/internal secret or another surface's token"
    return None


def verify_bearer(surface: str, presented: str) -> bool:
    """Constant-time bearer check. False for any unusable token configuration."""
    if token_problem(surface) is not None:
        return False
    expected = load_surface_token(surface) or ""
    if not presented:
        return False
    return hmac.compare_digest(presented, expected)


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
        # §1.1: the control bridge ignores `allow_remote` ENTIRELY — loopback-only
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


def inbound_cmd(args) -> int:
    """``personalclaw inbound token create|show <surface> [--rotate]``.

    The token is printed ONCE at creation. There is no "show me the token" that
    reveals it: a bearer credential you can re-read from the CLI is one an
    unattended process can also exfiltrate, and rotation is cheap.
    """
    action = getattr(args, "inbound_command", None)
    if action != "token":
        print("Usage: personalclaw inbound token create <surface> [--rotate]")
        return 2

    known = surfaces()
    surface = str(getattr(args, "surface", "") or "mcp").lower()
    if surface not in known:
        print(f"❌ Unknown surface {surface!r}. Known: {', '.join(known)}")
        return 1

    sub = str(getattr(args, "token_action", "") or "create")
    key = token_env_key(surface)

    if sub == "show":
        problem = token_problem(surface)
        if problem:
            print(f"❌ {surface}: {problem}")
            return 1
        print(f"✅ {surface}: a valid token is configured ({key}, credential store)")
        print("   The value is intentionally not printed — rotate if you've lost it.")
        return 0

    if sub != "create":
        print("Usage: personalclaw inbound token create <surface> [--rotate]")
        return 2

    rotate = bool(getattr(args, "rotate", False))
    if load_surface_token(surface) and not rotate:
        # Existence is asked of the CREDENTIAL STORE, not of a file: with the keychain
        # backend active there is no path to stat, so a file check would report "no
        # token" and silently clobber a live one on the next `create`.
        print(f"❌ A token already exists for {surface} ({key}).")
        print("   Re-run with --rotate to replace it (the old token stops working).")
        return 1

    token = create_surface_token(surface)
    print(f"✅ {'Rotated' if rotate else 'Created'} the {surface} inbound token.")
    print(f"🔑 stored as {key} in the credential store (keychain, else .env at 0600)")
    print()
    print("Copy it into your client now — it is not shown again:")
    print()
    print(f"    Authorization: Bearer {token}")
    print()
    print("Then enable the surface (BOTH switches — the master gate is separate):")
    print("    personalclaw config set external_access.enabled true")
    print(f"    personalclaw config set external_access.{surface}.enabled true")
    print(
        "The surface is loopback-only until you set external_access.public_url "
        f"+ external_access.{surface}.allow_remote."
    )
    return 0
