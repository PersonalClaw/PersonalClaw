"""``python -m tools.ci parity``: whether ``test-shard`` and ``test-macos`` ran the same tests.

``test-macos`` runs ``test-shard``'s split on macOS (#3913), so the two should run one set of test
node ids. This reads one attempt's history, the ``history-<run>-<attempt>.jsonl`` file in the
``telemetry-history-<run>-<attempt>`` artifact ``tools/ci/telemetry.py`` writes, and compares the
ids of the two legs' test lines, sorted to a side by the junit report each came from
(``junit-test-shard-<n>`` and ``junit-test-macos-<n>``). A skipped case is in its side's set: it
was collected there.

It reports every id only one side ran, and every id one side ran more than once (a shard plan
gives each file to one shard, so that should not happen, but it is no difference between the two
sides and does not fail the check). Exit 0 when the sets are equal, 1 when they differ, and 2
when the file can't be read: missing, empty, not JSON lines, another schema or a newer version, a
line or the whole over its limit, a test line with no id or report, or no report of one side
(macOS is skipped on a push whose tree was already tested, and that is no evidence either way).

It reads only the file it is given; it starts nothing and fetches nothing. Standard library only,
like every file in ``tools/ci``.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path

from . import telemetry

#: Each side, by the prefix of the junit reports its shards upload.
SIDES: Mapping[str, str] = {
    "test-shard": "junit-test-shard-",
    "test-macos": "junit-test-macos-",
}

#: The same limits the shard planner reads a history within (``tools/ci/shards.py``).
MAX_LINE_BYTES = 1024 * 1024
MAX_BYTES = 256 * 1024 * 1024


class UnreadableError(Exception):
    """The history can't be read as one attempt's history in the one schema."""


def read(path: Path) -> dict[str, Counter[str]]:
    """How many times each side ran each test id, from the history at *path*."""
    ran: dict[str, Counter[str]] = {side: Counter() for side in SIDES}
    budget = MAX_BYTES
    try:
        handle = path.open("rb")
    except OSError as exc:
        raise UnreadableError(f"{path}: {exc.strerror or exc}") from None
    with handle:
        number = -1
        while True:
            raw = handle.readline(min(budget, MAX_LINE_BYTES) + 1)
            if not raw:
                break
            number += 1
            if len(raw) > MAX_LINE_BYTES:
                raise UnreadableError(f"line {number + 1} passes {MAX_LINE_BYTES} bytes")
            budget -= len(raw)
            if budget < 0:
                raise UnreadableError(f"the history passes {MAX_BYTES} bytes")
            try:
                line = json.loads(raw)
            except ValueError:
                raise UnreadableError(f"line {number + 1} is not JSON") from None
            if not isinstance(line, dict):
                raise UnreadableError(f"line {number + 1} is not a JSON object")
            if number == 0:
                if line.get("kind") != "run" or line.get("schema") != telemetry.SCHEMA:
                    raise UnreadableError(f"its first line is not a {telemetry.SCHEMA} run line")
                if line.get("version") != telemetry.VERSION:
                    raise UnreadableError(
                        f"schema version {line.get('version')!r}, this check reads "
                        f"{telemetry.VERSION}"
                    )
                continue
            if line.get("kind") != "test":
                continue
            test, report = line.get("test"), line.get("report")
            if not isinstance(test, str) or not test or not isinstance(report, str):
                raise UnreadableError(f"line {number + 1} is a test line with no id or report")
            for side, prefix in SIDES.items():
                if report.startswith(prefix):
                    ran[side][test] += 1
    if number < 0:
        raise UnreadableError("the file is empty")
    for side, prefix in SIDES.items():
        if not ran[side]:
            raise UnreadableError(f"it holds no {prefix}* reports: {side} did not run here")
    return ran


def compare(ran: Mapping[str, Counter[str]]) -> tuple[int, list[str]]:
    """The exit code and the lines that say why."""
    left, right = SIDES
    only = [
        (side, test)
        for side, other in ((left, right), (right, left))
        for test in sorted(set(ran[side]) - set(ran[other]))
    ]
    said = [f"only {side}: {test}" for side, test in only]
    twice = [
        f"{side} ran {test} {count} times"
        for side in SIDES
        for test, count in sorted(ran[side].items())
        if count > 1
    ]
    if only:
        head = f"{len(only)} test{'s' if len(only) != 1 else ''} ran on one side only:"
        return 1, [head, *said, *twice]
    same = len(ran[left])
    return 0, [f"{left} and {right} ran the same {same} test{'s' if same != 1 else ''}", *twice]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.ci parity")
    parser.add_argument(
        "--history", type=Path, required=True, help="one attempt's history-<run>-<attempt>.jsonl"
    )
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        ran = read(args.history)
    except UnreadableError as err:
        print(f"::error title=the parity check did not run::{args.history} cannot be read: {err}")
        return 2
    code, lines = compare(ran)
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main())
