"""Every line of the gateway's log starts with its full local date and time.

``gateway.log`` and the console a service manager keeps started each line with the time of day
alone (``datefmt="%H:%M:%S"``). The file rotates at 2 MB, so a quiet install's holds weeks: read on
2026-10-30, one file held lines from more than a week, and a ``19:03`` warning could be from any of
its days (#5728). Now each line starts ``2026-10-30 19:03:24.512-07:00``: the local date and time,
with milliseconds and the offset, which reads back as the moment the record was made. So does the
line of a record whose words are withheld, and what the loop watch appends.
"""

from __future__ import annotations

import io
import logging
from datetime import datetime

import pytest

from personalclaw import cli, loop_watch, security

pytestmark = pytest.mark.security

#: 2026-10-30 19:03:24.512 local time, as a record's ``created``.
MADE = datetime(2026, 10, 30, 19, 3, 24, 512000).astimezone()


def _record(msg: str = "the scheduler fired a run") -> logging.LogRecord:
    record = logging.LogRecord(
        "personalclaw.scheduler", logging.WARNING, __file__, 1, msg, None, None
    )
    record.created = MADE.timestamp()
    record.msecs = 512.0
    return record


def _when(line: str) -> datetime:
    """The moment *line* says it was written: its first 29 characters."""
    stamp = line[:29]
    assert stamp[10] == " " and stamp[19] == "." and stamp[23] in "+-", line
    return datetime.fromisoformat(stamp)


def test_a_gateway_log_line_says_its_date_and_time(tmp_path) -> None:
    log = tmp_path / "gateway.log"
    handler = cli._gateway_log_handler(log)
    try:
        handler.emit(_record())
    finally:
        handler.close()
    (line,) = log.read_text().splitlines()

    assert _when(line) == MADE
    assert line[29:] == " WARNING personalclaw.scheduler: the scheduler fired a run"


def test_a_console_line_says_its_date_and_time() -> None:
    handler = cli._console_log_handler()
    stream = io.StringIO()
    handler.setStream(stream)
    handler.emit(_record())

    assert _when(stream.getvalue()) == MADE


def test_a_withheld_line_still_says_when(monkeypatch) -> None:
    """A record its masker cannot mask is written as its time, level and logger alone."""

    def cannot_mask(_text: str) -> str:
        raise ValueError("the masker raised")

    monkeypatch.setattr(security, "redact_for_display", cannot_mask)
    line = cli._console_log_handler().format(_record("the record's own words"))

    assert _when(line) == MADE
    assert "withheld" in line and "own words" not in line


def test_what_the_loop_watch_appends_says_when(monkeypatch, capfd) -> None:
    """With no log file to append to, the stalled loop's report goes to stderr, dated alike."""
    monkeypatch.setattr(logging.getLogger(), "handlers", [])
    before = datetime.now().astimezone().replace(microsecond=0)
    loop_watch._append("the event loop has not answered for 30 s")

    line = capfd.readouterr().err.splitlines()[0]
    assert _when(line) >= before


@pytest.mark.parametrize("offset_hours", [-7, 0, 5.5])
def test_the_offset_reads_back_as_the_same_moment(monkeypatch, offset_hours) -> None:
    """Whatever the machine's zone, the stamp reads back as the moment the record was made."""
    import time

    zone = {-7: "America/Los_Angeles", 0: "UTC", 5.5: "Asia/Kolkata"}[offset_hours]
    try:
        with monkeypatch.context() as here:
            here.setenv("TZ", zone)
            time.tzset()
            line = cli._console_log_handler().format(_record())
    finally:
        time.tzset()
    assert _when(line).timestamp() == pytest.approx(MADE.timestamp(), abs=0.001)
