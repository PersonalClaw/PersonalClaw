"""Detector for the real-home rail: nothing under the developer's own
``~/.personalclaw`` may be created, modified or grown by a test run.

Why this exists as its own module rather than inline in ``conftest.py``: the
detector has to be **drivable against a fake root** so the rail can be proven
non-vacuous (``tests/test_real_home_guard.py`` points it at a throwaway tree and
asserts it fires). A detector that can only ever be exercised by the thing it is
guarding is indistinguishable from one that never fires.

Detection shape — one walk, no "before" map:

``pytest_sessionstart`` records a nanosecond timestamp; ``pytest_sessionfinish``
walks the real home once and reports every entry whose ``st_mtime_ns`` **or**
``st_ctime_ns`` is newer than that timestamp. That catches all four shapes the
rail cares about:

* a file created during the run (its mtime is necessarily newer),
* a file modified or appended to in place (ditto — this is the one a
  directory-mtime check cannot see),
* an entry deleted or renamed (the surviving parent directory's mtime moves),
* a **metadata-preserving** write — ``shutil.copy2``, ``shutil.copystat``, a
  bare ``os.utime`` — which back-dates the new file's mtime to the source's and
  is therefore invisible to an mtime-only check.

Why ctime, and what it costs. That fourth shape is not hypothetical: a config
migration backed the user's ``config.json`` aside with ``shutil.copy2`` before
rewriting it, so CI reported *one* changed entry when *two* things changed —
the ``.bak`` carried the original's mtime and looked older than the session.
``st_ctime_ns`` is the inode-change time; userspace cannot set it, and
``utime()`` itself bumps it, so it is the one field a metadata-preserving copy
cannot forge. The cost of widening to it is **precision, not performance**:
``entry.stat()`` already returns ctime, so there is no extra syscall and the
walk stays single-pass. But ctime also moves for a metadata-only touch —
``chmod``, ``chown``, a hardlink count change, a rename — none of which alter a
byte. Those are reported too, deliberately: the rail's contract is that the
suite leaves the developer's home *alone*, and re-permissioning their config is
not leaving it alone. Reads do not move ctime (only atime), so simply walking or
reading the real home cannot red the rail.

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


def _touched_ns(st: os.stat_result) -> int:
    """The latest moment this inode was touched at all.

    ``max(mtime, ctime)``: mtime alone misses a metadata-preserving write (``copy2``
    back-dates it), and ctime alone is not guaranteed to be >= mtime on a filesystem
    that lets a write land without an inode update. Taking the max means a change has
    to beat BOTH clocks to hide.
    """
    return max(st.st_mtime_ns, st.st_ctime_ns)


def scan_changes(root: Path, since_ns: int) -> list[HomeChange]:
    """Return every entry under ``root`` touched more recently than ``since_ns``.

    "Touched" is ``max(mtime, ctime)`` — see the module docstring for why ctime is in
    the comparison and what widening to it costs.

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
            if _touched_ns(st) <= since_ns:
                continue
            rel = entry.path[prefix_len:]
            if rel in ALLOWED_RESIDUE:
                continue
            if is_dir:
                kind = "dir-entries-changed"
            else:
                birth = _birthtime_ns(st)
                if birth is not None and birth > since_ns:
                    kind = "created"
                elif st.st_mtime_ns > since_ns:
                    kind = "modified"
                else:
                    # ctime moved but mtime did not: a metadata-preserving write
                    # (``copy2``/``copystat``/``utime``) or a metadata-only touch.
                    # Named distinctly because the fix differs — you are looking for
                    # a copy, not for a writer.
                    kind = "metadata-preserving-write"
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
