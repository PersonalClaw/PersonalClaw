"""Process facts come from ``/proc`` where there is one, so a host without ``ps`` still has them.

The published container image ships no ``ps``. ``process_table()`` asked ``ps`` only, got
nothing back, and returned an empty table, which the app runtime read as "no process still runs
this app's previous version": after an app update in the image, a server an agent session kept
running from the old version was never named. ``personalclaw stop`` needs the same fact about one
pid. Both now read ``/proc`` on Linux and ask ``ps`` only where there is no ``/proc``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from personalclaw import process_facts


@pytest.fixture
def proc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A ``/proc`` holding exactly the processes a test adds, on a host with no ``ps``."""
    root = tmp_path / "proc"
    root.mkdir()
    (root / "self").mkdir()  # not a pid: ignored
    monkeypatch.setattr(process_facts, "PROC", root)

    def refuse(*args: object, **_kwargs: object) -> object:
        raise AssertionError(f"ran a program: {args[0] if args else '?'}")

    monkeypatch.setattr(subprocess, "run", refuse)

    def add(pid: int, ppid: int, comm: str, argv: list[str]) -> None:
        entry = root / str(pid)
        entry.mkdir()
        (entry / "stat").write_text(f"{pid} ({comm}) S {ppid} {pid} {pid} 0 -1 4194560 0 0")
        (entry / "cmdline").write_bytes(b"".join(a.encode() + b"\0" for a in argv))

    return add


def test_the_process_table_is_read_from_proc(proc) -> None:
    proc(1, 0, "tini", ["/usr/bin/tini", "--", "personalclaw", "gateway"])
    proc(7, 1, "personalclaw", ["/opt/venv/bin/python", "/opt/venv/bin/personalclaw", "gateway"])
    # A program name with spaces and parentheses of its own: the fields after it are counted
    # from the last `)`, or its ppid reads as garbage.
    proc(42, 7, "my (odd) server", ["node", "/data/apps/demo/server.js", "--port", "4000"])

    assert process_facts.process_table() == {
        1: (0, "/usr/bin/tini -- personalclaw gateway"),
        7: (1, "/opt/venv/bin/python /opt/venv/bin/personalclaw gateway"),
        42: (7, "node /data/apps/demo/server.js --port 4000"),
    }


def test_a_process_that_exits_mid_read_is_left_out(proc) -> None:
    proc(7, 1, "personalclaw", ["personalclaw", "gateway"])
    (process_facts.PROC / "8").mkdir()  # listed, but gone before its files were read
    assert set(process_facts.process_table()) == {7}


def test_descendants_come_through_the_proc_table(proc) -> None:
    proc(7, 1, "personalclaw", ["personalclaw", "gateway"])
    proc(42, 7, "node", ["node", "server.js"])
    proc(43, 42, "node", ["node", "worker.js"])
    proc(99, 1, "sh", ["sh"])
    below = process_facts.descendants(7, process_facts.process_table())
    assert below == {42: frozenset(), 43: frozenset({42})}


def test_a_command_line_is_read_from_proc(proc) -> None:
    proc(7, 1, "personalclaw", ["/opt/venv/bin/python", "/opt/venv/bin/personalclaw", "gateway"])
    assert process_facts.command_line(7) == (
        "/opt/venv/bin/python /opt/venv/bin/personalclaw gateway"
    )
    assert process_facts.command_line(8) == ""  # no such process
    assert process_facts.command_line(0) == ""
