"""Tests for the app-contributed CLI seams (plan 32: PROVIDER-BOUNDARY-COMPLETION).

Covers ``personalclaw.app_cli``:
- ``run_app_setup_steps`` — imports + runs each installed+enabled app's ``cli.setup``
  with a ``SetupContext``; a raising step warns and continues; ``--app`` filters.
- ``run_app_doctor_probes`` — imports + runs each ``cli.doctor`` under a timeout,
  renders ``DoctorLine``s, and turns a hung/raising probe into one fail line.

Fixture apps are written under a tmp ``apps/<name>/`` (installed.json + app.json +
a real module .py) mirroring what ``manager.list_apps()`` + ``app_dir()`` read.
"""

import json

import pytest

from personalclaw import app_cli
from personalclaw.apps import manager


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Point the apps dir at tmp_path so list_apps()/app_dir() read our fixtures."""
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    # app_cli imports save_credential from config.loader at call time; point the
    # credential store at tmp_path too so a setup step's write is isolated.
    from personalclaw.config import loader as cfg_loader

    monkeypatch.setattr(cfg_loader, "config_dir", lambda: tmp_path)
    return tmp_path


def _install_app(root, name, *, module_file="", module_body="", cli=None, enabled=True):
    """Write an installed app: installed.json + app.json (+ optional module .py)."""
    d = root / "apps" / name
    d.mkdir(parents=True)
    (d / "installed.json").write_text(
        json.dumps({"name": name, "version": "1.0.0", "enabled": enabled}),
        encoding="utf-8",
    )
    manifest = {"name": name, "version": "1.0.0", "displayName": name, "description": name}
    if cli is not None:
        manifest["cli"] = cli
    (d / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
    if module_file:
        (d / module_file).write_text(module_body, encoding="utf-8")
    return d


# ── run_app_setup_steps ───────────────────────────────────────────────────────


def test_setup_step_runs_and_receives_context(_isolate):
    # P5: an app with cli.setup runs; its run(ctx) can save a credential + read it back.
    _install_app(
        _isolate,
        "cfg-app",
        module_file="cli_setup.py",
        module_body=(
            "def run(ctx):\n"
            "    ctx.save_credential('CFG_APP_TOKEN', 'xyz')\n"
            "    assert ctx.get_credential('CFG_APP_TOKEN') == 'xyz'\n"
            "    ctx.print('cfg-app configured')\n"
        ),
        cli={"setup": "cli_setup:run"},
    )
    app_cli.run_app_setup_steps()
    # the credential landed in the isolated .env
    env = (_isolate / ".env").read_text(encoding="utf-8")
    assert "CFG_APP_TOKEN=xyz" in env


def test_setup_step_that_raises_does_not_abort(_isolate, capsys):
    # P6: a raising step prints a warning and setup continues to the next app.
    _install_app(
        _isolate,
        "a-bad",
        module_file="cli_setup.py",
        module_body="def run(ctx):\n    raise RuntimeError('boom')\n",
        cli={"setup": "cli_setup:run"},
    )
    _install_app(
        _isolate,
        "z-good",
        module_file="cli_setup.py",
        module_body="def run(ctx):\n    ctx.print('z-good ran')\n",
        cli={"setup": "cli_setup:run"},
    )
    app_cli.run_app_setup_steps()  # must not raise
    out = capsys.readouterr().out
    assert "a-bad" in out and "boom" in out  # warning shown
    assert "z-good ran" in out  # later app still ran (alphabetical order)


def test_setup_only_app_filter(_isolate, capsys):
    # P7: --app <name> runs only that app's step.
    _install_app(
        _isolate,
        "one",
        module_file="cli_setup.py",
        module_body="def run(ctx):\n    ctx.print('ONE ran')\n",
        cli={"setup": "cli_setup:run"},
    )
    _install_app(
        _isolate,
        "two",
        module_file="cli_setup.py",
        module_body="def run(ctx):\n    ctx.print('TWO ran')\n",
        cli={"setup": "cli_setup:run"},
    )
    app_cli.run_app_setup_steps(only_app="two")
    out = capsys.readouterr().out
    assert "TWO ran" in out
    assert "ONE ran" not in out


def test_setup_disabled_app_skipped(_isolate, capsys):
    # A disabled app's setup step never runs.
    _install_app(
        _isolate,
        "off",
        module_file="cli_setup.py",
        module_body="def run(ctx):\n    ctx.print('OFF ran')\n",
        cli={"setup": "cli_setup:run"},
        enabled=False,
    )
    app_cli.run_app_setup_steps()
    assert "OFF ran" not in capsys.readouterr().out


# ── run_app_doctor_probes ──────────────────────────────────────────────────────


def test_doctor_probe_renders_lines(_isolate, capsys):
    # P8: a probe returning DoctorLines renders a per-app section; a fail line is an issue.
    _install_app(
        _isolate,
        "probe-app",
        module_file="cli_doctor.py",
        module_body=(
            "from personalclaw.sdk.cli import DoctorLine\n"
            "def probe():\n"
            "    return [DoctorLine('token', 'ok', 'present'),\n"
            "            DoctorLine('workspace', 'fail', 'unreachable')]\n"
        ),
        cli={"doctor": "cli_doctor:probe"},
    )
    issues = app_cli.run_app_doctor_probes()
    out = capsys.readouterr().out
    assert "probe-app" in out and "token" in out and "workspace" in out
    assert any("workspace" in i for i in issues)  # the fail line became an issue


def test_doctor_probe_timeout_does_not_hang(_isolate, capsys):
    # P9: a hung probe becomes a single fail line within the timeout — never hangs.
    monkey_timeout = 0.3
    import personalclaw.app_cli as ac

    ac._DOCTOR_TIMEOUT_SECS = monkey_timeout  # shrink for a fast test
    _install_app(
        _isolate,
        "hang-app",
        module_file="cli_doctor.py",
        module_body="import time\ndef probe():\n    time.sleep(5)\n    return []\n",
        cli={"doctor": "cli_doctor:probe"},
    )
    issues = app_cli.run_app_doctor_probes()
    out = capsys.readouterr().out
    assert "hang-app" in out and "probe error" in out
    assert any("hang-app" in i for i in issues)


def test_doctor_probe_exception_becomes_fail(_isolate, capsys):
    _install_app(
        _isolate,
        "err-app",
        module_file="cli_doctor.py",
        module_body="def probe():\n    raise ValueError('nope')\n",
        cli={"doctor": "cli_doctor:probe"},
    )
    issues = app_cli.run_app_doctor_probes()
    assert "nope" in capsys.readouterr().out
    assert any("err-app" in i for i in issues)


def test_malformed_cli_ref_is_a_warning_not_a_crash(_isolate, capsys):
    # A cli.setup ref that isn't "module:function" warns and continues.
    _install_app(
        _isolate,
        "bad-ref",
        module_file="cli_setup.py",
        module_body="def run(ctx):\n    ctx.print('never')\n",
        cli={"setup": "not_a_valid_ref"},
    )
    app_cli.run_app_setup_steps()  # must not raise
    assert "bad-ref" in capsys.readouterr().out
