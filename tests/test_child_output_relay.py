"""The relay every supervised child's output goes through (``child_output``): what a line is,
how it is masked and bounded, and how much of a process the log takes.

The processes themselves (an app's backend, worker and engine) are driven in
``test_an_apps_child_processes_print_to_the_log.py``; this file pins the rules those rest on, on
lines fed straight in, so each rule is shown on its own and the rate is measured on a clock the
test moves rather than the wall's.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time

import pytest

from personalclaw import child_output
from personalclaw.child_output import STDERR, STDOUT, ChildOutput, LineSplitter, mask_line

#: The documented example access key: a credential shape the log's mask knows.
_KEY = "AKIAIOSFODNN7EXAMPLE"


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    tick = _Clock()
    monkeypatch.setattr(child_output, "_monotonic", tick)
    return tick


@pytest.fixture
def relayed(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    """The relay's own records, captured from INFO up (read ``.records`` when asserting: the
    list is the call phase's own)."""
    caplog.set_level(logging.INFO, logger="personalclaw.child_output")
    return caplog


def _output(**kw) -> ChildOutput:
    return ChildOutput(app="fixture", process="backend", pid=4242, **kw)


# ── what a line is ─────────────────────────────────────────────────────────────────────────


def test_a_line_is_what_a_terminal_would_show_of_it() -> None:
    splitter = LineSplitter()
    assert splitter.feed(b"one\r\ntw") == ["one"]
    assert splitter.feed(b"o\nDownloading 10%\rDownloading 55%\rDownloading 100%\n") == [
        "two",
        "Downloading 100%",
    ]
    assert splitter.feed(b"no line break at the end") == []
    assert splitter.close() == ["no line break at the end"]
    assert splitter.close() == []


def test_a_line_too_long_to_keep_keeps_its_start_and_never_half_a_word() -> None:
    """The cut falls inside a key: the part of it that was read is not kept, since a mask cannot
    recognise half of one."""
    start = "word " * 1636  # 8180 bytes: the read stops 12 bytes into the key
    splitter = LineSplitter()
    (line,) = splitter.feed(f"{start}{_KEY}{'x' * 5000}\n".encode())
    assert "AKIA" not in line and line.startswith("word word"), line[-60:]
    assert line.endswith(f"[… cut: the line was {len(start) + len(_KEY) + 5000} bytes]")

    (unbroken,) = LineSplitter().feed(b"y" * 20_000 + b"\n")
    assert unbroken == "[a line of 20000 bytes, not kept]"


def test_a_long_line_handed_over_whole_is_cut_the_same_way() -> None:
    output = _output()
    output.line(STDOUT, "z" * 50_000)
    assert output.lines() == ["[a line of 50000 bytes, not kept]"]


# ── how it is masked ───────────────────────────────────────────────────────────────────────


def test_a_child_cannot_print_a_line_that_reads_as_one_of_the_logs_own() -> None:
    shown = mask_line("done\x1b[2K\x0bINFO personalclaw.gateway: owner signed in\u2028\x07")
    assert "\x1b" not in shown and "\x0b" not in shown and "\u2028" not in shown, repr(shown)
    assert shown == "done\\x1b[2K\\x0bINFO personalclaw.gateway: owner signed in\\u2028\\x07"


def test_a_traceback_keeps_its_indentation_and_a_long_line_says_it_was_cut() -> None:
    assert mask_line('    File "server.py", line 3') == '    File "server.py", line 3'
    long = mask_line("a " * 900)
    assert len(long) <= child_output.LINE_MAX_CHARS + len(" [… cut]") and long.endswith("[… cut]")


def test_every_credential_the_child_was_handed_is_masked_by_value() -> None:
    """A credential with no shape of its own is masked because the child's environment named
    it as one; the rest of its environment is not masked."""
    value = "f00dfacecafebeef0123456789"
    output = _output(env={"EXAMPLE_SERVICE_TOKEN": value, "TMPDIR": "/tmp/scratch-dir"})
    output.line(STDERR, f"using {value} with {_KEY} under /tmp/scratch-dir")
    assert output.lines() == [
        "using [REDACTED: credential] with [REDACTED: credential] under /tmp/scratch-dir"
    ]


# ── how much the log takes ─────────────────────────────────────────────────────────────────


def _messages(records: list[logging.LogRecord]) -> list[str]:
    return [r.getMessage() for r in records if r.name == "personalclaw.child_output"]


def test_the_log_takes_a_burst_then_a_rate_and_counts_the_rest(
    clock: _Clock, relayed: pytest.LogCaptureFixture
) -> None:
    output = _output()
    for i in range(child_output.LOG_BURST + 50):
        output.line(STDERR, f"line {i}")
    logged = [m for m in _messages(relayed.records) if " stderr: " in m]
    assert len(logged) == child_output.LOG_BURST and logged[-1].endswith(
        f"stderr: line {child_output.LOG_BURST - 1}"
    )

    output.tick()
    notes = [m for m in _messages(relayed.records) if "not in the log" in m]
    assert notes == [
        "app fixture backend (pid 4242): 50 more lines it printed are not in the log, which "
        f"takes {child_output.LOG_BURST} at once from one process and then "
        f"{child_output.LOG_PER_SEC:g} a second"
    ]

    clock.now += 1.0  # one second's share
    for i in range(5):
        output.line(STDERR, f"later {i}")
    later = [m for m in _messages(relayed.records) if "stderr: later" in m]
    assert len(later) == int(child_output.LOG_PER_SEC), later
    output.tick()
    assert (
        len([m for m in _messages(relayed.records) if "not in the log" in m]) == 1
    ), "noted too often"
    clock.now += child_output.NOTE_EVERY_SECS
    output.tick()
    assert ": 3 more lines it printed are not in the log" in _messages(relayed.records)[-1]
    assert len(output.lines()) == child_output.TAIL_LINES and output.lines()[-1] == "later 4"


def test_what_the_log_does_not_show_costs_nothing(
    clock: _Clock, caplog: pytest.LogCaptureFixture
) -> None:
    """At the default level stdout is not shown, so it cannot crowd out what went wrong."""
    with caplog.at_level(logging.WARNING, logger="personalclaw.child_output"):
        output = _output()
        for i in range(5 * child_output.LOG_BURST):
            output.line(STDOUT, f"request {i}")
        output.line(STDERR, "the database file is locked")
        output.tick()
    assert _messages(caplog.records) == [
        "app fixture backend (pid 4242) stderr: the database file is locked"
    ]
    assert output.lines()[-2:] == [
        f"request {5 * child_output.LOG_BURST - 1}",
        "the database file is locked",
    ]


def test_a_note_is_logged_at_the_level_of_what_it_held_back(
    clock: _Clock, relayed: pytest.LogCaptureFixture
) -> None:
    quiet, loud = _output(), _output()
    for i in range(child_output.LOG_BURST + 1):
        quiet.line(STDOUT, f"out {i}")
        loud.line(STDERR, f"err {i}")
    quiet.tick()
    loud.tick()
    levels = [r.levelno for r in relayed.records if "not in the log" in r.getMessage()]
    assert levels == [logging.INFO, logging.WARNING]


# ── how it ended ───────────────────────────────────────────────────────────────────────────


def test_an_end_it_came_to_on_its_own_is_reported_and_one_personalclaw_made_is_not(
    relayed: pytest.LogCaptureFixture,
) -> None:
    crashed = _output()
    crashed.line(STDERR, "ValueError: the port must be a number")
    crashed.ended(1)
    report = crashed.report()
    assert report is not None and report["pid"] == 4242
    assert (report["exitCode"], report["ended"]) == (1, "exited with code 1")
    assert report["cause"] == "ValueError: the port must be a number"
    assert report["endedAt"].endswith("+00:00"), "a served time names its zone"
    assert _messages(relayed.records)[-1] == (
        "app fixture backend (pid 4242) exited with code 1: ValueError: the port must be a number"
    )

    stopped = _output()
    stopped.stopping()
    stopped.ended(-15)
    assert stopped.exit is not None and stopped.exit.stopped and stopped.report() is None
    assert not any(
        "signal 15" in m for m in _messages(relayed.records)
    ), "a stop was logged as an exit"

    crashed.ended(0)
    assert crashed.exit.code == 1, "only the first end counts"
    assert [child_output.ended_words(c) for c in (-9, 2, 0)] == [
        "was ended by signal 9",
        "exited with code 2",
        "exited",
    ]


def test_a_child_that_ends_while_what_it_started_holds_its_output_is_seen_to_end(
    tmp_path,
) -> None:
    """The pipe stays open as long as a program the child started holds it, so its end is read
    from the process itself, and what that program prints goes on being relayed."""
    script = (
        "import subprocess, sys\n"
        "helper = subprocess.Popen(['/bin/sleep', '30'])\n"
        "print(f'started helper {helper.pid}', file=sys.stderr, flush=True)\n"
        "sys.exit(2)\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    output = ChildOutput(app="fixture", process="worker 'worker'", pid=proc.pid)
    child_output.relay(proc, output)
    helper = 0
    try:
        deadline = time.monotonic() + 60
        while output.exit is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert output.exit is not None and output.exit.code == 2, output.exit
        (said,) = output.lines()
        helper = int(said.removeprefix("started helper "))
        assert proc.poll() == 2 and _alive(helper), "the helper no longer holds the pipe"
    finally:
        if helper and _alive(helper):
            os.kill(helper, signal.SIGKILL)
        proc.wait(timeout=30)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True
