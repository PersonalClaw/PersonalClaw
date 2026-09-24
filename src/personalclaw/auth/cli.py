"""``personalclaw auth`` — manage the owner login (REMOTE-USER-AUTH C5 / T2.3).

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
import json
import os
import sys

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
    """Write one `auth.*` field into config.json, preserving everything else."""
    from personalclaw.agent import _atomic_json_write
    from personalclaw.config.loader import config_path

    path = config_path()
    data: dict = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (json.JSONDecodeError, OSError) as exc:
            print(f"❌ Could not read {path}: {exc}")
            raise SystemExit(1) from exc
    section = data.get("auth")
    if not isinstance(section, dict):
        section = {}
    section[name] = value
    data["auth"] = section
    _atomic_json_write(path, data)


def _read_new_password() -> str | None:
    """Prompt twice for a password. Returns None if the user aborted or they differed."""
    if not sys.stdin.isatty():
        # Deliberately refuse to read a password from a pipe. A piped secret is one that
        # came from a shell history, a script, or a CI log — see `auth bootstrap` for the
        # unattended path, which takes it from the environment instead.
        print("❌ A password must be typed at a terminal.")
        print("   For unattended installs set PERSONALCLAW_LOGIN_USER/PERSONALCLAW_LOGIN_PASSWORD")
        print("   and the gateway will enroll it on first start.")
        return None
    try:
        first = getpass.getpass("New password: ")
        second = getpass.getpass("Confirm password: ")
    except (KeyboardInterrupt, EOFError):
        print("\nAborted.")
        return None
    if first != second:
        print("❌ The passwords did not match.")
        return None
    return first


def auth_cmd(args) -> int:
    """``personalclaw auth set-password|enable|disable|status|totp``."""
    action = str(getattr(args, "auth_command", "") or "")

    if action in ("", "status"):
        return _print_status()

    if action == "set-password":
        user = str(getattr(args, "user", "") or "").strip() or os.environ.get("USER", "owner")
        plaintext = _read_new_password()
        if plaintext is None:
            return 1
        try:
            creds.set_password(user, plaintext)
        except ValueError as exc:
            print(f"❌ {exc}")
            return 1
        except creds.CredentialError as exc:
            print(f"❌ {exc}")
            return 1
        print(f"✅ Password set for {user!r} ({creds.credentials_path()}, 0600).")
        if not _auth_config().get("login_enabled"):
            print("   Login is still off — run `personalclaw auth enable` to offer the form.")
        return 0

    if action == "enable":
        if not creds.has_credentials():
            print("❌ No credential is set. Run `personalclaw auth set-password` first.")
            print("   Enabling login without one would offer a form nobody can pass.")
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

    print("Usage: personalclaw auth set-password|enable|disable|status|totp|enroll|revoke")
    return 2


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

    Falls back to a direct store clear only when no gateway is running (nothing is holding
    contradictory state then, and refusing would leave no way to revoke offline).

    Only `--all` is offered. A per-nonce revoke would mean printing live nonces to choose one,
    and a nonce in a terminal or shell history is a credential.
    """
    if not bool(getattr(args, "all", False)):
        print("Usage: personalclaw auth revoke --all")
        print("  Ends every dashboard session. You will need to log in (or use a token) again.")
        return 2

    from personalclaw.config.loader import _DEFAULT_PORT

    port = int(getattr(args, "port", 0) or _DEFAULT_PORT)
    if _revoke_via_gateway(port):
        print("✅ Revoked every dashboard session (live gateway + on disk).")
        print("   Your password and 2FA enrollment are untouched.")
        return 0

    from personalclaw.dashboard.token_auth import revoke_all_sessions

    revoke_all_sessions()
    print("✅ Revoked every stored session (no gateway was running).")
    print("   Your password and 2FA enrollment are untouched.")
    return 0


def _revoke_via_gateway(port: int) -> bool:
    """Ask a running gateway to revoke everything. False when none is reachable.

    Reuses the existing loopback + `.local_secret` rail that `personalclaw logout` uses, so this
    adds no new authenticated surface — it is the same internal endpoint.
    """
    import json as _json
    import urllib.error
    import urllib.request

    from personalclaw.config.loader import config_dir

    try:
        secret = (config_dir() / ".local_secret").read_text(encoding="utf-8").strip()
    except OSError:
        return False
    if not secret:
        return False

    req = urllib.request.Request(
        f"http://localhost:{port}/api/logout",
        method="POST",
        headers={"X-Local-Secret": secret, "Content-Type": "application/json"},
        data=b"{}",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:  # nosec B310 - fixed loopback URL
            return bool(_json.loads(resp.read()).get("ok"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        return False


def _totp_cmd(args) -> int:
    sub = str(getattr(args, "totp_action", "") or "setup")
    if sub == "disable":
        creds.disable_totp()
        print("✅ 2FA turned off. The secret is kept, so re-enabling needs no re-enrollment.")
        return 0
    if sub != "setup":
        print("Usage: personalclaw auth totp setup|disable")
        return 2
    if not creds.has_credentials():
        print("❌ Set a password first — 2FA is a second factor, not the first.")
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
