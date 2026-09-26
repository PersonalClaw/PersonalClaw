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


# ── an app's step imports its own package (#124) ────────────────────────────────


def _install_packaged_app(root, name, *, step_file, step_body, cli):
    """An app whose step imports its OWN package — the shape of every channel app's
    ``cli_setup.py`` (``from telegram_runtime.settings import …``). ``demo_runtime`` exists
    only in the app's directory, so the import resolves only with that directory on the path."""
    d = _install_app(root, name, module_file=step_file, module_body=step_body, cli=cli)
    pkg = d / "demo_runtime"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "settings.py").write_text("TOKEN_KEY = 'DEMO_TOKEN'\n", encoding="utf-8")
    (pkg / "lazy.py").write_text("GREETING = 'lazy import resolved'\n", encoding="utf-8")
    return d


@pytest.fixture
def _no_demo_runtime():
    """``demo_runtime`` is a top-level name: drop it from sys.modules around each test, so one
    test's import can neither satisfy nor poison the next."""
    import sys

    for key in [k for k in sys.modules if k == "demo_runtime" or k.startswith("demo_runtime.")]:
        del sys.modules[key]
    yield
    for key in [k for k in sys.modules if k == "demo_runtime" or k.startswith("demo_runtime.")]:
        del sys.modules[key]


def test_a_setup_step_that_imports_its_own_package_runs(_isolate, capsys, _no_demo_runtime):
    """#124: `personalclaw setup --app telegram-channel` printed "setup step unavailable — No
    module named 'telegram_runtime'" because the loader exec'd the step without the app's dir on
    sys.path. The import at module top AND one inside the step (at call time) must resolve."""
    import sys

    d = _install_packaged_app(
        _isolate,
        "pkg-app",
        step_file="cli_setup.py",
        step_body=(
            "from demo_runtime.settings import TOKEN_KEY\n"
            "def run(ctx):\n"
            "    from demo_runtime.lazy import GREETING\n"
            "    ctx.save_credential(TOKEN_KEY, 'abc')\n"
            "    ctx.print(GREETING)\n"
        ),
        cli={"setup": "cli_setup:run"},
    )
    assert app_cli.run_app_setup_steps(only_app="pkg-app") == []
    out = capsys.readouterr().out
    assert "unavailable" not in out and "lazy import resolved" in out
    assert "DEMO_TOKEN=abc" in (_isolate / ".env").read_text(encoding="utf-8")
    assert str(d) not in sys.path, "the app dir must be held for the step only, not left behind"


def test_a_doctor_probe_that_imports_its_own_package_runs(_isolate, capsys, _no_demo_runtime):
    _install_packaged_app(
        _isolate,
        "pkg-probe",
        step_file="cli_doctor.py",
        step_body=(
            "from personalclaw.sdk.cli import DoctorLine\n"
            "from demo_runtime.settings import TOKEN_KEY\n"
            "def probe():\n"
            "    from demo_runtime.lazy import GREETING\n"
            "    return [DoctorLine(TOKEN_KEY, 'ok', GREETING)]\n"
        ),
        cli={"doctor": "cli_doctor:probe"},
    )
    issues = app_cli.run_app_doctor_probes()
    out = capsys.readouterr().out
    assert issues == [] and "probe error" not in out
    assert "DEMO_TOKEN" in out and "lazy import resolved" in out


def test_an_unavailable_step_exits_non_zero_with_its_reason(_isolate, capsys, _no_demo_runtime):
    """It printed the reason and exited 0, so a script (or a person) read setup as done."""
    from personalclaw.cli_setup import _setup

    _install_app(
        _isolate,
        "broken-app",
        module_file="cli_setup.py",
        module_body="import no_such_package_anywhere\ndef run(ctx):\n    pass\n",
        cli={"setup": "cli_setup:run"},
    )
    with pytest.raises(SystemExit) as exit_info:
        _setup(only_app="broken-app")
    assert exit_info.value.code == 1
    out = capsys.readouterr().out
    assert "broken-app: setup step unavailable — ModuleNotFoundError" in out
    assert "no_such_package_anywhere" in out


def test_a_step_that_raises_exits_non_zero(_isolate, capsys):
    from personalclaw.cli_setup import _setup

    _install_app(
        _isolate,
        "raises-app",
        module_file="cli_setup.py",
        module_body="def run(ctx):\n    raise RuntimeError('token rejected')\n",
        cli={"setup": "cli_setup:run"},
    )
    with pytest.raises(SystemExit) as exit_info:
        _setup(only_app="raises-app")
    assert exit_info.value.code == 1
    assert "setup step failed — RuntimeError: token rejected" in capsys.readouterr().out


def test_naming_an_app_with_no_step_exits_non_zero(_isolate, capsys):
    from personalclaw.cli_setup import _setup

    with pytest.raises(SystemExit) as exit_info:
        _setup(only_app="not-installed")
    assert exit_info.value.code == 1
    assert "not-installed" in capsys.readouterr().out


def test_a_step_that_runs_exits_zero(_isolate, capsys):
    """The control: a step that completes does not exit at all."""
    from personalclaw.cli_setup import _setup

    _install_app(
        _isolate,
        "fine-app",
        module_file="cli_setup.py",
        module_body="def run(ctx):\n    ctx.print('fine')\n",
        cli={"setup": "cli_setup:run"},
    )
    _setup(only_app="fine-app")
    assert "fine" in capsys.readouterr().out


def test_the_full_wizard_names_failed_app_steps_and_exits_non_zero(_isolate, capsys, monkeypatch):
    """One broken app never aborts the wizard, but the wizard no longer ends on "Done!"."""
    from personalclaw import cli_setup

    for step in (
        "_setup_workspace_dir",
        "_ensure_default_agent_in_config",
        "_setup_timezone",
        "_maybe_setup_dashboard_url",
        "_maybe_setup_custom_domain",
    ):
        monkeypatch.setattr(cli_setup, step, lambda *a, **k: None)
    monkeypatch.setattr("personalclaw.agent.rebuild_agent_config", lambda clean=False: "agent.json")
    _install_app(
        _isolate,
        "a-broken",
        module_file="cli_setup.py",
        module_body="def run(ctx):\n    raise RuntimeError('boom')\n",
        cli={"setup": "cli_setup:run"},
    )
    _install_app(
        _isolate,
        "b-fine",
        module_file="cli_setup.py",
        module_body="def run(ctx):\n    ctx.print('b-fine ran')\n",
        cli={"setup": "cli_setup:run"},
    )
    with pytest.raises(SystemExit) as exit_info:
        cli_setup._setup()
    assert exit_info.value.code == 1
    out = capsys.readouterr().out
    assert "b-fine ran" in out  # the wizard went on past the broken app
    assert "these app steps did not run" in out and "a-broken: setup step failed" in out
    assert "Done!" not in out


# ── SetupContext.delete_credential ──────────────────────────────────────────────


def test_a_setup_step_can_delete_a_plain_named_credential(_isolate, capsys):
    """An earlier release saved Slack's token under the plain name `SLACK_BOT_TOKEN`, which no
    uninstall can attribute to the app — the step had no way to clean it up."""
    from personalclaw.config.credentials import get_credential, save_credential

    save_credential("OLD_PLAIN_TOKEN", "leftover")
    _install_app(
        _isolate,
        "cleaner",
        module_file="cli_setup.py",
        module_body=(
            "def run(ctx):\n"
            "    ctx.print(f\"removed={ctx.delete_credential('OLD_PLAIN_TOKEN')}\")\n"
            "    ctx.print(f\"again={ctx.delete_credential('OLD_PLAIN_TOKEN')}\")\n"
        ),
        cli={"setup": "cli_setup:run"},
    )
    assert app_cli.run_app_setup_steps(only_app="cleaner") == []
    out = capsys.readouterr().out
    assert "removed=True" in out and "again=False" in out
    assert get_credential("OLD_PLAIN_TOKEN") == ""


def test_a_setup_step_cannot_delete_a_key_a_setting_owns(_isolate, capsys):
    """An owned key (`PCSECRET_…`) is the value behind some app's `sensitive` setting; deleting
    it from under the record would leave that setting pointing at nothing."""
    from personalclaw.config.credentials import OWNED_KEY_PREFIX, get_credential, save_credential

    owned = f"{OWNED_KEY_PREFIX}APP_SOMEONE_ELSE_TOKEN"
    save_credential(owned, "theirs")
    _install_app(
        _isolate,
        "grabby",
        module_file="cli_setup.py",
        module_body=f"def run(ctx):\n    ctx.delete_credential({owned!r})\n",
        cli={"setup": "cli_setup:run"},
    )
    failures = app_cli.run_app_setup_steps(only_app="grabby")
    assert failures and "owned by a settings record" in failures[0]
    assert get_credential(owned) == "theirs"


def test_a_context_built_without_a_store_refuses_to_delete():
    """An app's own test builds a SetupContext without the runner's delete; a silent False
    would read as "there was nothing to delete"."""
    from personalclaw.sdk.cli import SetupContext
    from personalclaw.sdk.settings import ProviderSettings

    ctx = SetupContext(
        app_name="t",
        get_credential=lambda k: "",
        save_credential=lambda k, v: None,
        settings=ProviderSettings,
    )
    with pytest.raises(RuntimeError, match="no credential store"):
        ctx.delete_credential("ANY")
