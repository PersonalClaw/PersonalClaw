"""A command that makes a value prints it alone, so ``| pbcopy`` and ``$(…)`` get just that.

``personalclaw pair email`` printed ``Pairing code for email: 12345678`` and a sentence; ``auth
enroll`` indented its code among four lines; ``auth totp`` printed a ``secret:`` and a ``uri:``
line among its instructions; ``inbound token create`` put the token in an ``Authorization:
Bearer`` line among twelve; ``spawn run --async`` printed ``Spawned subagent <id>: <task>``. A
script had to cut the value out of text that was free to change. Now stdout is the value and a
newline, the same at a terminal and in a pipe; what it is and what to do next is ``--explain``'s,
on stderr, after it and never repeating it, so ``… --explain | pbcopy`` keeps a secret off the
screen. A warning that the value will not work as printed prints on stderr every time.

``personalclaw token`` has its own file, ``tests/test_token_prints_one_link.py``.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from personalclaw import channel_trust
from personalclaw import cli as cli_module


def _cli(tmp_path: Path, *argv: str) -> subprocess.CompletedProcess:
    """``python -m personalclaw ARGV`` as a script runs it: a home of its own, stdin closed."""
    home = tmp_path / "pc"
    user = tmp_path / "user"
    user.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PERSONALCLAW_", "PYTEST_"))}
    src = Path(__file__).resolve().parents[1] / "src"
    env.update(
        PERSONALCLAW_HOME=str(home),
        PERSONALCLAW_CREDENTIAL_BACKEND="dotenv",
        HOME=str(user),
        PYTHONIOENCODING="utf-8",
        PYTHONPATH=os.pathsep.join([str(src), env.get("PYTHONPATH", "")]),
    )
    folder = tmp_path / "work"
    folder.mkdir(exist_ok=True)
    return subprocess.run(
        [sys.executable, "-m", "personalclaw", *argv],
        cwd=folder,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


def _args(*argv: str) -> argparse.Namespace:
    return cli_module.build_parser().parse_args(list(argv))


# ── pair ──────────────────────────────────────────────────────────────────────

PAIR_EXPLAINED = (
    "Pairing code for email. Have the person you're letting in send it to your agent on email "
    "within 10 minutes. It works once, then expires.\n"
)
PAIR_RULE = (
    "It lets nobody in until email's rule for strangers asks for a code again "
    "(Settings › Devices & Access › Sender trust).\n"
)


def _pair(capsys, *flags: str) -> tuple[str, str]:
    from personalclaw.cli_commands import _pair

    _pair(_args("pair", "email", *flags))
    captured = capsys.readouterr()
    return captured.out, captured.err


def test_pair_prints_the_code_alone_and_it_lets_the_sender_in(capsys):
    channel_trust.set_trust_policies("email", dm="pairing")
    out, err = _pair(capsys)
    assert re.fullmatch(r"\d{8}\n", out), out
    assert err == ""
    assert channel_trust.redeem_pairing_code("email", "rin@example.com", out.strip()) is True


def test_pair_explains_on_stderr_without_the_code(capsys):
    channel_trust.set_trust_policies("email", dm="pairing")
    out, err = _pair(capsys, "--explain")
    assert err == PAIR_EXPLAINED
    assert out.strip() not in err


def test_pair_warns_every_time_a_code_will_not_let_anyone_in(capsys):
    channel_trust.set_trust_policies("email", dm="owner_only")
    out, err = _pair(capsys)
    assert re.fullmatch(r"\d{8}\n", out), out
    assert err == PAIR_RULE
    out, err = _pair(capsys, "--explain")
    assert err == PAIR_EXPLAINED + PAIR_RULE, "the warning prints after any explain text"


def test_pair_in_a_pipe_prints_the_code_and_nothing_else(tmp_path):
    done = _cli(tmp_path, "pair", "email")
    assert done.returncode == 0, done.stderr
    assert re.fullmatch(r"\d{8}\n", done.stdout), done.stdout
    assert done.stderr == ""


# ── auth enroll ───────────────────────────────────────────────────────────────

ENROLL_EXPLAINED = (
    "Enrollment code, single use, valid for 5 minutes. Open the dashboard on the other device and "
    "enter it. It is shown once, works exactly once, and expires on its own: losing it costs "
    "nothing, just run this again.\n"
)


def _auth(capsys, *argv: str) -> tuple[int, str, str]:
    from personalclaw.auth.cli import auth_cmd

    code = auth_cmd(_args("auth", *argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_auth_enroll_prints_the_code_alone(monkeypatch, capsys):
    from personalclaw.auth import enrollment

    monkeypatch.setattr(enrollment, "issue_code", lambda *, label="": ("7K4MQP93", 0.0))
    assert _auth(capsys, "enroll") == (0, enrollment.format_code("7K4MQP93") + "\n", "")


def test_auth_enroll_prints_a_code_that_redeems(capsys):
    from personalclaw.auth import enrollment

    code, out, err = _auth(capsys, "enroll")
    assert (code, err) == (0, "")
    assert re.fullmatch(r"[0-9A-Z]{4}-[0-9A-Z]{4}\n", out), out
    assert enrollment.redeem_code(out.strip()) is True


def test_auth_enroll_explains_on_stderr_without_the_code(capsys):
    code, out, err = _auth(capsys, "enroll", "--explain")
    assert (code, err) == (0, ENROLL_EXPLAINED)
    assert out.strip() not in err


@pytest.mark.parametrize("extra", [["--explain"], ["--label", "phone"]])
def test_auth_enroll_clear_takes_no_flag_for_a_code(capsys, extra):
    code, out, err = _auth(capsys, "enroll", "--clear", *extra)
    assert (code, out) == (2, "")
    assert "takes neither --label nor --explain" in err, err


# ── auth totp ─────────────────────────────────────────────────────────────────


@pytest.fixture
def owner(monkeypatch):
    """An owner with a password, the credential store kept in a dict."""
    from personalclaw.auth import credentials as creds
    from personalclaw.config import credentials as cred_store

    saved: dict[str, str] = {}
    monkeypatch.setattr(cred_store, "save_credential", lambda k, v: saved.update({k: v}))
    monkeypatch.setattr(cred_store, "delete_credential", lambda k: saved.pop(k, None) is not None)
    creds.set_password("jordan", "correct-horse-battery")
    return saved


def test_auth_totp_json_is_the_secret_and_its_link(owner, capsys):
    import json

    from personalclaw.auth import credentials as creds
    from personalclaw.auth import totp

    code, out, err = _auth(capsys, "totp", "--json")
    secret = owner[creds.TOTP_SECRET_KEY]
    doc = {"secret": secret, "uri": totp.provisioning_uri(secret, "jordan")}
    assert (code, out, err) == (0, json.dumps(doc, indent=2) + "\n", "")


def test_auth_totp_will_not_replace_an_enrolled_secret_without_rotate(owner, capsys):
    from personalclaw.auth import credentials as creds
    from personalclaw.auth import totp

    assert _auth(capsys, "totp")[0] == 0
    first = owner[creds.TOTP_SECRET_KEY]
    code, out, err = _auth(capsys, "totp")
    assert (code, out) == (1, "")
    assert err == (
        "❌ 2FA is already enrolled. personalclaw auth totp --rotate replaces its secret, and "
        "codes from the old authenticator entry stop working.\n"
    )
    assert owner[creds.TOTP_SECRET_KEY] == first
    code, out, err = _auth(capsys, "totp", "--rotate")
    rotated = owner[creds.TOTP_SECRET_KEY]
    assert rotated != first
    assert (code, out, err) == (0, totp.provisioning_uri(rotated, "jordan") + "\n", "")


@pytest.mark.parametrize("flag", ["--json", "--explain", "--rotate"])
def test_auth_totp_disable_takes_no_flag_for_a_secret(owner, capsys, flag):
    code, out, err = _auth(capsys, "totp", "disable", flag)
    assert (code, out) == (2, "")
    assert "makes no secret" in err, err


# ── inbound token create ──────────────────────────────────────────────────────

SWITCHES_OFF = (
    "It is refused until these are on: personalclaw config set external_access.enabled true; "
    "personalclaw config set external_access.mcp.enabled true\n"
)


@pytest.fixture
def mcp_token(unset_env):
    """The mcp token's variable, given back after the test: creating a token mirrors it into the
    process environment, as a running gateway needs."""
    unset_env("PERSONALCLAW_INBOUND_MCP_TOKEN")


def _inbound(capsys, *argv: str) -> tuple[int, str, str]:
    from personalclaw.inbound import auth

    code = auth.inbound_cmd(_args("inbound", "token", *argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_inbound_token_create_prints_the_token_alone_and_warns_while_its_surface_is_off(
    mcp_token, capsys
):
    from personalclaw.inbound import auth

    code, out, err = _inbound(capsys, "create", "mcp")
    assert code == 0
    assert out == auth.load_surface_token("mcp") + "\n"
    assert err == SWITCHES_OFF, "the shipped config has both switches off"


def test_inbound_token_create_says_nothing_more_once_its_surface_is_on(mcp_token, capsys):
    import json

    from personalclaw.config import loader

    loader.config_path().parent.mkdir(parents=True, exist_ok=True)
    loader.config_path().write_text(
        json.dumps({"external_access": {"enabled": True, "mcp": {"enabled": True}}})
    )
    code, out, err = _inbound(capsys, "create", "mcp")
    assert (code, err) == (0, "")


@pytest.mark.parametrize(
    "config, switch",
    [
        ({"external_access": {"enabled": True}}, "external_access.mcp.enabled"),
        ({"external_access": {"mcp": {"enabled": True}}}, "external_access.enabled"),
    ],
)
def test_inbound_token_create_names_the_one_switch_that_is_off(mcp_token, capsys, config, switch):
    import json

    from personalclaw.config import loader

    loader.config_path().parent.mkdir(parents=True, exist_ok=True)
    loader.config_path().write_text(json.dumps(config))
    code, out, err = _inbound(capsys, "create", "mcp")
    assert code == 0
    assert err == f"It is refused until this is on: personalclaw config set {switch} true\n"


def test_inbound_token_create_json_is_one_document(mcp_token, capsys):
    import json

    from personalclaw.inbound import auth, tokens

    code, out, _err = _inbound(capsys, "create", "mcp", "--json", "--ttl", "7d")
    token = auth.load_surface_token("mcp")
    record = tokens.surface_token("mcp", token)
    doc = {
        "surface": "mcp",
        "token": token,
        "credential_key": "PERSONALCLAW_INBOUND_MCP_TOKEN",
        "expires_in": 604800,
        "expires_at": record.expires_at,
    }
    assert (code, out) == (0, json.dumps(doc, indent=2) + "\n")


@pytest.mark.parametrize("action", ["show", "revoke"])
@pytest.mark.parametrize("flags", [["--json"], ["--explain"], ["--ttl", "7d"], ["--rotate"]])
def test_inbound_token_show_and_revoke_take_no_flag_for_a_new_token(
    mcp_token, capsys, action, flags
):
    code, out, err = _inbound(capsys, action, "mcp", *flags)
    assert (code, out) == (2, "")
    assert f"inbound token {action} does not take {flags[0]}" in err, err


def test_inbound_token_show_names_every_flag_it_does_not_take(mcp_token, capsys):
    code, out, err = _inbound(capsys, "show", "mcp", "--explain", "--rotate", "--ttl", "7d")
    assert (code, out) == (2, "")
    assert err == (
        "❌ inbound token show does not take --explain, --rotate or --ttl: only create makes a "
        "token.\n"
    )


# ── inbound webhook create ────────────────────────────────────────────────────


def _webhook_automation() -> str:
    """A webhook automation its owner made and allowed, in this test's home."""
    from personalclaw.config import config_dir
    from personalclaw.triggers.models import Trigger
    from personalclaw.triggers.store import TriggerStore

    TriggerStore(base_dir=config_dir()).upsert(
        Trigger(
            id="webhook:build-finished",
            name="Build finished",
            kind="webhook",
            created_by="user",
            spec={},
            workflow={"inline": {"provider": "notify", "config": {"title_template": "Built"}}},
            capabilities={"providers": ["notify"]},
        )
    )
    return "store:webhook:build-finished"


def test_inbound_webhook_create_json_has_no_address_while_personalclaw_is_not_running(
    capsys, unset_env
):
    import json

    from personalclaw.inbound import auth

    unset_env("PERSONALCLAW_PORT")
    automation = _webhook_automation()
    assert auth.inbound_cmd(_args("inbound", "webhook", "create", automation, "--json")) == 0
    out, err = capsys.readouterr()
    doc = json.loads(out)
    assert list(doc) == [
        "client_id",
        "automation",
        "label",
        "token",
        "url",
        "header",
        "curl",
        "expires_at",
    ]
    assert (doc["url"], doc["curl"]) == (None, None)
    assert doc["header"] == f"Authorization: Bearer {doc['token']}"
    assert "PersonalClaw is not running, so the port it listens on is not known here" in err


# ── --explain never puts the value on stderr ──────────────────────────────────


@pytest.fixture
def made(monkeypatch, tmp_path, unset_env):
    """Each value command, run with --explain: what it printed on stdout and on stderr."""
    from personalclaw.auth import credentials as creds
    from personalclaw.config import credentials as cred_store

    saved: dict[str, str] = {}
    monkeypatch.setattr(cred_store, "save_credential", lambda k, v: saved.update({k: v}))
    unset_env("PERSONALCLAW_INBOUND_MCP_TOKEN", "PERSONALCLAW_PORT")

    def run(command: str, capsys) -> tuple[str, str]:
        from personalclaw.auth.cli import auth_cmd
        from personalclaw.cli_commands import _pair
        from personalclaw.inbound.auth import inbound_cmd

        if command == "token":
            from fakes import gateway_stand_in

            from personalclaw import cli_server

            home = tmp_path / "gw"
            home.mkdir()
            (home / ".local_secret").write_text("synthetic-local-secret\n")
            monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
            reply = {
                "token": "synthetic.placeholder.not-a-real-token",
                "expires_in": 3600,
                "expires_at": 0.0,
                "open_within": 3600,
            }
            with gateway_stand_in(home, routes={("GET", "/api/token/local"): (200, reply)}) as gw:
                gw.record()
                cli_server._token(_args("token", "--explain"))
        elif command == "pair":
            _pair(_args("pair", "email", "--explain"))
        elif command == "auth enroll":
            auth_cmd(_args("auth", "enroll", "--explain"))
        elif command == "auth totp":
            creds.set_password("jordan", "correct-horse-battery")
            auth_cmd(_args("auth", "totp", "--explain"))
        elif command == "inbound token create":
            inbound_cmd(_args("inbound", "token", "create", "mcp", "--explain"))
        else:
            inbound_cmd(_args("inbound", "webhook", "create", _webhook_automation(), "--explain"))
        captured = capsys.readouterr()
        return captured.out, captured.err

    return run


@pytest.mark.parametrize(
    "command",
    ["token", "pair", "auth enroll", "auth totp", "inbound token create", "inbound webhook create"],
)
def test_explain_never_puts_the_value_on_stderr(made, capsys, command):
    """`… --explain | pbcopy` copies the value and shows the guidance, never the value itself."""
    out, err = made(command, capsys)
    value = out.strip()
    assert value and "\n" not in value, out
    assert err, "--explain says something"
    assert value not in err
    if command == "auth totp":
        secret = value.split("secret=", 1)[1].split("&", 1)[0]
        assert secret not in err


# ── config get KEY ────────────────────────────────────────────────────────────


def _config_get(capsys, key: str) -> tuple[str, str]:
    from personalclaw.cli_config import _config_cmd

    _config_cmd(_args("config", "get", key))
    captured = capsys.readouterr()
    return captured.out, captured.err


def _write_config(doc: dict) -> None:
    import json

    from personalclaw.config import loader

    loader.config_path().parent.mkdir(parents=True, exist_ok=True)
    loader.config_path().write_text(json.dumps(doc), encoding="utf-8")


@pytest.mark.parametrize(
    "key, printed",
    [
        ("auth.login_enabled", "false\n"),
        ("probe.nothing", "null\n"),
        ("auth.lockout_threshold", "3\n"),
        ("agent.spawn_min_memory_gb", "0.5\n"),
        ("dashboard.url", "https://pc.example.com\n"),
    ],
)
def test_config_get_prints_a_scalar_as_json_writes_it(capsys, key, printed):
    _write_config(
        {
            "auth": {"login_enabled": False, "lockout_threshold": 3},
            "agent": {"spawn_min_memory_gb": 0.5},
            "dashboard": {"url": "https://pc.example.com"},
            "probe": {"nothing": None},
        }
    )
    assert _config_get(capsys, key) == (printed, "")


def test_config_get_in_a_pipe_prints_the_value_alone(tmp_path):
    import json

    (tmp_path / "pc").mkdir()
    (tmp_path / "pc" / "config.json").write_text(json.dumps({"auth": {"login_enabled": False}}))
    done = _cli(tmp_path, "config", "get", "auth.login_enabled")
    assert (done.returncode, done.stdout) == (0, "false\n"), done.stderr


# ── memory export ─────────────────────────────────────────────────────────────

EMPTY_MEMORY = {"semantic": [], "episodic": [], "events": []}


def _an_empty_folder_memory(tmp_path: Path) -> str:
    """A folder's memory that keeps no records yet: its directory, and no record store in it."""
    from personalclaw import memory_locality

    folder = tmp_path / "garden"
    folder.mkdir()
    path = memory_locality.partition_for(str(folder))
    path.mkdir(parents=True)
    return path.name


def test_memory_export_of_a_memory_with_no_records_is_still_one_document(tmp_path, capsys):
    import json

    from personalclaw.cli_commands import _memory_cmd

    pid = _an_empty_folder_memory(tmp_path)
    _memory_cmd(_args("memory", "export", "--partition", pid))
    assert capsys.readouterr() == (json.dumps(EMPTY_MEMORY, indent=2) + "\n", "")
    written = tmp_path / "garden-memory.json"
    _memory_cmd(_args("memory", "export", "--partition", pid, "-o", str(written)))
    assert capsys.readouterr().out == f"Exported to {written}\n"
    assert json.loads(written.read_text(encoding="utf-8")) == EMPTY_MEMORY


# ── snapshot ──────────────────────────────────────────────────────────────────


def _snapshot(capsys, *argv: str) -> tuple[int, str, str]:
    from personalclaw.snapshot import snapshot_main

    code = snapshot_main(parsed=_args("snapshot", *argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_snapshot_list_with_none_prints_nothing(tmp_path, capsys):
    code, out, err = _snapshot(capsys, str(tmp_path / "none-yet"), "--list")
    assert (code, out) == (0, "")
    assert "No snapshots found" in err


def test_snapshot_list_prints_one_path_per_line_newest_first(tmp_path, capsys):
    folder = tmp_path / "snaps"
    folder.mkdir()
    older = folder / "personalclaw-snapshot-20260101T000000Z.tar.gz"
    newer = folder / "personalclaw-snapshot-20260102T000000Z.tar.gz"
    for i, each in enumerate((older, newer)):
        each.write_bytes(b"")
        os.utime(each, (1_000_000 + i, 1_000_000 + i))
    assert _snapshot(capsys, str(folder), "--list") == (0, f"{newer}\n{older}\n", "")


def test_snapshot_json_names_the_snapshot_it_made_and_warns_on_stderr(tmp_path, capsys):
    import json

    from personalclaw.config import config_dir

    home = config_dir()
    home.mkdir(parents=True, exist_ok=True)
    (tmp_path / "elsewhere.json").write_text("[]")
    (home / "crons.json").symlink_to(tmp_path / "elsewhere.json")
    folder = tmp_path / "snaps"
    code, out, err = _snapshot(capsys, str(folder), "--json")
    assert code == 0
    (made,) = folder.glob("personalclaw-snapshot-*.tar.gz")
    assert json.loads(out) == {"path": str(made), "bytes": made.stat().st_size}
    assert "Skipping symlinked core file" in err and "Snapshot created" in err


def test_snapshot_list_and_json_at_once_is_a_usage_error(tmp_path, capsys):
    code, out, err = _snapshot(capsys, str(tmp_path), "--list", "--json")
    assert (code, out) == (2, "")
    assert "snapshot --list does not take --json" in err, err


# ── what an app prints as it loads ────────────────────────────────────────────

_APP = "prints-as-it-loads"


def _install_an_app_that_prints(home: Path) -> None:
    """An enabled installed provider app whose ``provider.py`` prints as it is imported, written
    as ``test_cli_provider_bootstrap`` writes one: a CLI command imports it before it runs."""
    import json

    root = home / "apps" / _APP
    root.mkdir(parents=True)
    provider = {
        "type": "model",
        "providerType": "prints-as-it-loads",
        "implementation": "provider:create_provider",
        "capabilities": ["embedding"],
    }
    app = {"name": _APP, "version": "1.0.0", "displayName": "Prints", "provider": provider}
    (root / "app.json").write_text(json.dumps(app), encoding="utf-8")
    installed = {
        "name": _APP,
        "version": "1.0.0",
        "enabled": True,
        "origin": "local",
        "lifecycle": "gateway",
        "resources": "gateway",
    }
    (root / "installed.json").write_text(json.dumps(installed), encoding="utf-8")
    (root / "provider.py").write_text(
        'print("an app says hello as it is imported")\n\n\n'
        "def create_provider(config=None):\n"
        "    return object()\n",
        encoding="utf-8",
    )


def test_an_app_that_prints_as_it_loads_leaves_memory_export_one_document(tmp_path):
    """The provider apps load before the command runs, and what one prints goes to stderr."""
    import json

    home = tmp_path / "pc"
    _install_an_app_that_prints(home)
    user = tmp_path / "user"
    user.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PERSONALCLAW_", "PYTEST_"))}
    src = Path(__file__).resolve().parents[1] / "src"
    env.update(
        PERSONALCLAW_HOME=str(home),
        HOME=str(user),
        PYTHONIOENCODING="utf-8",
        PYTHONPATH=os.pathsep.join([str(src), env.get("PYTHONPATH", "")]),
    )
    done = subprocess.run(
        [sys.executable, "-m", "personalclaw", "memory", "export"],
        cwd=user,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
    assert set(json.loads(done.stdout)) == {"semantic", "episodic", "events"}
    assert "an app says hello as it is imported" in done.stderr
