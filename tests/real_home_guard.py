"""The real-home guard: no test may touch the developer's real ``~/.personalclaw``, and the
test that tries FAILS, by name.

``tests/conftest.py`` redirects an unspecified home to a scratch directory for the whole of
collection and to a per-test directory for each test. This module is what makes a hole in
that isolation impossible to miss: an audit hook (:func:`sys.addaudithook`) installed before
the first import of ``personalclaw``, which sees every ``open``/``os.open``,
``sqlite3.connect``, directory creation and listing, rename, link and delete the interpreter
performs — in ANY thread — and refuses the ones aimed under the real home. Refusing means the
access never happens: the hook raises :class:`RealHomeAccessError` into the code that tried,
before the syscall.

Refusing is half of it. The other half is WHO, and every refusal is charged to a test:

* on the thread running the test protocol → the running test (setup, call or teardown);
* on any other thread → the test that STARTED that thread. Every thread is stamped at start
  with the running test's id, through the ``_thread.start_joinable_thread`` /
  ``_thread.start_new_thread`` audit events (so ``threading`` itself is not patched), and a
  thread inherits its parent's stamp, so a pool worker spawned by a thread the test started is
  still that test's.

A charged refusal fails its test from ``pytest_runtest_makereport`` — including when the code
under test SWALLOWED the exception, which production code is built to do:
``Path.mkdir(exist_ok=True)`` swallows any ``OSError`` on an existing directory, and most
best-effort persistence catches ``Exception``. A refusal no running test owns — a thread that
OUTLIVED the test that started it, or an import during collection — fails the session instead,
naming the thread, the test that started it, the test that happened to be running, and the
stack.

A child process is outside any in-process hook, so the kind that matters is checked at spawn:
a PersonalClaw interpreter (``-m personalclaw…`` or the ``personalclaw`` entry point) started
with neither ``PERSONALCLAW_HOME`` nor ``HOME`` pointing away from the real home is refused the
way an ``open`` would be. Measured before this existed: ``personalclaw gateway --help`` spawned
that way loaded the owner's real ``~/.personalclaw/.env`` into the child.

What this REPLACED — a walk of the real home at session end comparing mtimes against the
session start — and why that could not do this job:

* it could not say which test, or even which process, did it. It fired on ANY change in the
  window: on 2026-09-25 it failed one lane's run for a ``session_search.db`` write made by
  another lane's scratch script, with ~13 pytest runs sharing the machine;
* it saw only what CHANGED, and the leaks this guard found on its first full run were READS:
  18,761 of the owner's ``apps/`` and ``config.json`` by app watchdog threads that outlived
  the tests which booted a gateway, and the owner's ``skills/``, ``hooks/`` and ``mcp.json``
  read by 150+ tests through paths frozen at import;
* it cost 11 s to 107 s per invocation walking a >100k-file home, on every run, including a
  single-file one. This costs one dictionary lookup per audit event.

What this does NOT see, stated rather than implied: native code opening files by itself (the
SQLite C library is covered because the connection is refused before it opens; the one other
native writer, ``faiss.write_index``, sits beside its store's own SQLite database, which is
refused first); child processes that are not a PersonalClaw interpreter; and anything outside
``~/.personalclaw`` (``tests/test_packs_external_formats.py`` guards ``~/.claude`` and
``~/.cursor`` itself).

Detection is a :class:`Guard` over an arbitrary root, so it can be driven against a fake home
and proven to fire (``tests/test_real_home_guard.py``) — a guard only ever exercised by the
tree it guards cannot be told apart from one that never fires.
"""

from __future__ import annotations

import os
import shlex
import sys
import threading
import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest

#: The developer's REAL home, resolved once at import — before any test can repoint
#: ``Path.home`` or ``$HOME``. This module must stay an eager import of ``conftest.py`` for
#: exactly that reason (``tests/pycache_guard.py`` names it as one of the three files that
#: run before the bytecode rail exists).
REAL_HOME = Path.home() / ".personalclaw"

#: Where the session verdict is persisted, relative to the pytest rootdir: the same
#: ``reports/`` directory ``--junitxml`` writes into, so CI ships it with an existing
#: artifact. A session-level failure fails no testcase, so the JUnit XML cannot carry it
#: (#3386); this file does.
REPORT_RELPATH = "reports/real-home-guard.txt"

#: Frames kept per refusal: enough to reach the test or the thread target from inside
#: ``pathlib``/``sqlite3``, few enough that a report of many refusals stays readable.
_STACK_FRAMES = 18

#: Audit event → positional indices of the path arguments it carries.
_PATH_ARGS: dict[str, tuple[int, ...]] = {
    "open": (0,),  # builtin open(), io.open() and os.open(): (path, mode, flags)
    "sqlite3.connect": (0,),  # the stdlib driver; pysqlite3 is wrapped, see _wrap_driver
    "os.mkdir": (0,),
    "os.rmdir": (0,),
    "os.remove": (0,),  # os.remove and os.unlink
    "os.rename": (0, 1),  # os.rename and os.replace
    "os.link": (0, 1),
    "os.symlink": (1,),  # (src, dst): the entry created is dst
    "os.truncate": (0,),
    "os.utime": (0,),
    "os.chmod": (0,),
    "os.chown": (0,),
    "os.chflags": (0,),
    "os.mkfifo": (0,),
    "os.mknod": (0,),
    "os.listdir": (0,),
    "os.scandir": (0,),
    # Its inner unlinks are dir_fd-relative names, so the top path is the one to check.
    "shutil.rmtree": (0,),
}
_PATH, _THREAD, _SPAWN = 0, 1, 2
_DISPATCH: dict[str, int] = {
    **dict.fromkeys(_PATH_ARGS, _PATH),
    "_thread.start_joinable_thread": _THREAD,  # 3.13: threading.Thread.start
    "_thread.start_new_thread": _THREAD,  # 3.12: threading.Thread.start
    "subprocess.Popen": _SPAWN,  # (executable, args, cwd, env); asyncio subprocesses too
    "os.posix_spawn": _SPAWN,  # (path, argv, env)
}


class RealHomeAccessError(Exception):
    """Raised INTO the code that tried; the access to the real home never happened.

    Deliberately not an ``OSError``: production code treats ``OSError`` as an ordinary
    filesystem condition and degrades (``Path.mkdir(exist_ok=True)`` swallows it outright),
    which would turn a leak into a quietly different code path. A refusal that is swallowed
    anyway is still charged to its test — the exception is the fast signal, not the only one.
    """


@dataclass(frozen=True)
class Access:
    """One refused access, with what a reader needs to find the code that made it."""

    event: str
    path: str
    thread: str
    started_by: str | None
    during: str | None
    phase: str
    stack: str

    def render(self) -> str:
        lines = [f"{self.event}: {self.path}"]
        where = f"  on thread {self.thread!r}"
        if self.started_by:
            where += f", started by {self.started_by}"
        lines.append(where)
        if self.during is None:
            lines.append(f"  while no test was running ({self.phase})")
        elif self.started_by and self.started_by != self.during:
            lines.append(f"  during {self.phase} of {self.during}, which did not start this thread")
        else:
            lines.append(f"  during {self.phase} of {self.during}")
        lines.append("  stack, most recent call last:")
        lines.extend("    " + line for line in self.stack.rstrip().splitlines())
        return "\n".join(lines)


def _env_get(env: Mapping, name: str) -> str | None:
    """``env[name]`` for a str- or bytes-keyed mapping (``Popen`` accepts both)."""
    value = env.get(name)
    if value is None:
        try:
            value = env.get(name.encode())
        except TypeError:  # a str-only mapping — os.environ refuses a bytes key outright
            value = None
    if isinstance(value, bytes):
        return os.fsdecode(value)
    return value if isinstance(value, str) else None


def _argv_words(argv: object) -> list[str]:
    if isinstance(argv, (str, bytes)):
        text = os.fsdecode(argv)
        try:
            return shlex.split(text)
        except ValueError:
            return [text]
    try:
        return [os.fsdecode(os.fspath(word)) for word in argv]  # type: ignore[union-attr]
    except TypeError:
        return []


#: ``personalclaw.sandbox._SHIM_MODULE`` — spelled out, not imported, because this module runs
#: before ``personalclaw`` is first imported (``test_real_home_guard.py`` pins the two equal).
EXEC_SHIM_MODULE = "personalclaw._spawn_exec_shim"


def runs_personalclaw(words: list[str]) -> bool:
    """Whether an argv starts a PersonalClaw interpreter: the entry point, or ``-m`` of it.

    The resource-ceiling shim (``python -m personalclaw._spawn_exec_shim <policy> -- <argv>``)
    is looked THROUGH: it imports nothing that resolves a home and ``execvp``s its target, so
    the child that matters is the one after ``--``. Judging the wrapper instead refused every
    ``git`` a loop worktree runs.
    """
    if not words:
        return False
    for index, (flag, module) in enumerate(zip(words, words[1:])):
        if flag == "-m" and module == EXEC_SHIM_MODULE:
            rest = words[index + 2 :]
            return "--" in rest and runs_personalclaw(rest[rest.index("--") + 1 :])
    if os.path.basename(words[0]) == "personalclaw":
        return True
    for flag, module in zip(words, words[1:]):
        if flag == "-m" and (module == "personalclaw" or module.startswith("personalclaw.")):
            return True
    return False


class Guard:
    """Refuses, records and charges every access under ``root``. See the module docstring."""

    def __init__(self, root: Path) -> None:
        normal = os.path.normpath(os.path.abspath(root))
        forms = {normal, os.path.realpath(normal)}
        self.root = Path(normal)
        self._prefixes = tuple((form, form + os.sep) for form in forms)
        # An absolute path that contains none of these cannot name the root: the fast path.
        self._needles = tuple({os.path.basename(form) for form in forms})
        # The $HOME whose default resolution is ``root`` (``root`` is ``$HOME/.personalclaw``).
        self._home = os.path.dirname(normal)
        # Per guard, so a guard a test drives beside the suite's own never reads its stamps.
        self._stamp_attr = f"_pclaw_started_by_test_{id(self):x}"
        self._lock = threading.Lock()
        self._charged: list[tuple[str | None, Access]] = []
        self._busy = threading.local()
        self._installed = False
        self._runner: threading.Thread | None = None
        #: Node id of the test in progress, ``None`` outside one.
        self.test: str | None = None
        #: Where the run is: ``collection``, ``setup``/``call``/``teardown``, ``between tests``.
        self.phase = "collection"

    # ── matching ─────────────────────────────────────────────────────────────────────────

    def owns(self, path: object, *, sqlite: bool = False) -> str | None:
        """The normalized form of ``path`` when it is ``root`` or under it, else ``None``."""
        if isinstance(path, str):
            text = path
        elif isinstance(path, (bytes, os.PathLike)):
            try:
                text = os.fsdecode(os.fspath(path))
            except (TypeError, ValueError):
                return None
        else:
            return None  # a file descriptor, or None (the cwd)
        if sqlite:
            if text.startswith("file:"):
                text = text[5:].split("?", 1)[0]
            if text in ("", ":memory:"):
                return None
        if not text:
            return None
        if os.path.isabs(text):
            if not any(needle in text for needle in self._needles):
                return None
        else:
            try:
                text = os.path.join(os.getcwd(), text)
            except OSError:
                return None
        text = os.path.normpath(text)
        for exact, prefix in self._prefixes:
            if text == exact or text.startswith(prefix):
                return text
        return None

    def isolates(self, env: Mapping) -> bool:
        """Whether a child started with ``env`` resolves a home other than ``root``."""
        home = _env_get(env, "PERSONALCLAW_HOME")
        if home:
            if home.startswith("~"):
                child_home = _env_get(env, "HOME") or self._home
                home = child_home + home[1:]
            return self.owns(home) is None
        child_home = _env_get(env, "HOME")
        if not child_home:
            return False  # the child falls back to the passwd entry: the real home
        return os.path.normpath(os.path.abspath(child_home)) != self._home

    # ── the audit hook ───────────────────────────────────────────────────────────────────

    def audit(self, event: str, args: tuple) -> None:
        kind = _DISPATCH.get(event)
        if kind is None or getattr(self._busy, "on", False):
            return
        if kind == _PATH:
            sqlite = event == "sqlite3.connect"
            for index in _PATH_ARGS[event]:
                if index < len(args):
                    hit = self.owns(args[index], sqlite=sqlite)
                    if hit is not None:
                        self.refuse(event, hit)
        elif kind == _THREAD:
            self._stamp(args)
        else:
            self._check_spawn(event, args)

    def _stamp(self, args: tuple) -> None:
        thread = getattr(args[0], "__self__", None) if args else None
        if not isinstance(thread, threading.Thread):
            return
        parent = threading.current_thread()
        origin = getattr(parent, self._stamp_attr, None)
        if origin is None and parent is self._runner:
            origin = self.test
        if origin is not None:
            setattr(thread, self._stamp_attr, origin)

    def _check_spawn(self, event: str, args: tuple) -> None:
        if event == "subprocess.Popen":
            argv, env = (args[1] if len(args) > 1 else None), (args[3] if len(args) > 3 else None)
        else:
            argv, env = (args[1] if len(args) > 1 else None), (args[2] if len(args) > 2 else None)
        words = _argv_words(argv)
        if not runs_personalclaw(words):
            return
        if self.isolates(os.environ if env is None else env):
            return
        command = " ".join(words)
        self.refuse(
            event,
            f"{self.root} — a PersonalClaw child process given no isolated home "
            f"(neither PERSONALCLAW_HOME nor HOME points elsewhere): {command[:240]}",
        )

    def refuse(self, event: str, path: str) -> None:
        """Record the access, charge it, and raise it into the caller."""
        thread = threading.current_thread()
        started_by = getattr(thread, self._stamp_attr, None)
        during = self.test
        mine = thread is self._runner or started_by == during
        owner = during if during is not None and mine else None
        self._busy.on = True
        try:
            # Two frames up is the code that asked for the access: 0 is this, 1 is the hook.
            try:
                caller = sys._getframe(2)
            except ValueError:
                caller = sys._getframe(1)
            stack = "".join(traceback.format_stack(caller)[-_STACK_FRAMES:])
        finally:
            self._busy.on = False
        access = Access(event, path, thread.name, started_by, during, self.phase, stack)
        with self._lock:
            self._charged.append((owner, access))
        blame = owner or "no running test — reported at session end"
        raise RealHomeAccessError(
            f"refused {event} under the real home {path} (charged to {blame}). A test must "
            "never touch the developer's real ~/.personalclaw — point this code at tmp_path, "
            "or at the home tests/conftest.py isolates."
        )

    # ── lifecycle ────────────────────────────────────────────────────────────────────────

    def begin(self, nodeid: str) -> None:
        self._runner = threading.current_thread()
        self.test = nodeid
        self.phase = "setup"

    def end(self) -> None:
        self.test = None
        self.phase = "between tests"

    def take(self, nodeid: str) -> list[Access]:
        """Every refusal charged to ``nodeid`` not yet reported — removed as it is returned."""
        with self._lock:
            mine = [access for owner, access in self._charged if owner == nodeid]
            if mine:
                self._charged = [(o, a) for o, a in self._charged if o != nodeid]
        return mine

    def take_session(self) -> list[Access]:
        """Everything left: refusals no test owns, and any charged too late to be reported."""
        with self._lock:
            left = [access for _owner, access in self._charged]
            self._charged = []
        return left

    def install(self) -> None:
        """Arm the hook for the life of the process. Idempotent; audit hooks cannot be removed."""
        if self._installed:
            return
        self._installed = True
        sys.addaudithook(self.audit)
        _wrap_driver(self)


def _wrap_driver(guard: Guard) -> None:
    """Refuse ``pysqlite3`` connections too: that driver raises no audit events.

    Verified, not assumed: the ``pysqlite3-binary`` 0.5.4 wheel ``pyproject.toml`` installs on
    Linux x86_64 — i.e. CI's driver, the one ``personalclaw.sqlite_compat`` prefers — contains
    neither ``PySys_Audit`` nor the ``sqlite3.connect`` event name. Without this, every SQLite
    store would be invisible to the hook exactly where the suite runs unattended.
    """
    try:
        import pysqlite3  # type: ignore[import-not-found]
    except ImportError:
        return
    for module in (pysqlite3, getattr(pysqlite3, "dbapi2", None)):
        real: Callable | None = getattr(module, "connect", None)
        if real is None or getattr(real, "_pclaw_guarded", False):
            continue

        def connect(database, *args, _real=real, **kwargs):
            hit = guard.owns(database, sqlite=True)
            if hit is not None:
                guard.refuse("sqlite3.connect", hit)
            return _real(database, *args, **kwargs)

        connect._pclaw_guarded = True  # type: ignore[attr-defined]
        module.connect = connect


#: The one guard the suite runs under. ``tests/conftest.py`` installs it before importing
#: ``personalclaw`` and wires it into pytest through :class:`Plugin`.
GUARD = Guard(REAL_HOME)


def render_for_test(accesses: list[Access]) -> str:
    head = (
        f"real-home guard: this test touched the developer's real {REAL_HOME} "
        f"({len(accesses)} refused access{'es' if len(accesses) != 1 else ''}). "
        "Nothing was written; the test fails so the leak is fixed at its seam — tmp_path, or "
        "the home tests/conftest.py isolates — and never allowlisted."
    )
    return "\n\n".join([head, *(access.render() for access in accesses)])


def render_session(root: Path, rendered: list[str], tests_run: int) -> str:
    if not rendered:
        plural = "s" if tests_run != 1 else ""
        return f"real-home guard: {tests_run} test{plural} ran; none touched {root}."
    one = len(rendered) == 1
    head = [
        f"real-home guard FAILED: {len(rendered)} refused access{'' if one else 'es'} to "
        f"{root} {'belongs' if one else 'belong'} to no running test.",
        "",
        "Either a thread OUTLIVED the test that started it (the entry names that test) and",
        "touched the home after the test's isolation was undone, or an import reached the home",
        "during collection. Stop the thread when its owner stops, or resolve the path at call",
        "time. Nothing was written: every access below was refused.",
        "",
    ]
    return "\n".join(head) + "\n\n".join(rendered)


def write_report(rootdir: Path, report: str) -> Path | None:
    """Persist the session verdict under ``rootdir`` — in every case, so its presence is the
    evidence the guard ran. ``None`` if the filesystem refused: a failure here must never
    stack a second, misleading red on the one being reported."""
    path = Path(rootdir) / REPORT_RELPATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(report + "\n", encoding="utf-8")
    except OSError:
        return None
    return path


class Plugin:
    """The pytest half: phase tracking, per-test charging, the session verdict, xdist relay."""

    def __init__(self, guard: Guard) -> None:
        self.guard = guard
        self._from_workers: list[str] = []
        self._tests_run = 0

    def pytest_collection_finish(self, session) -> None:  # noqa: ARG002 — hook signature
        self.guard.phase = "between tests"

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_runtest_protocol(self, item, nextitem):  # noqa: ARG002 — hook signature
        self.guard.begin(item.nodeid)
        self._tests_run += 1
        try:
            return (yield)
        finally:
            self.guard.end()

    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_setup(self, item) -> None:  # noqa: ARG002 — hook signature
        self.guard.phase = "setup"

    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_call(self, item) -> None:  # noqa: ARG002 — hook signature
        self.guard.phase = "call"

    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_teardown(self, item, nextitem) -> None:  # noqa: ARG002 — hook signature
        self.guard.phase = "teardown"

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_runtest_makereport(self, item, call):  # noqa: ARG002 — hook signature
        # The outermost wrapper, so this sees the report AFTER xfail handling: a refusal inside
        # an xfail-marked test must not be absorbed as the failure it expected.
        report = yield
        found = self.guard.take(item.nodeid)
        if found:
            text = render_for_test(found)
            if report.failed:
                report.sections.append(("real-home guard", text))
            else:
                report.outcome = "failed"
                report.longrepr = text
                if hasattr(report, "wasxfail"):
                    del report.wasxfail
        return report

    @pytest.hookimpl(optionalhook=True)
    def pytest_testnodedown(self, node, error) -> None:  # noqa: ARG002 — xdist hook signature
        output = getattr(node, "workeroutput", {})
        self._from_workers.extend(output.get("real_home_guard", []))
        self._tests_run += output.get("real_home_guard_tests", 0)

    def pytest_sessionfinish(self, session, exitstatus) -> None:  # noqa: ARG002 — hook signature
        rendered = [access.render() for access in self.guard.take_session()]
        workeroutput = getattr(session.config, "workeroutput", None)
        if workeroutput is not None:
            # An xdist worker: the controller owns the one terminal and the one report file.
            workeroutput["real_home_guard"] = rendered
            workeroutput["real_home_guard_tests"] = self._tests_run
            return
        rendered += self._from_workers
        report = render_session(self.guard.root, rendered, self._tests_run)
        written = write_report(session.config.rootpath, report)
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        if reporter is not None:
            reporter.write_sep("=", "real-home guard", red=bool(rendered))
            reporter.write_line(report)
            if written is not None:
                reporter.write_line(f"real-home guard report written to {written}")
        else:  # pragma: no cover - only when the terminal plugin is disabled
            print(report)
        if rendered:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
