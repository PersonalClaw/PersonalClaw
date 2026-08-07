"""Fire-time liveness heuristics for `skip_if_active` (§3.5 / WF2AUT-9).

§3.5 asks for an OPTIONAL guard on a mutating trigger: "using cheap liveness heuristics (dirty
worktree, lockfiles, recent mtime) at fire time … a busy target defers rather than fires". This is
the distinct sibling of the named resource-slot gate (`claims.busy_slot`): a slot serializes two
trigger-fired runs against a NAMED resource one of them declared, while this defers a fire when the
working STATE it would act on looks like something else is touching it right now — a file that was
just written, a lock file, a dirty git worktree. The resource isn't a named slot; it's the state.

**Why it lives here and not in `claims.py`.** Claims answer "is a RUN of this trigger in flight"
from the sidecar claim store — a fact this system writes. Liveness answers "does the WORLD look
busy" from filesystem signals this system does not own. They are adjacent questions with opposite
data sources, and folding a git subprocess and mtime probes into the claim store's read path would
mix a pure-state reader with bounded external I/O. A small dedicated module keeps each honest.

**Three contracts, all load-bearing:**

* **Pure-ish and bounded.** Only `os.stat` mtime compares, `Path.exists()`, and ONE
  `git status --porcelain` with a hard timeout. No writes, anywhere — a liveness probe that touched
  disk would itself be the activity it screens for.
* **Never raises.** Every branch is wrapped; an exception becomes "not active". The caller walks a
  fire path that must not crash on a hand-edited `skip_if_active` block.
* **Fail-OPEN.** When a check cannot complete — git missing, a path unreadable, a timeout — the
  answer is NOT active (the fire proceeds). A stuck-closed liveness gate would defer an automation
  forever the moment its git binary went missing; the cost of the open direction is at most one run
  against a target that turned out to be busy, which the claim and resource-slot gates still bound.
  This matches the `slot` gate's own unreadable-store reasoning (`models.FAIL_OPEN_GATES`).

Each recognized `skip_if_active` key is EVALUATED here — a declared-but-unread sub-key would be the
"inert control" defect the plan's comment culture warns against, so there are none.
"""

from __future__ import annotations

import glob as _glob
import logging
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Default freshness window for `paths`, in seconds. A path modified within this many seconds of
#: `now` reads as "just touched". 300s (5 min) is long enough to catch an edit-in-progress and short
#: enough that a fire is not deferred by yesterday's change. Overridable per trigger via
#: `recent_secs`, because "recently" means different things for a notes folder and a build tree.
DEFAULT_RECENT_SECS = 300.0

#: Hard ceiling on the `git status` subprocess, in seconds. A git call that hangs must not hang the
#: whole fire path; the timeout expiry is treated as "cannot tell" → NOT active (fail-open).
_GIT_TIMEOUT_SECS = 3.0


def _resolve(target: Any, base_dir: Path | None) -> Path | None:
    """A declared path, resolved under `base_dir` when relative. None for an unusable value.

    Rooted at `base_dir` (the store's own root, as the claim store does) rather than a guess, so a
    liveness probe never wanders outside the tree the trigger belongs to and a test's `tmp_path`
    store can never make a probe stat the real home. An absolute declared path is honoured as-is.
    """
    text = str(target or "").strip()
    if not text:
        return None
    try:
        path = Path(text).expanduser()
        if not path.is_absolute() and base_dir is not None:
            path = Path(base_dir) / path
        return path
    except (OSError, ValueError):
        return None


def _recently_modified(path: Path, *, now: float, window: float) -> bool:
    """Whether `path` (or, for a dir, any entry one level down) changed within `window`.

    Never raises: an unreadable path reads as NOT modified (fail-open). Shallow for a directory — a
    full recursive walk is not "cheap", and the signal §3.5 wants is "did the target just change",
    which the directory's own mtime plus its immediate children answer without a deep scan.
    """
    try:
        if now - path.stat().st_mtime < window:
            return True
        if path.is_dir():
            for child in path.iterdir():
                try:
                    if now - child.stat().st_mtime < window:
                        return True
                except OSError:
                    continue
    except OSError:
        return False
    return False


def _paths_active(spec: dict[str, Any], *, now: float, base_dir: Path | None) -> str:
    """The `paths` heuristic: recent mtime under any declared glob. "" when not active.

    A glob is expanded against `base_dir` (or the root of an absolute pattern); each match is
    checked for a recent modification. A pattern that matches nothing is simply not active — an
    absent target cannot be busy.
    """
    patterns = spec.get("paths")
    if not isinstance(patterns, (list, tuple)) or not patterns:
        return ""
    try:
        window = float(spec.get("recent_secs") or DEFAULT_RECENT_SECS)
    except (TypeError, ValueError):
        window = DEFAULT_RECENT_SECS
    if window <= 0:
        return ""
    for pattern in patterns:
        text = str(pattern or "").strip()
        if not text:
            continue
        for match in _expand(text, base_dir):
            if _recently_modified(match, now=now, window=window):
                return f"{match} was modified within {int(window)}s; deferred until it settles"
    return ""


def _expand(pattern: str, base_dir: Path | None) -> list[Path]:
    """Every path a glob (or a plain path) names, never raising.

    An absolute pattern globs against the filesystem; a relative one globs under `base_dir`. A
    non-magic pattern that matches nothing but names a concrete existing path resolves directly, so
    the common "one named file" case works without the caller writing a glob.
    """
    try:
        root = Path(pattern).expanduser()
        if root.is_absolute():
            matches = [Path(p) for p in _glob.glob(str(root))]
        elif base_dir is not None:
            matches = list(Path(base_dir).glob(pattern))
        else:
            matches = []
    except (OSError, ValueError):
        return []
    if not matches:
        direct = _resolve(pattern, base_dir)
        if direct is not None:
            try:
                if direct.exists():
                    matches = [direct]
            except OSError:
                return []
    return matches


def _lockfiles_active(spec: dict[str, Any], *, base_dir: Path | None) -> str:
    """The `lockfiles` heuristic: any declared lock path exists. "" when not active."""
    locks = spec.get("lockfiles")
    if not isinstance(locks, (list, tuple)) or not locks:
        return ""
    for lock in locks:
        path = _resolve(lock, base_dir)
        if path is None:
            continue
        try:
            if path.exists():
                return f"lock file {path} is present; deferred until it clears"
        except OSError:
            continue  # fail-open: an unreadable lock path is not a reason to defer
    return ""


def _dirty_git_active(spec: dict[str, Any], *, base_dir: Path | None) -> str:
    """The `dirty_git` heuristic: a worktree with uncommitted changes. "" when not active.

    ONE bounded `git status --porcelain`. A non-empty output means the worktree is dirty. Any
    failure — git missing, not a repo, a timeout — reads as NOT dirty (fail-open): a broken check
    must not defer a fire forever, and "cannot tell" is not "busy".
    """
    target = spec.get("dirty_git")
    path = _resolve(target, base_dir)
    if path is None:
        return ""
    try:
        proc = subprocess.run(  # noqa: S603,S607 - fixed argv, no shell, bounded timeout
            ["git", "status", "--porcelain"],
            cwd=str(path),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECS,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if proc.returncode != 0:
        # Not a git worktree, or git refused — fail-open, exactly as an unregistered duty provider
        # allows the fire rather than blocking it.
        return ""
    if proc.stdout.strip():
        return f"git worktree {path} has uncommitted changes; deferred until it is clean"
    return ""


def is_target_active(
    skip_if_active: Any, *, now: float, base_dir: Path | str | None = None
) -> tuple[bool, str]:
    """Whether the target a `skip_if_active`-guarded fire would act on looks busy right now.

    Returns `(active, reason)`. `active` False (with reason "") when the guard is empty or off, when
    no heuristic fires, or when a check could not complete — the fail-OPEN direction. NEVER raises.

    The first firing heuristic wins and names itself, because the deferred ledger row needs one
    actionable reason, not a list: "git worktree … is dirty" tells the user what to do, while three
    stacked signals would leave them guessing which cleared it.
    """
    if not isinstance(skip_if_active, dict) or not skip_if_active:
        return (False, "")
    root: Path | None = Path(base_dir) if base_dir else None
    try:
        for reason in (
            _paths_active(skip_if_active, now=now, base_dir=root),
            _lockfiles_active(skip_if_active, base_dir=root),
            _dirty_git_active(skip_if_active, base_dir=root),
        ):
            if reason:
                return (True, reason)
    except Exception:  # noqa: BLE001 - a liveness probe must never crash the fire path
        logger.debug("skip_if_active probe failed; treating target as not active", exc_info=True)
        return (False, "")
    return (False, "")
