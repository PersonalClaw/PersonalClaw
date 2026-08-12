"""Detector for the real-home rail: nothing under the developer's own
``~/.personalclaw`` may be created, modified or grown by a test run.

Why this exists as its own module rather than inline in ``conftest.py``: the
detector has to be **drivable against a fake root** so the rail can be proven
non-vacuous (``tests/test_real_home_guard.py`` points it at a throwaway tree and
asserts it fires). A detector that can only ever be exercised by the thing it is
guarding is indistinguishable from one that never fires.

Detection shape — one walk, no "before" map:

``pytest_sessionstart`` records a nanosecond timestamp; ``pytest_sessionfinish``
walks the real home once and reports every entry whose ``st_mtime_ns`` is newer
than that timestamp. That catches all three shapes the rail cares about:

* a file created during the run (its mtime is necessarily newer),
* a file modified or appended to in place (ditto — this is the one a
  directory-mtime check cannot see),
* an entry deleted or renamed (the surviving parent directory's mtime moves).

The alternative — snapshot the tree at start, snapshot again at finish, diff —
costs two full walks of a real home that already holds >100k files, i.e. seconds
of tax on *every* ``pytest`` invocation including single-file runs. Startup here
is O(1) and only the finish path pays for a walk.

The detector never opens, reads, writes or creates anything under the root: it
uses ``os.scandir`` + ``lstat`` only, does not follow symlinks out of the tree,
and treats an absent root as "nothing to compare" rather than as a failure (a
fresh CI container has no ``~/.personalclaw`` at all).
"""

import os
from dataclasses import dataclass
from pathlib import Path

#: The developer's REAL home, resolved once at import time — i.e. before any test
#: has had a chance to patch ``Path.home`` or ``$HOME``. Everything else in the
#: suite is expected to stay out of it.
REAL_HOME = Path.home() / ".personalclaw"

#: Paths (relative to the root) that are permitted to change during a run.
#: MUST stay empty unless a residue is named here individually, with the reason,
#: and recorded in the owning plan's execution log. A blanket allowance — a
#: prefix, a glob, "whatever leaks today" — is not a rail; it is a baseline that
#: silently ratifies the next leak. Populated set members are compared exactly.
ALLOWED_RESIDUE: frozenset[str] = frozenset()


@dataclass(frozen=True)
class HomeChange:
    """One offending entry, named so the next reader can act on it."""

    path: str
    kind: str
    size: int

    def __str__(self) -> str:
        return f"{self.kind:<20} {self.path} ({self.size} bytes)"


def _birthtime_ns(st: os.stat_result) -> int | None:
    """Creation time in ns where the platform reports one (macOS/BSD), else None."""
    for attr in ("st_birthtime_ns", "st_birthtime"):
        value = getattr(st, attr, None)
        if value is None:
            continue
        return int(value) if attr.endswith("_ns") else int(value * 1_000_000_000)
    return None


def scan_changes(root: Path, since_ns: int) -> list[HomeChange]:
    """Return every entry under ``root`` whose mtime is newer than ``since_ns``.

    An absent (or non-directory) ``root`` yields ``[]`` — nothing to compare.
    Symlinks are stat'd but never followed, so the walk cannot escape the tree.
    """
    if not root.is_dir():
        return []

    changes: list[HomeChange] = []
    stack: list[str] = [str(root)]
    prefix_len = len(str(root)) + 1
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            is_dir = entry.is_dir(follow_symlinks=False)
            if is_dir:
                stack.append(entry.path)
            if st.st_mtime_ns <= since_ns:
                continue
            rel = entry.path[prefix_len:]
            if rel in ALLOWED_RESIDUE:
                continue
            if is_dir:
                kind = "dir-entries-changed"
            else:
                birth = _birthtime_ns(st)
                kind = "created" if birth is not None and birth > since_ns else "modified"
            changes.append(HomeChange(path=rel, kind=kind, size=st.st_size))
    changes.sort(key=lambda c: c.path)
    return changes


def format_report(root: Path, changes: list[HomeChange], *, limit: int = 40) -> str:
    """Render the rail's verdict. Always states which case it is in."""
    if not root.exists():
        return (
            f"real-home rail: {root} does not exist — nothing to compare, "
            "so nothing to report (this is the expected state in CI)."
        )
    if not changes:
        return f"real-home rail: {root} unchanged by this run."

    lines = [
        f"real-home rail FAILED: {len(changes)} entries under {root} changed during this run.",
        "",
        "The test suite must never touch the developer's real gateway home. Every",
        "subsystem resolves its home through a seam a test can point at tmp_path;",
        "fix the leak at that seam (see tests/conftest.py::_isolate_real_home_writers)",
        "rather than adding the path below to ALLOWED_RESIDUE.",
        "",
        "If you have a gateway running against the real home, that is the other cause —",
        "and it is also a defect: the dev gateway is documented to run against an",
        "isolated PERSONALCLAW_HOME, never ~/.personalclaw.",
        "",
    ]
    lines += [f"  {change}" for change in changes[:limit]]
    if len(changes) > limit:
        lines.append(f"  ... and {len(changes) - limit} more")
    return "\n".join(lines)
