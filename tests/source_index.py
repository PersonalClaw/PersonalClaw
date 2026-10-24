"""One cached, parsed view of the source tree, for every test that reads the tree.

A tree rail used to walk ``src/personalclaw`` (or ``web/src``, ``docs``, ``.github/workflows``)
itself and parse what it found, so a worker that ran forty rails walked and parsed the package
forty times, each inside some test's timeout. Every such rail now asks this module instead:

- ``package_files()``, ``web_files()``, ``docs_files()`` and ``workflow_files()`` are the walks,
  each a sorted tuple of absolute paths, each held to a vacuity floor (``FLOORS``): a walk that
  finds fewer files than its floor has broken, and a rail reading it would pass on nothing.
  ``package_files()`` holds the package's ``.py`` modules; ``package_any_files()`` holds every
  file under it, for the rails that read its data and docs too (an app's ``app.json``, a
  bundled workflow, a packaged weight).
- ``text(path)`` and ``tree(path)`` read and parse one file, once per process. A tree is shared
  by every rail that asks for it, so a rail reads it and never changes it.
- ``PARSES`` counts every parse this module made. Parsing a file a second time raises
  ``DoubleParse`` where it happens: two caches of the same file in one process are the waste this
  module exists to end. ``tests/rails/test_source_index.py`` holds the process to it.
- ``INDEX.observers`` are told of each walk and file a caller asks for, cached or not, so the
  record of what each rail reads (``tests/rails_reads.py``) sees an answer that read nothing.

``warm()`` walks every root and parses the package. The suite's runtest hook in
``tests/conftest.py`` calls it before the first test whose module reads this index, wrapped
outside pytest-timeout's own hook, so no single test pays the walk inside its time limit. A worker
then holds the index for the rest of its life, one copy per worker; a worker that runs no tree
rail never builds it.
"""

from __future__ import annotations

import ast
import gc
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[1]

#: The four roots a tree rail reads, repository-relative, each walk by its name. ``package_any``
#: is the package root again, every file under it rather than its ``.py`` modules alone.
ROOTS = {
    "package": "src/personalclaw",
    "package_any": "src/personalclaw",
    "web": "web/src",
    "docs": "docs",
    "workflows": ".github/workflows",
}

#: The suffix a walk keeps, by its name; a walk not named keeps every file.
SUFFIXES = {"package": ".py"}

#: Each walk's vacuity floor: about nine tenths of what the tree held when the floor was set
#: (1,466 package modules of 1,770 tracked package files, 2,220 web source files, 112 docs
#: files, 11 workflows). A walk that finds fewer has broken — a moved root, a wrong suffix — and
#: must say so rather than hand every rail built on it an empty tree to pass on.
FLOORS = {"package": 1300, "package_any": 1590, "web": 2000, "docs": 100, "workflows": 10}

#: Every parse this module made in this process, by path. Each count is 1.
PARSES: Counter[Path] = Counter()


class DoubleParse(AssertionError):
    """A file was parsed a second time in one process."""


class VacuousWalk(AssertionError):
    """A walk found fewer files than its floor."""


class NotIndexed(AssertionError):
    """A path outside every root the index holds was asked of it."""


def _parse(path: Path, source: str, rel: str) -> ast.Module:
    """The one place this module parses. A second parse of the same file is refused."""
    if PARSES[path]:
        raise DoubleParse(
            f"{rel} was parsed again in this process: read it through one source index "
            "(tests/source_index.py) instead of keeping a second copy of its tree"
        )
    PARSES[path] += 1
    return ast.parse(source, filename=rel)


class SourceIndex:
    """The walks, texts and trees of one repository root. ``INDEX`` is the real tree's; tests
    build one over a fixture root to check the floors and the counter."""

    def __init__(self, root: Path, floors: dict[str, int] | None = None) -> None:
        self.root = root
        self.floors = dict(FLOORS if floors is None else floors)
        self._walks: dict[str, tuple[Path, ...]] = {}
        self._texts: dict[Path, str] = {}
        self._trees: dict[Path, ast.Module] = {}
        #: Told of each walk (``"walk"``, its name) and file (``"file"``, its path) a caller asks
        #: for, cached or not, outside :meth:`warm`: the record of what each rail reads
        #: (``tests/rails_reads.py``), since a cached answer reads nothing an audit hook sees.
        self.observers: list[Callable[[str, object], None]] = []
        self._warming = False

    def _tell(self, kind: str, value: object) -> None:
        if self.observers and not self._warming:
            for observer in self.observers:
                observer(kind, value)

    def files(self, name: str) -> tuple[Path, ...]:
        """Every file under the root named *name* in ``ROOTS``, sorted, at least its floor."""
        self._tell("walk", name)
        if name not in self._walks:
            base = self.root / ROOTS[name]
            found = tuple(sorted(p for p in base.rglob("*") if p.is_file()))
            if name in SUFFIXES:
                found = tuple(p for p in found if p.suffix == SUFFIXES[name])
            floor = self.floors[name]
            if len(found) < floor:
                raise VacuousWalk(
                    f"the {name} walk found {len(found)} files under {base}, below its floor of "
                    f"{floor}: the walk is broken, and every rail reading it would pass on nothing"
                )
            self._walks[name] = found
        return self._walks[name]

    def holds(self, path: Path) -> bool:
        """Whether *path* lies under one of this index's roots."""
        return any(path.is_relative_to(self.root / root) for root in set(ROOTS.values()))

    def _refuse_unheld(self, path: Path, asked: str) -> None:
        # A rail whose control plants its own tree must read that tree, not this one. Answering
        # for a path outside every root would mix the two: the planted file read beside the real
        # walk, reported against a root it is not under.
        if not self.holds(path):
            raise NotIndexed(
                f"{asked} was asked for {path}, which is under none of the index's roots "
                f"({', '.join(sorted(set(ROOTS.values())))} of {self.root}): a tree the test "
                "planted is read by the test, not through the source index"
            )

    def rel(self, path: Path) -> str:
        self._refuse_unheld(path, "rel")
        return path.relative_to(self.root).as_posix()

    def text(self, path: Path) -> str:
        self._refuse_unheld(path, "text")
        self._tell("file", path)
        if path not in self._texts:
            self._texts[path] = path.read_text(encoding="utf-8")
        return self._texts[path]

    def tree(self, path: Path) -> ast.Module:
        self._refuse_unheld(path, "tree")
        self._tell("file", path)
        if path not in self._trees:
            self._trees[path] = _parse(path, self.text(path), self.rel(path))
        return self._trees[path]

    def warm(self) -> None:
        # The warm-up is no rail's read: each rail's own asks are told, as they come.
        self._warming = True
        try:
            for name in ROOTS:
                self.files(name)
            for path in self.files("package"):
                self.tree(path)
        finally:
            self._warming = False


INDEX = SourceIndex(REPO)

#: How long this process's ``warm()`` took, once it has run.
WARMED: dict[str, float] = {}


def package_files() -> tuple[Path, ...]:
    """Every ``.py`` file under ``src/personalclaw``."""
    return INDEX.files("package")


def package_any_files() -> tuple[Path, ...]:
    """Every file under ``src/personalclaw``, whatever its suffix."""
    return INDEX.files("package_any")


def web_files() -> tuple[Path, ...]:
    """Every file under ``web/src``."""
    return INDEX.files("web")


def docs_files() -> tuple[Path, ...]:
    """Every file under ``docs``."""
    return INDEX.files("docs")


def workflow_files() -> tuple[Path, ...]:
    """Every file under ``.github/workflows``."""
    return INDEX.files("workflows")


def holds(path: Path) -> bool:
    """Whether *path* lies under one of the roots the index holds."""
    return INDEX.holds(path)


def under(files: Iterable[Path], folder: Path, suffixes: tuple[str, ...] = ()) -> list[Path]:
    """The files of a walk that lie under *folder* (and end in one of *suffixes*, if given).

    A *folder* outside every root the index holds is refused: filtering the real walk by it
    would answer an empty list, which a rail reads as a clean tree."""
    INDEX._refuse_unheld(folder, "under")
    return [
        p for p in files if p.is_relative_to(folder) and (not suffixes or p.name.endswith(suffixes))
    ]


def text(path: Path) -> str:
    return INDEX.text(path)


def tree(path: Path) -> ast.Module:
    """The parsed module at *path*, a file under the repository; shared, so never change it."""
    return INDEX.tree(path)


def rel(path: Path) -> str:
    """*path* relative to the repository, with forward slashes."""
    return INDEX.rel(path)


def package_trees() -> list[tuple[Path, ast.Module]]:
    """Every package module with its tree, in path order."""
    return [(path, tree(path)) for path in package_files()]


def warm() -> float:
    """Walk every root and parse the package, once per process; return the seconds it took."""
    if "seconds" not in WARMED:
        # The parse allocates millions of AST nodes, every one a container the cyclic collector
        # tracks, so in a worker already holding a loaded suite it sets off full collections that
        # tripled the warm-up (about 4.0 s against 1.3 s, measured). Nothing a parse makes is
        # garbage, so the collector is paused for it and resumed after; what lives, and for how
        # long, is unchanged.
        #
        # One full collection then moves the trees to the oldest generation. Left in the young
        # ones, every collection the worker's later tests set off walked them again: a census
        # walk three times over spent 12-17 s collecting against 3 s once they were promoted
        # (measured), and tests that never read the index ran past their limits beside it. Here,
        # outside every test's timeout, the collection costs about 2 s once. Not ``gc.freeze()``:
        # a frozen object leaves ``gc.get_objects()``, which code under test reads.
        started = time.perf_counter()
        _build(INDEX)
        WARMED["seconds"] = time.perf_counter() - started
    return WARMED["seconds"]


def _build(index: SourceIndex) -> None:
    """Walk and parse *index* with the collector paused, then promote what it holds (see
    :func:`warm`)."""
    enabled = gc.isenabled()
    gc.disable()
    try:
        index.warm()
        gc.collect()
    finally:
        if enabled:
            gc.enable()


#: The warm-up's key in an xdist worker's output, which the controller reads as the worker ends.
_WORKER_KEY = "source_index_warm_up_seconds"
#: Each process's warm-up seconds, by worker id ("main" without xdist), for the run's summary.
WARM_UPS = pytest.StashKey[dict[str, float]]()


class WarmUp:
    """Warms the index before the first test whose module reads it, outside that test's timeout.

    pytest-timeout arms its timer inside its own ``pytest_runtest_protocol`` wrapper; ``tryfirst``
    puts this wrapper outside that one, so the walk and the parse run before the timer starts and
    no rail pays them inside its limit. A process that runs no tree rail never builds the index.
    The seconds are kept for the run's summary, since they fall outside every test's junit time.
    """

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_runtest_protocol(self, item: pytest.Item):
        if "seconds" not in WARMED and reads_the_index(getattr(item, "module", None)):
            seconds = warm()
            workeroutput = getattr(item.config, "workeroutput", None)
            if workeroutput is not None:
                workeroutput[_WORKER_KEY] = seconds
            else:
                item.config.stash.setdefault(WARM_UPS, {})["main"] = seconds
        yield

    @pytest.hookimpl(optionalhook=True)
    def pytest_testnodedown(self, node, error) -> None:  # noqa: ARG002 — xdist hook signature
        seconds = getattr(node, "workeroutput", {}).get(_WORKER_KEY)
        if seconds is not None:
            node.config.stash.setdefault(WARM_UPS, {})[node.gateway.id] = seconds


def report(terminalreporter) -> None:
    """Print each process's warm-up seconds, if any process warmed the index."""
    warm_ups = terminalreporter.config.stash.get(WARM_UPS, None)
    if warm_ups:
        terminalreporter.write_sep("-", "source index warm-up (outside every test's time)")
        for worker, seconds in sorted(warm_ups.items()):
            terminalreporter.write_line(f"{worker}: {seconds:.2f}s")


def reads_the_index(module: ModuleType | None) -> bool:
    """Whether a test module reads this index: it imported this module or one of its names."""
    if module is None:
        return False
    this = sys.modules[__name__]
    return any(
        value is this or getattr(value, "__module__", None) == __name__
        for value in vars(module).values()
    )
