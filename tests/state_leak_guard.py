"""A test that leaves process-global state changed is named, as itself (#3949).

``tests/conftest.py`` loads :class:`Plugin` in every process, and its fixture
``_a_test_leaves_process_state_as_it_found_it`` reads the state around each test. Its after-reading
is taken before any of the conftest's snapshot-and-restore fences put a registry back: it requests
every one of them, so they are set up before it and torn down after it, and ``monkeypatch``
requests it, so a test's own ``monkeypatch.chdir`` is undone before it looks.

What it reads, each against the reading taken as the test began:

- **threads**: a non-daemon thread the test started that is still alive after a short grace
  (:data:`GRACE_S`). A worker a ``concurrent.futures`` pool started (:func:`is_pool_worker`),
  asyncio's default executor's included, is recorded as a ``thread pool`` and never fails:
  executors keep their workers by design. It is known by the pool's own register of its workers,
  not by its name, since a pool may name its workers anything.
- **the working directory**, given back after it is read.
- **signal handlers** for the signals the product installs (:data:`SIGNALS`), given back.
- **child processes** the test started (``subprocess.Popen``, asyncio's included) still running.
- **listening sockets** this process opened during the test and still holds, as the port guard
  (``tests/port_guard.py``) tracks them.
- **fenced registries**: each registry a ``_restore_*`` fence covers, read through the accessors
  the fence uses (:func:`fenced_state`). Its fence puts it back, so it is recorded as ``fenced``
  and never fails, until that registry's fence retires.

- **files written** outside the run's temporary folders (:func:`temporary_roots`): each path this
  process opens for writing, makes, moves or removes while the test runs, read through an audit
  hook (:class:`Writes`). A process sees its own writes alone, so under xdist no worker's writes
  are charged to another worker's test; a thread the test's process runs is its process's, and a
  child's writes are the child's. Recorded, never failing, until the suite's own are known.

The environment is ``env_guard``'s, which already fails a leak, so it is not read twice.

**Report mode** is the default: every leak goes to ``reports/state-leaks.json`` with the test's
real xdist worker (``PYTEST_XDIST_WORKER``, ``main`` without xdist) and its position in that
process's order, and nothing fails. **``--strict-state``** fails the leaking test in its teardown
with what changed, unless :data:`ALLOWLIST` names it with an issue (at most :data:`CAP` entries).
A fenced change and a thread pool never fail. The report also carries how long the guard took.
"""

from __future__ import annotations

import concurrent.futures.thread
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pytest

#: The report, under the run's root directory.
REPORT = "reports/state-leaks.json"

#: The known leakers: ``[[entry]]`` tables of exactly ``nodeid`` and ``issue``.
ALLOWLIST = Path(__file__).resolve().parent / "state_leak_allowlist.toml"

#: The most entries the allowlist may hold.
CAP = 5

#: How long a thread the test started may take to end after the test returns.
GRACE_S = 0.25


#: The signals the product installs a handler for.
SIGNALS = tuple(
    getattr(signal, name)
    for name in ("SIGINT", "SIGTERM", "SIGUSR1", "SIGUSR2")
    if hasattr(signal, name)
)

_WORKER_KEY = "state_leaks"


@dataclass
class Leak:
    nodeid: str
    kind: str
    detail: str
    #: Whether ``--strict-state`` fails it.
    strict: bool
    #: Whether a conftest fence puts it back.
    fenced: bool = False
    #: The issue the allowlist names for the test, if any.
    allowlisted: int | None = None
    worker: str = ""
    position: int = 0


@dataclass
class Before:
    threads: frozenset[int]
    cwd: str | None
    handlers: dict[int, object]
    ports: frozenset[int]


def _cwd() -> str | None:
    try:
        return os.getcwd()
    except OSError:
        return None


def is_pool_worker(thread: threading.Thread) -> bool:
    """Whether a ``concurrent.futures`` thread pool started *thread*: every such pool registers
    each worker it starts, whatever it names it."""
    return thread in concurrent.futures.thread._threads_queues


# ── the children a test starts ─────────────────────────────────────────────────────────────


class Children:
    """Every ``subprocess.Popen`` made while a window is open, held until the window closes."""

    def __init__(self) -> None:
        self._started: list[subprocess.Popen] = []
        self._open = False
        self._real: Callable | None = None

    def install(self) -> None:
        if self._real is not None:
            return
        real = subprocess.Popen.__init__
        children = self

        def __init__(self, *args, **kwargs):  # noqa: N807 - Popen's own name
            real(self, *args, **kwargs)
            if children._open:
                children._started.append(self)

        self._real = real
        subprocess.Popen.__init__ = __init__  # type: ignore[method-assign]

    def open(self) -> None:
        self._started = []
        self._open = True

    def close(self) -> list[subprocess.Popen]:
        """The children started in the window that are still running."""
        self._open = False
        live = [p for p in self._started if p.poll() is None]
        self._started = []
        return live


# ── the files a test writes ────────────────────────────────────────────────────────────────

#: The audit events that write a path: an ``open`` for writing, read from its mode or its flags,
#: and those that make, move or remove one. ``shutil.rmtree`` names its tree once; the removals it
#: makes inside it name a folder's descriptor (``dir_fd``) and are not read.
WRITE_EVENTS = frozenset(
    {
        "open",
        "os.mkdir",
        "os.rename",
        "os.remove",
        "os.rmdir",
        "os.symlink",
        "os.link",
        "os.truncate",
        "shutil.copyfile",
        "shutil.copytree",
        "shutil.move",
        "shutil.rmtree",
    }
)

_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC

#: The most paths one window keeps, and the most a leak names.
KEPT_PATHS = 1000
SHOWN_PATHS = 5


def _no_fd(fd: object) -> bool:
    """Whether a path is read without a folder's descriptor: the audit events give ``-1``."""
    return fd is None or fd == -1


def _written(event: str, args: tuple) -> list[object]:
    """The paths an audit event writes, before they are made absolute."""
    if event == "open":
        path, mode, flags = (tuple(args) + (None, None, 0))[:3]
        writes = (isinstance(mode, str) and bool(set(mode) & set("wax+"))) or bool(
            isinstance(flags, int) and flags & _WRITE_FLAGS
        )
        return [path] if writes else []
    if event == "os.rename":  # os.replace's too
        src, dst, src_fd, dst_fd = (tuple(args) + (None, None, None, None))[:4]
        return [p for p, fd in ((src, src_fd), (dst, dst_fd)) if _no_fd(fd)]
    if event == "os.link":
        src, dst, src_fd, dst_fd = (tuple(args) + (None, None, None, None))[:4]
        return [dst] if _no_fd(dst_fd) else []
    if event in ("os.mkdir", "os.remove", "os.rmdir", "shutil.rmtree"):
        path = args[0] if args else None
        dir_fd = args[-1] if len(args) > 1 else None
        return [path] if _no_fd(dir_fd) else []
    if event == "os.symlink":
        src, dst, dir_fd = (tuple(args) + (None, None, None))[:3]
        return [dst] if _no_fd(dir_fd) else []
    if event in ("shutil.copyfile", "shutil.copytree"):
        return [args[1]] if len(args) > 1 else []
    if event == "shutil.move":
        return list(args[:2])
    if event == "os.truncate":
        return [args[0]] if args else []
    return []


def _text(path: object) -> str | None:
    if isinstance(path, bytes):
        return os.fsdecode(path)
    if isinstance(path, str):
        return path
    if isinstance(path, os.PathLike):
        return os.fsdecode(os.fspath(path))
    return None  # a file descriptor


def temporary_roots(config) -> list[str]:
    """Where a test may write: the system's temporary folder (pytest's own basetemp is in it), a
    basetemp the run names, the run's ``reports/`` and pytest's cache folder, the downloads folder
    a run names (``PERSONALCLAW_TEST_DOWNLOADS``), the device folder ``os.devnull`` is in, and
    coverage's data files beside the run's root."""
    root = Path(config.rootpath)
    roots = [tempfile.gettempdir(), str(root / "reports"), str(root / ".pytest_cache")]
    basetemp = config.getoption("basetemp", None)
    if basetemp:
        roots.append(str(basetemp))
    downloads = os.environ.get("PERSONALCLAW_TEST_DOWNLOADS")
    if downloads:
        roots.append(downloads)
    roots.append(os.path.dirname(os.devnull))
    return roots


class Writes:
    """Each path this process writes while a window is open, outside the roots it is given,
    read through an audit hook (``sys.addaudithook``). A hook cannot be taken away, so it is
    added once and records only while a window is open; it never raises.

    The hook's guard against its own calls (``_busy``) is the instance's, not a thread's: a write
    another thread makes while one call of the hook runs goes unrecorded. A gap in the evidence,
    never a failed write."""

    def __init__(self, roots: Sequence[str], files: Sequence[str] = ()) -> None:
        self._roots = tuple(os.path.join(os.path.realpath(r), "") for r in roots)
        self._files = tuple(os.path.realpath(f) for f in files)
        self._open = False
        self._busy = False
        self._seen: list[str] = []
        self._installed = False

    def install(self) -> None:
        if not self._installed:
            sys.addaudithook(self._hook)
            self._installed = True

    def _hook(self, event: str, args: tuple) -> None:
        if not self._open or event not in WRITE_EVENTS or self._busy:
            return
        self._busy = True
        try:
            for path in _written(event, args):
                text = _text(path)
                if text is not None and len(self._seen) < KEPT_PATHS:
                    self._seen.append(os.path.abspath(text))
        except Exception:  # noqa: BLE001 - a hook that raises would fail the write it watched
            pass
        finally:
            self._busy = False

    def open(self) -> None:
        self._seen = []
        self._open = True

    def close(self) -> list[str]:
        """The paths written in the window outside the roots, each once, in the order written."""
        self._open = False
        seen, self._seen = self._seen, []
        out: list[str] = []
        for path in dict.fromkeys(seen):
            real = os.path.realpath(path)
            if real.startswith(self._roots) or real.startswith(self._files):
                continue
            out.append(path)
        return out


# ── the fenced registries ──────────────────────────────────────────────────────────────────


def _keys(mapping: object) -> frozenset[str]:
    return frozenset(repr(k) for k in mapping) if isinstance(mapping, Mapping) else frozenset()


def fenced_state() -> dict[str, frozenset[str]]:
    """A digest of each registry a ``_restore_*`` fence covers, read as the fence reads it. A
    registry that cannot be read is named by why, so a reading never fails the test."""
    readers: dict[str, Callable[[], frozenset[str]]] = {}

    def notification_kinds() -> frozenset[str]:
        from personalclaw import notification_kinds as nk

        return _keys(nk._REGISTRY)

    def provider_registry() -> frozenset[str]:
        from personalclaw.llm import registry as reg

        current = reg.get_default_registry()
        return _keys(getattr(current, "_entries", {})) | {f"<registry {id(current)}>"}

    def workflow_defs() -> frozenset[str]:
        from personalclaw.workflows import defs

        return frozenset(defs.list_providers())

    def tool_providers() -> frozenset[str]:
        from personalclaw.tool_providers import registry as reg

        names = ("_providers", "_provider_app", "_registrations", "_claims")
        return frozenset(f"{n}:{k}" for n in names for k in _keys(getattr(reg, n, {})))

    def knowledge_providers() -> frozenset[str]:
        from personalclaw.knowledge_providers import registry as reg

        return _keys(reg._providers)

    def pipeline_nodes() -> frozenset[str]:
        from personalclaw.knowledge import pipeline
        from personalclaw.knowledge.pipeline import registry as reg

        return _keys(reg.NODE_REGISTRY) | {f"<registered {pipeline._REGISTERED}>"}

    def local_models() -> frozenset[str]:
        from personalclaw.local_models import registry as reg

        return (
            frozenset(f"providers:{k}" for k in _keys(reg._providers))
            | frozenset(f"capabilities:{k}" for k in _keys(reg._capabilities))
            | frozenset(f"owners:{k}" for k in _keys(reg._owners))
        )

    def logging_sinks() -> frozenset[str]:
        import logging

        root, top = logging.getLogger("personalclaw"), logging.getLogger()
        return frozenset(
            [f"<level {root.level}/{top.level}>"]
            + [f"personalclaw:{id(h)}" for h in root.handlers]
            + [f"root:{id(h)}" for h in top.handlers]
        )

    def event_router() -> frozenset[str]:
        from personalclaw import event_triggers

        router = event_triggers._router
        return frozenset() if router is None else frozenset({f"<router {id(router)}>"})

    readers = {
        "notification_kinds": notification_kinds,
        "provider_registry": provider_registry,
        "workflow_def_registry": workflow_defs,
        "tool_provider_registry": tool_providers,
        "knowledge_provider_registry": knowledge_providers,
        "pipeline_node_registry": pipeline_nodes,
        "local_model_registry": local_models,
        "personalclaw_logging": logging_sinks,
        "event_router": event_router,
    }
    out: dict[str, frozenset[str]] = {}
    for name, read in readers.items():
        try:
            out[name] = read()
        except Exception as exc:  # noqa: BLE001 - an unreadable registry is named, not raised
            out[name] = frozenset({f"<unreadable: {type(exc).__name__}>"})
    return out


# ── the guard ──────────────────────────────────────────────────────────────────────────────


def _no_ports() -> frozenset[int]:
    return frozenset()


def _no_children() -> list:
    return []


@dataclass
class Guard:
    listening: Callable[[], frozenset[int]] = _no_ports
    fenced: Callable[[], Mapping[str, frozenset[str]]] = lambda: {}
    children: Children | None = None
    writes: Writes | None = None
    leaks: list[Leak] = field(default_factory=list)
    seconds: float = 0.0
    tests: int = 0
    _nodeid: str = ""
    _position: int = 0
    _warmed: bool = False

    def start(self, nodeid: str, position: int) -> None:
        self._nodeid, self._position = nodeid, position
        self.tests += 1

    def _leak(self, kind: str, detail: str, strict: bool, **kw) -> Leak:
        leak = Leak(
            self._nodeid,
            kind,
            detail,
            strict,
            worker=os.environ.get("PYTEST_XDIST_WORKER", "main"),
            position=self._position,
            **kw,
        )
        self.leaks.append(leak)
        return leak

    def before(self) -> Before:
        began = time.perf_counter()
        reading = Before(
            threads=frozenset(t.ident or 0 for t in threading.enumerate()),
            cwd=_cwd(),
            handlers={s: signal.getsignal(s) for s in SIGNALS},
            ports=frozenset(self.listening()),
        )
        self.seconds += time.perf_counter() - began
        return reading

    def settled(self, before: Before) -> Before:
        """The reading once the test's fixtures are set up: the threads, ports and children they
        started (a module's or a session's fixture among them) are theirs, not the test's. The
        working directory and the handlers stay as they were before setup, so a fixture that
        changes one and puts it back is no leak."""
        began = time.perf_counter()
        reading = Before(
            threads=before.threads | frozenset(t.ident or 0 for t in threading.enumerate()),
            cwd=before.cwd,
            handlers=before.handlers,
            ports=before.ports | frozenset(self.listening()),
        )
        if self.children is not None:
            self.children.open()
        if self.writes is not None:
            self.writes.open()
        self.seconds += time.perf_counter() - began
        return reading

    def fenced_before(self) -> Mapping[str, frozenset[str]]:
        began = time.perf_counter()
        if not self._warmed:
            # The first reading imports the registries' modules, and an import may register into
            # another registry read earlier: read once to import, then again to compare against.
            self.fenced()
            self._warmed = True
        reading = dict(self.fenced())
        self.seconds += time.perf_counter() - began
        return reading

    def fenced_after(self, before: Mapping[str, frozenset[str]]) -> None:
        began = time.perf_counter()
        now = self.fenced()
        for name in sorted(set(before) | set(now)):
            was, is_ = before.get(name, frozenset()), now.get(name, frozenset())
            if was == is_:
                continue
            added, gone = sorted(is_ - was), sorted(was - is_)
            parts = ([f"added {', '.join(added)}"] if added else []) + (
                [f"removed {', '.join(gone)}"] if gone else []
            )
            self._leak("fenced registry", f"{name}: {'; '.join(parts)}", False, fenced=True)
        self.seconds += time.perf_counter() - began

    def after(self, before: Before, *, strict: bool, allowlist: Mapping[str, int]) -> str:
        """Record what the test left changed, give back the directory and the handlers, and return
        the failure strict mode gives it ("" for none)."""
        began = time.perf_counter()
        start = len(self.leaks)
        new = [t for t in threading.enumerate() if (t.ident or 0) not in before.threads]
        for thread in [t for t in new if is_pool_worker(t) and not t.daemon]:
            self._leak("thread pool", f"{thread.name} (a pool keeps its workers)", False)
        deadline = time.monotonic() + GRACE_S
        for thread in [t for t in new if not is_pool_worker(t) and not t.daemon]:
            thread.join(max(0.0, deadline - time.monotonic()))
            if thread.is_alive():
                self._leak("thread", f"{thread.name} is still running", True)
        cwd = _cwd()
        if cwd != before.cwd:
            self._leak(
                "working directory", f"{before.cwd} became {cwd or '(a folder now gone)'}", True
            )
            if before.cwd is not None:
                os.chdir(before.cwd)
        for sig, handler in before.handlers.items():
            current = signal.getsignal(sig)
            if current is not handler:
                name = signal.Signals(sig).name
                self._leak("signal handler", f"{name} is {current!r}, was {handler!r}", True)
                if threading.current_thread() is threading.main_thread():
                    signal.signal(sig, handler)  # type: ignore[arg-type]
        if self.children is not None:
            for child in self.children.close():
                self._leak(
                    "child process", f"pid {child.pid} ({child.args!r}) is still running", True
                )
        if self.writes is not None:
            written = self.writes.close()
            if written:
                shown = ", ".join(written[:SHOWN_PATHS])
                more = (
                    f" and {len(written) - SHOWN_PATHS} more" if len(written) > SHOWN_PATHS else ""
                )
                self._leak(
                    "file written",
                    f"wrote {len(written)} path(s) outside the temporary folders: {shown}{more}",
                    False,
                )
        opened = sorted(set(self.listening()) - before.ports)
        if opened:
            self._leak(
                "listening socket", f"port {', '.join(map(str, opened))} still listening", True
            )
        issue = allowlist.get(self._nodeid)
        failing = []
        for leak in self.leaks[start:]:
            leak.allowlisted = issue
            if leak.strict and issue is None:
                failing.append(leak)
        self.seconds += time.perf_counter() - began
        if not strict or not failing:
            return ""
        lines = "\n".join(f"  - {leak.kind}: {leak.detail}" for leak in failing)
        return (
            f"{self._nodeid} left process-global state changed (tests/state_leak_guard.py; "
            f"--strict-state):\n{lines}\nGive it back in the test, or list the test in "
            f"{ALLOWLIST.name} with the issue that tracks its fix."
        )


# ── the allowlist ──────────────────────────────────────────────────────────────────────────


def load_allowlist(path: Path) -> dict[str, int]:
    """The allowlist's ``{nodeid: issue}``; :class:`ValueError` naming each rule it breaks."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"there is no allowlist at {path}") from None
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"{path.name} is not TOML: {exc}") from None
    unknown = sorted(set(data) - {"entry"})
    if unknown:
        raise ValueError(f"{path.name}: unknown table {unknown}")
    entries = data.get("entry", [])
    if not isinstance(entries, list):
        raise ValueError(f"{path.name}: entry is a list of [[entry]] tables")
    if len(entries) > CAP:
        raise ValueError(f"{path.name} holds {len(entries)} entries, at most {CAP}")
    out: dict[str, int] = {}
    for n, entry in enumerate(entries, 1):
        where = f"{path.name} entry {n}"
        if not isinstance(entry, dict):
            raise ValueError(f"{where} is not a table")
        if "issue" not in entry:
            raise ValueError(f"{where} names no issue")
        if set(entry) != {"nodeid", "issue"}:
            raise ValueError(f"{where} must hold exactly nodeid and issue, got {sorted(entry)}")
        nodeid, issue = entry["nodeid"], entry["issue"]
        if not isinstance(nodeid, str) or not nodeid:
            raise ValueError(f"{where}: nodeid must name a test")
        if isinstance(issue, bool) or not isinstance(issue, int) or issue < 1:
            raise ValueError(f"{where}: issue must be an issue number, got {issue!r}")
        if nodeid in out:
            raise ValueError(f"{where}: {nodeid} is listed twice")
        out[nodeid] = issue
    return out


# ── the plugin ─────────────────────────────────────────────────────────────────────────────


def addoption(parser) -> None:
    parser.addoption(
        "--strict-state",
        action="store_true",
        default=False,
        help="fail a test that leaves process-global state changed (tests/state_leak_guard.py)",
    )
    parser.addoption(
        "--state-leak-allowlist",
        default=str(ALLOWLIST),
        help="the known leakers, each with its issue (default: tests/state_leak_allowlist.toml)",
    )


class Plugin:
    """Holds this process's :class:`Guard`, and writes or relays the report."""

    def __init__(self, config, *, listening: Callable[[], frozenset[int]]) -> None:
        import pytest

        self.config = config
        self.strict = bool(config.getoption("--strict-state"))
        try:
            self.allowlist = load_allowlist(Path(config.getoption("--state-leak-allowlist")))
        except ValueError as exc:
            raise pytest.UsageError(str(exc)) from None
        children = Children()
        children.install()
        writes = Writes(temporary_roots(config), [str(Path(config.rootpath) / ".coverage")])
        writes.install()
        self.guard = Guard(
            listening=listening, fenced=fenced_state, children=children, writes=writes
        )
        self.position = 0
        self._before: Before | None = None
        self._relayed: list[dict] = []
        self._tests = 0
        self._seconds = 0.0

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_setup(self, item):
        """Read the state as the test's setup begins, and again once its fixtures are set up."""
        self.position += 1
        self.guard.start(item.nodeid, self.position)
        self._before = self.guard.before()
        try:
            return (yield)
        finally:
            if self._before is not None:
                self._before = self.guard.settled(self._before)

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_teardown(self, item, nextitem):  # noqa: ARG002 - hook signature
        """Read it again once every fixture is torn down, give back the directory and the
        handlers, and under ``--strict-state`` fail the test that left the rest."""
        failure = ""
        try:
            return (yield)
        finally:
            before, self._before = self._before, None
            if before is not None:
                failure = self.guard.after(before, strict=self.strict, allowlist=self.allowlist)
            if failure:
                pytest.fail(failure, pytrace=False)

    @pytest.hookimpl(optionalhook=True)
    def pytest_testnodedown(self, node, error) -> None:  # noqa: ARG002 - xdist hook signature
        relayed = getattr(node, "workeroutput", {}).get(_WORKER_KEY)
        if relayed:
            self._relayed += relayed["leaks"]
            self._tests += relayed["tests"]
            self._seconds += relayed["seconds"]

    def _summary(self) -> dict:
        return {
            "version": 1,
            "strict": self.strict,
            "tests": self.guard.tests + self._tests,
            "guard_seconds": round(self.guard.seconds + self._seconds, 4),
            "leaks": [asdict(leak) for leak in self.guard.leaks] + self._relayed,
        }

    def pytest_sessionfinish(self, session) -> None:
        workeroutput = getattr(session.config, "workeroutput", None)
        if workeroutput is not None:
            workeroutput[_WORKER_KEY] = {
                "leaks": [asdict(leak) for leak in self.guard.leaks],
                "tests": self.guard.tests,
                "seconds": self.guard.seconds,
            }
            return
        out = Path(session.config.rootpath) / REPORT
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(self._summary(), indent=1, sort_keys=True) + "\n")

    def pytest_terminal_summary(self, terminalreporter) -> None:
        if getattr(self.config, "workeroutput", None) is not None:
            return
        summary = self._summary()
        tests = summary["tests"]
        per = 1000 * summary["guard_seconds"] / tests if tests else 0.0
        terminalreporter.write_line(summary_line(summary["leaks"], tests, per))


def summary_line(leaks: Sequence[Mapping], tests: int, per_ms: float) -> str:
    """The terminal's line: the leaks recorded, and how many of them ``--strict-state`` fails
    (strict and not allowlisted) and of how many kinds, so a run's log says it with no
    download."""
    failing = [leak for leak in leaks if leak["strict"] and leak["allowlisted"] is None]
    kinds = len({leak["kind"] for leak in failing})
    return (
        f"state leaks: {len(leaks)} recorded over {tests} tests, "
        f"{len(failing)} strict ({kinds} kind{'' if kinds == 1 else 's'}), "
        f"{per_ms:.2f} ms a test ({REPORT})"
    )
