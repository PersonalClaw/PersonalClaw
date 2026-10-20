"""A checkout's update that fails puts the checkout back on the commit it was on.

An update of a source checkout checks the new release out, then installs it into the running
environment. When that install failed for a reason other than a missing tool (the network, a
resolver conflict, a full disk), the checkout stayed on the new release while the environment kept
the old release's packages: the next start ran the new code on the old packages, and could fail to
start at all. ``personalclaw update`` had its own copy of the update, which built the new release's
dashboard before it installed anything.

These drive the three ways an update starts, the dashboard's Update, the staged auto-update and
``personalclaw update``, which run one update now (``checkout_update.update_checkout``), on a real
git checkout one release behind its origin. The installer is a stand-in ``uv`` on PATH. It writes
down what the checkout's HEAD named when it ran, so a test can tell the checkout had moved, then
succeeds, fails, holds the repository's lock as a git still running would, adds a package before it
fails, or keeps running until it is stopped. Nothing is installed and nothing restarts.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import json
import os
import shlex
import subprocess
import sys
import time
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web

import personalclaw
from personalclaw import _installer, checkout_update, cli_server
from personalclaw import self_update as su
from personalclaw.config.loader import AppConfig
from personalclaw.dashboard.handlers import updates as upd
from personalclaw.gateway import GatewayOrchestrator

_RELEASES = [{"tag": "v0.0.2", "prerelease": False}, {"tag": "v0.0.1", "prerelease": False}]
_SURFACES = ["dashboard", "auto-update", "cli"]

#: What the stand-in prints when it fails, the way uv prints a resolver conflict, and the line the
#: update makes of it.
_UV_SAYS = (
    "  × No solution found when resolving dependencies:\n"
    "  ╰─▶ Because alpha==2.0 needs beta>=3, alpha==2.0 cannot be used.\n"
)
_FAILED = (
    "uv sync failed: No solution found when resolving dependencies: Because alpha==2.0 needs "
    "beta>=3, alpha==2.0 cannot be used."
)
_PUT_BACK = "Nothing was changed: the checkout is back on v0.0.1."


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


def _detached(clone: Path) -> bool:
    return subprocess.run(["git", "symbolic-ref", "-q", "HEAD"], cwd=str(clone)).returncode == 1


@pytest.fixture
def checkout(tmp_path, git_over_ssh, package_in_checkout, monkeypatch):
    """A checkout of PersonalClaw on release 0.0.1, HEAD detached at its tag, whose origin has
    published 0.0.2 on ``main``, with the package running from it. Returns ``(checkout, the 0.0.1
    commit, the 0.0.2 commit)``."""
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
        (origin / "src" / "personalclaw" / "release.py").write_text(f'RELEASE = "{version}"\n')
        _git(origin, "add", "-A")
        _git(origin, "commit", "-qm", f"release {version}")
        _git(origin, "tag", f"v{version}")
    clone = tmp_path / "checkout"
    _git(tmp_path, "clone", "-q", git_over_ssh(origin), str(clone))
    _git(clone, "checkout", "-q", "v0.0.1")
    package_in_checkout(clone)
    return clone, _git(clone, "rev-parse", "v0.0.1"), _git(clone, "rev-parse", "v0.0.2")


@pytest.fixture
def installer(tmp_path, monkeypatch, environment_made_by):
    """``installer(does)``: uv made PersonalClaw's environment, and a stand-in ``uv`` on PATH
    installs into it. Returns the log every stand-in writes a line to (``tool|what HEAD
    named|args``), the environment and the stand-in's pid file.

    After its line the stand-in ``uv``: ``ok`` exits 0; ``fails`` says uv's resolver conflict and
    exits 1; ``locks`` first takes the repository's index lock, as a git still running would;
    ``changes`` first adds a package to the environment; ``hangs`` writes its pid and sleeps until
    it is stopped; ``locks-hangs`` and ``changes-hangs`` take the lock or add the package, then
    hang. ``node`` and ``npm`` are stand-ins that only write their line, and so is the
    interpreter, for the CLI's agent-config refresh. PATH holds them and the system's own folders,
    where git is and uv is not. The environment an update compares before and after the install
    (``_installer._site_dirs``) is a folder of the test's.
    """
    environment_made_by("uv")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "ran.log"
    environment = tmp_path / "environment"
    (environment / "personalclaw-0.0.1.dist-info").mkdir(parents=True)
    monkeypatch.setattr(_installer, "_site_dirs", lambda: [str(environment)])
    pid_file = tmp_path / "uv.pid"
    fails = f"printf '%s' '{_UV_SAYS}' >&2\nexit 1\n"
    locks = ": > .git/index.lock\n"
    changes = f'mkdir "{environment}/alpha-1.0.dist-info"\n'
    hangs = f'echo $$ > "{pid_file}"\nexec /bin/sleep 30\n'
    then = {
        "ok": "exit 0\n",
        "fails": fails,
        "locks": locks + fails,
        "changes": changes + fails,
        "hangs": hangs,
        "locks-hangs": locks + hangs,
        "changes-hangs": changes + hangs,
    }

    def _stand_in(name: str, body: str) -> None:
        path = bin_dir / name
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(0o755)

    def _set(does: str) -> types.SimpleNamespace:
        _stand_in(
            "uv",
            f'printf "uv|" >> "{log}"\n'
            f'tr -d "\\n" < .git/HEAD >> "{log}"\n'
            f'printf "|%s\\n" "$*" >> "{log}"\n' + then[does],
        )
        for name in ("node", "npm", "python"):
            _stand_in(name, f'printf "%s||%s\\n" "{name}" "$*" >> "{log}"\nexit 0\n')
        monkeypatch.setattr(sys, "executable", str(bin_dir / "python"))
        monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
        return types.SimpleNamespace(log=log, environment=environment, pid_file=pid_file)

    return _set


@pytest.fixture
def release(monkeypatch):
    """Running 0.0.1 on the stable channel (``updates.channel`` can be changed), with 0.0.2
    published. No network and no restart: the restarts asked for are kept."""

    async def _releases():
        return [dict(r) for r in _RELEASES]

    monkeypatch.setattr(su, "fetch_releases", _releases)
    monkeypatch.setattr(upd, "_local_version", "0.0.1")
    monkeypatch.setattr(personalclaw, "__version__", "0.0.1")
    monkeypatch.setattr(cli_server, "__version__", "0.0.1")
    updates = types.SimpleNamespace(channel="stable", pin="", check_enabled=True)
    cfg = types.SimpleNamespace(updates=updates)
    monkeypatch.setattr(upd.AppConfig, "load", classmethod(lambda cls: cfg))
    monkeypatch.setattr(upd, "_apply_in_flight", False)
    monkeypatch.setattr(checkout_update, "_running", None)
    restarts: list[dict] = []

    async def _restart(state, **kwargs):
        restarts.append(kwargs)

    monkeypatch.setattr(upd, "_graceful_reexec", _restart)
    return types.SimpleNamespace(restarts=restarts, updates=updates)


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

    def update_progress(self) -> dict[str, str] | None:
        if not self.progress or self.progress[-1][0] == "cleared":
            return None
        step, detail = self.progress[-1]
        return {"step": step, "detail": detail}

    def push_refresh(self, *kinds: str) -> None:
        self.refreshes.extend(kinds)

    def last_error(self) -> str:
        return next((detail for step, detail in reversed(self.progress) if step == "error"), "")


async def _settle(state: _State) -> None:
    """Wait out every task the update started."""
    for _ in range(200):
        if not state._background_tasks:
            return
        await asyncio.gather(*list(state._background_tasks), return_exceptions=True)
        await asyncio.sleep(0)
    raise AssertionError("the update's tasks never finished")


def _request(state: _State) -> MagicMock:
    app = web.Application()
    app["state"] = state
    app["auth_cfg"] = None
    request = MagicMock()
    request.app = app
    return request


async def _update(surface: str, state: _State, capsys) -> tuple[int | None, str]:
    """Start an update the way *surface* starts one, and wait it out.

    Returns what the surface answered (the dashboard's HTTP status, the CLI's exit code, nothing
    for the auto-update) and what it said went wrong, with its whitespace folded: the update's last
    error on the dashboard and the auto-update, or the dashboard's refusal, and the CLI's stderr.
    """
    if surface == "cli":

        def _cli() -> int:
            try:
                cli_server._update()
            except SystemExit as exc:
                return int(exc.code or 0)
            return 0

        code = await asyncio.to_thread(_cli)
        return code, " ".join(capsys.readouterr().err.split())
    if surface == "dashboard":
        resp = await upd.api_update_apply(_request(state))
        await _settle(state)
        said = state.last_error()
        if resp.status != 200:
            error = json.loads(resp.text)["error"]
            said = error["message"] if isinstance(error, dict) else error
        return resp.status, " ".join(said.split())
    orch = GatewayOrchestrator.__new__(GatewayOrchestrator)
    orch.dashboard_state = state
    await orch._auto_apply_update()
    await _settle(state)
    return None, " ".join(state.last_error().split())


def _runs(log: Path) -> list[list[str]]:
    """``[tool, what HEAD named, args]`` for each time a stand-in ran."""
    if not log.exists():
        return []
    return [line.split("|", 2) for line in log.read_text().splitlines()]


def _installs(log: Path) -> list[str]:
    """What HEAD named each time the stand-in ``uv`` ran."""
    return [head for tool, head, _ in _runs(log) if tool == "uv"]


async def _until_started(pid_file: Path) -> int:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        text = pid_file.read_text().strip() if pid_file.exists() else ""
        if text:
            return int(text)
        await asyncio.sleep(0.05)
    raise AssertionError("the install never started")


def _running(pid: int) -> bool:
    """Whether *pid* still runs. A stopped process nobody has waited for yet (a zombie) still
    answers ``kill(pid, 0)``, and a loaded machine can leave one for seconds: it runs nothing."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        # Linux: the state is the field after the parenthesised command name.
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except FileNotFoundError:
        if sys.platform.startswith("linux"):
            return False  # it ended between the two reads
        state = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False
        ).stdout.strip()
    return bool(state) and not state.startswith("Z")


def _gone(pid: int) -> bool:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if not _running(pid):
            return True
        time.sleep(0.05)
    return False


def _orchestrator() -> GatewayOrchestrator:
    cfg = AppConfig()
    with patch.object(cfg, "load_credentials", return_value={}):
        return GatewayOrchestrator(cfg)


# ── an install that fails puts the checkout back, and nothing changed ────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", _SURFACES)
async def test_a_failed_install_puts_the_checkout_back_and_says_nothing_changed(
    checkout, installer, release, capsys, surface
) -> None:
    clone, old, new = checkout
    ran = installer("fails")
    (clone / "web").mkdir()  # a dashboard to build, which must not be built for a failed install
    environment = sorted(p.name for p in ran.environment.iterdir())
    state = _State()

    code, said = await _update(surface, state, capsys)

    assert _installs(ran.log) == [new], "the install ran once, on the new release"
    assert _git(clone, "rev-parse", "HEAD") == old, "the checkout stayed on the new release"
    assert _detached(clone), "the checkout was detached at the release tag, as before"
    assert _git(clone, "status", "--porcelain") == "", "the update left changes in the tree"
    assert sorted(p.name for p in ran.environment.iterdir()) == environment
    assert [tool for tool, _, _ in _runs(ran.log)] == ["uv"], "the new dashboard was built"
    assert not release.restarts, "a failed update restarted into an environment it did not install"
    assert f"{_FAILED} {_PUT_BACK}" in said
    if surface == "cli":
        assert code == 1
    else:
        assert "update_failed" in state.refreshes
        assert upd._apply_in_flight is False, "a failed update must free the slot for the next one"


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", _SURFACES)
async def test_an_update_whose_install_succeeds_still_moves(
    checkout, installer, release, capsys, surface
) -> None:
    clone, _old, new = checkout
    ran = installer("ok")
    state = _State()

    code, said = await _update(surface, state, capsys)

    assert _git(clone, "rev-parse", "HEAD") == new
    assert _installs(ran.log) == [new]
    assert said == ""
    if surface == "cli":
        assert code == 0
    else:
        steps = [step for step, _ in state.progress if step != "warning"]
        assert steps == ["pulling", "installing", "building", "restarting"], state.progress
        assert len(release.restarts) == 1, "the gateway restarts into the release it installed"


@pytest.mark.asyncio
async def test_a_failed_install_on_the_nightly_channel_puts_the_branch_back(
    checkout, installer, release, capsys
) -> None:
    """The nightly channel fast-forwards the branch the checkout is on, so putting it back moves
    that branch back as well, and leaves HEAD on it."""
    clone, old, _new = checkout
    _git(clone, "checkout", "-q", "main")
    _git(clone, "reset", "-q", "--hard", old)  # one commit behind origin/main
    release.updates.channel = "nightly"
    ran = installer("fails")
    state = _State()

    _code, said = await _update("dashboard", state, capsys)

    assert _installs(ran.log) == ["ref: refs/heads/main"]
    reflog = _git(clone, "reflog", "--format=%gs", "main")
    assert "merge origin/main: Fast-forward" in reflog, "the branch never moved"
    assert _git(clone, "symbolic-ref", "--short", "HEAD") == "main"
    assert _git(clone, "rev-parse", "main") == old
    assert _git(clone, "status", "--porcelain") == ""
    assert f"{_FAILED} {_PUT_BACK}" in said
    assert not release.restarts


@pytest.mark.asyncio
async def test_an_install_that_runs_out_of_time_puts_the_checkout_back(
    checkout, installer, release, capsys, monkeypatch
) -> None:
    clone, old, _new = checkout
    monkeypatch.setattr(checkout_update, "_INSTALL_TIMEOUT", 1.0)
    ran = installer("hangs")
    state = _State()

    _code, said = await _update("auto-update", state, capsys)

    assert _gone(int(ran.pid_file.read_text())), "the install was left running"
    assert _git(clone, "rev-parse", "HEAD") == old
    assert said == f"uv sync timed out after 1s. {_PUT_BACK}"
    assert not release.restarts


@pytest.mark.asyncio
async def test_an_install_that_changed_the_environment_before_it_failed_says_so(
    checkout, installer, release, capsys
) -> None:
    """Nothing was changed only when the environment is as it was: an install that stopped
    half-way may have replaced some packages already."""
    clone, old, _new = checkout
    installer("changes")
    state = _State()

    _code, said = await _update("dashboard", state, capsys)

    assert _git(clone, "rev-parse", "HEAD") == old
    assert said == (
        f"{_FAILED} The checkout is back on v0.0.1, but the install had already changed alpha in "
        "PersonalClaw's environment; update again once that is fixed."
    )


def test_the_environment_an_update_compares_is_where_the_installer_puts_packages() -> None:
    """The folders read before and after an install are the running interpreter's own, where pip
    and uv install: the folder pytest is installed in is one."""
    site = Path(importlib.metadata.distribution("pytest").locate_file("")).resolve()
    assert str(site) in {str(Path(folder).resolve()) for folder in _installer._site_dirs()}
    assert any(name.startswith("pytest-") for name in _installer.installed_distributions())


# ── a checkout that cannot be put back: where it is, and the one command that puts it back ──


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", _SURFACES)
async def test_a_checkout_that_cannot_be_put_back_says_where_it_is_and_how_to_put_it_back(
    checkout, installer, release, capsys, surface
) -> None:
    clone, old, new = checkout
    installer("locks")
    state = _State()

    _code, said = await _update(surface, state, capsys)

    assert _git(clone, "rev-parse", "HEAD") == new, "git put it back while another git held it"
    assert (
        f"{_FAILED} The checkout could not be put back on v0.0.1 (fatal: Unable to create" in said
    )
    assert (
        "index.lock': File exists.), so it is on v0.0.2. To put it back on v0.0.1 before " in said
    )
    command = said.split("before PersonalClaw next starts, run: ", 1)[1]
    assert shlex.split(command) == ["git", "-C", str(clone), "checkout", "--detach", old]
    assert not release.restarts
    # Once the other git is done, the command puts it back as it was.
    (clone / ".git" / "index.lock").unlink()
    subprocess.run(shlex.split(command), check=True, capture_output=True)
    assert _git(clone, "rev-parse", "HEAD") == old and _detached(clone)
    assert _git(clone, "status", "--porcelain") == ""


# ── an update never moves a tree with uncommitted changes ──────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", _SURFACES)
async def test_an_update_never_moves_a_tree_with_uncommitted_changes(
    checkout, installer, release, capsys, surface
) -> None:
    """Putting a checkout back is safe because an update starts only on a clean tree: what is in
    the tree then is the commit's, and an edit of the owner's is never moved or overwritten."""
    clone, old, _new = checkout
    ran = installer("ok")
    edited = clone / "src" / "personalclaw" / "release.py"
    edited.write_text('RELEASE = "mine"\n')
    state = _State()

    code, said = await _update(surface, state, capsys)

    assert _git(clone, "rev-parse", "HEAD") == old
    assert edited.read_text() == 'RELEASE = "mine"\n'
    assert not _runs(ran.log) and not release.restarts
    if surface == "dashboard":
        assert code == 409
        assert said == "Working tree has uncommitted changes — commit or stash first"
    elif surface == "auto-update":
        assert said == "Update paused — commit or stash your local changes first."
    else:
        assert code == 1
        assert "src/personalclaw/release.py" in said and "git stash" in said


# ── an update stopped half-way: the install stops, and the checkout goes back first ─────────


@pytest.mark.asyncio
async def test_a_gateway_that_stops_mid_install_puts_the_checkout_back_first(
    checkout, installer, release
) -> None:
    clone, old, _new = checkout
    ran = installer("hangs")
    state = _State()
    resp = await upd.api_update_apply(_request(state))
    assert resp.status == 200, resp.text
    pid = await _until_started(ran.pid_file)

    await _orchestrator()._shutdown()
    await _settle(state)

    assert _gone(pid), "the install outlived the gateway"
    assert _git(clone, "rev-parse", "HEAD") == old
    assert _git(clone, "status", "--porcelain") == ""
    # Stopped with everything as it was: said as a cancel, not as a failure.
    assert state.progress[-1] == (
        "cancelled",
        f"The update was stopped before it finished. {_PUT_BACK}",
    )
    assert not release.restarts
    assert upd._apply_in_flight is False


@pytest.mark.asyncio
async def test_an_update_cancelled_mid_install_puts_the_checkout_back(
    checkout, installer, release
) -> None:
    """What a Ctrl-C does to ``personalclaw update``: ``asyncio.run`` cancels the update it runs."""
    clone, old, _new = checkout
    ran = installer("hangs")
    told: list[tuple[str, str]] = []
    update = asyncio.create_task(
        checkout_update.update_checkout(str(clone), "v0.0.2", lambda *step: told.append(step))
    )
    pid = await _until_started(ran.pid_file)

    update.cancel()
    with pytest.raises(asyncio.CancelledError):
        await update

    assert _gone(pid), "the install was left running"
    assert _git(clone, "rev-parse", "HEAD") == old
    assert told[-1] == ("cancelled", f"The update was stopped before it finished. {_PUT_BACK}")
