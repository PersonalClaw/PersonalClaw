"""``python -m tools.ci contract``: main's CI contract over a head's definitions (#4584 step 4).

A head that changes a CI definition is judged by two things, both green: its own runs (its
definitions and its tests), and this: ``main``'s copy of the CI contract and definition rails,
with their helpers, run against the head's tree. The head's ``tools/ci``, ``catalogue.toml`` and
workflows are what is tested; the rails that test them are ``main``'s. So a head that weakens CI in
a way ``main``'s contract forbids (a required job dropped, ``results`` not needing every job, a
pull request job with a secret, a plant removed) is red, whatever its own copy of the contract
says: its own copy is replaced before anything runs.

``merge-verdict.yml``'s ``contract`` job runs this from ``main``'s checkout, on the head's tree laid
out beside it, with ``main``'s locked environment. Which files are the contract is this module's
list, read from ``main``: a head cannot shorten it.

The judge is not in the head's process. The rails import the head's ``tools/ci``, so pytest runs
them in a child process, and this module (``main``'s, which imports nothing of the head's) reads
the child's junit report from a path it names and decides: green only when the child exited 0
and its report holds every rail with no failure and no error. A head whose ``tools/ci`` exits 0
when imported writes no report, so it is red.

What it does not defend against. The child runs the head's code, because that is what it tests,
so a head built to forge the child's report is not caught here. It catches a head that weakens
CI by mistake or by a shortcut (a dropped job, an edit to its own copy of the contract, a removed
plant, a forced exit). A forged report is covered by core review of every ``tools/ci`` change,
commit provenance and squash-only merges; a data-only child with no head code in it is #5518.
The job holds no secret and no write token and saves no cache. Standard library only.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

#: The contract's rails: every CI rail, and the workflow rails that hold the definitions' shape.
CONTRACT_GLOBS: tuple[str, ...] = (
    "tests/rails/test_ci_*.py",
    "tests/rails/test_workflow_path_filters_tree.py",
    "tests/rails/test_the_candidate_workflows_are_named_in_one_place_tree.py",
    "tests/rails/test_no_workflow_step_feeds_a_quiet_grep_from_a_pipe_under_pipefail.py",
)


def contract_rails(main: Path) -> list[str]:
    """The contract's rails as ``main`` holds them, by path from its root, sorted."""
    found = {p.relative_to(main).as_posix() for g in CONTRACT_GLOBS for p in main.glob(g)}
    return sorted(found)


#: The list of every test's own timeout mark, with its cap and why (``tests/timeout_marks.py``
#: reads it). It names the head's tests as well as the contract's, so the overlay writes a list of
#: its own: :func:`overlaid_marks`.
MARKS = "tests/timeout-marks.toml"
_MARK = re.compile(r"^\[\[mark\]\]\n", re.M)


def helpers(main: Path) -> list[str]:
    """``main``'s test helpers: every Python file under ``tests/`` that is not a test module,
    conftest files included, and what pytest and the rails read beside them."""
    out: list[str] = []
    for path in sorted((main / "tests").rglob("*.py")):
        rel = path.relative_to(main).as_posix()
        if "/fixtures/" in rel:
            continue
        if not path.name.startswith("test_"):
            out.append(rel)
    for extra in (MARKS,):
        if (main / extra).is_file():
            out.append(extra)
    return out


def _mark_entries(text: str) -> tuple[str, list[tuple[str, str]]]:
    """A marks list's header, and each ``[[mark]]`` entry as written, by the file it names."""
    starts = [m.start() for m in _MARK.finditer(text)]
    entries = []
    for start, end in zip(starts, [*starts[1:], len(text)]):
        entry = text[start:end].strip() + "\n"
        [mark] = tomllib.loads(entry)["mark"]
        if not isinstance(mark["site"], str):
            raise ValueError(f"a mark's site is not a path: {mark['site']!r}")
        entries.append((mark["site"].partition("::")[0], entry))
    return text[: starts[0]] if starts else text, entries


def overlaid_marks(main_list: str, head_list: str | None, written: set[str]) -> str:
    """The marks list the contract runs with: ``main``'s entries for the sites in ``written`` (the
    files the overlay puts there, so ``main``'s rails are listed at ``main``'s caps, whatever the
    head's list says of them), then the head's entries for every other site, its own tests. A
    head's list that does not read adds nothing, so the head's own marks read as unlisted."""
    header, ours = _mark_entries(main_list)
    theirs: list[tuple[str, str]] = []
    if head_list is not None:
        try:
            theirs = _mark_entries(head_list)[1]
        except (tomllib.TOMLDecodeError, KeyError, TypeError, ValueError):
            theirs = []
    kept = [e for f, e in ours if f in written] + [e for f, e in theirs if f not in written]
    return (header.rstrip("\n") + "\n\n" if header.strip() else "") + "\n".join(kept)


def overlay(main: Path, head: Path) -> list[str]:
    """Put ``main``'s contract rails and helpers over the head's tree; the paths written. The marks
    list is the one file both trees speak for: :func:`overlaid_marks` writes it."""
    written = [*contract_rails(main), *helpers(main)]
    for rel in written:
        source, target = main / rel, head / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if rel == MARKS:
            head_list = target.read_text(encoding="utf-8") if target.is_file() else None
            text = overlaid_marks(source.read_text(encoding="utf-8"), head_list, set(written))
            target.write_text(text, encoding="utf-8")
        else:
            shutil.copyfile(source, target)
    return written


def report_problems(report: Path, rails: list[str], *, every_rail: bool = True) -> list[str]:
    """Why the child's junit report at ``report`` is not green for ``rails``: missing, unreadable,
    empty, a failure or an error, or (``every_rail``) a rail that ran no test. Empty when green."""
    if not report.is_file():
        return [f"the contract run wrote no report at {report}: the run stopped before its rails"]
    try:
        root = ET.parse(report).getroot()
    except ET.ParseError as error:
        return [f"the contract run's report is not junit: {error}"]
    cases = list(root.iter("testcase"))
    if not cases:
        return ["the contract run's report holds no test, so nothing judged the head"]
    bad = [
        f"{case.get('classname', '?')}::{case.get('name', '?')} {outcome.tag}"
        for case in cases
        for outcome in case
        if outcome.tag in {"failure", "error"}
    ]
    if not bad:
        # A suite that counts a failure no test case carries (an error outside any test) still reds.
        bad = [
            f"{suite.get('name', 'pytest')} reports {suite.get(kind)} {kind}"
            for suite in root.iter("testsuite")
            for kind in ("failures", "errors")
            if suite.get(kind, "0") != "0"
        ]
    if every_rail:
        names = {case.get("classname", "") for case in cases}
        for rail in rails:
            module = rail.removesuffix(".py").replace("/", ".")
            if not any(n == module or n.startswith(module + ".") for n in names):
                bad.append(f"{rail} ran no test")
    return bad


def run(
    main: Path,
    head: Path,
    *,
    only: str = "",
    workers: str = "auto",
    subset: list[str] | None = None,
) -> int:
    """Overlay, then run the contract's rails on the head's tree in a child pytest with this
    interpreter and ``main``'s pytest settings, and judge the child's report: 0 when green, else
    the child's exit status or 1. ``subset`` narrows the rails a plant runs, and names only rails
    the contract holds."""
    rails = contract_rails(main)
    if subset is not None:
        if not set(subset) <= set(rails):
            raise ValueError(f"not contract rails: {sorted(set(subset) - set(rails))}")
        rails = sorted(subset)
    if not rails:
        print("::error title=no contract::main holds no contract rail, so nothing would judge")
        return 2
    overlay(main, head)
    env = {
        k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "GH_TOKEN", "GITHUB_TOKEN"}
    }
    env["PYTHONPATH"] = os.pathsep.join([str(head / "src"), str(head)])
    with tempfile.TemporaryDirectory(prefix="contract-") as scratch:
        report = Path(scratch) / "contract.xml"
        status = _child(main, head, rails, report, env=env, only=only, workers=workers)
        problems = report_problems(report, rails, every_rail=not only)
    if status == 0 and not problems:
        print(f"main's contract: green over the head's tree ({len(rails)} rails)", flush=True)
        return 0
    if status != 0:
        problems.insert(0, f"the contract run exited {status}")
    for problem in problems:
        print(f"::error title=main's contract::{problem}", flush=True)
    return status or 1


def _child(
    main: Path,
    head: Path,
    rails: list[str],
    report: Path,
    *,
    env: dict[str, str],
    only: str,
    workers: str,
) -> int:
    """The child pytest over ``rails`` on the head's tree, writing its junit report to ``report``;
    its exit status."""
    argv = [
        sys.executable,
        "-m",
        "pytest",
        "-c",
        str(main / "pyproject.toml"),
        "--rootdir",
        str(head),
        # main's pytest settings, but the head's import roots: ``pythonpath`` is read relative to
        # the settings' file, which is main's.
        "-o",
        f"pythonpath={head / 'src'} {head}",
        "-p",
        "no:cacheprovider",
        "-q",
        f"--junitxml={report}",
        "-o",
        "junit_family=xunit2",
        *(["-n", workers, "--dist", "worksteal"] if workers != "0" else []),
        *(["-k", only] if only else []),
        *rails,
    ]
    print(f"main's contract: {len(rails)} rails over the head's tree at {head}", flush=True)
    return subprocess.run(argv, cwd=head, env=env, check=False).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.ci contract", description=(__doc__ or "").split("\n")[0]
    )
    parser.add_argument("--main", required=True, type=Path, help="main's checkout (this one)")
    parser.add_argument("--head", required=True, type=Path, help="the head's tree, laid out")
    parser.add_argument("--workers", default="auto", help="pytest-xdist workers, 0 for none")
    parser.add_argument("--only", default="", help="a -k expression (plants only)")
    args = parser.parse_args(argv)
    main_dir, head_dir = args.main.resolve(), args.head.resolve()
    if main_dir == head_dir or not (head_dir / "tools" / "ci").is_dir():
        parser.error("--head must be another tree, holding tools/ci")
    return run(main_dir, head_dir, only=args.only, workers=args.workers)


if __name__ == "__main__":
    sys.exit(main())
