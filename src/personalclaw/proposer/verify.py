"""Fire-wait-**verify**: the disk re-diff that decides whether a handoff is accepted (§4.1).

A proposer's final message describes what it *meant* to do. The plan is explicit that this is
not evidence ("the runner's final message describes intent, not what landed") and that
claimed-but-absent edits are a FAILED handoff, recorded honestly. So acceptance is decided here,
against bytes on disk, and nowhere else.

Mechanism: :func:`snapshot_workspace` records a content digest per interesting path *before* the
proposer fires; :func:`rediff` recomputes them *after* and reports which paths actually moved.
A claimed path that did not move is ``missing`` → :attr:`DiffVerification.verified` is False →
the service rejects.

Content digests, not ``git diff``, are the gate — three reasons: it works in a workspace that is
not a git repo, it sees a change git would ignore (``.gitignore``d build output the run cares
about), and it cannot be fooled by a proposer that stages or stashes. The git view is still
captured, because a human reading the brief wants ``git status``/``git diff`` prose; it is
decoration on the brief, never the acceptance test.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path

#: Files above this size are digested from their (size, mtime_ns) rather than their bytes — a
#: 500MB model checkpoint must not cost a full read on every verify. Collision risk is
#: irrelevant here: we compare a path against ITSELF across two moments in time.
_FULL_DIGEST_MAX_BYTES = 8 * 1024 * 1024

#: Directory names never walked when building an implicit baseline.
_SKIP_DIRS = frozenset(
    {".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", "dist"}
)

#: Cap on an implicit (no explicit path list) walk, so a baseline over a huge tree stays cheap.
_MAX_WALK_FILES = 20000


def _digest(path: Path) -> str:
    """A stable per-file fingerprint, or ``""`` when the file does not exist.

    ``""`` is a real value in this table — "absent at baseline" — and a created file therefore
    reads as a change, which is what a proposer adding a file must be able to prove.
    """
    try:
        st = path.stat()
    except OSError:
        return ""
    if not os.path.isfile(path):
        return ""
    if st.st_size > _FULL_DIGEST_MAX_BYTES:
        return f"meta:{st.st_size}:{st.st_mtime_ns}"
    try:
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _normalise(workspace: str, candidate: str) -> str:
    """A claimed path → the workspace-relative key used in the digest table.

    A proposer may claim an absolute path, a ``./`` path, or a bare relative one. All three
    normalise to the same key, so a correct claim is never rejected on formatting.
    """
    text = (candidate or "").strip().strip("`\"'")
    if not text:
        return ""
    root = Path(workspace).resolve()
    p = Path(text)
    try:
        full = p if p.is_absolute() else (root / p)
        return os.path.relpath(str(full.resolve()), str(root))
    except (OSError, ValueError):
        return text.lstrip("./")


@dataclass(frozen=True)
class DiskBaseline:
    """The pre-fire state of a workspace: path → digest, plus the git prose for the brief."""

    workspace: str
    digests: dict[str, str] = field(default_factory=dict)
    git_status: str = ""
    git_diff: str = ""

    def digest_of(self, rel: str) -> str:
        return self.digests.get(rel, "")


@dataclass(frozen=True)
class DiffVerification:
    """The verdict. ``verified`` is the ONLY thing the acceptance decision reads."""

    verified: bool
    changed: tuple[str, ...] = ()
    verified_paths: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    reason: str = ""


def snapshot_workspace(workspace: str, *, paths: tuple[str, ...] = ()) -> DiskBaseline:
    """Digest *workspace* before firing a proposer.

    When *paths* is given only those are digested (the caller already knows the blast radius —
    the stalled node's touched files). Otherwise the tree is walked, skipping VCS/vendor dirs.
    Either way an unlisted path still verifies correctly: its baseline digest reads ``""``
    (absent), so a proposer that creates it can prove the creation.
    """
    root = Path(workspace)
    digests: dict[str, str] = {}
    if paths:
        for candidate in paths:
            rel = _normalise(workspace, candidate)
            if rel:
                digests[rel] = _digest(root / rel)
    elif root.is_dir():
        count = 0
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for fn in filenames:
                full = Path(dirpath) / fn
                digests[os.path.relpath(str(full), str(root))] = _digest(full)
                count += 1
                if count >= _MAX_WALK_FILES:
                    break
            if count >= _MAX_WALK_FILES:
                break
    status, diff = git_prose(workspace)
    return DiskBaseline(workspace=str(workspace), digests=digests, git_status=status, git_diff=diff)


def changed_paths(baseline: DiskBaseline) -> tuple[str, ...]:
    """Re-digest every path in *baseline* and return those whose bytes moved.

    Only paths present in the baseline table are re-read — the table is the universe of things
    this verification can speak about, and it is built to include every path a claim will name
    (the claim is normalised into the same key space before comparison).
    """
    root = Path(baseline.workspace)
    moved = [rel for rel, before in baseline.digests.items() if _digest(root / rel) != before]
    return tuple(sorted(moved))


def rediff(baseline: DiskBaseline, claimed: tuple[str, ...]) -> DiffVerification:
    """The acceptance test. Verified iff every claimed path actually moved on disk.

    Two rejections, both deliberate:

    * **no claim** — a proposer that named no edited file has produced advice, not a patch.
      There is nothing to verify, so there is nothing to accept.
    * **a claim that did not land** — the exact "confident garbage" case. Reported as
      ``missing`` so the consumer can show the user WHICH claim was false.
    """
    root = Path(baseline.workspace)
    table = dict(baseline.digests)
    normalised: list[str] = []
    for candidate in claimed:
        rel = _normalise(baseline.workspace, candidate)
        if not rel:
            continue
        normalised.append(rel)
        table.setdefault(rel, "")
    enriched = DiskBaseline(
        workspace=baseline.workspace,
        digests=table,
        git_status=baseline.git_status,
        git_diff=baseline.git_diff,
    )
    moved = set(changed_paths(enriched))
    if not normalised:
        return DiffVerification(
            verified=False,
            changed=tuple(sorted(moved)),
            reason="the proposer claimed no edits, so there is nothing on disk to confirm",
        )
    landed = [rel for rel in normalised if rel in moved]
    missing = [rel for rel in normalised if rel not in moved]
    del root
    if missing:
        return DiffVerification(
            verified=False,
            changed=tuple(sorted(moved)),
            verified_paths=tuple(landed),
            missing=tuple(missing),
            reason=(
                "the proposer claimed edits that are not on disk: " + ", ".join(sorted(missing))
            ),
        )
    return DiffVerification(
        verified=True,
        changed=tuple(sorted(moved)),
        verified_paths=tuple(landed),
        reason="every claimed edit is present on disk",
    )


def git_prose(workspace: str, *, diff_limit: int = 40000) -> tuple[str, str]:
    """``(git status --porcelain, git diff)`` for the brief, or ``("", "")`` outside a repo.

    Reuses the loop's git runner rather than minting a second one: that helper already carries
    the ``build`` resource ceiling through the post-exec shim and is already accounted for in
    the spawn-ceiling audit, so this adds no new spawn seam to classify.
    """
    from personalclaw.loop.worktree import _git, git_available, is_git_repo

    if not workspace or not git_available() or not is_git_repo(workspace):
        return "", ""
    rc_status, status = _git(workspace, "status", "--porcelain")
    rc_diff, diff = _git(workspace, "diff")
    return (
        status.strip() if rc_status == 0 else "",
        diff[:diff_limit] if rc_diff == 0 else "",
    )
