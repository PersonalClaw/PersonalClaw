"""An update installs PersonalClaw with the tool that made its environment, or changes nothing.

A source checkout's update reinstalled it with ``python -m pip install -e .``: the dashboard's
Update and the staged auto-update spelled that command out, and ``personalclaw update`` took uv
whenever uv was on PATH and pip otherwise, whichever tool had made the environment. An environment
uv made (``uv sync``) has no pip until it syncs a version that declares one, so its first Update
from the dashboard checked the new release out and then could not install it: the checkout had
moved, the environment had not, and the gateway kept running the old code over the new sources.

These drive the three ways an update starts (``POST /api/update``, the staged auto-update and
``personalclaw update``) on a real git checkout one release behind its origin. The environment
carries the record every installer leaves (its distribution's ``INSTALLER``), and the tools are
programs on PATH that write down how they were run: ``uv``, and the interpreter itself, which
answers ``-m pip`` the way an environment with or without pip answers it. Nothing is installed.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from aiohttp import web

import personalclaw
from personalclaw import _installer, cli_server
from personalclaw import self_update as su
from personalclaw.dashboard.handlers import updates as upd
from personalclaw.gateway import GatewayOrchestrator

_RELEASES = [{"tag": "v0.0.2", "prerelease": False}, {"tag": "v0.0.1", "prerelease": False}]

_UV_REFUSAL = (
    "Nothing was changed: uv made PersonalClaw's environment, and PersonalClaw cannot find `uv` "
    "on its PATH, so run `personalclaw update` from a terminal where `uv` works."
)


def _pip_refusal(python: str) -> str:
    return (
        f"Nothing was changed: pip made PersonalClaw's environment, and {python} has no `pip` "
        "module, though pip is one of PersonalClaw's own dependencies; reinstall PersonalClaw "
        "into that environment to put it back, then run `personalclaw update`."
    )


def _git(cwd: Path, *args: str) -> str:
    """The test's own git: no credential helper, and (with the fixture's environment) none of the
    owner's configuration."""
    done = subprocess.run(
        ["git", "-c", "credential.helper=", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )
    return done.stdout.strip()


@pytest.fixture
def checkout(tmp_path, git_over_ssh, package_in_checkout, monkeypatch):
    """A checkout of PersonalClaw on release 0.0.1 whose origin has published 0.0.2, with the
    package running from it. Returns ``(checkout, the 0.0.1 commit, the 0.0.2 commit)``."""
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    for key, value in (
        ("user.email", "owner@example.com"),
        ("user.name", "Owner"),
        ("commit.gpgsign", "false"),
        ("tag.gpgsign", "false"),
    ):
        _git(origin, "config", key, value)
    (origin / "src" / "personalclaw").mkdir(parents=True)
    (origin / "src" / "personalclaw" / "__init__.py").write_text("")
    for version in ("0.0.1", "0.0.2"):
        (origin / "pyproject.toml").write_text(
            f'[project]\nname = "personalclaw"\nversion = "{version}"\n'
        )
        _git(origin, "add", "-A")
        _git(origin, "commit", "-qm", f"release {version}")
        _git(origin, "tag", f"v{version}")
    clone = tmp_path / "checkout"
    _git(tmp_path, "clone", "-q", git_over_ssh(origin), str(clone))
    _git(clone, "checkout", "-q", "v0.0.1")
    package_in_checkout(clone)
    return clone, _git(clone, "rev-parse", "v0.0.1"), _git(clone, "rev-parse", "v0.0.2")


@pytest.fixture
def tools(tmp_path, monkeypatch, environment_made_by):
    """``tools(made_by=…, uv=…, pip=…)``: the environment *made_by* made, with ``uv`` on PATH or
    not and a ``pip`` module or not. Returns the log the tools write: ``tool|cwd|project env|args``.

    PATH holds the stand-ins and the system's own folders, where git is and uv is not."""
    log = tmp_path / "ran.log"

    def _set(*, made_by: str, uv: bool, pip: bool) -> Path:
        environment_made_by(made_by)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        record = (
            'printf "%s|%s|%s|%s\\n" "${0##*/}" "$PWD" "${UV_PROJECT_ENVIRONMENT:-}" "$*" >> '
            f'"{log}"\n'
        )
        if uv:
            (bin_dir / "uv").write_text("#!/bin/sh\n" + record + "exit 0\n")
            (bin_dir / "uv").chmod(0o755)
        refuse_pip = (
            ""
            if pip
            else 'if [ "$1" = "-m" ] && [ "$2" = "pip" ]; then\n'
            '  echo "$0: No module named pip" >&2; exit 1\nfi\n'
        )
        python = bin_dir / "python"
        python.write_text("#!/bin/sh\n" + record + refuse_pip + "exit 0\n")
        python.chmod(0o755)
        monkeypatch.setattr(sys, "executable", str(python))
        monkeypatch.setattr(_installer, "_have_pip", lambda: pip)
        monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
        return log

    return _set


@pytest.fixture
def release(monkeypatch):
    """Running 0.0.1 on the stable channel, with 0.0.2 published. No network and no restart: the
    restarts asked for are returned."""

    async def _releases():
        return [dict(r) for r in _RELEASES]

    monkeypatch.setattr(su, "fetch_releases", _releases)
    monkeypatch.setattr(upd, "_local_version", "0.0.1")
    monkeypatch.setattr(personalclaw, "__version__", "0.0.1")
    monkeypatch.setattr(cli_server, "__version__", "0.0.1")
    cfg = types.SimpleNamespace(
        updates=types.SimpleNamespace(channel="stable", pin="", check_enabled=True)
    )
    monkeypatch.setattr(upd.AppConfig, "load", classmethod(lambda cls: cfg))
    monkeypatch.setattr(upd, "_apply_in_flight", False)
    restarts: list[dict] = []

    async def _restart(state, **kwargs):
        restarts.append(kwargs)

    monkeypatch.setattr(upd, "_graceful_reexec", _restart)
    return restarts


class _State:
    """What the dashboard's update surfaces receive."""

    def __init__(self) -> None:
        self._background_tasks: set = set()
        self.progress: list[tuple[str, str]] = []
        self.refreshes: list[str] = []

    def push_update_progress(self, step: str, detail: str = "") -> None:
        self.progress.append((step, detail))

    def clear_update_progress(self) -> None:
        self.progress.append(("cleared", ""))

    def push_refresh(self, *kinds: str) -> None:
        self.refreshes.extend(kinds)


async def _settle(state: _State) -> None:
    """Wait out every task the update started."""
    for _ in range(200):
        if not state._background_tasks:
            return
        await asyncio.gather(*list(state._background_tasks), return_exceptions=True)
        await asyncio.sleep(0)
    raise AssertionError("the update's tasks never finished")


async def _from_the_dashboard(state: _State) -> web.Response:
    app = web.Application()
    app["state"] = state
    app["auth_cfg"] = None
    request = MagicMock()
    request.app = app
    resp = await upd.api_update_apply(request)
    await _settle(state)
    return resp


async def _unattended(state: _State) -> None:
    orch = GatewayOrchestrator.__new__(GatewayOrchestrator)
    orch.dashboard_state = state
    await orch._auto_apply_update()
    await _settle(state)


def _from_the_cli(capsys) -> tuple[int, str, str]:
    try:
        cli_server._update()
        code = 0
    except SystemExit as exc:
        code = int(exc.code or 0)
    out = capsys.readouterr()
    return code, out.out, out.err


def _runs(log: Path) -> list[list[str]]:
    """``[tool, cwd, project environment, args]`` for each time a stand-in ran."""
    if not log.exists():
        return []
    return [line.split("|", 3) for line in log.read_text().splitlines()]


def _installs(log: Path) -> list[list[str]]:
    """The runs that install: uv's, and the interpreter's ``-m pip``."""
    return [
        run
        for run in _runs(log)
        if run[0] == "uv" or (run[0] == "python" and run[3].startswith("-m pip"))
    ]


def _steps(state: _State) -> list[str]:
    return [step for step, _ in state.progress if step != "warning"]


# ── an environment uv made, with no pip: the update installs through uv ─────────────────────────


def _assert_synced_with_uv(log: Path, clone: Path) -> None:
    installs = _installs(log)
    assert len(installs) == 1, f"one install, through uv, was expected: {_runs(log)}"
    tool, cwd, project_env, args = installs[0]
    assert tool == "uv"
    assert args.split() == ["sync", "--locked", "--inexact", "--python", sys.executable, "--quiet"]
    assert Path(cwd).resolve() == clone.resolve()
    # The running environment is the one synced, wherever it is: never a .venv beside the sources.
    assert project_env == sys.prefix


@pytest.mark.asyncio
async def test_the_dashboards_update_of_a_uv_made_checkout_installs_through_uv(
    checkout, tools, release
) -> None:
    clone, _old, new = checkout
    log = tools(made_by="uv", uv=True, pip=False)
    state = _State()

    resp = await _from_the_dashboard(state)

    assert resp.status == 200, resp.text
    assert json.loads(resp.text)["status"] == "updating"
    assert _git(clone, "rev-parse", "HEAD") == new
    _assert_synced_with_uv(log, clone)
    assert _steps(state) == ["pulling", "installing", "building", "restarting"], state.progress
    assert len(release) == 1, "the gateway restarts into the release it installed"


@pytest.mark.asyncio
async def test_the_staged_auto_update_of_a_uv_made_checkout_installs_through_uv(
    checkout, tools, release
) -> None:
    clone, _old, new = checkout
    log = tools(made_by="uv", uv=True, pip=False)
    state = _State()

    await _unattended(state)

    assert _git(clone, "rev-parse", "HEAD") == new
    _assert_synced_with_uv(log, clone)
    assert _steps(state) == ["pulling", "installing", "building", "restarting"], state.progress
    assert len(release) == 1


def test_personalclaw_update_of_a_uv_made_checkout_installs_through_uv(
    checkout, tools, release, capsys
) -> None:
    clone, _old, new = checkout
    log = tools(made_by="uv", uv=True, pip=False)

    code, out, err = _from_the_cli(capsys)

    assert code == 0, err
    assert _git(clone, "rev-parse", "HEAD") == new
    _assert_synced_with_uv(log, clone)
    assert "PersonalClaw updated" in out
    # The gateway still runs the code it started with: the CLI says how to run the new one.
    assert "personalclaw restart" in out


# ── an environment pip made: the update installs through pip, even with uv on PATH ──────────────


def _assert_installed_with_pip(log: Path, clone: Path) -> None:
    installs = _installs(log)
    assert len(installs) == 1, f"one install, through pip, was expected: {_runs(log)}"
    tool, cwd, _project_env, args = installs[0]
    assert tool == "python"
    assert args.split() == ["-m", "pip", "install", "-e", ".", "--quiet"]
    assert Path(cwd).resolve() == clone.resolve()


@pytest.mark.asyncio
async def test_the_dashboards_update_of_a_pip_made_checkout_installs_through_pip(
    checkout, tools, release
) -> None:
    clone, _old, new = checkout
    log = tools(made_by="pip", uv=True, pip=True)
    state = _State()

    resp = await _from_the_dashboard(state)

    assert resp.status == 200, resp.text
    assert _git(clone, "rev-parse", "HEAD") == new
    _assert_installed_with_pip(log, clone)
    assert _steps(state) == ["pulling", "installing", "building", "restarting"], state.progress


@pytest.mark.asyncio
async def test_the_staged_auto_update_of_a_pip_made_checkout_installs_through_pip(
    checkout, tools, release
) -> None:
    clone, _old, new = checkout
    log = tools(made_by="pip", uv=True, pip=True)
    state = _State()

    await _unattended(state)

    assert _git(clone, "rev-parse", "HEAD") == new
    _assert_installed_with_pip(log, clone)


def test_personalclaw_update_of_a_pip_made_checkout_installs_through_pip(
    checkout, tools, release, capsys
) -> None:
    clone, _old, new = checkout
    log = tools(made_by="pip", uv=True, pip=True)

    code, _out, err = _from_the_cli(capsys)

    assert code == 0, err
    assert _git(clone, "rev-parse", "HEAD") == new
    _assert_installed_with_pip(log, clone)


# ── the tool that made it is not there: the update refuses, and nothing changes ────────────────


_NEITHER = [
    pytest.param("uv", lambda _python: _UV_REFUSAL, id="uv-made, no uv and no pip"),
    pytest.param("pip", _pip_refusal, id="pip-made, no pip and no uv"),
]


def _assert_nothing_changed(clone: Path, old: str, log: Path, restarts: list) -> None:
    assert _git(clone, "rev-parse", "HEAD") == old, "the checkout moved for an update that refused"
    assert _git(clone, "status", "--porcelain") == ""
    assert not _installs(log), f"something was installed: {_runs(log)}"
    assert not restarts


@pytest.mark.asyncio
@pytest.mark.parametrize("made_by, refusal", _NEITHER)
async def test_the_dashboard_refuses_before_changing_anything(
    checkout, tools, release, made_by, refusal
) -> None:
    clone, old, _new = checkout
    log = tools(made_by=made_by, uv=False, pip=False)
    state = _State()

    resp = await _from_the_dashboard(state)

    _assert_nothing_changed(clone, old, log, release)
    assert resp.status == 409, resp.text
    error = json.loads(resp.text)["error"]
    assert error == {"code": "update_installer_missing", "message": refusal(sys.executable)}
    assert state.progress == [] and "updating" not in state.refreshes
    assert upd._apply_in_flight is False, "a refusal must free the slot for the next update"


@pytest.mark.asyncio
@pytest.mark.parametrize("made_by, refusal", _NEITHER)
async def test_the_staged_auto_update_refuses_before_changing_anything(
    checkout, tools, release, made_by, refusal
) -> None:
    clone, old, _new = checkout
    log = tools(made_by=made_by, uv=False, pip=False)
    state = _State()

    await _unattended(state)

    _assert_nothing_changed(clone, old, log, release)
    assert state.progress == [("error", refusal(sys.executable))]
    assert upd._apply_in_flight is False


@pytest.mark.parametrize("made_by, refusal", _NEITHER)
def test_personalclaw_update_refuses_before_changing_anything(
    checkout, tools, release, capsys, made_by, refusal
) -> None:
    clone, old, _new = checkout
    log = tools(made_by=made_by, uv=False, pip=False)

    code, _out, err = _from_the_cli(capsys)

    _assert_nothing_changed(clone, old, log, release)
    assert code == 1
    assert refusal(sys.executable) in " ".join(err.split())
