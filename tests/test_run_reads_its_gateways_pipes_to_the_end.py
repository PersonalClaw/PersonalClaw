"""``run`` reads the pipes of the gateway it starts to their end, so that gateway never stalls.

``start_transient_gateway`` started the gateway with its stdout and stderr on pipes and read
stdout only up to the ``PERSONALCLAW_READY`` line. A pipe nobody reads fills at about 64 KB, and
the gateway then blocks on its next write: one that logged that much after its readiness line
stopped serving the ``run`` that started it, and one that logged that much on stderr before it
never said it was ready, so the run waited out its boot time.

These start a stand-in for the gateway (the same argv tail, through ``self_update.cli_argv``) that
writes up to 2 MB to its pipes, and see that it gets to the end of what it writes.
"""

from __future__ import annotations

import sys
import textwrap
import time

import pytest

from personalclaw import cli_run

_READY = 'print("PERSONALCLAW_READY:" + json.dumps({"port": 4242, "token": "t0k"}), flush=True)'
_LOUD = (
    "sys.stdout.write('o' * 2_000_000); sys.stdout.flush(); "
    "sys.stderr.write('e' * 2_000_000); sys.stderr.flush()"
)


def _stand_in(monkeypatch, tmp_path, body: str) -> None:
    """``run``'s gateway command, pointed at a script running *body* (with ``done`` the file it
    writes once it has written everything)."""
    script = tmp_path / "gateway.py"
    done = tmp_path / "done"
    script.write_text(
        "import json, sys, time\n"
        + textwrap.dedent(body).replace("DONE", repr(str(done)))
        + "\ntime.sleep(30)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("personalclaw.self_update.cli_argv", lambda: [sys.executable, str(script)])


def _written(tmp_path, within: float = 15.0) -> bool:
    ends = time.monotonic() + within
    while time.monotonic() < ends:
        if (tmp_path / "done").exists():
            return True
        time.sleep(0.05)
    return False


def test_a_gateway_that_logs_after_it_is_ready_is_not_held_up(monkeypatch, tmp_path):
    _stand_in(
        monkeypatch,
        tmp_path,
        f"{_READY}\n{_LOUD}\nopen(DONE, 'w').write('done')\n",
    )
    port, token, proc = cli_run.start_transient_gateway()
    try:
        assert (port, token) == (4242, "t0k")
        assert _written(tmp_path), "the gateway blocked on a pipe nobody read"
    finally:
        cli_run._shutdown_transient(proc)


def test_a_gateway_that_logs_before_it_is_ready_still_says_so(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_run, "_BOOT_TIMEOUT_SECS", 15.0)
    _stand_in(
        monkeypatch,
        tmp_path,
        "sys.stderr.write('e' * 2_000_000); sys.stderr.flush()\n"
        f"{_READY}\nopen(DONE, 'w').write('done')\n",
    )
    started = time.monotonic()
    port, _token, proc = cli_run.start_transient_gateway()
    try:
        assert port == 4242
        assert time.monotonic() - started < 15.0
    finally:
        cli_run._shutdown_transient(proc)


def test_a_gateway_that_exits_before_it_is_ready_is_said_with_the_end_of_its_stderr(
    monkeypatch, tmp_path
):
    _stand_in(
        monkeypatch,
        tmp_path,
        "sys.stderr.write('x' * 100_000 + 'the last words'); sys.stderr.flush(); sys.exit(3)\n",
    )
    with pytest.raises(cli_run.RunError) as refused:
        cli_run.start_transient_gateway()
    said = str(refused.value)
    assert "exited with code 3 before it was ready" in said
    assert said.endswith("the last words")
