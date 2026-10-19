"""``python -m tools.ci selection-map``: which test files ran each source file, read from a coverage
database built with per-test contexts (#3946).

A suite run with ``--cov=personalclaw --cov-context=test`` records, for every line it ran, the test
that was running then: its pytest id with ``|setup``, ``|run`` or ``|teardown`` after it. This
reads the combined database and writes one JSON file:

- ``map``: each measured source file, as the database names it (repository-relative with
  ``relative_files``; an absolute path under ``--root`` is made relative to it), to the sorted
  test files whose setup, call or teardown ran any line of it;
- ``hubs``: the source files that more than :data:`HUB_SHARE` of the map's test files reach. A
  change to one runs the whole suite, so selection buys nothing for it;
- ``outside_tests``: the source files whose lines ran only outside any test's context: at import,
  before the first test began (the empty context), or in a child interpreter, whose lines
  ``patch = ["subprocess"]`` records but with no test's context. No test file maps to them, so
  the selector covers them by the import closure of what a test launches, never by this map;
- ``commit``, the commit the suite ran at, ``built_at``, and ``test_files``, how many test files
  the contexts name.

The database is read with ``sqlite3``, in the schema coverage writes (:data:`COVERAGE_SCHEMA`,
which a test holds to the installed coverage's), so this module stays standard library only, like
every file in ``tools/ci``, and opens it read-only. Only which file each context touched is read,
from the line table and the branch table both; no line number is decoded. A file that is not a
coverage database, a database in another schema, and one with no test context (a run without
``--cov-context=test``, whose map would select nothing) exit 2 and write nothing.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sqlite3
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

SCHEMA = "personalclaw-selection-map"
VERSION = 1
#: The schema version of the coverage database this reads (coverage.sqldata.SCHEMA_VERSION).
COVERAGE_SCHEMA = 7
#: A source file more than this share of the map's test files reach is a hub.
HUB_SHARE = 0.4
#: The phases pytest-cov's test context names after the test's id.
PHASES = frozenset({"setup", "run", "teardown"})

_PAIRS = """
    SELECT DISTINCT file.path, context.context
    FROM {table} JOIN file ON file.id = {table}.file_id
    JOIN context ON context.id = {table}.context_id
"""


class Unreadable(Exception):
    """The database cannot be read into a map."""


def file_of_context(context: str) -> str | None:
    """The test file a test context names (``tests/test_x.py::test_y[case]|run``), or None for
    a context that is not one test's phase: the empty one, or a static label."""
    nodeid, sep, phase = context.rpartition("|")
    if not sep or phase not in PHASES:
        return None
    path, sep, test = nodeid.partition("::")
    if not sep or not test or not path.endswith(".py"):
        return None
    return path


def read_pairs(data: Path) -> list[tuple[str, str]]:
    """Every (source file, context) pair the database records a line or a branch for."""
    if not data.is_file():
        raise Unreadable(f"{data} does not exist")
    try:
        db = sqlite3.connect(f"{data.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise Unreadable(f"{data}: {exc}") from None
    try:
        rows = db.execute("SELECT version FROM coverage_schema").fetchall()
        if rows != [(COVERAGE_SCHEMA,)]:
            raise Unreadable(f"{data}: coverage schema {rows}, this reads {COVERAGE_SCHEMA}")
        pairs = db.execute(_PAIRS.format(table="line_bits")).fetchall()
        pairs += db.execute(_PAIRS.format(table="arc")).fetchall()
    except sqlite3.Error as exc:
        raise Unreadable(f"{data} is not a coverage database: {exc}") from None
    finally:
        db.close()
    return [(str(path), str(context)) for path, context in pairs]


def _relative(path: str, root: Path | None) -> str:
    if root is None or not Path(path).is_absolute():
        return Path(path).as_posix()
    try:
        return Path(path).relative_to(root).as_posix()
    except ValueError:
        return Path(path).as_posix()


def build(pairs: Iterable[tuple[str, str]], commit: str, root: Path | None) -> dict[str, Any]:
    """The map, its hubs and the files no test context reached, from (file, context) pairs."""
    tests_of: dict[str, set[str]] = {}
    for path, context in pairs:
        reached = tests_of.setdefault(_relative(path, root), set())
        test = file_of_context(context)
        if test is not None:
            reached.add(test)
    test_files = {t for tests in tests_of.values() for t in tests}
    if not test_files:
        raise Unreadable("no line was recorded under a test's context (--cov-context=test)")
    mapped = {path: sorted(tests) for path, tests in sorted(tests_of.items()) if tests}
    return {
        "schema": SCHEMA,
        "version": VERSION,
        "commit": commit,
        "built_at": datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z"),
        "test_files": len(test_files),
        "hub_share": HUB_SHARE,
        "hubs": [p for p, tests in mapped.items() if len(tests) > HUB_SHARE * len(test_files)],
        "outside_tests": sorted(p for p, tests in tests_of.items() if not tests),
        "map": mapped,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.ci selection-map")
    parser.add_argument("--data", type=Path, required=True, help="the combined coverage database")
    parser.add_argument("--commit", required=True, help="the commit the suite ran at")
    parser.add_argument("--out", type=Path, required=True, help="where to write the map")
    parser.add_argument("--root", type=Path, default=None, help="make absolute paths relative")
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        built = build(read_pairs(args.data), args.commit, args.root)
    except Unreadable as err:
        print(f"::error title=the selection map was not built::{err}")
        return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(built, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"selection map: {len(built['map'])} source files from {built['test_files']} test files,"
        f" {len(built['hubs'])} hubs, {len(built['outside_tests'])} reached outside any test"
    )
    for hub in built["hubs"]:
        print(f"  hub: {hub}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
