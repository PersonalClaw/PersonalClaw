"""Where the suite pays most for rows that could share a test.

A parametrized function runs one pytest item per case, and every item pays the per-test floor.
This census lists each function with :data:`THRESHOLD` or more cases, ranked by the seconds its
cases took in total, so a consolidation (docs/maintainers/testing.md) starts where it saves most.
It informs; it gates nothing.

Three inputs, each read once:

* **The collection**, ``pytest --collect-only -qq``. The suite's ``addopts`` start with
  ``--verbose``, so ``-qq`` prints one test id per line here. Case counts come from it.
* **The security collection**, the same command with ``-m security``. A function any of whose
  cases it selects carries the ``security`` marker, on its module or on itself, so it is
  flagged: a consolidation keeps every refusal path of such a function.
* **junit reports**, one or more (``--junitxml``, or CI's ``junit-*`` artifacts). Each case's
  ``time`` (setup, call and teardown) is summed on its function. A case no report names is
  counted, not timed.

``python -m tools.ci variation-census`` writes the table as markdown, under a header naming the
commit collected, the run the durations came from and the report files by name.
"""

from __future__ import annotations

import argparse
import io
import re
import sys
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

#: A function with this many cases or more is listed.
THRESHOLD = 20

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


@dataclass(frozen=True)
class Row:
    """One parametrized function: its id (``path::[Class::]name``), how many cases it has, how
    many of them a report timed, their summed seconds, and whether it carries the marker."""

    function: str
    cases: int
    timed: int
    seconds: float
    security: bool

    @property
    def mean_ms(self) -> float:
        """Milliseconds per timed case; 0 when no case was timed."""
        return self.seconds / self.timed * 1000 if self.timed else 0.0


def collected_ids(text: str) -> list[str]:
    """The test ids in ``--collect-only -qq`` output, each once, in order. A line is an id when
    it starts at the margin with a ``.py`` path followed by ``::``; pytest's own lines (the
    count, a plugin's notice, an indented tree) are not."""
    seen: dict[str, None] = {}
    for raw in text.splitlines():
        line = _ANSI.sub("", raw).rstrip()
        if not line or line[0].isspace():
            continue
        path, sep, rest = line.partition("::")
        if sep and rest and path.endswith(".py"):
            seen.setdefault(line, None)
    return list(seen)


def function_of(test_id: str) -> str:
    """The function a case belongs to: its id without the parameter part."""
    return test_id.split("[", 1)[0]


def _classname(function: str) -> tuple[str, str]:
    """``(classname, name)`` as a junit report writes a case of *function*."""
    path, _, rest = function.partition("::")
    classes, _, name = rest.rpartition("::")
    module = path[: -len(".py")].replace("/", ".")
    return (f"{module}.{classes.replace('::', '.')}" if classes else module), name


def _cases(collection: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for test_id in collected_ids(collection):
        if "[" in test_id:
            key = function_of(test_id)
            counts[key] = counts.get(key, 0) + 1
    return counts


def _times(reports: Iterable[bytes], functions: Iterable[str]) -> dict[str, tuple[int, float]]:
    by_junit = {_classname(f): f for f in functions}
    times: dict[str, tuple[int, float]] = {}
    for data in reports:
        try:
            tree = ET.parse(io.BytesIO(data))
        except ET.ParseError as exc:
            raise ValueError(f"not a junit report: {exc}") from exc
        for case in tree.iter("testcase"):
            key = (case.get("classname") or "", (case.get("name") or "").split("[", 1)[0])
            function = by_junit.get(key)
            if function is not None:
                timed, seconds = times.get(function, (0, 0.0))
                times[function] = (timed + 1, seconds + float(case.get("time") or 0.0))
    return times


def census(
    collection: str,
    security_collection: str,
    reports: Sequence[bytes],
    *,
    threshold: int = THRESHOLD,
) -> list[Row]:
    """Every function with *threshold* cases or more, most summed seconds first, then by id."""
    counts = {f: n for f, n in _cases(collection).items() if n >= threshold}
    marked = {function_of(i) for i in collected_ids(security_collection)}
    times = _times(reports, counts)
    rows = [
        Row(f, n, times.get(f, (0, 0.0))[0], times.get(f, (0, 0.0))[1], f in marked)
        for f, n in counts.items()
    ]
    return sorted(rows, key=lambda r: (-r.seconds, r.function))


def render(
    rows: Sequence[Row],
    collection: str,
    *,
    commit: str,
    run: str,
    run_commit: str,
    reports: Sequence[str],
    threshold: int = THRESHOLD,
) -> str:
    """The census as markdown: where its numbers came from, the totals, then the table."""
    items = collected_ids(collection)
    cases = _cases(collection)
    parametrized = sum(cases.values())
    listed = sum(r.cases for r in rows)
    seconds = sum(r.seconds for r in rows)
    durations = f"CI run {run}" + (f" (`{run_commit}`)" if run_commit else "")
    lines = [
        "# Variation census",
        "",
        "Generated by `python -m tools.ci variation-census` (`tools/ci/variation_census.py`); do "
        "not edit by hand. What it is for, and the rule it serves: "
        "[testing.md](testing.md).",
        "",
        f"- Collected at `{commit}`.",
        f"- Durations from {durations}, in " + ", ".join(f"`{name}`" for name in reports) + ".",
        f"- {len(items)} test items, {parametrized} of them cases of {len(cases)} parametrized "
        "functions.",
        f"- {len(rows)} functions have {threshold} or more cases: {listed} cases, "
        f"{seconds / 60:.1f} summed minutes.",
        "",
        "| Function | Cases | Seconds | Mean per case (ms) | Security |",
        "|---|---:|---:|---:|---|",
    ]
    for row in rows:
        timed = "" if row.timed == row.cases else f" ({row.timed} timed)"
        lines.append(
            f"| `{row.function}` | {row.cases}{timed} | {row.seconds:.1f} | "
            f"{row.mean_ms:.0f} | {'yes' if row.security else ''} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.ci variation-census")
    parser.add_argument("--collection", type=Path, required=True)
    parser.add_argument("--security-collection", type=Path)
    parser.add_argument("--junit", type=Path, action="append", required=True)
    parser.add_argument("--commit", required=True, help="the commit the collection was taken at")
    parser.add_argument("--run", required=True, help="the CI run the junit reports came from")
    parser.add_argument("--run-commit", default="", help="the commit that run tested")
    parser.add_argument("--threshold", type=int, default=THRESHOLD)
    parser.add_argument("--output", type=Path, help="write here instead of stdout")
    args = parser.parse_args(argv)

    collection = args.collection.read_text(encoding="utf-8")
    security = (
        args.security_collection.read_text(encoding="utf-8") if args.security_collection else ""
    )
    try:
        rows = census(
            collection,
            security,
            [path.read_bytes() for path in args.junit],
            threshold=args.threshold,
        )
    except ValueError as exc:
        print(f"variation-census: {exc}", file=sys.stderr)
        return 1
    text = render(
        rows,
        collection,
        commit=args.commit,
        run=args.run,
        run_commit=args.run_commit,
        reports=[path.name for path in args.junit],
        threshold=args.threshold,
    )
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0
