"""A suite leg that runs to its cap leaves every process's stacks in its job log (#3905, red (g)).

Three Linux shards ran to the 55-minute cap on 2026-10-18 with every pytest process still alive,
no worker crashed and no per-test timeout fired: pytest was killed before it wrote its JUnit, and
the job log stopped about 43 minutes before the cap. Nothing recorded which test hung, or where.

So each pytest process of a suite leg on GitHub Actions (the controller and every xdist worker)
arms ``faulthandler.dump_traceback_later``: if the process is still running ``MARGIN_SECS`` before
the leg's cap, it writes every thread's stack to the job log once, and goes on. A worker blocked
inside a test then shows that test's frames, and one stuck outside the test phases, where the
per-test timeout cannot reach, shows where it is. A run that finishes in time writes nothing.

The cap is the catalogue's own (``tools/ci/catalogue.toml``): the ``timeout_min`` of the job whose
``junit`` path is this run's ``--junitxml``, so no second number is kept. The dump goes to the
stderr pytest started with, taken while its capture is suspended (as pytest-timeout writes its
dumps), because during a test fd 2 is captured into a file the killed process never reports.
"""

from __future__ import annotations

import faulthandler
import os
from collections.abc import Iterable
from fnmatch import fnmatch
from pathlib import Path
from typing import IO, Any

#: How long before the leg's cap the stacks are written, counted from this process's start: the
#: steps before pytest (checkout, toolchain, ``uv sync``) spend part of the cap, so the dump fires
#: early enough to land before the runner kills the job.
MARGIN_SECS = 8 * 60

#: The stream the dump is armed on, held so it stays open for the process's life.
_STREAM: IO[str] | None = None


def deadline(xmlpath: str | None, jobs: Iterable[Any]) -> float | None:
    """Seconds after this process starts at which to write the stacks: the cap of the job whose
    ``junit`` pattern names ``xmlpath``, less ``MARGIN_SECS``. None when no job's does."""
    if not xmlpath:
        return None
    path = Path(xmlpath).as_posix()
    for job in jobs:
        for pattern in getattr(job, "junit", ()):
            if fnmatch(path, pattern.replace("{shard}", "*")) and job.timeout_min:
                return max(float(job.timeout_min * 60 - MARGIN_SECS), 60.0)
    return None


def _real_stderr(config: Any) -> IO[str]:
    """A writable copy of the stderr pytest started with, taken while its capture is suspended."""
    capman = config.pluginmanager.getplugin("capturemanager")
    if capman is not None:
        capman.suspend_global_capture(in_=False)
    try:
        fd = os.dup(2)
    finally:
        if capman is not None:
            capman.resume_global_capture()
    return os.fdopen(fd, "w", buffering=1)


def configure(config: Any) -> float | None:
    """Arm the dump in this process when it runs a suite leg on GitHub Actions. Returns the
    seconds it is armed for, or None when it is not armed."""
    global _STREAM
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return None
    try:
        # Imported here, inside the guard: a tree with no importable ``tools.ci`` (a child pytest
        # run on a tree of its own) arms nothing rather than failing at configure (#4778).
        from tools.ci import catalogue

        secs = deadline(getattr(config.option, "xmlpath", None), catalogue.read().jobs)
    except Exception:  # noqa: BLE001 - an unreadable catalogue arms nothing; it never fails a run
        return None
    if secs is None:
        return None
    _STREAM = _real_stderr(config)
    faulthandler.dump_traceback_later(secs, repeat=False, file=_STREAM, exit=False)
    return secs
