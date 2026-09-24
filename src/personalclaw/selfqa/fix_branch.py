"""Optional fix-branch stage — SV-10 §3.2 step 6, Success Criterion #8.

On a confirmed finding, and only when ``agent.self_qa.fix_branch_enabled`` is on, the companion
opens a branch named ``pclaw/selfqa-<sha8>`` off the commit under test. A coder can then propose a
diff on it; the human reviews and decides. Two properties are the whole point and are enforced
here rather than trusted to a prompt:

**It is created only when enabled.** The flag is a decision about writing code unattended, so a
disabled companion creates nothing — :func:`create_fix_branch` returns a result whose ``branch``
name is still computed (so a caller can show what *would* be created) but ``created`` is False.

**It is never merged and never pushed.** This module runs ``git branch`` and read-only
``rev-parse`` and nothing else — there is no push verb anywhere in it, so "we forgot and pushed"
is not a failure mode a reviewer has to check for. The branch name lands in the Task
(:class:`personalclaw.selfqa.findings.ScenarioFinding.fix_branch`); a human pushes or merges it, or
does neither.

The branch name follows the plan's ``<sha8>`` convention, not the full sha, so it is short enough
to read in a Task title and matches the branch a reviewer greps for. The git runner mirrors
:mod:`personalclaw.loop.worktree`'s discipline — a fixed argv, no shell, a hex-validated ref, a
time bound, and the ``build`` resource ceiling delivered via ``spawn_shim_argv`` — because this is
write-capable git driven by an unattended run, exactly the class that machinery bounds.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

#: The plan's branch convention. ``<sha8>`` — the first 8 hex chars of the commit under test.
BRANCH_PREFIX = "pclaw/selfqa-"
_SHA8_LEN = 8

#: A commit ref this module will hand to git — hex only, so nothing option-shaped reaches git and
#: the derived branch name cannot forge a ref. At least 8 chars because the branch name is the
#: first 8; the same discipline `selfqa/triage.py` applies to a model-reachable ref.
_SHA_RE = re.compile(r"[0-9a-fA-F]{8,64}\Z")

_GIT_TIMEOUT = 30


def fix_branch_name(sha: str) -> str:
    """The ``pclaw/selfqa-<sha8>`` branch name for a commit. Pure — no validation, no git."""
    return f"{BRANCH_PREFIX}{(sha or '')[:_SHA8_LEN]}"


@dataclass(frozen=True)
class FixBranchResult:
    """The outcome of a fix-branch request.

    ``branch`` is always the name that would be (or was) created, so a caller can surface it even
    when nothing happened. ``created`` is True only when this call made the branch;
    ``already_existed`` distinguishes an idempotent re-request from a fresh creation, and
    ``reason`` explains any non-creation (disabled, bad ref, no git, git error).
    """

    created: bool
    branch: str
    reason: str = ""
    already_existed: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "created": self.created,
            "branch": self.branch,
            "reason": self.reason,
            "already_existed": self.already_existed,
        }


def git_available() -> bool:
    """True iff a ``git`` binary is on PATH."""
    return shutil.which("git") is not None


def _git(repo: Path, *args: str) -> tuple[int, str]:
    """Run one git command in ``repo`` under the build resource ceiling. Returns ``(rc, output)``.

    Mirrors :func:`personalclaw.loop.worktree._git`: the ``build`` ceiling is delivered by the
    post-exec shim via ``spawn_shim_argv`` (argv-prepend, not ``preexec_fn``), the argv is fixed,
    there is no shell, and stderr is merged into the returned output so a caller can log the
    failure. Never raises.
    """
    from personalclaw.sandbox import PROFILE_BUILD, spawn_shim_argv

    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell, build-ceiling git
            spawn_shim_argv(["git", "-C", str(repo), *args], PROFILE_BUILD),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _is_git_repo(repo: Path) -> bool:
    """True iff ``repo`` is inside a git working tree."""
    if not repo.is_dir():
        return False
    rc, out = _git(repo, "rev-parse", "--is-inside-work-tree")
    return rc == 0 and out.strip() == "true"


def _branch_exists(repo: Path, branch: str) -> bool:
    """True iff ``branch`` already exists in ``repo`` (an idempotent re-request)."""
    rc, _ = _git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
    return rc == 0


def create_fix_branch(repo: Path | str, sha: str, *, enabled: bool) -> FixBranchResult:
    """Create ``pclaw/selfqa-<sha8>`` off ``sha`` in ``repo`` — only when ``enabled``.

    Creates the branch with ``git branch <name> <sha>``: it does NOT check the branch out and does
    NOT push it. When ``enabled`` is False the branch is not created and the reason says so; the
    ``branch`` field still carries the name that would have been used. A ref that is not plain hex
    is refused before it reaches git, and a non-git or git-less ``repo`` degrades to a typed
    reason rather than an exception.

    Idempotent: if the branch already exists, nothing is created and ``already_existed`` is True —
    a resumed run must not fail because its branch is already there.
    """
    branch = fix_branch_name(sha)

    if not enabled:
        return FixBranchResult(created=False, branch=branch, reason="fix_branch_enabled is off")
    if not _SHA_RE.match(sha or ""):
        return FixBranchResult(
            created=False,
            branch=branch,
            reason="refused: the commit ref is not a hex sha, so no branch was created",
        )
    if not git_available():
        return FixBranchResult(created=False, branch=branch, reason="git is not available")

    root = Path(repo)
    if not _is_git_repo(root):
        return FixBranchResult(
            created=False, branch=branch, reason=f"{root} is not a git repository"
        )

    if _branch_exists(root, branch):
        return FixBranchResult(created=False, branch=branch, already_existed=True)

    # `git branch <name> <sha>` creates the ref WITHOUT checking it out — the coder subagent (or a
    # human) checks it out or worktrees it later. No push: the ref stays local for review.
    rc, out = _git(root, "branch", branch, sha)
    if rc != 0:
        return FixBranchResult(
            created=False,
            branch=branch,
            reason=f"git could not create the branch: {out.strip()[:200]}",
        )
    logger.info("selfqa: opened fix branch %s at %s (never pushed)", branch, sha[:8])
    return FixBranchResult(created=True, branch=branch)
