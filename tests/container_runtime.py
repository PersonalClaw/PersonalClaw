"""No test drives this machine's container runtime unless the run asks for it.

A few tests build images, start a Compose stack, pull and run containers, or open a shell in a
virtual machine: the Compose parity check (``compose up --build``), the standalone Compose check
(``compose config``), and the end-to-end legs of the Docker and Lima sandbox providers. Each used to
run whenever its program was on ``PATH``, and on a developer's machine that program is the
developer's own runtime: one sweep of the suite rebuilt the ``personalclaw-gateway:local`` and
``personalclaw-web:local`` images in a container VM other work shared, and left a Compose volume
behind.

So a test that drives a container runtime asks :func:`require` (or :func:`available`, for a probe
that runs as its module is imported) first. Unless the run sets :data:`OPT_IN` to ``1``, which CI
does on the runners that carry Docker, the test is skipped, and the skip names the runtime it found
and the variable that lets it run; the run's summary lists every such skip
(:func:`skipped_without_the_opt_in`). And every program this process starts is checked before it
starts (:data:`GUARD`): the machine's own container runtime started without the opt-in is refused,
and ``conftest`` fails the test that started it by name, or the module whose import started it.
That catches a test whose ``require`` was forgotten, and product code under a test that reaches a
runtime by itself. A stand-in a test wrote into a temporary folder, named like a runtime to stand
for one, is the test's own, and starts.

What it cannot see: a runtime a shell starts from a command line, and one another program starts.
``tests/test_container_runtime_census.py`` reads every test module for those shapes.
"""

from __future__ import annotations

import errno
import functools
import inspect
import os
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Iterable, Sequence

import pytest

#: The variable a run sets to ``1`` to let its tests drive this machine's container runtime.
OPT_IN = "PERSONALCLAW_TEST_CONTAINER_RUNTIME"

#: The programs that act on a container runtime or the virtual machine one runs in: the Docker and
#: Podman CLIs and their Compose front ends, Finch and nerdctl (containerd), Apple's ``container``,
#: and Lima and Colima, which manage the VMs the others run in.
PROGRAMS = frozenset(
    {
        "docker",
        "docker-compose",
        "podman",
        "podman-compose",
        "finch",
        "nerdctl",
        "container",
        "limactl",
        "lima",
        "colima",
    }
)

#: How every skip for a missing opt-in begins, so the run's summary can find them.
SKIP_PREFIX = "drives this machine's container runtime"


def opted_in() -> bool:
    """Whether this run lets its tests drive the machine's container runtime."""
    return os.environ.get(OPT_IN, "").strip() == "1"


def _on_path(candidates: Sequence[str]) -> str:
    for name in candidates:
        if shutil.which(name):
            return name
    return ""


def skip_reason(candidates: Sequence[str]) -> str:
    """Why a test that would drive one of *candidates* does not run here; ``""`` when it may."""
    found = _on_path(candidates)
    if not found:
        return f"no {' or '.join(candidates)} on PATH"
    if not opted_in():
        return (
            f"{SKIP_PREFIX} ({found} is on PATH): it would build, start or remove containers or "
            f"VMs there. Set {OPT_IN}=1 to run it"
        )
    return ""


def available(*candidates: str) -> str:
    """The first of *candidates* this test may drive, or ``""``: one on ``PATH`` when the run
    opted in, never otherwise. For a module-level probe, whose skip comes from a mark
    (:func:`skip_reason` is its reason)."""
    return _on_path(candidates) if opted_in() else ""


def require(*candidates: str) -> str:
    """The first of *candidates* on ``PATH``, for a test or fixture that will drive it; the test
    is skipped, and the skip says why, when the run did not opt in or none is there."""
    reason = skip_reason(candidates)
    if reason:
        pytest.skip(reason)
    return _on_path(candidates)


def skipped_without_the_opt_in(reports: Iterable[object]) -> list[str]:
    """The node ids among skipped *reports* that were skipped for want of :data:`OPT_IN`."""
    found: list[str] = []
    for report in reports:
        longrepr = getattr(report, "longrepr", None)
        text = longrepr[2] if isinstance(longrepr, tuple) and len(longrepr) == 3 else ""
        if SKIP_PREFIX in str(text):
            found.append(str(getattr(report, "nodeid", "")))
    return found


def _program(args: object, executable: object, shell: bool) -> str:
    """The program a launch runs, as it was named; ``""`` for a shell's command line."""
    if executable:
        return os.fsdecode(executable)  # type: ignore[arg-type]
    if shell:
        return ""
    if isinstance(args, (str, bytes, os.PathLike)):
        first: object = args
    else:
        try:
            first = next(iter(args))  # type: ignore[call-overload]
        except (TypeError, StopIteration):
            return ""
    try:
        return os.fsdecode(first)  # type: ignore[arg-type]
    except TypeError:
        return ""


class Guard:
    """Refuses, before it starts, a container runtime a process starts without the opt-in.

    *own* are the folders a test's own stand-in may be in (the temporary folders): a program named
    ``docker`` that a test wrote there is the test's, not the machine's runtime, and starts."""

    def __init__(self, *, own: Iterable[str]) -> None:
        self.own = tuple(sorted({os.path.realpath(root) for root in own}))
        self._refused: list[str] = []
        self._lock = threading.Lock()
        self._undo: list = []

    def refusal(
        self, args: object, env: object, executable: object = None, shell: bool = False
    ) -> str:
        """Why starting *args* with *env* would drive this machine's container runtime without the
        opt-in; ``""`` when it would not (another program, a stand-in, or the run opted in)."""
        named = _program(args, executable, shell)
        if os.path.basename(named) not in PROGRAMS or opted_in():
            return ""
        path = env.get("PATH") if isinstance(env, dict) else None
        found = named if os.sep in named else shutil.which(named, path=path)
        if not found:
            return ""  # nothing by that name would start
        real = os.path.realpath(found)
        if any(real == root or real.startswith(root + os.sep) for root in self.own):
            return ""
        return f"{named} is {real}, this machine's own"

    def take(self) -> list[str]:
        """The refusals since the last take (``"<who> -> <command>"``), cleared."""
        with self._lock:
            refused, self._refused = self._refused, []
        return refused

    def install(self) -> None:
        """Check every child this process starts, until :meth:`undo`: a refused one raises
        ``PermissionError`` in place of starting, and is noted for :meth:`take`."""
        real = subprocess.Popen._execute_child  # type: ignore[attr-defined]
        # Read through any guard installed before this one (each carries `__wrapped__`), so a
        # launch's arguments bind by the names `subprocess`'s own method gives them.
        signature = inspect.signature(real)
        guard = self

        @functools.wraps(real)
        def execute_child(popen, *args, **kwargs):
            try:
                call = signature.bind(popen, *args, **kwargs).arguments
            except TypeError:
                return real(popen, *args, **kwargs)
            env = call.get("env")
            why = guard.refusal(
                call.get("args"),
                dict(os.environ) if env is None else dict(env),
                call.get("executable"),
                bool(call.get("shell")),
            )
            if why:
                who = os.environ.get("PYTEST_CURRENT_TEST", "").rsplit(" ", 1)[0] or "(no test)"
                words = call.get("args")
                shown = words if isinstance(words, str) else " ".join(map(str, words or ()))
                with guard._lock:
                    guard._refused.append(f"{who} -> {shown} ({why})")
                raise PermissionError(
                    errno.EPERM,
                    f"refused by the test suite: {why}, and it drives this machine's container "
                    f"runtime, which a test may do only in a run that sets {OPT_IN}=1 "
                    "(tests/container_runtime.py).",
                )
            return real(popen, *args, **kwargs)

        subprocess.Popen._execute_child = execute_child  # type: ignore[attr-defined]
        self._undo.append(real)

    def undo(self) -> None:
        """Put ``subprocess`` back as :meth:`install` found it."""
        while self._undo:
            subprocess.Popen._execute_child = self._undo.pop()  # type: ignore[attr-defined]


def failure(refused: list[str]) -> str:
    """The failure for a test, or a module's import, that started a container runtime."""
    return (
        "this started a container runtime without the opt-in, and it was refused: "
        + "; ".join(refused)
        + f". A test that drives one asks container_runtime.require() first, which skips it "
        f"unless the run sets {OPT_IN}=1 (tests/container_runtime.py)."
    )


class Plugin:
    """The collection half: a module whose import started a runtime fails its collection."""

    @pytest.hookimpl(wrapper=True)
    def pytest_make_collect_report(self, collector):  # noqa: ARG002 — hook signature
        report = yield
        refused = GUARD.take()
        if refused and report.outcome != "failed":
            report.outcome = "failed"
            report.longrepr = failure(refused)
        return report


#: The suite's guard, installed as this module is imported, which ``conftest`` does before
#: anything is collected. A stand-in a test writes is in a temporary folder.
GUARD = Guard(own=[tempfile.gettempdir(), "/tmp"])
GUARD.install()
