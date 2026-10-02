"""Update checks follow `updates.check_enabled`: with automatic checks off, nothing reaches out.

Every path that looks for a newer release is driven here: the gateway's start, the status poll's
schedule, a page that shows the update status, the owner's Check now, and the staged install
that follows a check. Each way such a path could reach the network is recorded and refused: an
HTTP session (the releases list and its notes), a spawned git that talks to a remote (a source
checkout's `git fetch`), and a name lookup. So nothing leaves the machine, and with automatic
checks off none of them may even be tried. A control with checks on beside each one shows the
path is a gate rather than a constant.

Check now is the owner's own action, so it runs whatever the setting says. The setting's copy
says exactly that (`web/src/pages/settings/updatesPanelCheckNow.test.tsx`).
"""

from __future__ import annotations

import ast
import asyncio
import json
import socket
import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock

import aiohttp
import pytest

import personalclaw
from personalclaw import self_update
from personalclaw.dashboard.handlers import updates
from personalclaw.net.git import talks_to_remote

_NEWER = [
    {"tag_name": "v9.9.9", "name": "9.9.9", "body": "What 9.9.9 brings.", "prerelease": False}
]
_SAME = [{"tag_name": "v0.2.0", "name": "0.2.0", "body": "What 0.2.0 brings.", "prerelease": False}]


class _Resp:
    def __init__(self, payload: list[dict[str, object]]) -> None:
        self.status = 200
        self.headers = {"ETag": 'W/"fixture"'}
        self._payload = payload

    async def json(self) -> list[dict[str, object]]:
        return self._payload

    async def __aenter__(self) -> "_Resp":
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False


class _Session:
    def __init__(self, payload: list[dict[str, object]]) -> None:
        self._payload = payload

    def get(self, _url: str, headers: dict[str, str] | None = None) -> _Resp:
        return _Resp(self._payload)

    async def __aenter__(self) -> "_Session":
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False


class _Proc:
    """A spawned process that already exited: a refused network git, or an unanswered local one."""

    def __init__(self, returncode: int) -> None:
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"", b""


class _Network:
    """Every way an update check can reach the network, recorded and refused.

    ``answer`` is what the releases list answers with, or ``None`` for an offline machine. A
    session is counted when it is opened, so a request that would have failed is counted too.
    """

    def __init__(
        self, monkeypatch: pytest.MonkeyPatch, answer: list[dict[str, object]] | None
    ) -> None:
        self.sessions = 0
        self.remote_git: list[list[str]] = []
        self.lookups: list[str] = []
        self._answer = answer
        self._run = subprocess.run
        self._getaddrinfo = socket.getaddrinfo
        monkeypatch.setattr(aiohttp, "ClientSession", self._session)
        monkeypatch.setattr(asyncio, "create_subprocess_exec", self._spawn)
        monkeypatch.setattr(subprocess, "run", self._run_sync)
        monkeypatch.setattr(socket, "getaddrinfo", self._lookup)

    def _session(self, *_a: object, **_k: object) -> _Session:
        self.sessions += 1
        if self._answer is None:
            raise aiohttp.ClientConnectionError("this test has no network")
        return _Session(self._answer)

    def _is_remote_git(self, argv: tuple[object, ...]) -> bool:
        words = [str(a) for a in argv]
        return bool(words) and Path(words[0]).name == "git" and talks_to_remote(words[1:])

    async def _spawn(self, *argv: object, **_k: object) -> _Proc:
        if self._is_remote_git(argv):
            self.remote_git.append([str(a) for a in argv])
            return _Proc(128)
        return _Proc(1)

    def _run_sync(self, argv, *a, **k):  # type: ignore[no-untyped-def]
        if self._is_remote_git(tuple(argv)):
            self.remote_git.append([str(x) for x in argv])
            return subprocess.CompletedProcess(argv, 128, "", "this test has no network")
        return self._run(argv, *a, **k)

    def _lookup(self, host, *a, **k):  # type: ignore[no-untyped-def]
        name = host.decode() if isinstance(host, bytes) else str(host)
        if name not in ("localhost", "127.0.0.1", "::1"):
            self.lookups.append(name)
            raise socket.gaierror(socket.EAI_NONAME, "this test has no network")
        return self._getaddrinfo(host, *a, **k)

    def tried(self) -> dict[str, object]:
        return {"sessions": self.sessions, "remote_git": self.remote_git, "lookups": self.lookups}

    def nothing_tried(self) -> bool:
        return self.sessions == 0 and not self.remote_git and not self.lookups


@pytest.fixture(autouse=True)
def _fresh_update_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """A neutral git environment, no check run yet, and the cached answer put back afterwards."""
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.delenv("PERSONALCLAW_INSTALL_KIND", raising=False)
    monkeypatch.delenv("PERSONALCLAW_PROJECT_DIR", raising=False)
    monkeypatch.setattr(updates, "_last_update_check", 0.0)
    monkeypatch.setattr(updates, "_local_version", "0.2.0")
    saved = dict(updates._update_info)
    updates._update_info.update({"available": False, "changes": "", "checked": False})
    yield
    updates._update_info.clear()
    updates._update_info.update(saved)


def _home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **block: object) -> Path:
    """A scratch PersonalClaw home whose config carries this `updates` block."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    (home / "config.json").write_text(json.dumps({"updates": block}), encoding="utf-8")
    return home


def _install(
    kind: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, package_in_checkout
) -> None:
    if kind == "git":
        package_in_checkout(tmp_path / "checkout")
    elif kind == "container":
        monkeypatch.setenv("PERSONALCLAW_INSTALL_KIND", "container")


def _gateway() -> object:
    """The gateway's own start-up check and staged install, on a stand-in with no dashboard."""
    from personalclaw.gateway import GatewayOrchestrator

    class _Gateway:
        _staged_apply_task = None
        dashboard_state = None

        _check_for_updates = GatewayOrchestrator._check_for_updates
        _work_in_flight = GatewayOrchestrator._work_in_flight
        _staged_auto_apply = GatewayOrchestrator._staged_auto_apply
        _await_idle_then_apply = GatewayOrchestrator._await_idle_then_apply
        _auto_apply_update = GatewayOrchestrator._auto_apply_update

    return _Gateway()


async def _check_now() -> dict[str, object]:
    resp = await updates.api_update_check_now(MagicMock())
    return json.loads(resp.body)


async def _show_status() -> dict[str, object]:
    resp = await updates.api_update_check(MagicMock())
    return json.loads(resp.body)


# ── the gateway's start ──────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["git", "pip"])
async def test_a_start_with_automatic_checks_off_sends_nothing_and_says_so(
    kind, monkeypatch, tmp_path, package_in_checkout, capsys
) -> None:
    _home(monkeypatch, tmp_path, check_enabled=False)
    _install(kind, monkeypatch, tmp_path, package_in_checkout)
    net = _Network(monkeypatch, answer=_NEWER)

    await _gateway()._check_for_updates()

    out = capsys.readouterr().out
    assert net.nothing_tried(), net.tried()
    assert "Automatic update checks are off" in out
    assert "Checking for updates" not in out, "it said it was checking with checks off"
    assert "Already on latest version" not in out, "it claimed a result no check produced"


def test_the_start_up_line_is_said_only_once_the_gate_has_answered() -> None:
    """The gateway's run printed "Checking for updates…" itself, before the check could ask the
    setting, so the line came out with automatic checks off too."""
    import inspect

    from personalclaw.gateway import GatewayOrchestrator

    assert "Checking for updates" not in inspect.getsource(GatewayOrchestrator.run)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer, line",
    [
        (_NEWER, "Update available: v9.9.9"),
        (_SAME, "You're on the newest release (v0.2.0)"),
        (None, "Couldn't check for updates"),
    ],
)
async def test_a_start_with_checks_on_asks_for_the_releases_and_says_what_it_found(
    answer, line, monkeypatch, tmp_path, capsys
) -> None:
    """A wheel install compared nothing at start and still printed "Already on latest version"."""
    _home(monkeypatch, tmp_path, check_enabled=True)
    net = _Network(monkeypatch, answer=answer)

    await _gateway()._check_for_updates()

    out = capsys.readouterr().out
    assert net.sessions == 1, net.tried()
    assert "Checking for updates…" in out
    assert line in out, out
    assert "Already on latest version" not in out


@pytest.mark.parametrize(
    "status, line",
    [
        ({"available": True, "latest": "", "checked_now": True}, "Update available — see"),
        (
            {"checked_now": True, "latest": "", "pin": "0.2.1", "pin_miss": True},
            "No release matches the version pin 0.2.1",
        ),
        (
            {"checked_now": True, "latest": "0.1.3", "current": "0.2.0", "pin_older": True},
            "Pinned to v0.1.3, older than this build (v0.2.0)",
        ),
        (
            {"checked_now": True, "latest": "0.1.3", "current": "0.2.0"},
            "You're on v0.2.0, newer than the newest release (v0.1.3)",
        ),
        ({"checked_now": True, "latest": "", "current": "0.2.0"}, "Up to date (v0.2.0)"),
        ({"checked_now": False, "latest": "0.2.0"}, "Couldn't check for updates"),
    ],
)
def test_the_start_up_line_names_what_the_check_found(status, line) -> None:
    """Every answer a check can give has its own sentence, and none claims more than it found:
    a pin back is not "the newest release", and a build ahead of every release is not "on" it."""
    assert updates.update_result_line(status).startswith(line), updates.update_result_line(status)


@pytest.mark.asyncio
async def test_a_start_with_checks_on_fetches_a_checkout_s_origin(
    monkeypatch, tmp_path, package_in_checkout
) -> None:
    """Control for the checks-off start: on a source checkout the start-up check runs its fetch."""
    _home(monkeypatch, tmp_path, check_enabled=True)
    package_in_checkout(tmp_path / "checkout")
    net = _Network(monkeypatch, answer=_SAME)

    await _gateway()._check_for_updates()

    assert [argv[-2:] for argv in net.remote_git] == [["fetch", "--quiet"]], net.tried()
    assert net.sessions == 1


# ── a page that shows the update status ──────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["git", "pip", "container"])
async def test_showing_the_update_status_with_checks_off_sends_nothing(
    kind, monkeypatch, tmp_path, package_in_checkout
) -> None:
    """The Settings hub tile and Settings › Updates read this on every visit. On a source
    checkout it ran `git fetch` against the checkout's origin with checks off."""
    _home(monkeypatch, tmp_path, check_enabled=False)
    _install(kind, monkeypatch, tmp_path, package_in_checkout)
    self_update.write_releases_cache({"releases": [self_update._release_view(_SAME[0])]})
    net = _Network(monkeypatch, answer=_NEWER)

    body = await _show_status()

    assert net.nothing_tried(), net.tried()
    assert body["check_enabled"] is False
    assert body["latest"] == "0.2.0", "the status is still read from what the last check found"
    assert "checked_now" not in body, "no check ran, so there is no fresh answer to report"


@pytest.mark.asyncio
async def test_with_checks_on_pages_ask_at_most_once_per_check_interval(
    monkeypatch, tmp_path
) -> None:
    """Opening Settings asked GitHub on every visit, whatever `check_interval_hours` said."""
    _home(monkeypatch, tmp_path, check_enabled=True, check_interval_hours=12)
    net = _Network(monkeypatch, answer=_NEWER)
    clock = [time.time()]
    monkeypatch.setattr(updates.time, "time", lambda: clock[0])

    first = await _show_status()
    second = await _show_status()
    assert net.sessions == 1, "a second visit inside the interval asked again"
    assert first["available"] is True and second["available"] is True
    assert second["latest"] == "9.9.9", "the second visit reads what the first one found"

    clock[0] += 12 * 3600 + 1
    await _show_status()
    assert net.sessions == 2, "once the interval has passed, the next visit asks again"


@pytest.mark.asyncio
async def test_checks_turned_off_while_a_check_runs_stop_what_is_left_of_it(
    monkeypatch, tmp_path
) -> None:
    """The setting is read when each request would leave, not once per check: a checkout's fetch
    can take a while, and turning automatic checks off meanwhile stops the releases list after
    it."""
    home = _home(monkeypatch, tmp_path, check_enabled=True)
    net = _Network(monkeypatch, answer=_NEWER)

    async def _git_half_while_the_owner_turns_checks_off(*, asked: bool) -> bool:
        (home / "config.json").write_text(
            json.dumps({"updates": {"check_enabled": False}}), encoding="utf-8"
        )
        return False

    monkeypatch.setattr(updates, "_do_update_check", _git_half_while_the_owner_turns_checks_off)

    await _show_status()

    assert net.nothing_tried(), net.tried()


# ── the status poll's schedule ───────────────────────────────────────────────


async def _poll_status() -> None:
    """Serve `GET /api/status` from the real handler, then let what it started finish."""
    from aiohttp import web
    from aiohttp.test_utils import make_mocked_request

    from personalclaw.dashboard import handlers_system
    from personalclaw.dashboard.state import DashboardState

    state = DashboardState(
        sessions=MagicMock(count=0),
        start_time=time.time() - 60,
        subagents=MagicMock(count=0),
        context_builder=None,
    )
    state._owner_hash = "fixture-owner"
    app = web.Application()
    app["state"] = state
    req = make_mocked_request("GET", "/api/status", app=app)
    req["user"] = "fixture-owner"
    await handlers_system.api_status(req)
    others = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    await asyncio.gather(*others, return_exceptions=True)


@pytest.mark.asyncio
async def test_the_status_poll_starts_no_check_with_checks_off(
    monkeypatch, tmp_path, package_in_checkout
) -> None:
    _home(monkeypatch, tmp_path, check_enabled=False)
    package_in_checkout(tmp_path / "checkout")
    net = _Network(monkeypatch, answer=_NEWER)

    await _poll_status()

    assert net.nothing_tried(), net.tried()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["git", "pip"])
async def test_the_status_poll_checks_when_one_is_due_with_checks_on(
    kind, monkeypatch, tmp_path, package_in_checkout
) -> None:
    """Control: on the schedule, the releases list is asked on every kind (a wheel install's
    scheduled check compared nothing), and a checkout also fetches its origin."""
    _home(monkeypatch, tmp_path, check_enabled=True)
    _install(kind, monkeypatch, tmp_path, package_in_checkout)
    net = _Network(monkeypatch, answer=_NEWER)

    await _poll_status()

    assert net.sessions == 1, net.tried()
    assert len(net.remote_git) == (1 if kind == "git" else 0), net.tried()


# ── Check now: the owner asks ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_check_now_asks_once_even_with_automatic_checks_off(
    monkeypatch, tmp_path, package_in_checkout
) -> None:
    _home(monkeypatch, tmp_path, check_enabled=False)
    package_in_checkout(tmp_path / "checkout")
    net = _Network(monkeypatch, answer=_NEWER)

    body = await _check_now()

    assert net.sessions == 1, net.tried()
    assert [argv[-2:] for argv in net.remote_git] == [["fetch", "--quiet"]], net.tried()
    assert body["checked_now"] is True
    assert body["latest"] == "9.9.9" and body["available"] is True
    assert body["release_notes"] == "What 9.9.9 brings."
    assert body["check_enabled"] is False, "asking once does not turn automatic checks on"


@pytest.mark.asyncio
async def test_check_now_says_when_nothing_answered(monkeypatch, tmp_path) -> None:
    _home(monkeypatch, tmp_path, check_enabled=False)
    self_update.write_releases_cache({"releases": [self_update._release_view(_SAME[0])]})
    _Network(monkeypatch, answer=None)

    body = await _check_now()

    assert body["checked_now"] is False, "an offline check reported a fresh answer"


@pytest.mark.asyncio
async def test_check_now_is_counted_by_the_schedule(monkeypatch, tmp_path) -> None:
    """A check the owner just ran is the latest answer: a page opened right after reads it."""
    _home(monkeypatch, tmp_path, check_enabled=True)
    net = _Network(monkeypatch, answer=_NEWER)

    await _check_now()
    await _show_status()

    assert net.sessions == 1, net.tried()


# ── the staged install that follows a check ──────────────────────────────────


def _staged_install_seams(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[str]:
    called: list[str] = []

    async def _resolve(*_a: object, **_k: object) -> str:
        called.append("resolve")
        return "v9.9.9"

    def _git(step: str):  # type: ignore[no-untyped-def]
        def _record(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
            called.append(step)
            return subprocess.CompletedProcess([], 1, "", "")

        return _record

    monkeypatch.setattr(self_update, "source_checkout", lambda: str(tmp_path / "checkout"))
    monkeypatch.setattr(self_update, "git_tracked_changes", lambda _proj: [])
    monkeypatch.setattr(self_update, "resolve_target", _resolve)
    monkeypatch.setattr(self_update, "git_fetch_tags", _git("fetch_tags"))
    monkeypatch.setattr(self_update, "git_checkout", _git("checkout"))
    return called


@pytest.mark.asyncio
async def test_a_staged_install_does_not_reach_out_once_checks_are_off(
    monkeypatch, tmp_path
) -> None:
    """A staged install can be waiting for work to finish when the owner turns checks off. It
    reads the setting when it runs, and installs nothing it would have to fetch."""
    _home(monkeypatch, tmp_path, check_enabled=False, auto="staged")
    called = _staged_install_seams(monkeypatch, tmp_path)
    net = _Network(monkeypatch, answer=_NEWER)

    await _gateway()._auto_apply_update()

    assert called == [], called
    assert net.nothing_tried(), net.tried()


@pytest.mark.asyncio
async def test_a_staged_install_proceeds_with_checks_on(monkeypatch, tmp_path) -> None:
    """Control: the same install resolves its release and fetches it while checks are on."""
    _home(monkeypatch, tmp_path, check_enabled=True, auto="staged")
    called = _staged_install_seams(monkeypatch, tmp_path)
    _Network(monkeypatch, answer=_NEWER)

    await _gateway()._auto_apply_update()

    assert called[:2] == ["resolve", "fetch_tags"], called


# ── one gate ─────────────────────────────────────────────────────────────────


class _Readers(ast.NodeVisitor):
    """``(function, line)`` for every read of a ``check_enabled`` attribute, by its function."""

    def __init__(self) -> None:
        self.found: list[tuple[str, int]] = []
        self._where: list[str] = ["<module>"]

    def _enter(self, node: ast.AST, name: str) -> None:
        self._where.append(name)
        self.generic_visit(node)
        self._where.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._enter(node, node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._enter(node, node.name)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr == "check_enabled":
            self.found.append((self._where[-1], node.lineno))
        self.generic_visit(node)


def test_one_function_decides_whether_an_update_check_may_reach_out() -> None:
    """Three places read the setting each for itself, and the one that forgot it (a checkout's
    commits-behind count) fetched with checks off. Now one function answers, read when it is
    asked; the only other read is the value the Updates screen shows its switch with."""
    root = Path(personalclaw.__file__).parent
    readers: set[tuple[str, str]] = set()
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        if rel.startswith("config/") or "check_enabled" not in text:
            continue
        visitor = _Readers()
        visitor.visit(ast.parse(text))
        readers |= {(rel, where) for where, _line in visitor.found}
    assert readers, "the scan found no reader at all: it is not reading the tree"
    assert readers == {
        ("self_update.py", "may_check_for_updates"),
        ("dashboard/handlers/updates.py", "update_status"),
    }, sorted(readers)
