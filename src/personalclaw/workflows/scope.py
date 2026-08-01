"""Filesystem write-scope enforcement — the authoritative layer (WF2-R19).

The frozen-region invariant protects spec state and the effect ledger protects external
API effects. Nothing else stops a `stage` subagent writing or deleting files outside its
intended scope, and this platform has already been bitten by that failure class: the
destructive-test-isolation incident deleted the user's real bound model.

Three enforcement layers exist by design, and this module is the middle one:

* **advisory** — the prompt tells the agent its scope (`_SYSTEM_PREFIX`, `validate_cwd`).
  All that exists today, and a prompt is not an enforcement mechanism.
* **authoritative** — snapshot the tree before the node, diff after, flag what escaped.
  That is this module.
* **OS seatbelt** — a future sandbox provider receives `allowed_write_paths` as policy.

Deliberately DETECTIVE rather than preventive: a node's writes are its own work, and
intercepting every syscall would need a sandbox we do not yet have. Detection with a
typed `scope_violation` outcome is what makes the escape visible in the ledger instead
of silent, and `warn` vs `reject` is the user's call because a warn-only default on an
existing template is the difference between a useful signal and a broken run.

**A snapshot is not free.** It walks the declared roots, so the roots matter: the
default is the run workspace, never the whole home. Walking `$HOME` on every node would
cost more than the node.
"""

from __future__ import annotations

import fnmatch
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Names never descended into when snapshotting. These churn constantly and are never
#: what a scope violation is about — walking them would make every diff noisy and slow.
_PRUNE_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".next",
        ".turbo",
    }
)

#: Cap on entries per snapshot. A runaway tree must not turn scope checking into the
#: dominant cost of a run; hitting the cap degrades to "could not verify", never to a
#: false pass.
MAX_SNAPSHOT_ENTRIES = 50_000


class ScopeMode:
    """What to do when a write escapes. `WARN` keeps the node's outcome and records the
    violation; `REJECT` flips the node to `scope_violation`."""

    WARN = "warn"
    REJECT = "reject"


@dataclass
class Snapshot:
    """Paths → (mtime_ns, size). Compared by value, so a rewrite that preserves size is
    still caught via mtime, and a same-mtime size change is caught via size."""

    entries: dict[str, tuple[int, int]] = field(default_factory=dict)
    truncated: bool = False

    def __len__(self) -> int:
        return len(self.entries)


@dataclass
class ScopeReport:
    """The diff verdict. `violations` are the paths that escaped every allowed glob."""

    created: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    #: True when a snapshot was truncated — the diff is then INCOMPLETE and must not be
    #: read as a clean pass.
    incomplete: bool = False

    @property
    def clean(self) -> bool:
        return not self.violations

    @property
    def changed(self) -> list[str]:
        return sorted({*self.created, *self.modified, *self.deleted})

    def to_dict(self) -> dict[str, Any]:
        return {
            "created": list(self.created),
            "modified": list(self.modified),
            "deleted": list(self.deleted),
            "violations": list(self.violations),
            "incomplete": self.incomplete,
        }


# ── path normalization ───────────────────────────────────────────────────────


def normalize(path: str | os.PathLike[str]) -> str:
    """Resolve to an absolute real path, following symlinks.

    Symlinks are resolved rather than compared as-written: a link inside the workspace
    pointing at `~/.ssh` is exactly the escape this check exists to catch, and comparing
    the link's own path would call it in-scope.
    """
    try:
        return str(Path(path).resolve())
    except (OSError, RuntimeError):
        # A broken link or a resolution loop: fall back to a lexical normalization so a
        # weird path is still comparable rather than crashing the check.
        return os.path.normpath(os.path.abspath(str(path)))


def in_scope(path: str, allowed: list[str]) -> bool:
    """Is `path` inside any allowed glob or directory prefix?

    A plain directory in `allowed` covers its whole subtree — the common case is "the
    workspace", and forcing every caller to write `dir/**` invites the mistake of
    writing `dir/*` and silently missing nested files. `..` in a candidate cannot
    survive, because both sides are normalized before comparison.
    """
    if not allowed:
        return False
    target = normalize(path)
    for raw in allowed:
        pattern = str(raw or "").strip()
        if not pattern:
            continue
        if any(ch in pattern for ch in "*?["):
            # Glob form: match the normalized pattern against the normalized path, and
            # also allow a `**` prefix pattern to cover the subtree beneath it.
            norm_pattern = os.path.normpath(os.path.abspath(os.path.expanduser(pattern)))
            if fnmatch.fnmatch(target, norm_pattern):
                return True
            root = norm_pattern.split("*", 1)[0].rstrip(os.sep)
            if root and (target == root or target.startswith(root + os.sep)):
                return True
            continue
        base = normalize(os.path.expanduser(pattern))
        if target == base or target.startswith(base + os.sep):
            return True
    return False


# ── snapshot + diff ──────────────────────────────────────────────────────────


def snapshot(roots: list[str]) -> Snapshot:
    """Walk the roots recording (mtime_ns, size) per file.

    Only the DECLARED roots are walked. Snapshotting the whole home to catch a write
    anywhere would cost more than the node it guards; the roots are the workspace plus
    whatever the node declared, and a write outside all of them shows up as a violation
    on the paths we do watch or not at all — a limitation stated here rather than
    papered over.
    """
    snap = Snapshot()
    for raw in roots:
        root = normalize(os.path.expanduser(str(raw or "")))
        if not root or not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = [d for d in dirnames if d not in _PRUNE_DIRS]
            for name in filenames:
                full = os.path.join(dirpath, name)
                try:
                    st = os.stat(full, follow_symlinks=False)
                except OSError:
                    continue  # vanished mid-walk: the diff will read it as deleted
                snap.entries[full] = (st.st_mtime_ns, st.st_size)
                if len(snap.entries) >= MAX_SNAPSHOT_ENTRIES:
                    snap.truncated = True
                    logger.warning(
                        "write-scope snapshot truncated at %d entries under %s",
                        MAX_SNAPSHOT_ENTRIES,
                        root,
                    )
                    return snap
    return snap


def diff(before: Snapshot, after: Snapshot, allowed: list[str]) -> ScopeReport:
    """Compare two snapshots and classify every change against the allowed scope."""
    report = ScopeReport(incomplete=before.truncated or after.truncated)
    for path, meta in after.entries.items():
        prior = before.entries.get(path)
        if prior is None:
            report.created.append(path)
        elif prior != meta:
            report.modified.append(path)
    for path in before.entries:
        if path not in after.entries:
            report.deleted.append(path)
    report.created.sort()
    report.modified.sort()
    report.deleted.sort()
    report.violations = [p for p in report.changed if not in_scope(p, allowed)]
    return report


def allowed_write_paths(node_config: dict[str, Any], workspace: str) -> list[str]:
    """The node's declared scope, defaulting to the run workspace.

    An empty declaration is NOT "allow everything": it is "the workspace only". A node
    that genuinely needs to write elsewhere says so, which is what makes the declaration
    reviewable.
    """
    declared = (node_config or {}).get("allowed_write_paths")
    paths: list[str] = []
    if isinstance(declared, list):
        paths = [str(p) for p in declared if str(p or "").strip()]
    elif isinstance(declared, str) and declared.strip():
        paths = [declared.strip()]
    if workspace:
        paths.append(workspace)
    return paths


def watch_roots(node_config: dict[str, Any], workspace: str) -> list[str]:
    """The trees to SNAPSHOT — necessarily a superset of what is allowed.

    This distinction is the whole check. Snapshotting only the allowed paths would make a
    violation undetectable by construction: an escape lands outside the allowed set, so
    nothing outside it is ever observed. The watched set therefore reaches ONE level above
    the workspace, which is where the realistic escapes land — a sibling project
    directory, or the parent the workspace sits in.

    It is deliberately NOT `$HOME` or `/`. Walking those on every node would cost more
    than the node, and the honest statement of the limitation is: a write to an unrelated
    far-away tree is caught by the OS-seatbelt layer, not by this one. `watch_roots` is
    overridable for the case where a node legitimately writes to a known distant path.
    """
    declared = (node_config or {}).get("watch_roots")
    if isinstance(declared, list) and declared:
        return [str(p) for p in declared if str(p or "").strip()]
    if isinstance(declared, str) and declared.strip():
        return [declared.strip()]
    roots = list(allowed_write_paths(node_config, workspace))
    if workspace:
        parent = os.path.dirname(normalize(workspace))
        # Never widen to the filesystem root: a workspace at `/x` would otherwise walk `/`.
        if parent and parent != os.sep:
            roots.append(parent)
    return roots


def scope_mode(node_config: dict[str, Any], default: str = ScopeMode.WARN) -> str:
    """`warn` (record and continue) or `reject` (flip to `scope_violation`)."""
    raw = str((node_config or {}).get("write_scope_mode", "") or "").strip().lower()
    if raw in (ScopeMode.WARN, ScopeMode.REJECT):
        return raw
    return default if default in (ScopeMode.WARN, ScopeMode.REJECT) else ScopeMode.WARN


def enforces_scope(node_config: dict[str, Any]) -> bool:
    """Whether a node opts into scope checking.

    Opt-IN, because the snapshot is real work: a fan-out of a hundred fast transforms
    should not each walk a tree. `stage` and command-running `action` nodes are what
    write files, and those are what declare it.
    """
    cfg = node_config or {}
    return bool(cfg.get("allowed_write_paths") or cfg.get("write_scope_mode"))
