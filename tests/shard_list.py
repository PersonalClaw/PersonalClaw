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
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

import pytest

OPTION = "--shard-list"
_KEY = pytest.StashKey["ShardList"]()


class ShardList:
    def __init__(self, root: Path, listed: frozenset[Path], patterns: list[str]) -> None:
        self.root, self.listed, self.patterns = root, listed, patterns
        self.reached: set[Path] = set()

    def is_test_file(self, path: Path) -> bool:
        return path.suffix == ".py" and any(fnmatch.fnmatch(path.name, p) for p in self.patterns)


def addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        OPTION,
        default=None,
        metavar="FILE",
        help="collect only the test files FILE names, one per line (a CI shard's list)",
    )


def configure(config: pytest.Config) -> None:
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
