"""``personalclaw auth`` — manage the owner login.

The CLI is the ONLY way a password is set. There is no "set the password over the API"
endpoint, and `auth.login_enabled` being PATCH-editable does not change that: a credential
is not a setting. A password that arrives in a request body ends up in whatever logs,
proxies and browser histories sit in front of the gateway, whereas a `getpass` prompt on
the box itself has none of that exhaust.

Nothing here reveals a stored secret. `auth status` reports whether a credential exists,
never the hash; `auth totp setup` prints the new secret exactly once because the user has
to type it into an authenticator app, and says so. `auth enroll` prints a short single-use
code for pairing another device — the store keeps only its hash, so it cannot be read back.
"""

from __future__ import annotations

import getpass
import os
import sys
from typing import Any

from personalclaw.auth import credentials as creds


def _print_status() -> int:
    st = creds.status()
    cfg = _auth_config()
    print("Owner login")
    print(f"  enabled:     {'yes' if cfg.get('login_enabled') else 'no'}")
    if st["configured"]:
        print(f"  credential:  set for {st['username']!r} ({st['algo']}, {st['updated_at']})")
    else:
        print("  credential:  NOT set — run `personalclaw auth set-password`")
    print(f"  2FA (TOTP):  {'on' if st['totp_enabled'] else 'off'}")
    print(f"  session TTL: {cfg.get('session_ttl')}")
    print(f"  lockout:     after {cfg.get('lockout_threshold')} tries, {cfg.get('lockout_window')}")
    if cfg.get("login_enabled") and not st["configured"]:
        # Not a crash, but the one combination that guarantees nobody can log in. Say so
        # plainly rather than letting it be discovered at the login page.
        print()
        print("⚠️  Login is enabled but no credential is set — password login cannot succeed.")
        print("   The local token link still works. Set a password, or run `auth disable`.")
    if cfg.get("require_totp") and not st["totp_enabled"]:
        print()
        print("⚠️  A 2FA code is required but no TOTP secret is enrolled — login would fail.")
        print("   Run `personalclaw auth totp setup`, or turn require_totp off.")
    return 0


def _auth_config() -> dict:
    from personalclaw.config.loader import AppConfig

    return AppConfig.load().to_dict().get("auth", {})


def _set_auth_field(name: str, value: object) -> None:
    """Write one `auth.*` field into config.json, preserving everything else, in the config
    transaction — so a running gateway saving a setting at the same moment keeps it."""
    from personalclaw.config.loader import ConfigWriteError
    from personalclaw.config.transactions import mutate_config

    def _apply(data: dict) -> None:
        section = data.get("auth")
        if not isinstance(section, dict):
            section = data["auth"] = {}
        section[name] = value

    try:
        mutate_config(_apply)
    except ConfigWriteError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


def _read_new_password() -> str | None:
    """Prompt twice for a password. Returns None if the user aborted or they differed."""
    if not sys.stdin.isatty():
        # Deliberately refuse to read a password from a pipe. A piped secret is one that
        # came from a shell history, a script, or a CI log — see `auth bootstrap` for the
        # unattended path, which takes it from the environment instead.
        print("❌ A password must be typed at a terminal.", file=sys.stderr)
        print(
            "   For unattended installs set PERSONALCLAW_LOGIN_USER/PERSONALCLAW_LOGIN_PASSWORD",
            file=sys.stderr,
        )
        print("   and the gateway will enroll it on first start.", file=sys.stderr)
        return None
    try:
        first = getpass.getpass("New password: ")
        second = getpass.getpass("Confirm password: ")
    except (KeyboardInterrupt, EOFError):
        print("\nAborted.", file=sys.stderr)
        return None
    if first != second:
        print("❌ The passwords did not match.", file=sys.stderr)
        return None
    return first


def auth_cmd(args) -> int:
    """``personalclaw auth [status|set-password|enable|disable|totp|enroll|revoke|rotate-key]``.

    A bare ``auth`` shows the status. So does anything else the dispatch below does not name,
    the one answer that changes nothing (the parser allows no other command).
    """
    action = str(getattr(args, "auth_command", "") or "")

    if action == "set-password":
        user = str(getattr(args, "user", "") or "").strip() or os.environ.get("USER", "owner")
        plaintext = _read_new_password()
        if plaintext is None:
            return 1
        try:
            creds.set_password(user, plaintext)
        except ValueError as exc:
            print(f"❌ {exc}", file=sys.stderr)
            return 1
        except creds.CredentialError as exc:
            print(f"❌ {exc}", file=sys.stderr)
            return 1
        print(f"✅ Password set for {user!r} ({creds.credentials_path()}, 0600).")
        if not _auth_config().get("login_enabled"):
            print("   Login is still off — run `personalclaw auth enable` to offer the form.")
        return 0

    if action == "enable":
        if not creds.has_credentials():
            print(
                "❌ No credential is set. Run `personalclaw auth set-password` first.",
                file=sys.stderr,
            )
            print(
                "   Enabling login without one would offer a form nobody can pass.", file=sys.stderr
            )
            return 1
        _set_auth_field("login_enabled", True)
        print("✅ Owner login enabled. Restart the gateway for it to take effect.")
        print("   The local token link keeps working — it stays the escape hatch.")
        return 0

    if action == "disable":
        _set_auth_field("login_enabled", False)
        print("✅ Owner login disabled. The stored credential is kept.")
        print("   Use `personalclaw auth set-password` to change it, or delete it with --clear.")
        return 0

    if action == "totp":
        return _totp_cmd(args)

    if action == "enroll":
        return _enroll_cmd(args)

    if action == "revoke":
        return _revoke_cmd(args)

    if action == "rotate-key":
        return _rotate_key_cmd(args)

    return _print_status()


def _enroll_cmd(args) -> int:
    """``personalclaw auth enroll [--label NAME] [--clear]`` — a single-use device code."""
    from personalclaw.auth import enrollment

    if bool(getattr(args, "clear", False)):
        enrollment.clear_codes()
        print("✅ Cleared every outstanding enrollment code.")
        return 0

    code, _expires_at = enrollment.issue_code(label=str(getattr(args, "label", "") or ""))
    mins = enrollment.CODE_TTL_SECS // 60
    print("✅ Enrollment code (single use, valid for %d minutes):" % mins)
    print()
    print(f"    {enrollment.format_code(code)}")
    print()
    print("Open the dashboard on the other device and enter this code. It is shown once,")
    print("works exactly once, and expires on its own — losing it costs nothing, just")
    print("run this again.")
    return 0


def _revoke_cmd(args) -> int:
    """``personalclaw auth revoke --all`` — end every session.

    **Routed through the RUNNING gateway**, not by editing the session file. Clearing the file
    from a separate process leaves the live gateway's in-memory nonce set untouched, so it keeps
    honoring exactly the sessions you just tried to kill — the command prints success and the
    stolen cookie still works. Found in live validation, which is the only place a
    two-process-state bug like this shows up.

    The gateway is this home's (``home_gateway.reach``). Only when no gateway of this home is
    running is the store cleared here: nothing is holding contradictory state then, and refusing
    would leave no way to revoke offline. A port named that is not this home's gateway, or a
    gateway that refused, changes nothing.

    Only `--all` is offered. A per-nonce revoke would mean printing live nonces to choose one,
    and a nonce in a terminal or shell history is a credential.
    """
    if not bool(getattr(args, "all", False)):
        print("Usage: personalclaw auth revoke --all", file=sys.stderr)
        print(
            "  Ends every dashboard session. You will need to log in (or use a token) again.",
            file=sys.stderr,
        )
        return 2

    from personalclaw import home_gateway

    try:
        gateway = home_gateway.reach(getattr(args, "port", None))
    except home_gateway.NoGatewayRunning:
        _sessions_here().revoke_all_sessions()
        print("✅ Revoked every stored session (no gateway was running).")
        print("   Your password and 2FA enrollment are untouched.")
        return 0
    except home_gateway.GatewayError as exc:
        print(f"❌ {exc} Nothing was revoked.", file=sys.stderr)
        return 1
    try:
        status, answer = gateway.post("/api/logout", {}, secret_header="X-Local-Secret")
    except home_gateway.GatewayError as exc:
        print(f"❌ {exc} Nothing was revoked.", file=sys.stderr)
        return 1
    if status != 200 or not answer.get("ok"):
        print(
            "❌ The gateway did not revoke the sessions: "
            f"{home_gateway.error_text(answer, status)}",
            file=sys.stderr,
        )
        return 1
    print("✅ Revoked every dashboard session (live gateway + on disk).")
    print("   Your password and 2FA enrollment are untouched.")
    return 0


def _sessions_here() -> Any:
    """The session module, for acting on the store in THIS process — only when no gateway is
    running, since a running one holds the sessions (and the key) in memory. One import site for
    both commands that fall back to it."""
    import personalclaw.dashboard.token_auth as token_auth

    return token_auth


#: The work ``personalclaw auth rotate-key`` names on its call to the gateway: your own command,
#: made at this computer, which is no chat's, job's or app's.
ROTATE_KEY_WORK = "cli:auth-rotate-key"


def _rotate_key_cmd(args) -> int:
    """``personalclaw auth rotate-key`` — replace the key every sign-in is signed with.

    Signs out every browser, paired device and token at once (``token_auth.rotate_signing_key``),
    and each is told why when it next connects. For when the key, or a sign-in, may have been
    copied: signing sessions out one by one leaves the key that could mint new ones.

    **Routed through the RUNNING gateway**, for the reason ``auth revoke --all`` is: it holds the
    key and every live session in memory, so a key written from another process would leave it
    signing and accepting with the old one until it restarted. It goes to this home's gateway
    (``home_gateway.reach``) with the local secret (``/api/auth/rotate-key`` is a mixed internal
    path). With no gateway of this home running there is nothing holding the old key, so it is
    replaced here; a port named that is not this home's gateway changes nothing.
    """
    from personalclaw import home_gateway

    try:
        gateway = home_gateway.reach(getattr(args, "port", None))
    except home_gateway.NoGatewayRunning:
        gateway = None
    except home_gateway.GatewayError as exc:
        print(f"❌ {exc} Nobody was signed out.", file=sys.stderr)
        return 1
    if gateway is None:
        signed_out = _sessions_here().rotate_signing_key(actor="cli")
    else:
        try:
            status, answer = gateway.post(
                "/api/auth/rotate-key",
                {"confirm": True},
                secret_header="X-Internal-Secret",
                work=ROTATE_KEY_WORK,
            )
        except home_gateway.GatewayError as exc:
            print(f"❌ {exc} Nobody was signed out.", file=sys.stderr)
            return 1
        if status != 200 or not answer.get("ok"):
            print(
                "❌ The gateway did not replace the key, so nobody was signed out: "
                f"{home_gateway.error_text(answer, status)}",
                file=sys.stderr,
            )
            return 1
        signed_out = int(answer.get("signed_out") or 0)
    ended = f"{signed_out} sign-in{'' if signed_out == 1 else 's'}"
    print(f"✅ Replaced the sign-in key and signed everyone out ({ended} ended).")
    print("   Every browser, paired device and token is told why the next time it connects.")
    print("   Integration tokens are separate and keep working. To sign this computer's browser")
    print("   back in, run `personalclaw token` and open the link it prints.")
    return 0


def _totp_cmd(args) -> int:
    sub = str(getattr(args, "totp_action", "") or "setup")
    if sub == "disable":
        if _auth_config().get("require_totp"):
            # Deleting the secret while login still asks for a code would make password login
            # impossible — the state `auth status` warns about, reached on purpose.
            print(
                "❌ Login still requires a 2FA code (auth.require_totp). Turn that off first "
                "(Settings → Login), then turn 2FA off.",
                file=sys.stderr,
            )
            return 1
        creds.disable_totp()
        print("✅ 2FA turned off and its secret deleted. Turning it on again enrolls a new one.")
        return 0
    if not creds.has_credentials():
        print("❌ Set a password first — 2FA is a second factor, not the first.", file=sys.stderr)
        return 1

    from personalclaw.auth.totp import new_secret, provisioning_uri

    secret = new_secret()
    creds.set_totp_secret(secret)
    user = creds.status()["username"] or "owner"
    print("✅ 2FA enrolled. Add this to your authenticator app NOW — it is shown once:")
    print()
    print(f"    secret: {secret}")
    print(f"    uri:    {provisioning_uri(secret, user)}")
    print()
    print("Then set `auth.require_totp` to true (Settings → Login, or config.json) to")
    print("require the code at login. Verify the code works BEFORE requiring it.")
    return 0
