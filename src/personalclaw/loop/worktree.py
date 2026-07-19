"""Git worktree management for parallel task execution (unified loop engine).

When a loop's workspace is a git repo, the scheduler can run several READY tasks
of a phase at once — each in its own worktree (a linked checkout sharing the
repo's object store) on its own branch, so concurrent workers don't stomp each
other's files. When a phase's tasks all finish, their worktrees are merged back
to the base branch and removed. Vendor-neutral git infra shared by any kind that
parallelizes (code today; design later).

Capability detection decides parallel-vs-sequential: parallel needs a present
``git`` binary AND a workspace that is (or was just) a git repo. A brownfield
workspace with no git, or a missing git binary, falls back to sequential (one
task at a time in the workspace directly) — handled by the caller.

All git calls are best-effort and time-bounded; failures degrade to sequential
rather than raising.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
from typing import NamedTuple

logger = logging.getLogger(__name__)

_TIMEOUT = 30
# Worktrees live under PersonalClaw's OWN working dir — NOT inside the user's
# project workspace — so a parallel run never pollutes the user's checkout with a
# scratch dir (which would show in git status, risk being committed, or trip up
# their tooling). A git worktree is just a linked checkout: its working files can
# sit anywhere on disk while the branch + object store stay in the repo.
#
# When the code project is bound to a containing **Project** (Projects native
# entity), its worktrees live under ``projects/<project_id>/worktrees/<task_id>`` —
# so the spec's "the project directory holds the worktrees for the workspace it
# operates on" holds, and two projects on the SAME workspace get isolated worktree
# roots (one's teardown can't wipe the other's). Without a bound project we fall
# back to the legacy workspace-hash root so the location is still deterministic.
# The branch name mirrors the task id.
_BRANCH_PREFIX = "pclaw/task-"


def _worktrees_root(workspace: str, project_id: str = "") -> str:
    """The PClaw-owned directory holding this work's task worktrees — under
    ``config_dir()``, NOT under the workspace itself.

    Prefers a per-PROJECT root (``projects/<project_id>/worktrees``) so projects on
    one shared workspace stay isolated; falls back to a stable workspace-hash root
    when no project is bound. Deterministic in its args so every caller agrees on
    the location."""
    from personalclaw.config.loader import config_dir

    if project_id:
        return str(config_dir() / "projects" / project_id / "worktrees")
    key = hashlib.sha1(os.path.abspath(workspace).encode("utf-8")).hexdigest()[:12]
    return str(config_dir() / "code" / "worktrees" / key)


def git_available() -> bool:
    """True iff a ``git`` binary is on PATH."""
    return shutil.which("git") is not None


def _git(workspace: str, *args: str, timeout: int = _TIMEOUT) -> tuple[int, str]:
    """Run a git command in ``workspace``; return (returncode, combined output)."""
    try:
        p = subprocess.run(
            ["git", *args], cwd=workspace, capture_output=True,
            timeout=timeout, check=False,
        )
        out = (p.stdout or b"").decode("utf-8", "replace") + (p.stderr or b"").decode("utf-8", "replace")
        return p.returncode, out
    except (OSError, subprocess.SubprocessError) as e:
        return 1, str(e)


def is_git_repo(workspace: str) -> bool:
    """True iff ``workspace`` is inside a git working tree."""
    if not workspace or not os.path.isdir(workspace):
        return False
    rc, out = _git(workspace, "rev-parse", "--is-inside-work-tree")
    return rc == 0 and out.strip() == "true"


def can_parallelize(workspace: str) -> bool:
    """Whether parallel worktree execution is possible for this workspace: git is
    installed and the workspace is a git repo. The caller falls back to sequential
    single-worker execution when this is False."""
    return bool(workspace) and git_available() and is_git_repo(workspace)


def base_branch(workspace: str) -> str:
    """The repo's current branch (the merge target for task worktrees). Falls back
    to 'main' if it can't be resolved (e.g. an unborn HEAD on a fresh init)."""
    rc, out = _git(workspace, "symbolic-ref", "--short", "HEAD")
    name = out.strip()
    return name if (rc == 0 and name) else "main"


# A task id is filename-safe (mirrors store._TASK_ID_RE): alphanumerics, '_' and '-'
# only. It's the LAST path segment of a worktree dir + part of a branch ref, so a
# stray '../' or '/' would traverse out of the worktrees root / forge a ref. Task ids
# come from the Tasks store (generated 't-<hex>'), so this is defense-in-depth, not a
# live hole — but a path-building primitive must never trust its input blindly.
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _safe_task_id(task_id: str) -> bool:
    return bool(_TASK_ID_RE.match(task_id or ""))


def worktree_path(workspace: str, task_id: str, project_id: str = "") -> str:
    """Absolute path of a task's worktree — under PClaw's working dir, not the
    workspace (see :func:`_worktrees_root`). ``project_id`` roots it under the
    containing project when set. Raises ``ValueError`` on a non-filename-safe
    ``task_id`` (the public ops catch it + treat the op as a no-op/failure) so a
    traversal id can never escape the worktrees root."""
    if not _safe_task_id(task_id):
        raise ValueError(f"unsafe task_id for worktree path: {task_id!r}")
    return os.path.join(_worktrees_root(workspace, project_id), task_id)


def branch_name(task_id: str) -> str:
    return f"{_BRANCH_PREFIX}{task_id}"


def add_worktree(workspace: str, task_id: str, project_id: str = "") -> str | None:
    """Create (idempotently) a worktree + branch for ``task_id``; return its path,
    or None on failure (caller falls back). Requires at least one commit on HEAD;
    on a fresh repo the caller makes an initial commit first (see ensure_base_commit)."""
    if not _safe_task_id(task_id):
        logger.warning("worktree add refused — unsafe task_id %r", task_id)
        return None
    path = worktree_path(workspace, task_id, project_id)
    if os.path.isdir(path):
        return path  # already exists (resume / re-schedule)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    branch = branch_name(task_id)
    # -B resets the branch if it somehow exists; -f tolerates a stale registration.
    rc, out = _git(workspace, "worktree", "add", "-f", "-B", branch, path, "HEAD")
    if rc != 0:
        logger.debug("worktree add failed for %s: %s", task_id, out.strip()[:200])
        return None
    return path


def ensure_base_commit(workspace: str) -> bool:
    """Guarantee HEAD points at a commit so worktrees can branch from it. A freshly
    ``git init``'d repo has an unborn HEAD; stage + commit whatever's there (or an
    empty commit) so worktrees work. Returns True if HEAD has a commit afterward."""
    rc, _ = _git(workspace, "rev-parse", "--verify", "HEAD")
    if rc == 0:
        return True  # already has a commit
    _git(workspace, "add", "-A")
    rc, _ = _git(
        workspace, "-c", "user.name=PersonalClaw", "-c", "user.email=code@personalclaw.local",
        "commit", "-q", "--allow-empty", "-m", "Initial commit (PersonalClaw Code)",
    )
    rc2, _ = _git(workspace, "rev-parse", "--verify", "HEAD")
    return rc2 == 0


class MergeResult(NamedTuple):
    """Outcome of merging a task worktree back. ``ok`` = clean merge. On failure,
    ``conflicts`` lists the conflicted files (empty for a non-conflict git error) —
    captured BEFORE the merge is aborted, since the abort clears the unmerged state
    and a post-abort ``conflict_paths`` would always read empty (the bug this fixes:
    the caller would misreport every real conflict as a 'git error')."""
    ok: bool
    conflicts: list[str] = []

    def __bool__(self) -> bool:  # back-compat: callers/tests can still treat it as a bool
        return self.ok


def merge_worktree(workspace: str, task_id: str, project_id: str = "") -> MergeResult:
    """Merge a finished task's branch back into the base branch, then remove its
    worktree. Returns ``MergeResult(ok=True)`` on a clean merge. A conflict/failure
    leaves the worktree in place (so it's not lost) and returns ``ok=False`` with the
    conflicted paths (if any) — the caller surfaces an accurate message."""
    if not _safe_task_id(task_id):
        logger.warning("worktree merge refused — unsafe task_id %r", task_id)
        return MergeResult(ok=False, conflicts=[])
    branch = branch_name(task_id)
    # commit any uncommitted work in the worktree first
    wt = worktree_path(workspace, task_id, project_id)
    if os.path.isdir(wt):
        _git(wt, "add", "-A")
        _git(
            wt, "-c", "user.name=PersonalClaw", "-c", "user.email=code@personalclaw.local",
            "commit", "-q", "-m", f"task {task_id}: work",
        )
    # merge into base from the main workspace checkout. A non-fast-forward merge
    # (the common case — multiple task branches diverge from base) creates a MERGE
    # COMMIT, which needs a committer identity; supply the same isolated identity
    # used elsewhere so a freshly git-init'd workspace with no user/email configured
    # (e.g. a clean container) doesn't fail the merge + falsely wedge as a conflict.
    rc, out = _git(
        workspace, "-c", "user.name=PersonalClaw", "-c", "user.email=code@personalclaw.local",
        "merge", "--no-edit", branch,
    )
    if rc != 0:
        # rc != 0 is NOT necessarily a conflict — only abort an in-progress merge
        # (MERGE_HEAD present). A non-conflict failure (e.g. a git error) left no
        # merge to abort, and `merge --abort` would itself error. Capture the
        # conflicted paths BEFORE aborting — the abort clears them, so reading them
        # afterward (in the caller) would always be empty → every conflict misreported.
        conflicts = conflict_paths(workspace)
        logger.info("worktree merge %s for %s: %s",
                    "conflict" if conflicts else "failed", task_id, out.strip()[:200])
        if conflicts:
            _git(workspace, "merge", "--abort")
        return MergeResult(ok=False, conflicts=conflicts)
    remove_worktree(workspace, task_id, project_id)
    return MergeResult(ok=True, conflicts=[])


def conflict_paths(workspace: str) -> list[str]:
    """Files with unmerged (conflict) entries in ``workspace``, or [] if none / not
    mid-merge. Used to tell a genuine merge CONFLICT apart from a non-conflict merge
    failure so the caller surfaces an accurate message + only aborts a real merge."""
    rc, out = _git(workspace, "diff", "--name-only", "--diff-filter=U")
    if rc != 0:
        return []
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def branch_exists(workspace: str, task_id: str) -> bool:
    """True iff this task's branch still exists in the repo. A done task whose
    branch lingers (its merge previously conflicted/failed and was aborted) still
    has unmerged work — the scheduler retries the merge on resume rather than
    skipping it past forever once its worker session is gone."""
    if not _safe_task_id(task_id):
        return False
    rc, _ = _git(workspace, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch_name(task_id)}")
    return rc == 0


def remove_worktree(workspace: str, task_id: str, project_id: str = "") -> None:
    """Remove a task's worktree + delete its branch (best-effort cleanup)."""
    if not _safe_task_id(task_id):
        return
    path = worktree_path(workspace, task_id, project_id)
    _git(workspace, "worktree", "remove", "--force", path)
    _git(workspace, "branch", "-D", branch_name(task_id))
    # if the worktree dir lingers (e.g. remove failed), drop it so it doesn't pile up
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)


def cleanup_all(workspace: str, project_id: str = "") -> None:
    """Remove the whole worktrees dir + prune registrations (project teardown).

    With ``project_id`` set, only THIS project's worktree root is swept — so tearing
    down one project on a shared workspace can't wipe another's worktrees. The
    trailing branch sweep is still global (pclaw/task-* branches live in the one
    shared repo) but only removes branches whose worktree we just dropped."""
    if not workspace:
        return
    # Explicitly remove each registered worktree under our (PClaw-owned) dir first —
    # `prune` only drops STALE entries, not active ones, so an in-use worktree would
    # linger.
    root = _worktrees_root(workspace, project_id)
    if os.path.isdir(root):
        for name in os.listdir(root):
            _git(workspace, "worktree", "remove", "--force", os.path.join(root, name))
            _git(workspace, "branch", "-D", branch_name(name))
    _git(workspace, "worktree", "prune")
    # Sweep ANY remaining pclaw/task-* branches — a branch whose worktree dir was
    # already removed (merged, or a prior failed branch-delete) wouldn't be caught by
    # the per-dir loop above, and would otherwise be left orphaned in a brownfield
    # user's repo after the project is deleted. ONLY in legacy (no project_id) mode:
    # with a per-project worktree root, a shared workspace may host OTHER projects'
    # branches, and a global sweep would delete their in-flight work — the per-dir
    # loop above already dropped this project's branches.
    if not project_id:
        rc, out = _git(workspace, "for-each-ref", "--format=%(refname:short)", f"refs/heads/{_BRANCH_PREFIX}*")
        if rc == 0:
            for ref in (ln.strip() for ln in out.splitlines() if ln.strip()):
                _git(workspace, "branch", "-D", ref)
    if os.path.isdir(root):
        shutil.rmtree(root, ignore_errors=True)
