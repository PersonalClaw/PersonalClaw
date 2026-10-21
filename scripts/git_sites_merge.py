#!/usr/bin/env python3
"""git's merge driver for the site baselines: a three-way merge of two sets of lines.

A site baseline (``tests/site_baseline.py``) lists one site per line, and the order of its lines
means nothing, so two branches' copies of one merge as sets, against the copy they started from:
every line either side added is kept, and every line either side deleted or replaced goes. git's
``union`` driver merged them as text instead, and kept both sides of every hunk both branches had
changed, so a line one branch deleted or replaced beside a line the other changed came back, with
no conflict: a rebase brought back a ceiling's old row next to its new one.

The merge stops as a conflict when the result lists one site twice, since only a person can say
which of two lines for a site is right: both branches changed its line, or both added one. The
file then holds both lines, and the driver names the file and the site; keep one line, and the
rails say whether it was the right one. A line that is not a JSON object naming a ``site`` stands
for itself, so it is kept or dropped as a whole and never taken for another site's line.

The result is sorted by site, as ``python tests/site_baseline.py --prune`` writes a baseline.

``.gitattributes`` assigns the driver to every ``*.sites.jsonl`` (``merge=sites-set``), and
``npm run hooks:install`` (``scripts/install_git_hooks.sh``) configures it in a clone::

    git config merge.sites-set.driver "python3 scripts/git_sites_merge.py %O %A %B %P"

git runs it from the top of the work tree with the ``python3`` on ``PATH``, not the project's
environment, so it imports only the standard library. A clone without that configuration merges a
baseline as git merges text: a hunk both branches changed stops as a conflict to resolve by hand,
and no line comes back unseen.

Usage, as git runs it: ``git_sites_merge.py BASE OURS THEIRS [PATH]``. The result is written to
OURS; the exit is 0 for a clean merge and 1 for a conflict, as git reads a merge driver's.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

#: The exit for a clean merge, and for one that stops as a conflict.
CLEAN, CONFLICT = 0, 1

USAGE = "usage: git_sites_merge.py BASE OURS THEIRS [PATH]"


def lines(path: str | Path) -> list[str]:
    """The non-blank lines of the file at *path*, without their line breaks."""
    text = Path(path).read_text(encoding="utf-8")
    return [line for line in text.splitlines() if line.strip()]


def site(line: str) -> str | None:
    """The ``site`` a line lists; ``None`` for a line that is not a JSON object naming one."""
    try:
        fields = json.loads(line)
    except ValueError:
        return None
    name = fields.get("site") if isinstance(fields, dict) else None
    return name if isinstance(name, str) else None


def _order(line: str) -> tuple[str, str]:
    return (site(line) or line, line)


def merge(base: list[str], ours: list[str], theirs: list[str]) -> tuple[list[str], list[str]]:
    """The lines of the merge of *ours* and *theirs* from *base*, sorted by site, and every site
    the merge lists more than once, sorted."""
    deleted = (set(base) - set(ours)) | (set(base) - set(theirs))
    kept = dict.fromkeys(line for line in [*ours, *theirs] if line not in deleted)
    merged = sorted(kept, key=_order)
    listed = Counter(site(line) for line in merged)
    twice = sorted(name for name, count in listed.items() if name is not None and count > 1)
    return merged, twice


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) not in (3, 4):
        print(USAGE, file=sys.stderr)
        return 2
    base, ours, theirs = (lines(path) for path in args[:3])
    label = args[3] if len(args) == 4 else args[1]
    merged, twice = merge(base, ours, theirs)
    Path(args[1]).write_text("".join(line + "\n" for line in merged), encoding="utf-8")
    for name in twice:
        print(
            f"sites-set: {label} lists the site {name} twice, since both sides changed or added "
            "its line: keep one",
            file=sys.stderr,
        )
    return CONFLICT if twice else CLEAN


if __name__ == "__main__":
    sys.exit(main())
