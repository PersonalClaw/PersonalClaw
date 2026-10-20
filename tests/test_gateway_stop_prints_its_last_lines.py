"""A stopping gateway's last lines reach its log.

The stop ends in ``os._exit``, which skips the interpreter's own flush of stdout and stderr. Those
are block-buffered whenever they are a pipe or a file — a service's log, a harness reading the
ready line — so "Goodbye!", and every line still in the buffer with it ("Shutting down…"), was
lost. The stop now writes out what it printed before it ends the process.

Driven through the real stop (``GatewayOrchestrator._finish``) in a child process whose stdout is a
pipe, because a buffered stream is exactly what an in-process test cannot see: pytest's capture
replaces ``sys.stdout``, and a terminal is line-buffered.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

_CHILD = textwrap.dedent("""
    import asyncio

    import personalclaw.session as session
    from personalclaw import gateway

    # Nothing to reap in a scratch home, and a stop test must not go looking.
    session.cleanup_orphaned_sessions = lambda: None

    orchestrator = gateway.GatewayOrchestrator.__new__(gateway.GatewayOrchestrator)


    async def _already_stopped() -> None:
        return None


    orchestrator._shutdown = _already_stopped
    print("Shutting down…")
    asyncio.run(orchestrator._finish())
    """)


def test_the_stops_last_lines_reach_a_piped_log(tmp_path: Path) -> None:
    """🔴 Red before: the child exited 0 with an EMPTY stdout — both lines were still buffered."""
    env = {
        **os.environ,
        "PERSONALCLAW_HOME": str(tmp_path / "home"),
        "PYTHONIOENCODING": "utf-8",
    }
    done = subprocess.run(
        [sys.executable, "-c", _CHILD],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
        env=env,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines()[-2:] == ["Shutting down…", "Goodbye!"], (
        done.stdout,
        done.stderr[-2000:],
    )
