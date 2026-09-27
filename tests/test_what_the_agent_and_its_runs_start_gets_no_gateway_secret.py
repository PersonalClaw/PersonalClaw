"""No gateway secret reaches what the agent, its loops and workflows, and the Store's git start.

The gateway holds every secret saved in PersonalClaw in its own environment (the credential store
exports them for its children), and the shell that started it may hold more. #3721 and #3771 gave
an app's children and every ACP agent CLI the child allowlist (``sandbox.build_child_env``). These
children still got a copy of all of it: the native agent's bash tool, a loop's check command and
its worktree git, a workflow's setup steps (bare and durable) and its teardown commands, a runner
CLI's version probe, and every git that reads or clones an app source, the guarded listing fetch
included.

Each is driven here through its real spawn, with a stub program that records the environment it
was handed. ``tests/test_spawn_env_audit.py`` is the census that keeps a new spawn from missing
the allowlist.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
from pathlib import Path

import pytest

import personalclaw.config.loader as loader

#: What the gateway's environment may hold that none of these children should see: provider
#: API keys, a code host token, a channel's bot token, an AWS key pair and a service password.
PLANTED = {
    "ANTHROPIC_API_KEY": "planted-provider-key",
    "OPENAI_API_KEY": "planted-provider-key",
    "GITHUB_TOKEN": "planted-code-host-token",
    "SLACK_BOT_TOKEN": "planted-bot-token",
    "AWS_ACCESS_KEY_ID": "planted-access-key-id",
    "AWS_SECRET_ACCESS_KEY": "planted-secret",
    "BILLING_SERVICE_PASSWORD": "planted-password",
}

_RECORDER = """#!{python}
import json, os, sys
with open({record!r}, "a") as f:
    f.write(json.dumps({{"argv": sys.argv[1:], "env": dict(os.environ)}}) + "\\n")
print("stub 1.2.3")
"""


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A scratch PersonalClaw home, a scratch HOME (so `bash -l` and git read nothing of the
    machine's), and the planted secrets in this process's environment."""
    pc = tmp_path / ".personalclaw"
    (pc / "workspace").mkdir(parents=True)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(pc))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(loader, "config_dir", lambda: pc)
    for name, value in PLANTED.items():
        monkeypatch.setenv(name, value)
    return pc


class _Recorder:
    """A program on PATH (or at an absolute path) that records what it was started with."""

    def __init__(self, tmp_path: Path, monkeypatch, name: str) -> None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir(exist_ok=True)
        self.record = tmp_path / f"{name}.jsonl"
        self.path = bin_dir / name
        self.path.write_text(_RECORDER.format(python=sys.executable, record=str(self.record)))
        self.path.chmod(self.path.stat().st_mode | stat.S_IXUSR)
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    def runs(self) -> list["_Seen"]:
        assert self.record.exists(), "the stub was never started"
        return [_Seen(**json.loads(line)) for line in self.record.read_text().splitlines()]


class _Seen:
    """What one child was handed. Its repr lists NAMES only, and every assert below compares names
    or one chosen value: a failure must never print the environment a child was handed, which on a
    broken tree is the environment of whatever runs the tests."""

    def __init__(self, argv: list[str], env: dict[str, str]) -> None:
        self.argv = argv
        self._env = dict(env)
        self.names = frozenset(env)

    def get(self, name: str) -> str | None:
        return self._env.get(name)

    def carrying(self, word: str) -> list[str]:
        return sorted(name for name, value in self._env.items() if word in value)

    def __repr__(self) -> str:
        return f"<child argv={self.argv!r} names={sorted(self.names)!r}>"


def _assert_no_gateway_secret(seen: _Seen, home: Path) -> None:
    leaked = sorted(set(PLANTED) & seen.names)
    assert leaked == []
    carrying = seen.carrying("planted")
    assert carrying == []
    # What it needs to run: the allowlist's base.
    missing = [name for name in ("PATH", "HOME") if not seen.get(name)]
    pc_home = seen.get("PERSONALCLAW_HOME")
    assert missing == [] and pc_home == str(home)


# ── the agent's command and what its runs execute ──


@pytest.mark.parametrize("sandbox_mode", ["off", "auto"])
def test_the_agents_bash_command_runs_without_the_gateways_secrets(
    home, tmp_path, monkeypatch, sandbox_mode
):
    """🔴 Red on main: the native agent's bash tool inherited the gateway's whole environment;
    with the sandbox on it lost only the credential floor's few prefixes."""
    from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider

    stub = _Recorder(tmp_path, monkeypatch, "pclaw-env-recorder")
    tools = NativeBuiltinToolProvider(cwd=home / "workspace", sandbox_mode=sandbox_mode)

    result = asyncio.run(tools._t_bash({"command": f"{stub.path} from-bash"}))

    assert result.success, result.error
    (run,) = stub.runs()
    _assert_no_gateway_secret(run, home)


def test_a_name_the_owner_passed_through_reaches_the_agents_command(home, tmp_path, monkeypatch):
    """The lever for a command that legitimately needs one more variable is the owner's."""
    from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider

    (home / "config.json").write_text(
        json.dumps({"sandbox": {"env_passthrough": ["GITHUB_TOKEN"]}})
    )
    stub = _Recorder(tmp_path, monkeypatch, "pclaw-env-recorder")
    tools = NativeBuiltinToolProvider(cwd=home / "workspace", sandbox_mode="off")

    assert asyncio.run(tools._t_bash({"command": str(stub.path)})).success
    (run,) = stub.runs()
    passed = run.get("GITHUB_TOKEN")
    reached = sorted(run.names & set(PLANTED))
    assert passed == "planted-code-host-token" and reached == ["GITHUB_TOKEN"]


def test_a_loops_check_command_runs_without_the_gateways_secrets(home, tmp_path, monkeypatch):
    """🔴 Red on main: `/bin/sh -c <persisted command>` with the gateway's environment."""
    from personalclaw.loop.gates import run_verify_command

    stub = _Recorder(tmp_path, monkeypatch, "pclaw-env-recorder")

    assert asyncio.run(run_verify_command(f"{stub.path} check", str(home / "workspace"))) is True
    (run,) = stub.runs()
    _assert_no_gateway_secret(run, home)


def test_a_loop_worktrees_git_runs_without_the_gateways_secrets(home, tmp_path, monkeypatch):
    """🔴 Red on main: `commit` and `merge` run the repository's hooks, and an inherited
    `GIT_DIR` pointed every step at another repository."""
    from personalclaw.loop import worktree

    monkeypatch.setenv("GIT_DIR", str(tmp_path / "another-repository"))
    stub = _Recorder(tmp_path, monkeypatch, "git")

    rc, _out = worktree._git(str(home / "workspace"), "status")

    assert rc == 0
    (run,) = stub.runs()
    assert run.argv == ["status"]
    _assert_no_gateway_secret(run, home)
    names = run.names
    assert "GIT_DIR" not in names


def test_a_workflow_step_runs_without_the_gateways_secrets(home, tmp_path, monkeypatch):
    """🔴 Red on main: `{**os.environ, **env}`."""
    from personalclaw.workflows import provisioning

    stub = _Recorder(tmp_path, monkeypatch, "pclaw-env-recorder")
    ws = home / "workspace"

    ok, detail = asyncio.run(
        provisioning.run_step(f"{stub.path} setup", ws, env={"PERSONALCLAW_RUN_WORKTREE": str(ws)})
    )

    assert ok, detail
    (run,) = stub.runs()
    _assert_no_gateway_secret(run, home)
    worktree = run.get("PERSONALCLAW_RUN_WORKTREE")
    assert worktree == str(ws)


def test_a_durable_step_gets_the_same_environment_whatever_its_tmux_server_holds(
    home, tmp_path, monkeypatch
):
    """🔴 Red on main: a durable step started from the environment of its tmux SERVER, which is
    whatever the first client on the socket had (the gateway's, with every secret), plus `-e`
    additions. Here the "server" is as hostile as that: it starts the command with this process's
    environment, planted secrets and all."""
    import subprocess

    from personalclaw import tmux_substrate
    from personalclaw.workflows import provisioning

    stub = _Recorder(tmp_path, monkeypatch, "pclaw-env-recorder")
    ws = home / "workspace"
    handed: list[list[str]] = []

    async def server_starts(name, *, workspace, command, env=None):
        handed.append(list(command))
        hostile = {**os.environ, **(env or {})}
        subprocess.run(command, cwd=workspace, env=hostile, check=False)
        return False  # finished before the liveness check: its rc file is the result

    async def no_session(_name):
        return False

    monkeypatch.setattr(provisioning, "_durable_enabled", lambda: True)
    monkeypatch.setattr(tmux_substrate, "new_session", server_starts)
    monkeypatch.setattr(tmux_substrate, "has_session", no_session)

    ok, detail = asyncio.run(
        provisioning.run_step(
            f"{stub.path} durable",
            ws,
            env={"PERSONALCLAW_RUN_WORKTREE": str(ws)},
            durable_session="pclaw-run-envcheck",
        )
    )

    assert ok, detail
    started_with = handed[0][:2] if handed else []
    assert len(started_with) == 2 and os.path.isabs(started_with[0])
    assert started_with[1] == "-i"
    (run,) = stub.runs()
    _assert_no_gateway_secret(run, home)
    worktree = run.get("PERSONALCLAW_RUN_WORKTREE")
    assert worktree == str(ws)


def test_a_workflow_teardown_runs_without_the_gateways_secrets(home, tmp_path, monkeypatch):
    """🔴 Red on main: `{**os.environ, "EFFECT_OUTPUT_ID": ...}`."""
    from personalclaw.workflows import effects

    stub = _Recorder(tmp_path, monkeypatch, "pclaw-env-recorder")

    ok, detail = asyncio.run(effects.run_teardown(str(stub.path), "output-42"))

    assert ok, detail
    (run,) = stub.runs()
    output_id = run.get("EFFECT_OUTPUT_ID")
    assert run.argv == ["output-42"] and output_id == "output-42"
    _assert_no_gateway_secret(run, home)


def test_a_runner_clis_version_probe_runs_without_the_gateways_secrets(home, tmp_path, monkeypatch):
    """🔴 Red on main. An agent CLI keeps what it inherits, even when asked only its version."""
    from personalclaw.agents import runners

    _Recorder(tmp_path, monkeypatch, "pclaw-fake-runner")
    defn = runners.RunnerDefinition(
        id="fake-runner",
        display_name="Fake Runner",
        runtime_id="acp:fake-runner",
        bin_names=("pclaw-fake-runner",),
    )

    evidence = runners.probe_runner(defn, persist=False)

    assert evidence.ok, evidence.error
    record = tmp_path / "pclaw-fake-runner.jsonl"
    runs = [_Seen(**json.loads(line)) for line in record.read_text().splitlines()]
    count = len(runs)
    assert count == 1
    _assert_no_gateway_secret(runs[0], home)


# ── git that fetches an app source ──


def _git_run_is_clean(run: _Seen, home: Path) -> None:
    _assert_no_gateway_secret(run, home)
    prompt = run.get("GIT_TERMINAL_PROMPT")
    assert "GIT_DIR" not in run.names and prompt == "0"


def test_the_stores_git_reads_an_app_source_without_the_gateways_secrets(
    home, tmp_path, monkeypatch
):
    """🔴 Red on main: the registry read (clone + show), the multi-app scan and the install clone
    each ran git with the gateway's environment."""
    import shutil

    from personalclaw.apps import catalog
    from personalclaw.apps import source as app_source

    monkeypatch.setenv("GIT_DIR", str(tmp_path / "another-repository"))
    # Behind a proxy that re-signs TLS, git verifies the server only with its own trust setting.
    monkeypatch.setenv("GIT_SSL_CAINFO", str(tmp_path / "proxy-ca.pem"))
    monkeypatch.setattr(catalog, "_git_scan_cache", {})
    stub = _Recorder(tmp_path, monkeypatch, "git")

    assert catalog._read_git_registry("https://example.invalid/apps.git") == "stub 1.2.3\n"
    assert catalog._scan_git_source("https://example.invalid/more-apps.git", now=0.0) == []
    resolved = app_source._clone_git("https://example.invalid/one-app.git")
    shutil.rmtree(resolved.cleanup_path, ignore_errors=True)

    runs = stub.runs()
    assert [r.argv[0] for r in runs] == ["clone", "-C", "clone", "clone"]
    for run in runs:
        _git_run_is_clean(run, home)
        trust = run.get("GIT_SSL_CAINFO")
        assert trust == str(tmp_path / "proxy-ca.pem")


def test_a_registry_listings_guarded_clone_starts_from_the_allowlist(home, tmp_path, monkeypatch):
    """🔴 Red on main: the guarded fetch dropped only the names that route around its tunnel,
    and kept every secret."""
    from personalclaw.net.git import run_git_guarded
    from personalclaw.net.policy import LISTING

    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
    stub = _Recorder(tmp_path, monkeypatch, "git")

    proc = run_git_guarded(
        ["ls-remote", "https://example.invalid/x.git"], policy=LISTING, timeout=30
    )

    assert proc.returncode == 0
    (run,) = stub.runs()
    _git_run_is_clean(run, home)
    assert "HTTPS_PROXY" not in run.names  # the tunnel is the only proxy
    pinned = (run.get("GIT_CONFIG_GLOBAL"), run.get("GIT_ALLOW_PROTOCOL"))
    assert pinned == (os.devnull, "https")


# ── the SDK's answer for an app's own children ──


def test_an_apps_provider_gets_the_allowlist_for_its_children(home, monkeypatch):
    from personalclaw.sdk.util import child_process_env

    monkeypatch.setenv("npm_config_registry", "https://registry.example.invalid/")
    monkeypatch.setenv("npm_config__authToken", "planted-registry-login")

    seen = _Seen([], child_process_env({"NO_COLOR": "1"}, installer="npm"))
    plain = _Seen([], child_process_env())

    _assert_no_gateway_secret(seen, home)
    kept = (seen.get("NO_COLOR"), seen.get("npm_config_registry"))
    assert kept == ("1", "https://registry.example.invalid/")
    assert "npm_config__authToken" not in seen.names
    assert "npm_config_registry" not in plain.names
