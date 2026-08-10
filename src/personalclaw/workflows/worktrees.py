"""Code-kind run worktrees: preserve, setup, resume, teardown, reintegration (§4.1 — S52).

S49 declared the `workspace` block and returned an ordered plan; this module performs the code-kind
half of it on the machinery that already exists. `loop/worktree.py` is proven —
`.worktrees/<id>` under
the project's own dir, `pclaw/task-*` branches, idempotent `add_worktree`, `merge_worktree` with a
typed result — so nothing here re-implements git.

Measured against a real repo before writing any of it:

* `add_worktree` on an existing id returns the SAME path rather than failing, which is what makes
  resume free.
* An untracked `.env` is genuinely ABSENT from a fresh worktree. That is why `preserve_patterns` is
  the adoption-critical detail and not a nicety: a worktree where every build fails reads
  to a user as
  "isolation is broken".

Four asymmetries, each chosen:

* **Setup failure does not block the run; teardown failure does not block deletion.** Setup is
  convenience — refusing to run because `npm install` failed makes the block a liability. Teardown
  runs BEFORE deletion because its job is to stop services and sync work out while the
  directory still
  exists.
* **Work is committed before the worktree is removed.** An ephemeral workspace whose run
record points
  at a deleted directory has lost the work; a per-run branch survives, so the record references git.
* **Preserve copies IN, never OUT.** A pattern that copied a worktree file back over the user's real
  tree would make an isolated run able to modify the thing it was isolated from.
* **Reintegration is offered, never performed.** `Apply Locally` and `Checkout Branch` are the two
  verbs a user picks between; a run that auto-merged would decide for them, and the decision is the
  whole reason the work was isolated.

Pure planning + explicit performers. Every function that touches the filesystem takes the paths
explicitly, so the ordering rules are testable in a scratch repo.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Marker directory for setup idempotency, inside the worktree. Shared name with S49's
#: `workspace.SETUP_MARKER_DIR` so a plan built there and performed here agree — two names for one
#: convention would mean setup re-running because the performer looked in the wrong place.
from personalclaw.workflows.workspace import SETUP_MARKER_DIR, setup_marker  # noqa: E402

#: Per-run branch prefix for durable persistence. Distinct from `loop/worktree.py`'s `pclaw/task-`
#: because these are RUNS, and a user reading `git branch` should be able to tell which subsystem
#: made a branch without looking it up.
RUN_BRANCH_PREFIX = "pclaw/run-"

#: Files never copied in by a preserve pattern, whatever the glob says. `.git` would corrupt the
#: worktree's own repo state; the rest are caches whose size defeats the point of a copy-in.
PRESERVE_DENYLIST = frozenset(
    {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache"}
)

#: Bytes above which a preserved file is skipped with a warning. `preserve_patterns`
#: exists for local
#: config — a multi-megabyte match is a mistake in the pattern, and copying it silently
#: would make a
#: cheap isolation step slow for reasons the user cannot see.
MAX_PRESERVE_BYTES = 2 * 1024 * 1024


class Reintegration(str, Enum):
    """The two verbs a user picks between after a code run.

    `APPLY_LOCALLY` puts the diff in the working tree so the user reviews and commits it themselves;
    `CHECKOUT_BRANCH` switches to the run's branch so they own the history. Both are offered because
    they suit different situations, and picking for the user is what "review before it lands" exists
    to prevent.
    """

    APPLY_LOCALLY = "apply_locally"
    CHECKOUT_BRANCH = "checkout_branch"


@dataclass
class PreserveResult:
    """What a preserve pass actually copied, and what it refused.

    `skipped` is returned rather than logged because a user whose build fails needs to know their
    `.env` was skipped for being 4MB — a silent skip makes the isolation look broken for a reason
    nothing reports.
    """

    copied: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"copied": list(self.copied), "skipped": list(self.skipped)}


def preserve(source: str | Path, target: str | Path, patterns: list[str]) -> PreserveResult:
    """Copy `patterns` from the real tree INTO the worktree. One direction only.

    Copying the other way would let an isolated run modify the tree it was isolated from, which is
    the whole property isolation buys.

    Denylisted names are refused whatever the glob matches: `.git` would corrupt the worktree's own
    repo state, and a `node_modules` copy defeats the point of a cheap isolation step.
    """
    result = PreserveResult()
    src_root, dst_root = Path(source), Path(target)
    if not src_root.is_dir() or not dst_root.is_dir():
        return result
    for pattern in patterns or []:
        for match in sorted(src_root.glob(pattern)):
            rel = match.relative_to(src_root)
            if any(part in PRESERVE_DENYLIST for part in rel.parts):
                result.skipped.append(f"{rel}: denylisted")
                continue
            if match.is_dir():
                result.skipped.append(f"{rel}: directories are not preserved")
                continue
            try:
                size = match.stat().st_size
            except OSError:
                result.skipped.append(f"{rel}: unreadable")
                continue
            if size > MAX_PRESERVE_BYTES:
                result.skipped.append(f"{rel}: {size} bytes exceeds the preserve cap")
                continue
            destination = dst_root / rel
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(match, destination)
                result.copied.append(str(rel))
            except OSError as exc:
                result.skipped.append(f"{rel}: {exc}")
    return result


@dataclass
class SetupResult:
    """The outcome of a setup pass. `blocked_run` is always False, by contract.

    Setup is best-effort convenience: refusing to run the workflow because `npm install`
    failed would
    make declaring setup a liability, and a user would stop declaring it. The failures are
    RECORDED so
    a run whose first stage fails for a missing dependency has an explanation in reach.
    """

    ran: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    @property
    def blocked_run(self) -> bool:
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ran": list(self.ran),
            "skipped": list(self.skipped),
            "failed": list(self.failed),
            "blocked_run": self.blocked_run,
        }


def setup_steps(setup: str) -> list[str]:
    """Split a setup block into steps.

    Newlines only. Splitting on `&&` or `;` would shred a single shell command that legitimately
    chains, and each step is marker-guarded individually — so getting the boundaries wrong means
    either re-running a chain or marking half of it done.
    """
    return [line.strip() for line in (setup or "").splitlines() if line.strip()]


def pending_setup(worktree: str | Path, setup: str) -> tuple[list[str], list[str]]:
    """`(to_run, already_done)` for a setup block, by marker file.

    Setup runs on EVERY resume by contract, so each step guards itself. Markers are
    content-addressed
    (S49's `setup_marker`), which means editing a step re-runs it — a marker keyed by position would
    skip an edited step as though it had already run.
    """
    root = Path(worktree)
    to_run: list[str] = []
    done: list[str] = []
    for step in setup_steps(setup):
        marker = root / setup_marker(step)
        (done if marker.exists() else to_run).append(step)
    return to_run, done


def mark_setup_done(worktree: str | Path, step: str) -> bool:
    """Write one step's marker. Returns False when it could not be written.

    A failure here is worth reporting rather than raising: the step DID run, and losing the marker
    costs one re-run on the next resume, while raising would fail a run whose setup succeeded.
    """
    marker = Path(worktree) / setup_marker(step)
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("done\n", encoding="utf-8")
        return True
    except OSError:
        logger.debug("could not write setup marker for %r", step[:60], exc_info=True)
        return False


@dataclass
class TeardownPlan:
    """The ordered teardown, with deletion LAST.

    The order is the contract, not a preference: teardown stops services and syncs work
    out, and both
    need the directory to still exist. A plan that deleted first would run its own teardown against
    nothing and report success.
    """

    steps: list[str] = field(default_factory=list)
    commits_first: bool = True
    deletes: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "steps": list(self.steps),
            "commits_first": self.commits_first,
            "deletes": self.deletes,
        }


def plan_teardown(
    *, teardown: str = "", ephemeral: bool = True, keep_open: bool = False
) -> TeardownPlan:
    """The teardown order for a code-kind run.

    `keep_open` exists for the case where the workspace IS the deliverable — a run that produced a
    working tree the user wants to inspect. Deleting it because the run ended would destroy the
    output, so the override skips deletion while still running teardown and committing.
    """
    plan = TeardownPlan(commits_first=ephemeral, deletes=not keep_open)
    for step in setup_steps(teardown):
        plan.steps.append(f"teardown: {step}")
    if ephemeral:
        # Durable-branch persistence: an ephemeral workspace whose run record points at a deleted
        # directory has lost the work. A per-run branch means the record references git instead.
        plan.steps.append("commit outstanding work to the per-run branch")
    if keep_open:
        plan.steps.append("KEEP the workspace (keep_open: the workspace is the deliverable)")
    else:
        plan.steps.append("remove the worktree")
    return plan


def run_branch(run_id: str) -> str:
    """The per-run branch name. Deterministic, so a retry lands on the same branch.

    A random suffix would leave one abandoned branch per retry, and a user reading `git
    branch` could
    not tell which one held the work.
    """
    safe = (
        "".join(c if (c.isalnum() or c in "-_.") else "-" for c in str(run_id or "")) or "unknown"
    )
    return f"{RUN_BRANCH_PREFIX}{safe}"


@dataclass
class DiffEntry:
    """One changed file in the run's worktree.

    `staged` and `status` are separate because the cockpit's stage/discard affordances need both: a
    file can be modified-and-staged, modified-and-not, or untracked, and collapsing them would make
    "discard" ambiguous about what it discards.
    """

    path: str
    status: str  # modified | added | deleted | untracked | renamed
    staged: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "status": self.status, "staged": self.staged}


#: git's porcelain status codes, mapped to words a cockpit can render. Kept explicit rather than
#: passing the raw code through: `??` means nothing to a user, and a UI that showed it
#: would need its
#: own mapping — a second one, which would drift from this.
_STATUS_WORDS = {
    "M": "modified",
    "A": "added",
    "D": "deleted",
    "R": "renamed",
    "C": "copied",
    "?": "untracked",
    "U": "conflicted",
    "!": "ignored",
}


#: Paths excluded from the REVIEW diff. Measured on a live worktree: the preserved `.env` and the
#: engine's own `.pclaw-setup/` markers showed up in the changed-files panel as though the run had
#: edited them. A review panel that lists machinery the user did not touch is one they
#: learn to skim,
#: and the file they should have noticed is in the same list.
INFRASTRUCTURE_PATHS = (SETUP_MARKER_DIR,)


def is_infrastructure(path: str, preserved: list[str] | None = None) -> bool:
    """Whether a changed path is engine/preserve machinery rather than the run's work.

    Path comparison is normalized on BOTH sides. Measured: git reports an untracked directory as
    `.pclaw-setup/` with a trailing slash, so a prefix check written against the bare name matched
    nothing and the markers stayed in the review diff — the exclusion existed and did half
    its job.

    Preserved files are passed IN rather than pattern-matched here: the preserve pass already knows
    exactly what it copied, and re-deriving it from globs would disagree with reality the moment a
    pattern matched something the copy skipped (oversize, denylisted).
    """
    cleaned = (path or "").strip().lstrip("./").rstrip("/")
    if not cleaned:
        return False
    for marker in INFRASTRUCTURE_PATHS:
        bare = marker.strip().lstrip("./").rstrip("/")
        if cleaned == bare or cleaned.startswith(bare + "/"):
            return True
    return cleaned in {str(p).strip().lstrip("./").rstrip("/") for p in (preserved or [])}


def parse_status(porcelain: str) -> list[DiffEntry]:
    """Parse `git status --porcelain` into typed entries.

    Reads the TWO-column form: the first column is the index (staged), the second the working tree.
    A parser that looked at one column would report a staged deletion as unstaged, and the cockpit's
    stage/discard buttons would act on the wrong thing.

    Unknown codes are kept with the raw code as the status rather than dropped — a file the parser
    does not understand is still a file the user changed, and hiding it would make the diff panel
    quietly incomplete.
    """
    entries: list[DiffEntry] = []
    for line in (porcelain or "").splitlines():
        if len(line) < 4:
            continue
        index_code, tree_code, path = line[0], line[1], line[3:].strip()
        if not path:
            continue
        # A rename reads as `R  old -> new`; the NEW path is what the user is reviewing.
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        staged = index_code not in (" ", "?")
        code = index_code if staged else tree_code
        entries.append(
            DiffEntry(
                path=path,
                status=_STATUS_WORDS.get(code, code.strip() or "unknown"),
                staged=staged,
            )
        )
    return entries


def reintegration_offer(
    run_id: str, *, branch: str = "", changed: int = 0, conflicts: list[str] | None = None
) -> dict[str, Any]:
    """The two verbs, with the state a user needs to choose between them.

    Both are OFFERED; neither is performed. A run that auto-merged would decide for the
    user, and the
    decision is the whole reason the work was isolated in the first place.

    Conflicts are surfaced on the offer rather than discovered at apply time: "apply this" that then
    fails with a conflict is a worse experience than "apply this (2 files conflict)".
    """
    conflicts = list(conflicts or [])
    return {
        "run_id": run_id,
        "branch": branch or run_branch(run_id),
        "changed_files": changed,
        "conflicts": conflicts,
        "verbs": [
            {
                "verb": Reintegration.APPLY_LOCALLY.value,
                "label": "Apply Locally",
                "detail": "put the diff in your working tree — you review and commit it",
                "safe": not conflicts,
            },
            {
                "verb": Reintegration.CHECKOUT_BRANCH.value,
                "label": "Checkout Branch Locally",
                "detail": f"switch to {branch or run_branch(run_id)} — you own the history",
                # Checking out is safe even with conflicts: nothing merges, so there is nothing to
                # conflict WITH until the user decides to merge.
                "safe": True,
            },
        ],
        "note": (
            "Nothing is applied automatically. Reviewing before it lands is why the run"
            " was isolated."
            if not conflicts
            else f"{len(conflicts)} file(s) conflict with your working tree — "
            "checkout is the safer verb."
        ),
    }


@dataclass
class WorktreeState:
    """Everything the cockpit and the boot sweep need about one run's worktree.

    `alive` feeds S46's substrate check directly: an isolated substrate that survived a
    restart makes
    the run SUSPENDED and resumable rather than a zombie, and getting that backwards destroys
    recoverable work while reporting success.
    """

    run_id: str
    path: str = ""
    branch: str = ""
    alive: bool = False
    dirty: bool = False
    changed: list[DiffEntry] = field(default_factory=list)

    @property
    def preserved_workspace_path(self) -> str:
        """The path to surface on the run record when a dirty workspace is kept.

        Empty for a clean workspace: pointing a user at a directory with nothing in it is a
        false lead, and the run record would carry a path that means nothing.
        """
        return self.path if (self.alive and self.dirty) else ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "path": self.path,
            "branch": self.branch,
            "alive": self.alive,
            "dirty": self.dirty,
            "changed": [c.to_dict() for c in self.changed],
            "preserved_workspace_path": self.preserved_workspace_path,
        }


def inspect_worktree(
    run_id: str, path: str | Path, porcelain: str = "", preserved: list[str] | None = None
) -> WorktreeState:
    """Build the state record from a path and a status dump.

    Takes the porcelain text rather than shelling out, so the parsing rules are testable without a
    repo and the caller owns the one subprocess call.

    `preserved` names the files the preserve pass copied in, so they are excluded from the REVIEW
    diff. Measured live: without this, a run's changed-files panel listed the preserved
    `.env` and the
    engine's own setup markers as user changes — and a review panel full of machinery is
    one the user
    skims, with the file that mattered in the same list.
    """
    root = Path(path)
    # An EMPTY path is not a worktree. `Path("")` is `.`, whose `is_dir()` is True — so without
    # this an unprovisioned run would report the gateway's own working directory as its live
    # workspace, and the boot sweep would then read it as a survived substrate and SUSPEND a run
    # that has nothing to resume into. Found by WF2WOR-4's first production caller; no test passed
    # an empty path before, because nothing called this with one.
    alive = bool(str(path).strip()) and root.is_dir()
    changed = (
        [c for c in parse_status(porcelain) if not is_infrastructure(c.path, preserved)]
        if alive
        else []
    )
    return WorktreeState(
        run_id=run_id,
        path=str(root) if alive else "",
        branch=run_branch(run_id),
        alive=alive,
        dirty=bool(changed),
        changed=changed,
    )


def substrate_for(state: WorktreeState) -> Any:
    """The S46 `Substrate` for this worktree, so the boot sweep has ONE source of truth.

    Built here rather than re-derived in the sweep: the sweep's whole decision turns on whether an
    isolated substrate is alive, and two places computing that would eventually disagree —
    with the
    disagreement showing up as a run aborted despite having recoverable work.
    """
    from personalclaw.workflows.containers import Substrate

    return Substrate(
        kind="worktree",
        alive=state.alive,
        detail=state.path or "worktree is gone",
    )


def resume_safe(worktree: str | Path, setup: str) -> tuple[bool, str]:
    """Whether this worktree can be resumed into, and why not when it cannot.

    A missing directory is not resumable — the work is gone, and offering a Resume that
    cannot work is
    worse than an honest abort. A present directory always is, because `add_worktree` is idempotent
    and setup is marker-guarded: the two properties that make resume free were both measured on the
    real machinery rather than assumed.
    """
    root = Path(worktree)
    if not root.is_dir():
        return False, "the worktree is gone — nothing to resume into"
    to_run, done = pending_setup(root, setup)
    if to_run and done:
        return True, f"resumable; {len(done)} setup step(s) already done, {len(to_run)} to run"
    if to_run:
        return True, f"resumable; {len(to_run)} setup step(s) to run"
    return True, "resumable; setup already complete"


def worktree_env(worktree: str | Path) -> dict[str, str]:
    """Env additions for a stage running in a worktree.

    `PWD` is set alongside `cwd` because some tools read it rather than calling `getcwd`,
    and a stale
    `PWD` makes a build resolve relative paths against the user's real tree — which is
    precisely the
    isolation failure the worktree exists to prevent.
    """
    root = str(Path(worktree))
    return {"PWD": root, "PERSONALCLAW_RUN_WORKTREE": root}


def cleanup_markers(worktree: str | Path) -> int:
    """Drop the setup markers, so the NEXT run in a reused worktree re-runs setup.

    Only for a reused (named) workspace: a fresh worktree has no markers, and a per-run one is about
    to be deleted. Returns the count removed, since "0" and "5" mean different things to a caller
    deciding whether the reuse was clean.
    """
    root = Path(worktree) / SETUP_MARKER_DIR
    if not root.is_dir():
        return 0
    removed = 0
    for marker in root.glob("*.done"):
        try:
            os.unlink(marker)
            removed += 1
        except OSError:
            logger.debug("could not remove setup marker %s", marker, exc_info=True)
    return removed
