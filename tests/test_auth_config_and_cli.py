"""The `auth` config section and the `personalclaw auth` CLI (REMOTE-USER-AUTH C4/C5).

Two things are load-bearing here and are asserted rather than assumed:

1. **The defaults reproduce today's behavior.** `login_enabled` off means an install that
   never touches this section behaves exactly as it did before the section existed.
2. **The password is not reachable through the config surface.** `auth.login_enabled` being
   PATCH-editable must not drag the credential along with it.
"""

from __future__ import annotations

import json

import pytest

from personalclaw.auth import credentials as creds
from personalclaw.config import credentials as cred_store
from personalclaw.config.loader import AppConfig


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(creds, "config_dir", lambda: tmp_path)
    return tmp_path


# ── Defaults preserve today's behavior ────────────────────────────────────


def test_login_is_off_by_default() -> None:
    """The whole plan hinges on this: an existing install must not sprout a login page."""
    assert AppConfig().auth.login_enabled is False


def test_default_values() -> None:
    auth = AppConfig().auth
    assert auth.session_ttl == "30d"
    assert auth.require_totp is False
    assert auth.lockout_threshold == 5
    assert auth.lockout_window == "15m"


def test_a_config_with_no_auth_section_loads_the_defaults(_isolated_home) -> None:
    (_isolated_home / "config.json").write_text(json.dumps({"agent": {}}), encoding="utf-8")
    assert AppConfig.load().auth == AppConfig().auth


# ── Round trip ────────────────────────────────────────────────────────────


def test_auth_section_round_trips_through_to_dict(_isolated_home) -> None:
    written = {
        "login_enabled": True,
        "session_ttl": "7d",
        "require_totp": True,
        "lockout_threshold": 3,
        "lockout_window": "5m",
    }
    (_isolated_home / "config.json").write_text(json.dumps({"auth": written}), encoding="utf-8")
    assert AppConfig.load().to_dict()["auth"] == written


def test_save_then_load_preserves_the_section(_isolated_home) -> None:
    cfg = AppConfig()
    cfg.auth.login_enabled = True
    cfg.auth.session_ttl = "12h"
    cfg.save()
    reloaded = AppConfig.load()
    assert reloaded.auth.login_enabled is True
    assert reloaded.auth.session_ttl == "12h"


# ── Hand-edited nonsense must not brick the gateway ───────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"lockout_threshold": 0}, 1),  # 0 would lock out on the zeroth attempt
        ({"lockout_threshold": -5}, 1),
        ({"lockout_threshold": "oops"}, 5),  # non-numeric → the default
        ({"lockout_threshold": 7}, 7),
    ],
)
def test_lockout_threshold_is_clamped_not_fatal(_isolated_home, raw: dict, expected: int) -> None:
    (_isolated_home / "config.json").write_text(json.dumps({"auth": raw}), encoding="utf-8")
    assert AppConfig.load().auth.lockout_threshold == expected


def test_a_non_dict_auth_section_falls_back_to_defaults(_isolated_home) -> None:
    (_isolated_home / "config.json").write_text(
        json.dumps({"auth": "yes please"}), encoding="utf-8"
    )
    assert AppConfig.load().auth == AppConfig().auth


def test_an_empty_session_ttl_falls_back_to_the_default(_isolated_home) -> None:
    (_isolated_home / "config.json").write_text(
        json.dumps({"auth": {"session_ttl": ""}}), encoding="utf-8"
    )
    assert AppConfig.load().auth.session_ttl == "30d"


# ── The PATCH surface ─────────────────────────────────────────────────────


def test_the_editable_allowlist_covers_the_intended_knobs() -> None:
    from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

    for key in (
        "auth.login_enabled",
        "auth.require_totp",
        "auth.session_ttl",
        "auth.lockout_threshold",
        "auth.lockout_window",
    ):
        assert key in _EDITABLE_CONFIG, f"{key} should be runtime-editable"


#: The one credential-WORDED key that carries no credential material. `security.credential_keychain`
#: (SH-2) is a boolean naming WHERE secrets are written — the secrets themselves go through
#: `save_credential`, never through a config PATCH. Listed explicitly so a second such key cannot
#: appear without someone making the same argument out loud.
_NON_SECRET_CREDENTIAL_WORDED_KEYS = frozenset({"security.credential_keychain"})


def test_no_credential_field_is_ever_patchable() -> None:
    """A password is not a setting. Nothing password-shaped may enter the PATCH allowlist.

    The forbidden-word list is a NAME heuristic for the real property — "does this key carry
    secret material" — and SH-2 produced the first true false positive: a boolean gate whose name
    contains "credential" but whose value is `True`/`False`. Rather than rename the field away
    from the atom's own vocabulary, the rail now measures the property it always meant:

    * a credential-worded key must be declared `{"type": "bool"}`, because secret material can
      only travel through a STRING — a `str`/`str_list` spec is the hazard, and the substring
      match never checked the type at all; and
    * it must be named in :data:`_NON_SECRET_CREDENTIAL_WORDED_KEYS`, so the exemption is a
      reviewed list of one rather than a widened pattern.

    Strictly stronger than the version it replaces: `{"security.api_password": {"type": "str"}}`
    fails on BOTH clauses, and a bool sneaking in unlisted fails on the second.
    """
    from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

    forbidden = ("password", "credential", "hash", "totp_secret", "secret")
    worded = [k for k in _EDITABLE_CONFIG if any(word in k.lower() for word in forbidden)]

    unlisted = [k for k in worded if k not in _NON_SECRET_CREDENTIAL_WORDED_KEYS]
    assert unlisted == [], (
        f"credential-worded key(s) in the PATCH allowlist: {unlisted}. A secret is not a setting. "
        "If it genuinely carries no secret material, say so in _NON_SECRET_CREDENTIAL_WORDED_KEYS."
    )
    not_bool = [k for k in worded if _EDITABLE_CONFIG[k].get("type") != "bool"]
    assert not_bool == [], (
        f"credential-worded key(s) that are not booleans: {not_bool}. Secret material travels as "
        "a string — a credential-worded key holding one is the defect this rail exists to catch."
    )
    # Vacuity floor: the population must be non-empty, or both assertions above pass by finding
    # nothing and the rail says nothing about the allowlist it is meant to police.
    assert worded, "the forbidden-word sweep matched nothing — the rail is vacuous"


def test_every_auth_field_is_either_patchable_or_deliberately_not() -> None:
    """Catches a NEW auth field added without deciding whether it is runtime-editable."""
    from dataclasses import fields

    from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

    known = {f.name for f in fields(AppConfig().auth)}
    editable = {k.split(".", 1)[1] for k in _EDITABLE_CONFIG if k.startswith("auth.")}
    assert known == editable, (
        "an auth config field is neither in the PATCH allowlist nor removed from it — "
        "decide explicitly (a credential-adjacent field should NOT be editable)"
    )


# ── The duration parser used to read session_ttl ──────────────────────────


@pytest.mark.parametrize(
    ("raw", "secs"),
    [("30d", 30 * 86400), ("1d", 86400), ("12h", 12 * 3600), ("15m", 900), (" 7d ", 7 * 86400)],
)
def test_config_duration_parsing(raw: str, secs: int) -> None:
    from personalclaw.dashboard.token_auth import parse_config_duration

    assert parse_config_duration(raw, default_secs=999) == secs


@pytest.mark.parametrize("raw", ["", "d", "30", "30s", "abc", "-1d", "1.5h", "30D", None])
def test_unparseable_config_duration_uses_the_default(raw) -> None:  # noqa: ANN001
    """A typo in config.json must not brick the box — it takes the documented default."""
    from personalclaw.dashboard.token_auth import parse_config_duration

    assert parse_config_duration(raw, default_secs=4242) == 4242


def test_config_duration_is_capped() -> None:
    from personalclaw.dashboard.token_auth import MAX_SESSION_TTL_SECS, parse_config_duration

    assert parse_config_duration("99999d", default_secs=1) == MAX_SESSION_TTL_SECS


def test_the_token_duration_parser_is_unchanged() -> None:
    """`parse_duration` serves `--ttl`, where an unknown unit must stay a hard error."""
    from personalclaw.dashboard.token_auth import parse_duration

    assert parse_duration("30d") is None  # deliberately still rejected here
    assert parse_duration("12h") == 12 * 3600


# ── The CLI ───────────────────────────────────────────────────────────────


#: `cli.py` has no ``__main__`` guard (it is reached through the ``personalclaw`` console
#: script), so a ``-m`` run imports the module and exits silently. Call `main` directly to
#: exercise the REAL parser rather than a reimplementation of it.
_RUN_CLI = "from personalclaw.cli import main; main()"


class _Args:
    def __init__(self, **kw) -> None:
        self.__dict__.update(kw)


def test_status_runs_with_nothing_configured(capsys) -> None:
    from personalclaw.auth.cli import auth_cmd

    assert auth_cmd(_Args(auth_command="status")) == 0
    out = capsys.readouterr().out
    assert "NOT set" in out
    assert "enabled:     no" in out


def test_enable_is_refused_without_a_credential(capsys) -> None:
    """Enabling login with no password would offer a form nobody can pass."""
    from personalclaw.auth.cli import auth_cmd

    assert auth_cmd(_Args(auth_command="enable")) == 1
    assert "No credential is set" in capsys.readouterr().out


def test_enable_then_disable_writes_only_the_flag(_isolated_home, capsys) -> None:
    from personalclaw.auth.cli import auth_cmd

    creds.set_password("jordan", "correct-horse-battery")
    assert auth_cmd(_Args(auth_command="enable")) == 0

    # Read the FILE, not the loaded object: load() materialises every default section in
    # memory, so asserting on `to_dict()` would report defaults the CLI never wrote. (It no
    # longer WRITES them back — PHF-15 made load() a pure read — but the in-memory filling
    # is still there, and that is what this ordering guards against.) The property under
    # test is that `enable` touches one key.
    on_disk = json.loads((_isolated_home / "config.json").read_text(encoding="utf-8"))
    assert on_disk == {"auth": {"login_enabled": True}}, "only the flag should be written"
    assert AppConfig.load().auth.login_enabled is True

    assert auth_cmd(_Args(auth_command="disable")) == 0
    assert AppConfig.load().auth.login_enabled is False
    # Disabling must not throw the credential away — that would make "turn it off for a
    # minute" destructive.
    assert creds.has_credentials() is True


def test_enable_preserves_unrelated_config(_isolated_home) -> None:
    from personalclaw.auth.cli import auth_cmd

    (_isolated_home / "config.json").write_text(
        json.dumps({"agent": {"bot_name": "Ada"}, "auth": {"session_ttl": "7d"}}), encoding="utf-8"
    )
    creds.set_password("jordan", "correct-horse-battery")
    assert auth_cmd(_Args(auth_command="enable")) == 0
    on_disk = json.loads((_isolated_home / "config.json").read_text(encoding="utf-8"))
    assert on_disk["agent"]["bot_name"] == "Ada"
    assert on_disk["auth"] == {"session_ttl": "7d", "login_enabled": True}
    assert AppConfig.load().agent.bot_name == "Ada"


def test_set_password_refuses_a_non_tty(monkeypatch, capsys) -> None:
    """A piped password came from a shell history or a CI log. Point at the env path."""
    import sys

    from personalclaw.auth.cli import auth_cmd

    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    assert auth_cmd(_Args(auth_command="set-password", user="jordan")) == 1
    out = capsys.readouterr().out
    assert "must be typed at a terminal" in out
    assert "PERSONALCLAW_LOGIN_USER" in out
    assert creds.has_credentials() is False


def test_set_password_prompts_twice_and_rejects_a_mismatch(monkeypatch, capsys) -> None:
    import sys

    import personalclaw.auth.cli as cli

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    answers = iter(["correct-horse-battery", "correct-horse-bettery"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda *_a, **_k: next(answers))
    assert cli.auth_cmd(_Args(auth_command="set-password", user="jordan")) == 1
    assert "did not match" in capsys.readouterr().out
    assert creds.has_credentials() is False


def test_set_password_stores_on_a_match(monkeypatch, capsys) -> None:
    import sys

    import personalclaw.auth.cli as cli

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(cli.getpass, "getpass", lambda *_a, **_k: "correct-horse-battery")
    assert cli.auth_cmd(_Args(auth_command="set-password", user="jordan")) == 0
    assert creds.verify_password("jordan", "correct-horse-battery") is True
    # The plaintext must not be echoed back at the user's terminal.
    assert "correct-horse-battery" not in capsys.readouterr().out


def test_set_password_reports_a_too_short_password(monkeypatch, capsys) -> None:
    import sys

    import personalclaw.auth.cli as cli

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(cli.getpass, "getpass", lambda *_a, **_k: "short")
    assert cli.auth_cmd(_Args(auth_command="set-password", user="jordan")) == 1
    assert "at least" in capsys.readouterr().out


def test_status_warns_when_login_is_on_but_unconfigured(_isolated_home, capsys) -> None:
    """The one combination that guarantees nobody can log in must be called out."""
    from personalclaw.auth.cli import auth_cmd

    (_isolated_home / "config.json").write_text(
        json.dumps({"auth": {"login_enabled": True}}), encoding="utf-8"
    )
    assert auth_cmd(_Args(auth_command="status")) == 0
    assert "cannot succeed" in capsys.readouterr().out


def test_status_warns_when_totp_is_required_but_not_enrolled(_isolated_home, capsys) -> None:
    from personalclaw.auth.cli import auth_cmd

    creds.set_password("jordan", "correct-horse-battery")
    (_isolated_home / "config.json").write_text(
        json.dumps({"auth": {"login_enabled": True, "require_totp": True}}), encoding="utf-8"
    )
    assert auth_cmd(_Args(auth_command="status")) == 0
    assert "no TOTP secret is enrolled" in capsys.readouterr().out


def test_totp_setup_needs_a_password_first(capsys) -> None:
    from personalclaw.auth.cli import auth_cmd

    assert auth_cmd(_Args(auth_command="totp", totp_action="setup")) == 1
    assert "Set a password first" in capsys.readouterr().out


def test_totp_setup_prints_the_secret_once(monkeypatch, capsys) -> None:
    from personalclaw.auth import totp
    from personalclaw.auth.cli import auth_cmd

    saved: dict[str, str] = {}
    monkeypatch.setattr(cred_store, "save_credential", lambda k, v: saved.update({k: v}))
    creds.set_password("jordan", "correct-horse-battery")
    assert auth_cmd(_Args(auth_command="totp", totp_action="setup")) == 0

    out = capsys.readouterr().out
    secret = saved[creds.TOTP_SECRET_KEY]
    assert secret in out and "shown once" in out
    # The printed secret must be the one that will actually verify.
    assert totp.verify_code(secret, totp.code_now(secret)) is True


def test_an_unknown_subcommand_is_a_usage_error(capsys) -> None:
    from personalclaw.auth.cli import auth_cmd

    assert auth_cmd(_Args(auth_command="frobnicate")) == 2
    assert "Usage:" in capsys.readouterr().out


def test_the_cli_registers_the_auth_command(tmp_path) -> None:
    """A handler nobody can reach is not shipped, so drive the REAL entry point.

    A subprocess (rather than importing a parser factory — `main` builds its parser inline)
    with an isolated PERSONALCLAW_HOME, so this can never touch the developer's real
    credential file.
    """
    import os
    import subprocess
    import sys

    env = {**os.environ, "PERSONALCLAW_HOME": str(tmp_path)}
    proc = subprocess.run(
        [sys.executable, "-c", _RUN_CLI, "auth", "status"],
        capture_output=True,
        text=True,
        env=env,
        timeout=90,
    )
    assert proc.returncode == 0, proc.stderr
    assert "Owner login" in proc.stdout
    assert "NOT set" in proc.stdout


def test_auth_help_lists_the_subcommands(tmp_path) -> None:
    import os
    import subprocess
    import sys

    env = {**os.environ, "PERSONALCLAW_HOME": str(tmp_path)}
    proc = subprocess.run(
        [sys.executable, "-c", _RUN_CLI, "auth", "--help"],
        capture_output=True,
        text=True,
        env=env,
        timeout=90,
    )
    assert proc.returncode == 0, proc.stderr
    for word in ("set-password", "enable", "disable", "status", "totp"):
        assert word in proc.stdout
