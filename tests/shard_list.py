"""``--shard-list FILE``: collect only the test files a shard's list names.

``python -m tools.ci shards list`` writes one shard's files, one per line. With this option a
run still collects from ``testpaths``, as every run does, so node ids, conftest order and
fixtures are what a whole-suite run sees, but it skips every test file the list does not name,
so each worker imports a shard's files and no others.

Measured on a laptop: collecting one planned quarter of the suite through this hook took
69-85 s and about 610-675 MB, against 192-582 s for the same files passed to pytest as an
argument file. Whole-suite collection took 74-691 s on the same machine, so the figures are
noisy, but the hook was the fast one every time.

The run fails, rather than quietly running less or more, when the list is missing, empty or
unreadable (pytest given no selection would collect every test), or when a file it names is
never reached by collection (renamed, deleted, or outside ``testpaths``).

Each process that collects a shard's files also records, for the run's summary, how long it
took from its start to the end of its collection, the most memory (peak RSS) it had held by
then, and the most it held over the whole run, which is what decides how many workers a runner's
memory holds. The summary also names the machine's CPUs, since a shard whose every file ran
slower together ran on a slower host. An xdist worker sends its figures to the controller as
it ends; the controller prints one line per worker under "shard collection and memory" and
writes the same to the job summary, so a CI run shows both against #3923's bars (collection
30 s, 600 MB per worker).
"""

from __future__ import annotations

import fnmatch
import os
import resource
import sys
import time
from pathlib import Path

import pytest

OPTION = "--shard-list"
#: ``--shard-ids FILE``: write every test id the run collected, one per line, sorted, for
#: breadth's union check (``python -m tools.ci shards union``).
IDS_OPTION = "--shard-ids"
_KEY = pytest.StashKey["ShardList"]()
#: Each process's (collection seconds, peak RSS at collection's end, peak RSS over the run),
#: in seconds and MB, by worker id ("main" without xdist).
COSTS = pytest.StashKey[dict[str, tuple[float, int, int]]]()
#: The figures' key in an xdist worker's output, which the controller reads as the worker ends.
_WORKER_KEY = "shard_list_collection"


class ShardList:
    def __init__(self, root: Path, listed: frozenset[Path], patterns: list[str]) -> None:
        self.root, self.listed, self.patterns = root, listed, patterns
        self.reached: set[Path] = set()
        self.started = time.perf_counter()
        self.collected: tuple[float, int] | None = None

    def is_test_file(self, path: Path) -> bool:
        return path.suffix == ".py" and any(fnmatch.fnmatch(path.name, p) for p in self.patterns)


def addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        OPTION,
        default=None,
        metavar="FILE",
        help="collect only the test files FILE names, one per line (a CI shard's list)",
    )
    parser.addoption(
        IDS_OPTION,
        default=None,
        metavar="FILE",
        help="write every test id this run collected to FILE, one per line (breadth's union)",
    )


class _Ids:
    """Writes the ids the run collected, sorted, each as many times as the run holds it, so a
    test the run would run twice is named twice: the process's own items without xdist, or, in
    the xdist controller, which collects nothing itself, a worker's ids as it reports them
    (xdist runs nothing unless every worker collected the same list). A worker writes nothing;
    its controller does. The file is written as soon as collection ends, so a run that fails
    later still names what it collected."""

    def __init__(self, out: Path) -> None:
        self.out = out

    def _write(self, ids: list[str]) -> None:
        self.out.parent.mkdir(parents=True, exist_ok=True)
        self.out.write_text("".join(f"{i}\n" for i in sorted(ids)), encoding="utf-8")

    @pytest.hookimpl(trylast=True)
    def pytest_collection_finish(self, session: pytest.Session) -> None:
        self._write([item.nodeid for item in session.items])

    @pytest.hookimpl(optionalhook=True)
    def pytest_xdist_node_collection_finished(self, node, ids) -> None:  # noqa: ARG002
        self._write(list(ids))


def configure(config: pytest.Config) -> None:
    ids_to = config.getoption(IDS_OPTION, None)
    if ids_to and not hasattr(config, "workerinput"):
        config.pluginmanager.register(_Ids(Path(ids_to).resolve()), "shard-list-ids")
    named = config.getoption(OPTION)
    if not named:
        return
    try:
        lines = Path(named).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise pytest.UsageError(f"{OPTION} {named}: {exc}") from exc
    root = Path(config.rootpath)
    listed = frozenset((root / line.strip()).resolve() for line in lines if line.strip())
    if not listed:
        raise pytest.UsageError(f"{OPTION} {named} names no file, so nothing would be selected")
    config.stash[_KEY] = ShardList(root, listed, list(config.getini("python_files")))
    config.pluginmanager.register(_Controller(), "shard-list-costs")


def ignore_collect(collection_path: Path, config: pytest.Config) -> bool | None:
    shard = config.stash.get(_KEY, None)
    if shard is None or not collection_path.is_file() or not shard.is_test_file(collection_path):
        return None
    resolved = collection_path.resolve()
    if resolved in shard.listed:
        shard.reached.add(resolved)
        return None
    return True


def collection_finish(session: pytest.Session) -> None:
    shard = session.config.stash.get(_KEY, None)
    if shard is None:
        return
    missing = sorted(p.relative_to(shard.root).as_posix() for p in shard.listed - shard.reached)
    if missing:
        raise pytest.UsageError(
            f"{OPTION} names {len(missing)} file(s) collection never reached: {missing[:5]}"
        )
    shard.collected = (round(time.perf_counter() - shard.started, 2), peak_rss_mb())


def session_finish(session: pytest.Session) -> None:
    """Record this process's figures: its collection's, and its peak RSS over the whole run,
    which is what decides how many workers a runner's memory holds."""
    shard = session.config.stash.get(_KEY, None)
    if shard is None or shard.collected is None:
        return
    figures = (*shard.collected, peak_rss_mb())
    workeroutput = getattr(session.config, "workeroutput", None)
    if workeroutput is not None:
        workeroutput[_WORKER_KEY] = list(figures)
    else:
        session.config.stash.setdefault(COSTS, {})["main"] = figures


def cpu() -> str:
    """The machine's CPU count and, on Linux, its model: a shard whose every file ran slower
    together ran on a slower host, which no plan can see."""
    model = ""
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("model name"):
                model = " (" + line.split(":", 1)[1].strip() + ")"
                break
    except OSError:
        pass
    return f"{os.cpu_count()} CPUs{model}"


def peak_rss_mb() -> int:
    """This process's peak resident memory so far, in MB. ``ru_maxrss`` is bytes on macOS and
    kilobytes on Linux."""
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(peak / (1024 * 1024 if sys.platform == "darwin" else 1024))


class _Controller:
    """Receives each xdist worker's figures as the worker ends."""

    @pytest.hookimpl(optionalhook=True)
    def pytest_testnodedown(self, node, error) -> None:  # noqa: ARG002 — xdist hook signature
        figures = getattr(node, "workeroutput", {}).get(_WORKER_KEY)
        if figures is not None:
            seconds, collected, run = figures
            node.config.stash.setdefault(COSTS, {})[node.gateway.id] = (
                float(seconds),
                int(collected),
                int(run),
            )


def report(terminalreporter) -> None:
    """One line per process that collected a shard: its collection seconds and peak RSS, in the
    terminal and in the job summary."""
    costs = terminalreporter.config.stash.get(COSTS, None)
    if not costs:
        return
    lines = [
        f"{worker}: collected in {seconds:.2f}s, peak RSS {collected} MB, {run} MB over the run"
        for worker, (seconds, collected, run) in sorted(costs.items())
    ]
    lines.append(f"on {cpu()}")
    terminalreporter.write_sep("-", "shard collection and memory")
    for line in lines:
        terminalreporter.write_line(line)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(
                "### shard collection and memory\n\n" + "".join(f"- {line}\n" for line in lines)
            )
