"""Each PersonalClaw home has a tmux server of its own.

Persistent terminals and durable workflow workers run in tmux sessions. On ``main`` every tmux
command named one socket, ``-L personalclaw``: one server per MACHINE, in tmux's own folder
(``/tmp/tmux-$UID/personalclaw``), shared by every home on it. A dev gateway on a scratch home
and the owner's real gateway saw, reattached and deleted each other's terminals, a session got
the environment of whichever gateway started the server, and the socket outlived them all.

Now the socket is the home's own, ``<home>/tmux.sock``, resolved when tmux runs; a home whose
path is too long for a socket gets a short name derived from it instead. Uninstalling the
service and wiping a home stop that home's server and remove its socket.
"""

from __future__ import annotations

import ast
import asyncio
import os
import shutil
import socket
import tempfile
from pathlib import Path

import pytest

import personalclaw
from personalclaw import tmux_substrate

SRC = Path(personalclaw.__file__).resolve().parent


@pytest.fixture
def short_home():
    """A home whose socket path fits (a pytest tmp_path on macOS is too long for one)."""
    made: list[Path] = []

    def make() -> Path:
        home = Path(tempfile.mkdtemp(prefix="pch-", dir="/tmp")).resolve()
        made.append(home)
        return home

    try:
        yield make
    finally:
        # Every home goes, even when stopping one's server raises: a raise from the first
        # would otherwise leave the others in /tmp.
        failures: list[BaseException] = []
        for home in made:
            try:
                tmux_substrate.kill_server(home)
            except Exception as exc:  # noqa: BLE001 - re-raised below, once every home is gone
                failures.append(exc)
            shutil.rmtree(home, ignore_errors=True)
        if failures:
            raise failures[0]


def _active(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)


def test_two_homes_address_two_servers_each_inside_its_home(short_home, monkeypatch):
    first, second = short_home(), short_home()

    _active(monkeypatch, first)
    in_first = tmux_substrate._argv("list-sessions")
    _active(monkeypatch, second)
    in_second = tmux_substrate._argv("list-sessions")

    assert in_first == ["tmux", "-S", str(first / "tmux.sock"), "list-sessions"]
    assert in_second == ["tmux", "-S", str(second / "tmux.sock"), "list-sessions"]


def test_a_home_reached_through_a_link_is_the_same_server(short_home, monkeypatch):
    home = short_home()
    link = home.parent / f"{home.name}-link"
    link.symlink_to(home)
    try:
        _active(monkeypatch, link)
        assert tmux_substrate._argv("ls") == ["tmux", "-S", str(home / "tmux.sock"), "ls"]
    finally:
        link.unlink()


def test_a_home_too_long_for_a_socket_still_gets_a_server_of_its_own(tmp_path, monkeypatch):
    """A socket path has a hard limit (104 bytes on macOS, 108 on Linux, with the NUL)."""
    homes = [tmp_path / ("h" * 60) / ("a" * 60), tmp_path / ("h" * 60) / ("b" * 60)]
    flags = []
    for home in homes:
        home.mkdir(parents=True)
        _active(monkeypatch, home)
        argv = tmux_substrate._argv("ls")
        assert argv[1] == "-L" and argv[2].startswith("pclaw-"), argv
        assert len(os.fsencode(tmux_substrate.socket_path(home))) <= 103
        flags.append(argv[1:3])
    assert flags[0] != flags[1]


def test_no_tmux_command_is_built_outside_the_substrate():
    """Three private copies in the terminal handler kept naming the machine-wide socket."""
    stray = []
    for py in sorted(SRC.rglob("*.py")):
        rel = py.relative_to(SRC).as_posix()
        if rel == "tmux_substrate.py" or rel.startswith("skills/bundled/"):
            continue
        for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
            args = []
            if isinstance(node, (ast.List, ast.Tuple)):
                args = node.elts
            elif isinstance(node, ast.Call):
                args = node.args
            first = args[0] if args else None
            if isinstance(first, ast.Constant) and first.value == "tmux":
                stray.append(f"{rel}:{node.lineno}")
    assert stray == [], stray


def _stale_socket(path: Path) -> None:
    """A socket file with nothing listening, as a server that died leaves it."""
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    listener.close()


def test_stopping_a_homes_server_removes_a_socket_left_by_one_that_died(short_home, monkeypatch):
    home = short_home()
    _stale_socket(home / "tmux.sock")
    monkeypatch.setenv("PATH", "/nonexistent")  # no tmux to ask: the file alone is the leftover

    tmux_substrate.kill_server(home)

    assert not (home / "tmux.sock").exists()


def test_the_homes_socket_is_one_the_home_audit_accounts_for(short_home):
    """The Doctor's home audit reports every top-level path that nothing claims or ignores; a
    socket it did not know would turn the health strip coral on every home that ran tmux."""
    from personalclaw.durability.inventory import audit_home

    home = short_home()
    _stale_socket(home / "tmux.sock")

    result = audit_home(home)

    assert result.unclaimed == [] and result.ignored == 1, result


def test_stopping_a_home_that_never_ran_tmux_is_a_no_op(short_home):
    home = short_home()
    tmux_substrate.kill_server(home)
    assert list(home.iterdir()) == []


_REAL_TMUX = pytest.mark.skipif(shutil.which("tmux") is None, reason="needs the tmux binary")


@_REAL_TMUX
def test_a_session_in_one_home_is_invisible_from_another_and_goes_with_its_server(
    short_home, monkeypatch
):
    first, second = short_home(), short_home()
    name = tmux_substrate.durable_session_name("proj", "run", "one-home")

    _active(monkeypatch, first)
    started = asyncio.run(
        tmux_substrate.new_session(name, workspace=str(first), command=["sleep", "60"])
    )
    assert started and tmux_substrate.has_session_sync(name)
    assert (first / "tmux.sock").exists()

    _active(monkeypatch, second)
    assert not tmux_substrate.has_session_sync(name), "another home's server answered"
    assert asyncio.run(tmux_substrate.list_sessions()) == []

    tmux_substrate.kill_server(first)
    _active(monkeypatch, first)
    assert not tmux_substrate.has_session_sync(name)
    assert not (first / "tmux.sock").exists()


@_REAL_TMUX
def test_a_stale_socket_does_not_stop_the_next_server(short_home, monkeypatch):
    home = short_home()
    _stale_socket(home / "tmux.sock")
    _active(monkeypatch, home)
    name = tmux_substrate.durable_session_name("proj", "run", "after-stale")

    assert asyncio.run(
        tmux_substrate.new_session(name, workspace=str(home), command=["sleep", "60"])
    )
    assert tmux_substrate.has_session_sync(name)
