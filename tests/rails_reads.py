"""What each rail read, written beside a rails leg's JUnit report, for ``rails select`` (#3920).

A lane runs the rails its change can break: ``python -m tools.ci rails select --paths ...``
(``tools/ci/rails_select.py``) names every rail whose recorded reads touch a changed path. This
plugin records those reads in a rails leg (a JUnit report named ``junit-rails-<leg>.xml``, as
each rails leg writes) of a push to main or of a pull request's run: ``GITHUB_EVENT_NAME``
``push`` with ``GITHUB_REF`` ``refs/heads/main``, or ``pull_request`` with
``refs/pull/<number>/merge`` (or :data:`FORCE` set, for a record made by hand). It writes
``rails-reads-<leg>.json`` beside the report. Since the flip a tested push runs no rails, so the
record of main's newest commit is most often its pull request's run. The selector reads a pull
request's record only once that pull request was merged as a main commit whose tree the run
tested, and every other run's leg records nothing.

**A read.** Each event below counts in a process that runs tests. It is charged to the rail module
then running (its collection, or one of its tests' setup, call or teardown). When no rail module
is running, it is charged to the run's own setup (``harness``): what conftest and its plugins
read, which every rail runs under.

- a walk or a file asked of the source index (``tests/source_index.py``'s observers), cached or
  not;
- a repository file opened for reading, or a directory listed (the ``open``, ``os.listdir`` and
  ``os.scandir`` audit events; a glob or an ``rglob`` lists through ``os.scandir``);
- a repository path stat'd (``os.stat``, ``os.lstat``, and so ``exists``, ``is_file`` and their
  like), whether or not a file is there: a rail that checks a path exists reads it;
- a module a repository file imports and does not find: each file that would have answered it,
  as a stat of a path not there;
- a child process started (``subprocess.Popen``, ``os.system``, ``os.posix_spawn``, ``os.exec*``,
  ``os.fork``). Nothing a child reads is seen, so such a rail reads the whole tree.

**A cache's answer.** A helper that caches reads only for the first rail that asks, and a module
is imported once, for the first rail that imports it. So the plugin also does two things:

- It charges each event to the repository file whose code made it (the innermost repository
  frame, the source index's own excepted).
- A fixture the harness defines (a conftest fence, a guard's) runs as the harness, its setup and
  its teardown, so what it reads inside a rail's test is the harness's read, not that rail's.
- It records each import statement run, cached or not, as an edge from the importing file to the
  imported one.

A rail then reads three things: what it read itself, the files of every module it imports
(transitively), and every read those modules' code made for any rail. That can be more than the
rail needs, and is never less.

**The harness.** The harness's own files select every rail: conftest, the plugin and guard modules
it loads from ``tests/``, the files they open, ``pyproject.toml`` and ``uv.lock``. Its stats are
not reads: they route its own work (pytest's collection asks ``shard_list`` about every file
under ``tests/``), and a rail that needs a file the harness found opens it. A package module
the harness imports (conftest's registry fences import some two hundred) does not. It selects only
the rails that read it themselves. An import-time break in such a module fails the change's own
tests, which load the same harness, so nothing slips. The package is the source index's
``package`` root, and neither its files nor what its code reads for the harness count as the
harness's own.

**What is kept.** A rail module with a test that failed or errored read only part of what it
reads. It is written as ``unrecorded``, and the selector always selects it.

Standard library and pytest only. A failure inside the plugin never fails a test: it is written
into the record as ``error``, and the selector then trusts no rail of that leg.
"""

from __future__ import annotations

import builtins
import importlib
import inspect
import json
import os
import re
import subprocess
import sys
import threading
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

#: The record's schema, which ``tools/ci/rails_select.py`` reads.
SCHEMA = 1

#: Where rails live (``tools/ci/rails_census.py``'s ``RAILS``).
RAILS = "tests/rails"

#: What one owner read, by kind: files (opened, stat'd, asked of the index), folders (listed),
#: walks (the source index's, by name) and spawns (programs started).
KINDS = ("files", "folders", "walks", "spawns")

#: Top-level folders that hold nothing tracked, whose reads no change can touch.
_UNTRACKED = frozenset(
    {".git", ".venv", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".local", "reports"}
)

#: Programs that read the machine, not a tree (``platform`` starts ``uname`` and ``file``). One
#: started with no argument that names a repository path reads nothing a change can touch.
MACHINE_PROGRAMS = frozenset({"uname", "file", "sw_vers", "sysctl", "getconf", "nproc", "lscpu"})

_SPAWNS = frozenset(
    {"subprocess.Popen", "os.system", "os.posix_spawn", "os.spawn", "os.exec", "os.fork"}
)
_EVENTS = frozenset({"open", "os.listdir", "os.scandir", "exec", "os.chdir"}) | _SPAWNS
_EVENTS |= {"os.forkpty"}

#: Set to ``1``, records a rails leg wherever it runs: a record made by hand (``rails select
#: --map``), and the plants that check the recorder.
FORCE = "PERSONALCLAW_RAILS_READS"

#: The worker's record, in its output to the xdist controller.
_WORKER_KEY = "rails_reads"

#: How many absolute paths' answers :meth:`Recorder.rel` keeps.
_KEPT_PATHS = 500_000

_OTHER = object()
_INDEX = object()
_SKIP = object()

Facts = dict[str, set[str]]


def _facts() -> Facts:
    return {kind: set() for kind in KINDS}


def armed(environ: Mapping[str, str] = os.environ) -> bool:
    """Whether this run records: a push to main's or a pull request's, the two whose records the
    selector reads, or one :data:`FORCE` arms."""
    if environ.get(FORCE) == "1":
        return True
    event, ref = environ.get("GITHUB_EVENT_NAME"), environ.get("GITHUB_REF", "")
    if event == "push":
        return ref == "refs/heads/main"
    return event == "pull_request" and re.fullmatch(r"refs/pull/[0-9]+/merge", ref) is not None


def report_path(config: pytest.Config) -> tuple[Path, str] | None:
    """``rails-reads-<leg>.json`` beside ``--junitxml``'s ``junit-rails-<leg>.xml``, with the leg,
    or ``None`` when the run writes no rails leg's report."""
    xml = getattr(config.option, "xmlpath", None)
    if not xml:
        return None
    junit = Path(xml)
    if not junit.name.startswith("junit-rails-") or junit.suffix != ".xml":
        return None
    leg = junit.stem.removeprefix("junit-rails-")
    return junit.parent / f"rails-reads-{leg}.json", leg


class Recorder:
    """One process's reads, by owner and by the code that made them, and its import edges."""

    def __init__(self, root: Path, index_file: str, package: str | None = None) -> None:
        self.root = root
        #: The package's folder, repository-relative: its modules are never the harness's own.
        self.package = package
        #: The harness's own modules: what the run loaded before recording began, the package's
        #: excepted (:meth:`install`).
        self.harness: set[str] = set()
        self._prefixes = tuple(dict.fromkeys(f"{p}{os.sep}" for p in (root, root.resolve())))
        self._roots = {str(root), str(root.resolve())}
        self._index_file = os.path.normpath(index_file)
        #: The rail module running, repository-relative; "" for the run's own setup.
        self.owner = ""
        self.owners: dict[str, Facts] = {}
        self.readers: dict[str, Facts] = {}
        self.edges: set[tuple[str, str]] = set()
        self.active = False
        self.error = ""
        self._busy = threading.local()
        self._kinds: dict[str, object] = {}
        self._rels: dict[str, str | None] = {}
        #: The working directory, asked again after each ``os.chdir`` (``getcwd`` is a syscall).
        self._cwd: str | None = None
        self._undo: list[Callable[[], None]] = []

    # ── paths and frames ────────────────────────────────────────────────────────────────────

    def rel(self, path: object) -> str | None:
        """*path* relative to the repository, with forward slashes; ``""`` for the root itself;
        a compiled module as its source. ``None`` outside the repository or in an untracked
        folder. An absolute path's answer is kept: a rail stats one path many times."""
        if isinstance(path, str) and path in self._rels:
            return self._rels[path]
        found = self._rel(path)
        if isinstance(path, str) and os.path.isabs(path) and len(self._rels) < _KEPT_PATHS:
            self._rels[path] = found
        return found

    def _rel(self, path: object) -> str | None:
        if isinstance(path, str):
            text = path
        elif isinstance(path, (bytes, os.PathLike)):
            try:
                text = os.fsdecode(os.fspath(path))
            except (TypeError, ValueError):
                return None
        else:
            return None
        if not text:
            return None
        if not os.path.isabs(text):
            if self._cwd is None:
                try:
                    self._cwd = os.getcwd()
                except OSError:
                    return None
            text = os.path.join(self._cwd, text)
        text = os.path.normpath(text)
        for prefix in self._prefixes:
            if text.startswith(prefix):
                rel = text[len(prefix) :]
                break
        else:
            return "" if text in self._roots else None
        if os.sep != "/":
            rel = rel.replace(os.sep, "/")
        parts = rel.split("/")
        if parts[0] in _UNTRACKED or "node_modules" in parts:
            return None
        if "__pycache__" in parts:
            # ``a/__pycache__/b.cpython-312.pyc`` (or pytest's rewritten ``...-pytest-8.pyc``) is
            # ``a/b.py`` compiled; anything else in a cache folder is no source.
            if len(parts) < 2 or parts[-2] != "__pycache__" or not parts[-1].endswith(".pyc"):
                return None
            return "/".join([*parts[:-2], parts[-1].split(".", 1)[0] + ".py"])
        return rel

    def _kind(self, filename: str) -> object:
        """What a frame's file is: a repository module (its path), the index, or other."""
        kind = self._kinds.get(filename)
        if kind is None:
            if filename.startswith("<"):
                kind = _OTHER
            elif os.path.normpath(filename) == self._index_file:
                kind = _INDEX
            else:
                rel = self.rel(filename)
                kind = rel if rel and rel.endswith(".py") and rel != _THIS else _OTHER
            self._kinds[filename] = kind
        return kind

    def _reader(self, *, audit: bool) -> object:
        """The innermost repository module on the stack, ``None`` when there is none, or
        :data:`_SKIP` for an audit event the source index made (its observers tell those)."""
        frame = sys._getframe(1)
        while frame is not None:
            kind = self._kind(frame.f_code.co_filename)
            if kind is _INDEX:
                if audit:
                    return _SKIP
            elif kind is not _OTHER:
                return kind
            frame = frame.f_back
        return None

    # ── events ──────────────────────────────────────────────────────────────────────────────

    def _note(self, kind: str, value: str, *, audit: bool = True, probe: bool = False) -> None:
        reader = self._reader(audit=audit)
        if reader is _SKIP:
            return
        owner = self.owner
        if not owner and (probe or (kind == "folders" and reader is None)):
            # The harness's stats route its own work, not a rail's: pytest's collection walk asks
            # shard_list's ignore_collect about every file beside the rails (`is_file`), and a
            # guard probes the machine. What the harness reads is the files it opens. A listing
            # outside any repository code is pytest's own walk.
            return
        if owner or not (isinstance(reader, str) and _under(reader, self.package)):
            # What package code reads for the harness is that module's read, not the harness's.
            self.owners.setdefault(owner, _facts())[kind].add(value)
        if isinstance(reader, str) and reader != owner:
            self.readers.setdefault(reader, _facts())[kind].add(value)

    def _path(self, kind: str, path: object, *, probe: bool = False) -> None:
        rel = self.rel(path)
        if rel is not None and (rel or kind == "folders"):
            self._note(kind, rel, probe=probe)

    def _guarded(self, work: Callable[[], None]) -> None:
        if not self.active or getattr(self._busy, "on", False):
            return
        self._busy.on = True
        try:
            work()
        except Exception as exc:  # a broken record is said, never a broken test
            self.error = self.error or f"{type(exc).__name__}: {exc}"
        finally:
            self._busy.on = False

    def stat(self, path: object) -> None:
        """A stat of *path*, the file there or not."""
        if getattr(self._busy, "on", False):
            return
        self._busy.on = True
        try:
            self._path("files", path, probe=True)
        except Exception as exc:  # a broken record is said, never a broken test
            self.error = self.error or f"{type(exc).__name__}: {exc}"
        finally:
            self._busy.on = False

    def audit(self, event: str, args: tuple[Any, ...]) -> None:
        if event in _EVENTS:
            self._guarded(lambda: self._audited(event, args))

    def _audited(self, event: str, args: tuple[Any, ...]) -> None:
        if event == "os.chdir":
            self._cwd = None
        elif event == "open":
            path, _mode, flags = args
            if isinstance(flags, int) and flags & os.O_ACCMODE == os.O_WRONLY:
                return
            self._path("files", path)
        elif event in ("os.listdir", "os.scandir"):
            if not _importing():
                self._path("folders", "." if args[0] is None else args[0])
        elif event == "exec":
            code = args[0]
            if getattr(code, "co_name", None) == "<module>":
                self._edge(self.rel(code.co_filename))
        else:
            program, argv = _program(event, args)
            if program in MACHINE_PROGRAMS and not any(
                ("/" in arg or arg in (".", "..")) and self.rel(arg) is not None for arg in argv
            ):
                return
            self._note("spawns", program)

    def observe(self, kind: str, value: object) -> None:
        """A source index observer: a walk by name, or a file by its path."""

        def work() -> None:
            if kind == "walk":
                self._note("walks", str(value), audit=False)
            else:
                rel = self.rel(value)
                if rel:
                    self._note("files", rel, audit=False)

        self._guarded(work)

    def _edge(self, target: str | None) -> None:
        if target and target.endswith(".py"):
            importer = self._reader(audit=False)
            if isinstance(importer, str) and importer != target:
                self.edges.add((importer, target))

    def _imported(self, names: Iterable[str]) -> None:
        for name in names:
            module = sys.modules.get(name)
            self._edge(self.rel(getattr(module, "__file__", None)))

    def _missing(self, name: str | None, frame: Any) -> None:
        """A module a repository file asked for and did not find was looked for by name in its
        package's folders (or, for a top-level one, in each folder on the path). The files that
        would have been found are read, as a stat of a path not there is."""
        if not name or not isinstance(self._kind(frame.f_code.co_filename), str):
            return
        package, _, leaf = name.rpartition(".")
        parent = sys.modules.get(package) if package else None
        if package and parent is None:
            return  # its package was not found either, and that is the miss recorded
        folders = list(getattr(parent, "__path__", ())) if parent is not None else list(sys.path)
        for folder in folders:
            for candidate in (f"{leaf}.py", f"{leaf}/__init__.py"):
                self._path("files", os.path.join(folder or ".", candidate), probe=True)

    # ── installing ──────────────────────────────────────────────────────────────────────────

    def install(self, index: Any) -> None:
        """Start recording in this process: the audit hook (added once, inert once stopped),
        the stat and import wrappers, and the index's observer."""
        sys.addaudithook(self.audit)
        recorder = self

        for name in ("stat", "lstat"):
            original = getattr(os, name)

            def stat(path, *args, _original=original, **kwargs):
                if recorder.active and kwargs.get("dir_fd") is None:
                    recorder.stat(path)
                return _original(path, *args, **kwargs)

            setattr(os, name, stat)
            self._undo.append(lambda name=name, original=original: setattr(os, name, original))

        original_import = builtins.__import__

        def importing(name, globals=None, locals=None, fromlist=(), level=0):
            try:
                module = original_import(name, globals, locals, fromlist, level)
            except ModuleNotFoundError as missing:
                name_missing, frame = missing.name, sys._getframe(1)
                recorder._guarded(lambda: recorder._missing(name_missing, frame))
                raise
            recorder._guarded(lambda: recorder._imported(_names(name, module, fromlist, level)))
            return module

        builtins.__import__ = importing
        self._undo.append(lambda: setattr(builtins, "__import__", original_import))

        original_import_module = importlib.import_module

        def import_module(name, package=None):
            try:
                module = original_import_module(name, package)
            except ModuleNotFoundError as missing:
                name_missing, frame = missing.name, sys._getframe(1)
                recorder._guarded(lambda: recorder._missing(name_missing, frame))
                raise
            recorder._guarded(lambda: recorder._imported(_prefixes(module.__name__)))
            return module

        importlib.import_module = import_module
        self._undo.append(lambda: setattr(importlib, "import_module", original_import_module))

        index.observers.append(self.observe)
        self._undo.append(lambda: index.observers.remove(self.observe))
        # What the run imported before this (conftest and what it imports) is its own setup.
        loaded = self.owners.setdefault("", _facts())["files"]
        for module in list(sys.modules.values()):
            rel = self.rel(getattr(module, "__file__", None))
            if rel and rel.endswith(".py") and rel != _THIS:
                loaded.add(rel)
                if not _under(rel, self.package):
                    self.harness.add(rel)
        self.active = True

    def as_harness(self, finalizer: Callable[[], object]) -> Callable[[], object]:
        """*finalizer*, run as the harness's."""

        def run() -> object:
            previous, self.owner = self.owner, ""
            try:
                return finalizer()
            finally:
                self.owner = previous

        return run

    def stop(self) -> None:
        self.active = False
        while self._undo:
            self._undo.pop()()

    def dump(self) -> dict[str, Any]:
        """This process's record, in plain lists, for a worker's output or :func:`resolve`."""
        return {
            "owners": {o: {k: sorted(v) for k, v in f.items()} for o, f in self.owners.items()},
            "readers": {o: {k: sorted(v) for k, v in f.items()} for o, f in self.readers.items()},
            "edges": sorted(self.edges),
            "error": self.error,
        }


_THIS = "tests/rails_reads.py"


def _under(path: str, folder: str | None) -> bool:
    return bool(folder) and (path == folder or path.startswith(f"{folder}/"))


def _prefixes(name: str) -> list[str]:
    parts = name.split(".")
    return [".".join(parts[:i]) for i in range(1, len(parts) + 1)]


def _names(name: str, module: ModuleType, fromlist: Iterable[str] | None, level: int) -> list[str]:
    """The modules an import statement reached: the packages on its path, and each submodule
    its ``from`` list names."""
    if fromlist:
        base = getattr(module, "__name__", "")
        return [*_prefixes(base), *(f"{base}.{n}" for n in fromlist if n != "*")]
    if level == 0:
        return _prefixes(name)
    return _prefixes(getattr(module, "__name__", ""))


def _program(event: str, args: tuple[Any, ...]) -> tuple[str, list[str]]:
    """The program a spawn event starts, by its file name, and its arguments."""
    try:
        if event == "subprocess.Popen":
            executable, given = args[0], args[1]
            argv = [given] if isinstance(given, (str, bytes, os.PathLike)) else list(given)
            argv = [os.fsdecode(os.fspath(a)) for a in argv]
            if len(argv) == 1 and executable is None:
                argv = argv[0].split()
            first = os.fsdecode(os.fspath(executable)) if executable else argv[0]
        elif event == "os.system":
            argv = os.fsdecode(args[0]).split()
            first = argv[0]
        else:
            if event == "os.spawn":
                first, given = args[1], args[2]
            elif event in ("os.posix_spawn", "os.exec"):
                first, given = args[0], args[1]
            else:
                return event.removeprefix("os."), []
            first = os.fsdecode(os.fspath(first))
            argv = [os.fsdecode(os.fspath(a)) for a in given]
        return os.path.basename(first) or "?", argv[1:]
    except (IndexError, TypeError, ValueError, AttributeError):
        return "?", []


def _importing() -> bool:
    """Whether the import system is listing a folder to find a module (``FileFinder``), which
    no rail reads: the innermost frame outside this module is importlib's own."""
    frame = sys._getframe(1)
    while frame is not None and frame.f_code.co_filename == __file__:
        frame = frame.f_back
    return frame is not None and frame.f_code.co_filename.startswith("<frozen importlib")


# ── one leg's record ────────────────────────────────────────────────────────────────────────


def _merge(target: dict[str, Facts], source: Mapping[str, Mapping[str, Iterable[str]]]) -> None:
    for owner, facts in source.items():
        mine = target.setdefault(owner, _facts())
        for kind in KINDS:
            mine[kind].update(facts.get(kind, ()))


def resolve(
    dumps: Iterable[Mapping[str, Any]],
    *,
    walks: Mapping[str, tuple[str, str]],
    outcomes: Mapping[str, str],
    leg: str,
    commit: str,
    recorded_at: str,
    package: str | None = None,
) -> dict[str, Any]:
    """One leg's record from its processes' dumps.

    *outcomes* holds each rail module the leg ran, with ``""`` for a module whose every test
    passed or skipped and the reason otherwise. *walks* names each source index walk's folder
    and the suffix it keeps. Nothing under *package* is the harness's own."""
    owners: dict[str, Facts] = {}
    readers: dict[str, Facts] = {}
    edges: dict[str, set[str]] = {}
    errors: list[str] = []
    for dump in dumps:
        _merge(owners, dump.get("owners", {}))
        _merge(readers, dump.get("readers", {}))
        for importer, target in dump.get("edges", ()):
            edges.setdefault(importer, set()).add(target)
        if dump.get("error"):
            errors.append(str(dump["error"]))

    def closure(starts: Iterable[str]) -> set[str]:
        seen: set[str] = set()
        todo = [s for s in starts if s.endswith(".py")]
        while todo:
            module = todo.pop()
            if module not in seen:
                seen.add(module)
                todo.extend(edges.get(module, ()))
        return seen

    def reads(own: Facts, modules: set[str]) -> Facts:
        found = {kind: set(values) for kind, values in own.items()}
        for module in modules:
            for kind, values in readers.get(module, _facts()).items():
                found[kind] |= values
        found["files"] |= modules
        return found

    # The harness's reads are its own events: at the session's level, or made by its code inside
    # a rail's test (Recorder._note). It inherits no module's reads, since a cache of its own
    # answers at the session's level, where its read was charged. Nothing under the package is
    # its own.
    found = {kind: set(values) for kind, values in owners.get("", _facts()).items()}
    for kind in ("files", "folders"):
        found[kind] = {p for p in found[kind] if not _under(p, package)}
    found["walks"] = {w for w in found["walks"] if not _under(walks.get(w, ("", ""))[0], package)}
    harness = _written(found, walks)
    rails: dict[str, Any] = {}
    unrecorded: dict[str, str] = {}
    for module, failed in sorted(outcomes.items()):
        if not module.startswith(RAILS + "/"):
            continue
        if failed:
            unrecorded[module] = failed
            continue
        own = owners.get(module, _facts())
        found = reads(own, closure({module}) | {module})
        rails[module] = _written(found, walks, minus=harness)
    record: dict[str, Any] = {
        "schema": SCHEMA,
        "commit": commit,
        "recorded_at": recorded_at,
        "legs": [leg],
        "harness": harness,
        "rails": rails,
        "unrecorded": unrecorded,
    }
    if errors:
        record["error"] = "; ".join(sorted(set(errors)))
    return record


def _written(
    found: Facts, walks: Mapping[str, tuple[str, str]], minus: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """*found* as the record writes it: each walk as its folder and suffix, the files a walk of
    its own holds dropped, and what *minus* (the harness, which every rail reads) holds."""
    named = sorted({walks[name] for name in found["walks"] if name in walks})
    unknown = sorted(name for name in found["walks"] if name not in walks)
    if unknown:
        raise ValueError(f"the source index walks {unknown}, which the record cannot place")
    files = {
        f
        for f in found["files"]
        if not any(f.startswith(folder + "/") and f.endswith(suffix) for folder, suffix in named)
    }
    folders = set(found["folders"])
    if minus is not None:
        files -= set(minus["files"])
        folders -= set(minus["folders"])
        named = [w for w in named if list(w) not in minus["walks"]]
    return {
        "files": sorted(files),
        "folders": sorted(folders),
        "walks": [list(w) for w in named],
        "spawns": sorted(found["spawns"]),
    }


def _commit(root: Path) -> str:
    try:
        done = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return os.environ.get("GITHUB_SHA", "")
    return done.stdout.strip() if done.returncode == 0 else os.environ.get("GITHUB_SHA", "")


# ── the plugin ──────────────────────────────────────────────────────────────────────────────


class Plugin:
    """Charges each event to the rail module running; on the process that writes the report
    (xdist's controller, or the one process without it), gathers each module's outcome and
    every process's dump, and writes the leg's record at the session's end."""

    def __init__(
        self,
        config: pytest.Config,
        recorder: Recorder | None,
        path: Path,
        leg: str,
        walks: Mapping[str, tuple[str, str]],
    ) -> None:
        self.config = config
        self.recorder = recorder
        self.path = path
        self.leg = leg
        self.walks = walks
        self.writes = not hasattr(config, "workerinput")
        self._dumps: list[Mapping[str, Any]] = []
        self._expected: dict[str, set[str]] = {}
        self._done: set[str] = set()
        self._failed: dict[str, str] = {}

    def _charged(self, nodeid: str):
        recorder = self.recorder
        if recorder is None:
            yield
            return
        previous, recorder.owner = recorder.owner, nodeid.split("::", 1)[0]
        try:
            yield
        finally:
            recorder.owner = previous

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_make_collect_report(self, collector: pytest.Collector):
        path = getattr(collector, "path", None)
        if self.recorder is not None and isinstance(path, Path) and path.suffix == ".py":
            yield from self._charged(self.recorder.rel(path) or "")
        else:
            yield

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_runtest_setup(self, item: pytest.Item):
        yield from self._charged(item.nodeid)

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_runtest_call(self, item: pytest.Item):
        yield from self._charged(item.nodeid)

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_runtest_teardown(self, item: pytest.Item):
        yield from self._charged(item.nodeid)

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_fixture_setup(self, fixturedef, request):  # noqa: ARG002 — pytest's signature
        """A fixture the harness defines (a conftest fence, a guard's) runs as the harness, its
        setup and its teardown: what it reads inside a rail's test is the harness's read, which
        every rail makes, not that rail's."""
        recorder = self.recorder
        code = getattr(inspect.unwrap(getattr(fixturedef, "func", None)), "__code__", None)
        if (
            recorder is None
            or code is None
            or recorder._kind(code.co_filename) not in recorder.harness
        ):
            yield
            return
        finalizers = getattr(fixturedef, "_finalizers", None)
        before = len(finalizers) if isinstance(finalizers, list) else 0
        previous, recorder.owner = recorder.owner, ""
        try:
            yield
        finally:
            recorder.owner = previous
        if isinstance(finalizers, list):
            # A yield fixture's teardown is the finalizer its setup added (pytest's own list).
            for index in range(before, len(finalizers)):
                finalizers[index] = recorder.as_harness(finalizers[index])

    def _expect(self, nodeids: Iterable[str]) -> None:
        for nodeid in nodeids:
            self._expected.setdefault(nodeid.split("::", 1)[0], set()).add(nodeid)

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        if self.writes and self.recorder is not None:
            self._expect(item.nodeid for item in session.items)

    @pytest.hookimpl(optionalhook=True)
    def pytest_xdist_node_collection_finished(self, node, ids) -> None:  # noqa: ARG002
        self._expect(ids)

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if not self.writes:
            return
        if report.failed:
            self._failed.setdefault(
                report.nodeid.split("::", 1)[0], f"{report.nodeid} failed in its {report.when}"
            )
        if report.when == "call" or (report.when == "setup" and report.skipped):
            self._done.add(report.nodeid)

    @pytest.hookimpl(optionalhook=True)
    def pytest_testnodedown(self, node, error) -> None:  # noqa: ARG002
        dump = getattr(node, "workeroutput", {}).get(_WORKER_KEY)
        if dump is not None:
            self._dumps.append(dump)

    def _outcomes(self) -> dict[str, str]:
        outcomes: dict[str, str] = {}
        for module, nodeids in self._expected.items():
            missing = sorted(nodeids - self._done)
            if module in self._failed:
                outcomes[module] = self._failed[module]
            elif missing:
                outcomes[module] = f"{len(missing)} of its tests did not run, {missing[0]} first"
            else:
                outcomes[module] = ""
        return outcomes

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session: pytest.Session) -> None:
        recorder = self.recorder
        if recorder is not None:
            recorder.stop()
            if not self.writes:
                self.config.workeroutput[_WORKER_KEY] = recorder.dump()
                return
            self._dumps.append(recorder.dump())
        if not self.writes:
            return
        root = Path(str(self.config.rootpath))
        record = resolve(
            self._dumps,
            package=self.walks.get("package", ("", ""))[0] or None,
            walks=self.walks,
            outcomes=self._outcomes(),
            leg=self.leg,
            commit=_commit(root),
            recorded_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(record, indent=None, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, self.path)


def configure(config: pytest.Config, source_index: ModuleType) -> None:
    """Register the plugin in a rails leg's run (one that writes ``junit-rails-<leg>.xml``) of a
    push to main or a pull request (:func:`armed`), recording in each process that runs tests:
    an xdist worker, or the one process without workers. The controller only gathers and
    writes."""
    found = report_path(config)
    if found is None or not armed():
        return
    path, leg = found
    walks = {
        name: (folder, source_index.SUFFIXES.get(name, ""))
        for name, folder in source_index.ROOTS.items()
    }
    worker = hasattr(config, "workerinput")
    controller = not worker and bool(getattr(config.option, "numprocesses", None))
    recorder = None
    if not controller:
        root = Path(str(config.rootpath))
        recorder = Recorder(root, source_index.__file__, walks.get("package", ("", ""))[0] or None)
        recorder.install(source_index.INDEX)
        # Read before any plugin was configured, and so before this one: pytest's own settings,
        # and the lock that decides every package the run imports from outside the tree.
        for given in (config.inipath, root / "uv.lock"):
            if given is not None and Path(given).is_file():
                recorder.owners[""]["files"].add(recorder.rel(given) or "")
    config.pluginmanager.register(Plugin(config, recorder, path, leg, walks), "rails-reads")
