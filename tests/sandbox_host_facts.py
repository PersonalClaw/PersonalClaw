"""The sandbox's check of this computer, asked once per worker and put back before every test.

``tests/test_gateway_base_owner.py``'s two cron tests failed on #5638's macOS shard (4/5), and
failed again alone. Each fakes the global ``subprocess.run`` with a function that raises, then runs
a script through ``schedule_script.run_script_sandboxed``, which asks the sandbox first. When no
test before them in that worker had asked, the sandbox-exec check (``sandbox._probe_sandbox_exec``)
ran its ``sandbox-exec`` through the fake, did not answer, and the run was refused
(``sandbox.UNCHECKED_REFUSAL``). Since #5651 a check that raises is unknown, never a no; before it,
the raise read as no and the script ran without its sandbox, into the fake. In a whole shard the
tests passed whenever an earlier test in the worker had asked, so the order of the shard decided.

So the check is asked for real once, as ``conftest`` is imported, before anything is collected
(:func:`settle`), and before every test its answers and the backend derived from them are put back
as they were then (:func:`restore`): a test's own fake ``subprocess`` is never the check's runner,
whatever the test before it cleared or faked. A test about the check itself clears it
(``sandbox.reset_backend()`` or a probe's ``cache_clear()``) and gets a fresh one.

An answer the settle could not get (the check did not answer in time) is put back as unknown, so
the next call asks again, as it does outside the suite.

Linux's check starts a PersonalClaw child (``python -m personalclaw._namespace_probe``), and the
real-home guard refuses one given no home of its own (``real_home_guard``'s ``isolates``). No test
has made one yet when the settle runs, and main's CI contract runs the head's suite with ``$HOME``
the runner's own, so there the settle stopped conftest's import (run 40825648136). The settle gives
the children it starts a scratch home of its own, as ``git_helper_guard.cores_git_refusal`` does,
and puts ``PERSONALCLAW_HOME`` back as it found it.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time

from personalclaw import sandbox

#: The host facts the sandbox keeps once answered (``sandbox._HostFact``), by name.
FACTS = ("_probe_unshare", "_probe_unshare_net", "_probe_sandbox_exec")

_answers: dict[object, bool | None] = {}
_backend: tuple[str | None, str | None] = (None, None)


def settle() -> float:
    """Ask the check for real, keep its answers and the backend derived from them, and return
    the seconds it took."""
    global _backend
    started = time.monotonic()
    chosen = os.environ.get("PERSONALCLAW_HOME")
    home = tempfile.mkdtemp(prefix="pclaw-tests-settle-")
    os.environ["PERSONALCLAW_HOME"] = home
    try:
        sandbox.reset_backend()
        for name in FACTS:
            fact = getattr(sandbox, name)
            _answers[fact] = fact()
        sandbox.detect_backend()
        _backend = (sandbox._backend, sandbox._backend_config_mode)
    finally:
        if chosen is None:
            os.environ.pop("PERSONALCLAW_HOME", None)
        else:
            os.environ["PERSONALCLAW_HOME"] = chosen
        shutil.rmtree(home, ignore_errors=True)
    return time.monotonic() - started


def answers() -> dict[str, bool | None]:
    """What :func:`settle` found, by fact name."""
    return {getattr(fact, "__name__", repr(fact)): known for fact, known in _answers.items()}


def backend() -> str | None:
    """The backend :func:`settle` derived, or ``None`` when a check did not answer."""
    return _backend[0]


def restore() -> None:
    """Put the check back as :func:`settle` found it."""
    for fact, known in _answers.items():
        fact._known = known  # type: ignore[attr-defined]
    sandbox._backend, sandbox._backend_config_mode = _backend
