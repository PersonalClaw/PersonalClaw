"""Run a test's measurement in an interpreter of its own, and read back what it found.

``tracemalloc`` counts what EVERY thread of a process allocates. A worker of the suite is one
long-lived process, and a thread an earlier test started (an index filling in the background, a
reading pass, a server's loop) can still be running and allocating while a later test measures.
Measured: a scan's reading pass peaked at 19.3 MB in a worker of the full suite and at 4.4 MB
alone, against a bound of 8.1 MB. A peak taken in a fresh interpreter holds only what the
measured code allocated.

The child imports ``personalclaw`` from the tree this process imported it from, keeps the OS
keychain out as the suite does (``keychain_off``), and inherits this process's environment. Its
homes are the test's own when the test chose them (``PERSONALCLAW_HOME`` set), and otherwise
fresh ones under *scratch*: a child is outside every guard of the suite, so it must never be
able to resolve the real home.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import personalclaw

#: How long a measurement may take, inside the suite's 120 s limit for the test that runs it.
TIMEOUT_SECONDS = 100

_PROLOGUE = """\
import json
from personalclaw.config.credentials import keychain_off
keychain_off()
VALUES = json.loads({values!r})
"""


def measure(program: str, scratch: Path, **values: object) -> dict:
    """Run *program* in a fresh interpreter and return the JSON object it printed last.

    *values* reach the program as the dict ``VALUES``. A failure of the child fails the test
    with what the child said on stderr.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(personalclaw.__file__).resolve().parent.parent)
    if not env.get("PERSONALCLAW_HOME"):
        (scratch / "user").mkdir(parents=True, exist_ok=True)
        env["HOME"] = str(scratch / "user")
        env["PERSONALCLAW_HOME"] = str(scratch / "personalclaw")
    source = _PROLOGUE.format(values=json.dumps(values)) + textwrap.dedent(program)
    done = subprocess.run(
        [sys.executable, "-c", source],
        env=env,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
        check=False,
    )
    assert (
        done.returncode == 0
    ), f"the measurement failed ({done.returncode}):\n{done.stderr[-3000:]}"
    return json.loads(done.stdout.strip().splitlines()[-1])
