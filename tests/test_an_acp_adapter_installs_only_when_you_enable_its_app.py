"""An app's ACP adapter is npm-installed only as the user installs or enables the app.

That is the consent moment. The claude-code and codex apps used to install their adapter from
every start of the gateway while it was missing (each start ran `node --version` and
`npm install` again, silently), and a failure left no trace but a log line. Now:

* a gateway start, or any load that is not an install or an enable, installs nothing;
* an install or an enable does, and a failure is kept with its reason until one succeeds;
* the reason reaches the runtime's card (with Retry, which enables the app again) and doctor.

Driven with a real fixture app whose provider asks for an adapter the way those apps do, and a
stub `npm` on PATH that records every run and, when told to, fails the way npm does.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personalclaw.acp import cli_resolve
from personalclaw.apps import app_manager, app_runtime, manager

PKG = "@example/fake-acp-adapter"
BIN = "fake-acp-adapter"
_REGISTRY_PASSWORD = "planted-registry-password"
_REGISTRY = f"https://ada:{_REGISTRY_PASSWORD}@registry.example.invalid"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated home, installs switched back on (the suite turns them off), a Node >= 20
    that is a stub, and an `npm` beside it that records each run in `npm-runs.log`."""
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    monkeypatch.delenv("PERSONALCLAW_ACP_NO_PROVISION", raising=False)
    node_dir = tmp_path / "node" / "bin"
    node_dir.mkdir(parents=True)
    (node_dir / "node").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    log = tmp_path / "npm-runs.log"
    fail = tmp_path / "npm-fails"
    (node_dir / "npm").write_text(
        "#!/bin/sh\n" f'echo "$*" >> "{log}"\n' f'if [ -f "{fail}" ]; then\n'
        # npm prints the registry it asked, login and all, and that is kept as the reason.
        '  echo "npm ERR! code E404" >&2\n'
        f'  echo "npm ERR! 404 Not Found - GET {_REGISTRY}/{PKG}" >&2\n'
        "  exit 1\n"
        "fi\n"
        'prefix="$3"\n'
        'mkdir -p "$prefix/node_modules/.bin"\n'
        f'printf "#!/bin/sh\\n" > "$prefix/node_modules/.bin/{BIN}"\n'
        f'chmod +x "$prefix/node_modules/.bin/{BIN}"\n',
        encoding="utf-8",
    )
    for tool in ("node", "npm"):
        (node_dir / tool).chmod(0o755)
    monkeypatch.setattr(cli_resolve, "resolve_node_ge", lambda *a, **k: str(node_dir / "node"))
    return tmp_path


def _npm_runs(home: Path) -> list[str]:
    log = home / "npm-runs.log"
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def _make_adapter_app(home: Path) -> Path:
    """An agent app whose provider asks for its adapter the way claude-code-agent does."""
    src = home / "src" / "adapter-app"
    src.mkdir(parents=True)
    (src / "app.json").write_text(
        json.dumps(
            {
                "name": "adapter-app",
                "version": "1.0.0",
                "displayName": "Adapter App",
                "description": "An agent app that needs an ACP adapter.",
                "provider": {"type": "agent", "implementation": "provider:create_provider"},
            }
        ),
        encoding="utf-8",
    )
    (src / "provider.py").write_text(
        "from personalclaw.sdk.acp import provision_acp_adapter\n\n\n"
        "def create_provider(config=None):\n"
        f"    provision_acp_adapter({PKG!r}, [{BIN!r}])\n"
        "    return None\n",
        encoding="utf-8",
    )
    return src


def test_a_gateway_start_installs_nothing_even_while_the_adapter_is_missing(home):
    assert app_manager.install(_make_adapter_app(home), confirm=True).ok
    installed = _npm_runs(home)
    assert len(installed) == 1 and PKG in installed[0], "installing the app installs its adapter"
    adapter = home / "acp-adapters" / "node_modules" / ".bin" / BIN
    adapter.unlink()  # gone again, as after a failed install or a cleaned folder

    app_runtime.start_installed()

    assert _npm_runs(home) == installed, "a gateway start installed the adapter"


def test_a_failed_install_is_kept_with_its_reason_and_not_retried_until_you_enable_again(home):
    (home / "npm-fails").touch()
    assert app_manager.install(_make_adapter_app(home), confirm=True).ok
    assert len(_npm_runs(home)) == 1

    failed = cli_resolve.adapter_install_failure(PKG)
    assert failed is not None and "404 Not Found" in failed["error"] and failed["at"]
    assert _REGISTRY_PASSWORD not in failed["error"], "npm's output is kept masked"
    assert failed["error"].startswith("npm install exited 1: npm ERR! code E404 npm ERR! 404")
    assert "\n" not in failed["error"] and "\\n" not in failed["error"], "npm's lines read as one"

    app_runtime.start_installed()
    app_runtime.start_installed()
    assert len(_npm_runs(home)) == 1, "a failed install was retried behind the user's back"
    assert cli_resolve.adapter_install_failure(PKG) == failed, "the reason is kept"

    # Retry on the card is enabling the app again: the consent moment, so it tries once more.
    (home / "npm-fails").unlink()
    assert app_manager.enable("adapter-app")
    assert len(_npm_runs(home)) == 2
    assert cli_resolve.adapter_install_failure(PKG) is None, "a success clears the failure"


@pytest.mark.parametrize(
    "breaks, reason",
    [
        ("no-node", "no Node 20 or newer"),
        ("no-npm", "npm was not found beside"),
        ("timeout", "ran past 180 seconds"),
        ("cannot-run", "could not run: [Errno 13] Permission denied"),
        ("no-command", f"no {BIN} command came with it"),
    ],
)
def test_every_way_an_install_can_fail_is_kept_with_a_reason(home, monkeypatch, breaks, reason):
    import subprocess

    node_bin = home / "node" / "bin"
    if breaks == "no-node":
        monkeypatch.setattr(cli_resolve, "resolve_node_ge", lambda *a, **k: None)
    elif breaks == "no-npm":
        (node_bin / "npm").unlink()
        monkeypatch.setenv("PATH", str(node_bin))
    elif breaks in ("timeout", "cannot-run"):

        def run(*args, **kwargs):
            if breaks == "timeout":
                raise subprocess.TimeoutExpired(cmd="npm", timeout=180)
            raise PermissionError(13, "Permission denied")

        monkeypatch.setattr(cli_resolve.subprocess, "run", run)
    else:  # npm says it installed the package, and no command came with it
        (node_bin / "npm").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

    with cli_resolve.adapter_installs_allowed():
        assert cli_resolve.provision_acp_adapter(PKG, [BIN]) is None

    failed = cli_resolve.adapter_install_failure(PKG)
    assert failed is not None, "a failed install left no reason behind"
    assert reason in failed["error"], failed["error"]


def test_the_runtime_row_and_doctor_say_why_while_it_runs_through_npx(home, capsys):
    from personalclaw.agents import runtime_tests
    from personalclaw.cli_doctor import _report_acp_agent
    from personalclaw.llm.registry import ProviderEntry

    (home / "npm-fails").touch()
    with cli_resolve.adapter_installs_allowed():
        cli_resolve.provision_acp_adapter(PKG, [BIN])
    entry = ProviderEntry(
        name="acp:fake",
        type="acp_agent",
        model="",
        options={"command": ["npx", "-y", PKG], "dialect": "default"},
        credential=None,
    )
    failed = runtime_tests.adapter_install_failed(entry)
    assert failed is not None and "404 Not Found" in failed["error"]

    issues: list[str] = []
    _report_acp_agent(entry, "acp:fake (acp_agent)", issues, start=False)
    out = capsys.readouterr().out
    assert "did not install when its app was enabled" in out and "404 Not Found" in out
    assert "acp:fake (acp_agent): ACP adapter not installed" in issues
    assert _npm_runs(home) == [
        f"install --prefix {home / 'acp-adapters'} --no-fund --no-audit {PKG}"
    ]

    # Installed some other way (the runtime no longer goes through npx), nothing is said.
    installed = ProviderEntry(
        name="acp:fake",
        type="acp_agent",
        model="",
        options={"command": ["/opt/example/fake-acp-adapter"], "dialect": "default"},
        credential=None,
    )
    assert runtime_tests.adapter_install_failed(installed) is None


def test_the_npx_package_is_read_off_the_argv():
    assert cli_resolve.npx_package(["/usr/bin/npx", "-y", PKG, "--acp"]) == PKG
    assert cli_resolve.npx_package(["/opt/example/fake-acp-adapter"]) == ""
    assert cli_resolve.npx_package(None) == ""
