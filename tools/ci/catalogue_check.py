"""``python -m tools.ci catalogue-check``: a pull request's catalogue, checked by main's copy.

The ``changes`` job runs this from main's ``tools/ci``, archived into a folder of its own, never
from the checkout: main's loader reads the pull request's ``tools/ci/catalogue.toml`` with main's
rules and main's own catalogue as its reference (:func:`catalogue.against`). So a pull request
cannot drop a security or live job, turn one ordinary, or take it out of a tier, by editing the
catalogue and the rules that judge it in the same change.

Exit 0 when both catalogues read and main's has no objection; 1 when either cannot be read, or
main's catalogue refuses the pull request's; 2 for a usage error. Every problem is one line on
stdout, each starting ``catalogue-check:``, so none reads as a workflow command: a line break in
a problem, which can quote the pull request's text (a duplicated id, say), is printed escaped.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from . import catalogue


def _read(
    path: Path, whose: str, reference: catalogue.Catalogue | None = None
) -> tuple[catalogue.Catalogue | None, list[str]]:
    try:
        return catalogue.load(catalogue._text(path, str(path)), str(path), reference), []
    except OSError as exc:
        return None, [f"{whose} catalogue cannot be read: {exc.strerror or exc}"]
    except catalogue.CatalogueError as exc:
        return None, [f"{whose} catalogue: {problem}" for problem in exc.problems]


def _one_line(problem: str) -> str:
    """``problem`` with every character ``str.splitlines`` breaks at written as its escape."""
    return "".join(ch if ch.splitlines() == [ch] else repr(ch)[1:-1] for ch in problem)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.ci catalogue-check", description=__doc__.splitlines()[0]
    )
    parser.add_argument(
        "--catalogue",
        type=Path,
        default=catalogue.REPO / catalogue.CATALOGUE,
        help="the pull request's catalogue",
    )
    parser.add_argument("--against", type=Path, required=True, help="main's catalogue")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) and exc.code else 2
    trusted, problems = _read(args.against, "main's")
    # Main's catalogue unread, the pull request's is still read alone, so its own faults show.
    _, more = _read(args.catalogue, "this pull request's", trusted)
    problems += more
    for problem in problems:
        print(f"catalogue-check: {_one_line(problem)}")
    if problems:
        return 1
    print("catalogue-check: main's catalogue has no objection to this one")
    return 0
