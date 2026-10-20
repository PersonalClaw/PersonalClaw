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
import threading
import time
from typing import NamedTuple

from personalclaw.security import mask_child_output

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

#: The worktrees a task (or a workflow run) works in, home-relative, as :func:`worktree_path`
#: makes them: under its project, or under the root keyed by the workspace when it has none. A
#: task's worker session works in its worktree, so each has a memory partition of its own, which
#: goes with it (:func:`remove_worktree`, and ``memory_locality.settle_partitions`` at the start).
SESSION_FOLDERS = ("projects/*/worktrees/*", "code/worktrees/*/*")


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
    """True iff a ``git`` binary is on PATH and new enough for PersonalClaw's git
    (``net.git.git_problem``)."""
    from personalclaw.net.git import git_problem

    return not git_problem()


def _git(
    workspace: str, *args: str, timeout: int = _TIMEOUT, errors: bool = True
) -> tuple[int, str]:
    """Run a git command in ``workspace``; return (returncode, its output then its errors). With
    *errors* off, its output alone: a listing a caller parses, which a warning must not join."""
    # Resource ceiling: loop worktree git steps are agent-influenced (a loop
    # run drives them). Deliver the ``build`` ceiling (raised NOFILE for many file
    # handles + OOM bias) via the post-exec shim, prepended to argv. Synchronous run
    # off no event loop, so no fork-wedge hazard; the shim applies the limit after exec.
    # The loop's own work can edit this repository's `.git` as easily as its files, so git runs
    # with the settings that keep a hook, an fsmonitor or any other program the repository names
    # from running (`net.git.git_argv`), and with the child allowlist (`net.git.git_env`): none of
    # the gateway's secrets, and no inherited `GIT_DIR` or `GIT_WORK_TREE` pointing every step at
    # another repository.
    from personalclaw.net.git import git_argv, git_env
    from personalclaw.sandbox import PROFILE_BUILD, spawn_shim_argv

    try:
        p = subprocess.run(
            spawn_shim_argv(git_argv(args), PROFILE_BUILD),
            cwd=workspace,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=git_env(site="loop-worktree-git"),
        )
        out = (p.stdout or b"").decode("utf-8", "replace")
        if errors:
            out += (p.stderr or b"").decode("utf-8", "replace")
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


# ── creation-cost instrumentation ("measure first") ──
#
# This is explicitly a MEASURED-bottleneck design: the hydration tuning (sparse
# checkout, pooled creation, a reuse pool) is only allowed to be built if a fan-out
# actually pays for it. That decision needs a number from the real function, on real
# repos, over time — so the timing line ships whether or not the gate opens.
#
# The line is a CONTRACT, not a debug aid, because its whole purpose is comparison
# across runs and across machines. Hence a fixed prefix and fixed `key=value` fields:
#
#   worktree add outcome=created task=t-abc ms=812 files=10432 size_class=large
#
# * ``outcome`` first, because it decides whether the row is a hydration sample at all.
#   ``created`` is the cost to attack; ``reused`` is add_worktree's idempotent
#   early return (near-zero, and the datapoint a reuse pool would be judged against);
#   ``failed`` carries a duration too — a creation that burned the whole ``_TIMEOUT``
#   before failing is the most interesting row on the page, and dropping it would make
#   the timeout case invisible in exactly the measurement meant to find it.
# * ``ms`` is an integer of milliseconds. Not seconds-with-decimals: these are compared
#   by eye and by grep, and a float would print `1e-05` on the reuse path.
# * ``files`` AND ``size_class`` both, even though the class is derived from the count.
#   The count is what makes two runs comparable when a repo grows; the class is what
#   makes a mixed log greppable without arithmetic.
# * ``task`` so a fan-out's N rows can be told apart and joined to the run.
TIMING_LOG_PREFIX = "worktree add"

OUTCOME_CREATED = "created"
OUTCOME_REUSED = "reused"
OUTCOME_FAILED = "failed"

#: Upper bound (exclusive) of tracked files per class name. Decade buckets, so the
#: benchmark case — a 10K-file repo — sits exactly on the ``large`` floor
#: rather than straddling a boundary. Coarse on purpose: the tag exists to say which
#: measurements may be compared with which, and a finer class would imply the timing
#: number is repeatable to a precision it does not have.
_SIZE_CLASSES: tuple[tuple[int, str], ...] = (
    (100, "tiny"),
    (1_000, "small"),
    (10_000, "medium"),
    (100_000, "large"),
)
SIZE_CLASS_HUGE = "huge"
#: Reported when git cannot answer. Distinct from any real class so a reader never
#: mistakes an unmeasured repo for a small one.
SIZE_CLASS_UNKNOWN = "unknown"

#: Sentinel for "git could not count". 0 cannot double as unknown — an empty repo is a
#: real answer, and conflating them would tag it ``unknown`` forever.
FILE_COUNT_UNKNOWN = -1

#: workspace abspath → tracked-file count. Cached for the PROCESS because
#: ``git ls-files`` on the very repo we are timing is itself a full index walk — run
#: per creation it would make the instrumentation a share of the cost it reports, which
#: is the one thing a measurement may not do. Staleness is harmless at decade
#: granularity: a repo has to grow 10x to change its class. Failures are cached too,
#: for the same reason — a workspace with no git must not re-pay the probe N times.
_FILE_COUNT_CACHE: dict[str, int] = {}


def size_class(file_count: int) -> str:
    """The size-class tag for a tracked-file count (``FILE_COUNT_UNKNOWN`` → unknown)."""
    if file_count < 0:
        return SIZE_CLASS_UNKNOWN
    for ceiling, name in _SIZE_CLASSES:
        if file_count < ceiling:
            return name
    return SIZE_CLASS_HUGE


def repo_file_count(workspace: str) -> int:
    """Tracked files in ``workspace`` (``git ls-files``), cached per workspace.

    ``FILE_COUNT_UNKNOWN`` when git cannot answer. Counts non-empty lines of the
    combined git output; ``_git`` merges stderr, so a git warning could inflate the
    count by a line or two — which cannot move a decade bucket, and reusing ``_git``
    keeps every git call in this module behind the one resource-ceilinged runner
    instead of adding a second, unshimmed subprocess path just to count files.
    """
    key = os.path.abspath(workspace or "")
    cached = _FILE_COUNT_CACHE.get(key)
    if cached is not None:
        return cached
    rc, out = _git(workspace, "ls-files")
    count = sum(1 for ln in out.splitlines() if ln.strip()) if rc == 0 else FILE_COUNT_UNKNOWN
    _FILE_COUNT_CACHE[key] = count
    return count


def repo_size_class(workspace: str) -> str:
    """The cached size class of ``workspace``."""
    return size_class(repo_file_count(workspace))


def _log_creation(workspace: str, task_id: str, elapsed: float, outcome: str) -> None:
    """Emit the one timing line. Called AFTER the clock stops, always.

    The size class is resolved here rather than up front precisely so a cache MISS
    (one ``git ls-files``) lands outside the measured window. Instrumented the other
    way round, the first worktree of every process would report its own probe as part
    of the hydration cost — and the first is the one a fan-out benchmark reads.
    """
    count = repo_file_count(workspace)
    logger.info(
        "%s outcome=%s task=%s ms=%d files=%d size_class=%s",
        TIMING_LOG_PREFIX,
        outcome,
        task_id,
        round(elapsed * 1000),
        count,
        size_class(count),
    )


# ── sparse hydration, pooled creation, reuse-reset ──
#
# The measurement opened the gate: a fan-out of 4 on a 10K-file repo cost 5216 ms
# mean per worktree against a 286 ms ambient floor, so ~2.7 s of even the CHEAPEST
# sample is hydration. The three levers all attack that hydration, and each has a
# different failure mode, so each is a separate seam here:
#
# * SPARSE — hydrate only the paths a task names. Scope is a HINT: it is
#   derived from task text, validated against the index, and any miss auto-widens.
# * POOL — the phase's READY worktrees are created concurrently, bounded.
# * REUSE — a SURVIVING worktree is reset instead of handed back as-is.
#
# **The measured reason auto-widening is load-bearing, not a nicety.** In a cone-mode
# sparse worktree an out-of-cone write is not refused by the filesystem — the file lands.
# But ``git add -A`` then declines to stage it and exits 1, staging NOTHING, and the
# following ``commit`` exits 1 with "nothing added to commit". Git does signal this;
# :func:`merge_worktree` DISCARDS both exit codes (it always has), so the branch is
# merged without the work and the merge reports success. Net effect without widening: a
# task that writes one file outside its stated scope silently loses it, and every status
# surface says the task merged cleanly. That is why :func:`widen_for_pending` runs inside
# :func:`merge_worktree` BEFORE its ``add -A``, and why the covering tests assert the file
# is in the resulting commit rather than asserting that a widen command was issued.
#
# Cone mode (the default for ``sparse-checkout set``) is used deliberately: its entries
# are DIRECTORIES and root-level files stay hydrated, so a scoped worktree still has the
# repo's build/config files (pyproject, Makefile, package.json) that any real task needs.

#: Ceiling on concurrent ``git worktree add`` calls (bounded by os.cpu_count(),
#: ceiling 4). Matches ``sdlc._POOL_CAP`` — a phase never has more than that many
#: task-workers in flight, so a wider pool could not be used even if the box were bigger.
POOL_CEILING = 4

#: Candidate path token: at least one ``/`` so a bare word ("worktree", "tests") can
#: never become a cone entry, and no whitespace. The final component must START with a
#: word char or dash, which is what stops a sentence-final "…do not touch web/." from
#: yielding the token ``web/.`` (measured: it did, and then resolved to the ``web``
#: directory). Bounded by ``_MAX_SCOPE_CANDIDATES`` below because this text is
#: model-authored — an over-long list would spend more git time resolving scope than the
#: sparse checkout saves.
#:
#: **Polarity is deliberately NOT modelled.** "Do not touch web/src" contributes ``web/src``
#: exactly like "edit web/src" would. That is acceptable because the two failure directions
#: are not symmetric: over-inclusion only costs some of the hydration saving, while
#: under-inclusion is caught by :func:`widen_for_pending`. Neither can break a task, which
#: is the whole reason task scope is a HINT rather than a contract.
_PATH_TOKEN_RE = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)+[\w-][\w.-]*)")
_MAX_SCOPE_CANDIDATES = 16
#: Cone entries per worktree. A scope this wide is not a scope; treat it as "no usable
#: scope" and hydrate fully rather than paying to enumerate it.
_MAX_SCOPE_DIRS = 8

#: workspace abspath → frozenset of tracked DIRECTORY paths (repo-relative, POSIX).
#: One ``git ls-files`` per workspace per process, same rationale as
#: ``_FILE_COUNT_CACHE``: resolving scope must not become a share of the cost it saves.
_TRACKED_DIRS_CACHE: dict[str, frozenset[str]] = {}


def _tracked_dirs(workspace: str) -> frozenset[str]:
    """Every directory that contains a tracked file, repo-relative (cached).

    Directories, not files, because cone-mode sparse-checkout entries are directories.
    Empty frozenset when git cannot answer — which makes every candidate unresolvable
    and so degrades to a full checkout, the documented fallback."""
    key = os.path.abspath(workspace or "")
    cached = _TRACKED_DIRS_CACHE.get(key)
    if cached is not None:
        return cached
    rc, out = _git(workspace, "ls-files")
    dirs: set[str] = set()
    if rc == 0:
        for line in out.splitlines():
            rel = line.strip()
            if not rel:
                continue
            parts = rel.split("/")[:-1]
            for i in range(1, len(parts) + 1):
                dirs.add("/".join(parts[:i]))
    result = frozenset(dirs)
    _TRACKED_DIRS_CACHE[key] = result
    return result


def scope_candidates(text: str) -> list[str]:
    """Path-like tokens in task text, de-duplicated, order preserved.

    Pure text extraction — no git, no filesystem. Whether a token is REAL is
    :func:`resolve_scope`'s job; keeping the two apart is what lets a hallucinated
    path be dropped instead of producing an empty working tree."""
    seen: list[str] = []
    for m in _PATH_TOKEN_RE.finditer(text or ""):
        # Trailing dots are sentence punctuation, not part of the path ("Edit
        # web/main.ts." → ``web/main.ts``). Harmless for cone resolution either way, but
        # the candidate list is logged and read by humans.
        tok = m.group(1).strip("/").rstrip(".")
        if tok and tok not in seen:
            seen.append(tok)
        if len(seen) >= _MAX_SCOPE_CANDIDATES:
            break
    return seen


def resolve_scope(workspace: str, candidates: list[str]) -> list[str]:
    """Turn candidate path tokens into cone-mode sparse-checkout directories.

    A candidate resolves when it names a tracked directory, or a file inside one (its
    parent becomes the cone entry). Anything else — a hallucinated path, a file at the
    repo root, a path from another project — is dropped. Returns ``[]`` when nothing
    resolves or the result is too wide to be a scope, and ``[]`` means FULL hydration:
    the required fallback whenever scope is absent or unreliable."""
    if not candidates:
        return []
    tracked = _tracked_dirs(workspace)
    if not tracked:
        return []
    out: list[str] = []
    for cand in candidates:
        norm = cand.replace(os.sep, "/").strip("/")
        if not norm or norm.startswith("../") or ".." in norm.split("/"):
            continue
        entry = norm if norm in tracked else norm.rsplit("/", 1)[0] if "/" in norm else ""
        if entry and entry in tracked and entry not in out:
            out.append(entry)
    if not out or len(out) > _MAX_SCOPE_DIRS:
        return []
    return sorted(out)


def sparse_enabled() -> bool:
    """Whether ``loops.worktree_sparse`` permits sparse hydration (default true).

    Fails OPEN to today's behaviour: any config problem returns False → full checkout,
    which is the slower but never-wrong path. ``AppConfig.load()`` rather than a cached
    accessor because that is how the sibling ``loops`` reader does it
    (``sdlc._check_work_post_gate``), and there is no cached accessor in this repo."""
    try:
        from personalclaw.config.loader import AppConfig

        return bool(AppConfig.load().loops.worktree_sparse)
    except Exception:
        return False


def scope_for_task(workspace: str, *texts: str) -> list[str]:
    """The cone directories for a task described by ``texts`` (title, description,
    action-plan lines…), or ``[]`` for full hydration.

    THE config chokepoint: ``loops.worktree_sparse=False`` returns ``[]`` here, so the
    setting cannot be bypassed by a caller that forgets to check it."""
    if not sparse_enabled():
        return []
    return resolve_scope(workspace, scope_candidates("\n".join(t for t in texts if t)))


def set_sparse_scope(wt_path: str, paths: list[str]) -> bool:
    """Restrict ``wt_path``'s working tree to ``paths`` (cone mode). False on any
    failure — the caller keeps the fully-hydrated worktree it already has.

    The first ``sparse-checkout set`` in a repo also writes
    ``extensions.worktreeConfig`` into the SHARED ``.git/config``; when several
    worktrees of one repo arm sparse concurrently (the :func:`add_worktrees`
    pool) the losers hit ``could not lock config file … File exists`` and would
    silently fall back to full hydration. Bounded retry on that one transient
    error class; anything else fails immediately as before.
    """
    if not paths:
        return False
    for attempt in range(3):
        rc, out = _git(wt_path, "sparse-checkout", "set", *paths)
        if rc == 0:
            return True
        if "could not lock config file" not in out:
            break
        time.sleep(0.05 * (attempt + 1))
    logger.debug("sparse-checkout set failed in %s: %s", wt_path, mask_child_output(out))
    return False


def sparse_scope(wt_path: str) -> list[str]:
    """The worktree's current cone entries (``[]`` when not sparse). Lets a caller —
    and a test — observe the cone WIDEN rather than trusting that a command ran."""
    rc, out = _git(wt_path, "sparse-checkout", "list")
    if rc != 0:
        return []
    return sorted(ln.strip() for ln in out.splitlines() if ln.strip())


def widen_scope(wt_path: str, paths: list[str]) -> bool:
    """Add ``paths`` to the cone (``git sparse-checkout add``). Never narrows."""
    if not paths:
        return False
    rc, out = _git(wt_path, "sparse-checkout", "add", *paths)
    if rc != 0:
        logger.debug("sparse-checkout add failed in %s: %s", wt_path, mask_child_output(out))
        return False
    return True


def widen_for_pending(wt_path: str) -> list[str]:
    """Widen the cone to cover every out-of-cone change present in ``wt_path``;
    return the directories added (``[]`` when nothing needed widening).

    This is the auto-widen: an out-of-scope write must SUCCEED, and in git's
    sparse world "succeed" can only mean "reaches the commit" — an unstaged file is
    dropped silently (see the block above). Called by :func:`merge_worktree` before it
    stages, so the widening happens on the path where the loss would otherwise occur.
    A no-op on a non-sparse worktree: with no cone, nothing is out of it."""
    if not sparse_scope(wt_path):
        return []
    rc, out = _git(wt_path, "status", "--porcelain", "--untracked-files=all")
    if rc != 0:
        return []
    wanted: list[str] = []
    for line in out.splitlines():
        rel = line[3:].strip().strip('"')
        if not rel:
            continue
        rel = rel.split(" -> ")[-1]  # renames: widen for the destination
        entry = rel.rsplit("/", 1)[0] if "/" in rel else ""
        if entry and entry not in wanted:
            wanted.append(entry)
    if not wanted:
        return []
    cone = set(sparse_scope(wt_path))
    # A path already covered by a cone entry (itself or an ancestor) needs no widening.
    missing = [d for d in wanted if not any(d == c or d.startswith(c + "/") for c in cone)]
    if not missing or not widen_scope(wt_path, missing):
        return []
    logger.info("worktree scope widened in %s: %s", wt_path, ",".join(sorted(missing)))
    return sorted(missing)


def pool_size(n_items: int | None = None) -> int:
    """Worker count for batched worktree creation: ``min(cpu_count, POOL_CEILING)``,
    never below 1, and never more than there is work for.

    Bounded on BOTH sides deliberately. The ceiling is ``os.cpu_count()``, at most
    4: what the pool overlaps is hydration, which is I/O-bound,
    and each worker also serializes briefly on the per-repo registration lock (see
    ``_REGISTER_LOCKS``) — so more threads than 4 buys contention, not throughput. The
    cpu_count leg keeps a 2-core box from being asked for 4."""
    size = min(os.cpu_count() or 1, POOL_CEILING)
    if n_items is not None:
        size = min(size, n_items)
    return max(1, size)


# ``git worktree add`` is not safe against a concurrent ``git worktree add`` in the SAME
# repo, and it fails HARD rather than degrading. Measured on git 2.55 (macOS runner and
# this dev box): an add writes ``.git/worktrees/<id>/gitdir`` BEFORE it writes that
# entry's ``commondir``, so a sibling entry is briefly DISCOVERABLE while its
# ``commondir`` is still a zero-byte file — polling the directory through a 16-wide
# batch caught the state directly (``commondir=0 gitdir=118``). A concurrent add
# enumerates the worktrees, opens that sibling and dies:
#
#     fatal: failed to read .git/worktrees/<sibling>/commondir: Undefined error: 0
#
# exit 128, which :func:`add_worktree` can only report as a creation failure (``None``)
# — the red that took ``main`` down (8-wide batch, ~1 round in 20 locally).
#
# Forging that state makes the death deterministic, and shows the blast radius is
# exactly ONE command: ``worktree add`` dies on it, while ``checkout``,
# ``sparse-checkout set`` and ``status`` run inside a worktree are unaffected. So
# REGISTRATION is serialized per repository and HYDRATION — the expensive part the pool
# exists to overlap — stays in the pool. Not a lock on the batch: a lock around the one
# metadata command, held for milliseconds.
_REGISTER_LOCKS: dict[str, threading.Lock] = {}
_REGISTER_LOCKS_GUARD = threading.Lock()


def _register_lock(workspace: str) -> threading.Lock:
    """The per-repository lock guarding ``git worktree add``.

    Keyed by workspace abspath like the sibling caches above: every worktree of a repo
    is registered through that repo's main workspace, so the key IS the repo."""
    key = os.path.abspath(workspace)
    with _REGISTER_LOCKS_GUARD:
        lock = _REGISTER_LOCKS.get(key)
        if lock is None:
            lock = _REGISTER_LOCKS[key] = threading.Lock()
        return lock


def add_worktrees(
    workspace: str,
    specs: list[tuple[str, list[str]]],
    project_id: str = "",
) -> dict[str, str | None]:
    """Create worktrees for many tasks at once through a bounded thread pool.

    ``specs`` is ``[(task_id, scope_paths), …]``; returns ``{task_id: path|None}``.
    Each entry is exactly :func:`add_worktree`, so the timing-log contract, the
    reuse-reset and the sparse setup are identical to the one-at-a-time path — the pool
    changes only how many run at once. A single spec skips the pool entirely (a
    ThreadPoolExecutor for one item is pure overhead)."""
    if not specs:
        return {}
    if len(specs) == 1:
        tid, scope = specs[0]
        return {tid: add_worktree(workspace, tid, project_id, scope=scope)}
    if any(scope for _, scope in specs):
        # Pre-arm the ONE shared-config write sparse setup needs
        # (``extensions.worktreeConfig``) while still serial. Without this the
        # pool's concurrent ``sparse-checkout set`` calls race on
        # ``.git/config``'s lockfile and the losers silently lose their cone
        # (full hydration). Best-effort: on failure the per-call retry in
        # :func:`set_sparse_scope` still covers the race.
        _git(workspace, "config", "extensions.worktreeConfig", "true")
    from personalclaw import memory_writes

    results: dict[str, str | None] = {}
    with memory_writes.ScopeCarryingExecutor(max_workers=pool_size(len(specs))) as pool:
        futures = {
            pool.submit(add_worktree, workspace, tid, project_id, scope): tid
            for tid, scope in specs
        }
        for fut, tid in futures.items():
            try:
                results[tid] = fut.result()
            except Exception as e:  # a worker must never take the whole batch down
                logger.debug("worktree add raised for %s: %s", tid, e)
                results[tid] = None
    return results


def reset_worktree(workspace: str, task_id: str, project_id: str = "") -> bool:
    """Reset a SURVIVING worktree so the next run of this task starts clean, KEEPING its
    hydration (and its sparse cone). True iff the tree is now genuinely clean.

    The reuse pool: where hydration dominates, resetting an existing checkout beats
    remove + re-add. False means the caller must tear down (remove + add fresh) — a
    half-reset worktree is worse than none, because it silently hands the next run the
    previous one's leftovers.

    **The recipe is three commands, not two.** The obvious pair is
    ``checkout -B <branch> <base>`` + ``clean -fd``; measured, that pair leaves BOTH a
    modified tracked file and a staged index in place — ``checkout -B`` carries local
    modifications across on purpose, and ``clean`` only touches untracked files. So a
    ``reset --hard`` sits between them, and ``clean`` takes ``-x`` as well: ignored files
    are the previous run's build output, and leaving them is exactly the cross-run leak
    this exists to prevent. Both gaps are covered by their own tests.
    """
    path = worktree_path(workspace, task_id, project_id)
    if not os.path.isdir(path):
        return False
    rc, out = _git(workspace, "rev-parse", "HEAD")
    base = out.strip().splitlines()[0] if rc == 0 and out.strip() else "HEAD"
    rc, out = _git(path, "checkout", "-B", branch_name(task_id), base)
    if rc != 0:
        logger.debug("worktree reset checkout failed for %s: %s", task_id, mask_child_output(out))
        return False
    rc, out = _git(path, "reset", "--hard", base)
    if rc != 0:
        logger.debug("worktree reset --hard failed for %s: %s", task_id, mask_child_output(out))
        return False
    rc, out = _git(path, "clean", "-fdx")
    if rc != 0:
        logger.debug("worktree reset clean failed for %s: %s", task_id, mask_child_output(out))
        return False
    return True


def add_worktree(
    workspace: str,
    task_id: str,
    project_id: str = "",
    scope: list[str] | None = None,
) -> str | None:
    """Create (idempotently) a worktree + branch for ``task_id``; return its path,
    or None on failure (caller falls back). Requires at least one commit on HEAD;
    on a fresh repo the caller makes an initial commit first (see ensure_base_commit).

    ``scope`` (from :func:`scope_for_task`) hydrates only those directories; empty or
    absent means a full checkout. A sparse-setup failure is NOT a creation failure —
    the worktree is already usable, just fully hydrated.

    An EXISTING worktree is handed back as-is. That is deliberately NOT the reuse-pool
    reset: this path is also the RESUME path (a loop restarting mid-task finds its
    worker's worktree), and resetting here would delete a live task's in-progress work.
    The reuse reset belongs where the work is finished with: a redo its owner chose after
    the task's work conflicted (:func:`reset_worktree`, called by ``loop.conflicts``).

    Emits one ``TIMING_LOG_PREFIX`` line per call (see the block above): the duration
    covers the work that call actually did, so ``reused`` reports the real cost of the
    idempotent early return rather than a fabricated zero."""
    if not _safe_task_id(task_id):
        logger.warning("worktree add refused — unsafe task_id %r", task_id)
        return None
    started = time.perf_counter()
    path = worktree_path(workspace, task_id, project_id)
    if os.path.isdir(path):
        _log_creation(workspace, task_id, time.perf_counter() - started, OUTCOME_REUSED)
        return path  # already exists (resume / re-schedule)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    branch = branch_name(task_id)
    # -B resets the branch if it somehow exists; -f tolerates a stale registration.
    #
    # ORDER IS THE WHOLE SAVING, and it now buys two things.
    #
    # ``--no-checkout`` ALWAYS, then an explicit hydration step. That splits the call in
    # two along exactly the line the concurrency needs: REGISTRATION is metadata-only and
    # takes the per-repo lock (see ``_REGISTER_LOCKS`` — a concurrent registration is a
    # hard exit 128, not a slow path), while HYDRATION is the expensive half and runs
    # outside the lock, so a batch still overlaps the work that costs the time.
    #
    # With ``scope`` the same split is what saves the work outright: the cone is recorded
    # BEFORE anything is written, so the out-of-scope files are never created at all.
    # Doing it the obvious way round (full ``worktree add``, then ``sparse-checkout
    # set``) is measurably WORSE than today: it pays the entire hydration cost and then
    # pays again to delete what it just wrote.
    with _register_lock(workspace):
        rc, out = _git(
            workspace, "worktree", "add", "-f", "-B", branch, "--no-checkout", path, "HEAD"
        )
    if rc == 0:
        # Both steps stay INSIDE the timed window: hydration is what the log line
        # measures, and the ``checkout`` below IS the hydration. It runs whether or not a
        # cone was recorded — a failed (or absent) ``sparse-checkout set`` must still
        # leave a populated (just full) worktree, not the empty one ``--no-checkout``
        # created.
        if scope:
            set_sparse_scope(path, scope)
        rc, out = _git(path, "checkout")
    elapsed = time.perf_counter() - started
    if rc != 0:
        _log_creation(workspace, task_id, elapsed, OUTCOME_FAILED)
        logger.debug("worktree add failed for %s: %s", task_id, mask_child_output(out))
        return None
    _log_creation(workspace, task_id, elapsed, OUTCOME_CREATED)
    return path


#: Every commit the loop makes is made as git is configured to commit there: ``user.name`` and
#: ``user.email`` from the repository's own settings, the owner's or the machine's. With
#: ``user.useConfigOnly`` git never guesses an author from the account and host names, so a commit
#: with no identity configured fails rather than ships an invented one (:func:`commit_identity`
#: is what the loop asks before it commits, and it asks its owner when there is none).
_CONFIGURED_IDENTITY_ONLY = ("-c", "user.useConfigOnly=true")


def commit_identity(workspace: str) -> tuple[str, str] | None:
    """The name and email git commits as in *workspace*, read from its configuration the way git
    reads them, or ``None`` when either is not set. The loop commits only as that identity, and asks
    its owner to set one rather than invent one."""
    rc_name, name = _git(workspace, "config", "--get", "user.name")
    rc_email, email = _git(workspace, "config", "--get", "user.email")
    name, email = name.strip(), email.strip()
    if rc_name != 0 or rc_email != 0 or not name or not email:
        return None
    return name, email


def has_base_commit(workspace: str) -> bool:
    """Whether HEAD points at a commit a worktree can branch from."""
    rc, _ = _git(workspace, "rev-parse", "--verify", "HEAD")
    return rc == 0


def has_uncommitted_changes(workspace: str, *, untracked: bool = False) -> bool:
    """Whether ``workspace`` has changes to tracked files that are not committed (and, with
    ``untracked``, files git does not track yet).

    A task's worktree is cut from HEAD, so it cannot see them, and merging its branch back
    would collide with them. Untracked files are not counted by default: they block a merge only
    when the branch adds the same path. A status git cannot give reads as uncommitted, the
    direction that keeps work out of worktrees rather than in them, and keeps it from being
    deleted."""
    shown = "--untracked-files=all" if untracked else "--untracked-files=no"
    rc, out = _git(workspace, "status", "--porcelain", shown)
    return rc != 0 or bool(out.strip())


def ensure_base_commit(workspace: str) -> bool:
    """Guarantee HEAD points at a commit so worktrees can branch from it. A freshly
    ``git init``'d repo has an unborn HEAD; stage + commit whatever's there (or an
    empty commit), as git is configured to commit there, so worktrees work. Returns True if HEAD
    has a commit afterward. Only an Unattended loop commits this way: an Attended one puts nothing
    on its owner's branch unasked, so it does not fan out from an unborn HEAD at all."""
    if has_base_commit(workspace):
        return True
    _git(workspace, "add", "-A")
    _git(
        workspace,
        *_CONFIGURED_IDENTITY_ONLY,
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "Initial commit (PersonalClaw Code)",
    )
    return has_base_commit(workspace)


class MergeResult(NamedTuple):
    """Outcome of merging a task worktree back. ``ok`` = clean merge, and ``head`` the commit
    the workspace's branch is at after it. On failure, ``conflicts`` lists the conflicted files
    (empty for a non-conflict git error) — captured BEFORE the merge is aborted, since the abort
    clears the unmerged state and a post-abort ``conflict_paths`` would always read empty (the bug
    this fixes: the caller would misreport every real conflict as a 'git error')."""

    ok: bool
    conflicts: list[str] = []
    head: str = ""
    #: The branch brought nothing the base lacks (its change was already there), so nothing was
    #: merged: no commit and no merge commit, and the task's worktree and branch are removed.
    already: bool = False

    def __bool__(self) -> bool:  # back-compat: callers/tests can still treat it as a bool
        return self.ok


def new_commits(workspace: str, branch: str) -> int | None:
    """How many of ``branch``'s commits bring a change the checked-out branch does not have.

    A commit already there, as itself or as the same patch (a cherry-pick of it, or a second
    worker that made the identical change), is not counted (``git cherry``). ``None`` when git
    cannot say."""
    rc, out = _git(workspace, "cherry", "HEAD", branch)
    if rc != 0:
        return None
    return sum(1 for ln in out.splitlines() if ln.startswith("+"))


def brings_nothing_new(workspace: str, branch: str) -> bool:
    """Whether merging ``branch`` would leave the checked-out tree exactly as it is.

    Every commit of it already there as the same patch, or (where git can say without touching
    the working tree, ``merge-tree --write-tree``) a merge whose result is the tree it has. Two
    workers that made the same change used to land it twice: the first fast-forwarded, the second
    as its own commit joined by a merge commit that changed nothing."""
    if new_commits(workspace, branch) == 0:
        return True
    rc, out = _git(workspace, "merge-tree", "--write-tree", "HEAD", branch)
    if rc != 0:  # a conflict, or a git without --write-tree: the merge itself decides
        return False
    rc_head, head = _git(workspace, "rev-parse", "HEAD^{tree}")
    merged = out.split()
    return rc_head == 0 and bool(merged) and merged[0] == head.strip()


def merge_worktree(workspace: str, task_id: str, project_id: str = "") -> MergeResult:
    """Merge a finished task's branch back into the base branch, then remove its
    worktree. Returns ``MergeResult(ok=True)`` on a clean merge. A conflict/failure
    leaves the worktree in place (so it's not lost) and returns ``ok=False`` with the
    conflicted paths (if any) — the caller surfaces an accurate message."""
    if not _safe_task_id(task_id):
        logger.warning("worktree merge refused — unsafe task_id %r", task_id)
        return MergeResult(ok=False, conflicts=[])
    branch = branch_name(task_id)
    if not commit_pending(workspace, task_id, project_id):
        return MergeResult(ok=False, conflicts=[])
    if brings_nothing_new(workspace, branch):
        logger.info("worktree merge for %s: its change is already on the base", task_id)
        _rc, head = _git(workspace, "rev-parse", "HEAD")
        remove_worktree(workspace, task_id, project_id)
        return MergeResult(ok=True, conflicts=[], head=head.strip(), already=True)
    # merge into base from the main workspace checkout. A non-fast-forward merge
    # (the common case — multiple task branches diverge from base) creates a MERGE
    # COMMIT, authored as git is configured to commit there (the caller asked
    # `commit_identity` first, so a workspace with none never gets this far).
    rc, out = _git(workspace, *_CONFIGURED_IDENTITY_ONLY, "merge", "--no-edit", branch)
    if rc != 0:
        # rc != 0 is NOT necessarily a conflict — only abort an in-progress merge
        # (MERGE_HEAD present). A non-conflict failure (e.g. a git error) left no
        # merge to abort, and `merge --abort` would itself error. Capture the
        # conflicted paths BEFORE aborting — the abort clears them, so reading them
        # afterward (in the caller) would always be empty → every conflict misreported.
        conflicts = conflict_paths(workspace)
        logger.info(
            "worktree merge %s for %s: %s",
            "conflict" if conflicts else "failed",
            task_id,
            mask_child_output(out),
        )
        if conflicts:
            _git(workspace, "merge", "--abort")
        return MergeResult(ok=False, conflicts=conflicts)
    _rc, head = _git(workspace, "rev-parse", "HEAD")
    remove_worktree(workspace, task_id, project_id)
    return MergeResult(ok=True, conflicts=[], head=head.strip())


def commit_pending(workspace: str, task_id: str, project_id: str = "") -> bool:
    """Commit what the task's worktree holds and has not committed, on its branch, as git is
    configured to commit there. True when nothing is left uncommitted (a worktree that is gone has
    nothing). A task's work is committed this way before anyone is shown it to merge, so what its
    owner reviews is what would land."""
    if not _safe_task_id(task_id):
        return False
    wt = worktree_path(workspace, task_id, project_id)
    if not os.path.isdir(wt):
        return True
    # AUTO-WIDEN FIRST. On a sparse worktree, ``add -A`` silently declines to
    # stage out-of-cone paths — exit 0, no error, work gone. Widening the cone to
    # cover whatever the task actually wrote is what makes an out-of-scope write
    # succeed; skip it and a scoped task's stray file vanishes at merge-back.
    widen_for_pending(wt)
    _git(wt, "add", "-A")
    rc, out = _git(wt, "status", "--porcelain")
    if rc == 0 and not out.strip():
        return True
    rc, out = _git(wt, *_CONFIGURED_IDENTITY_ONLY, "commit", "-q", "-m", f"task {task_id}: work")
    if rc != 0:
        logger.info("task %s: its work could not be committed: %s", task_id, mask_child_output(out))
    return rc == 0


def branch_tip(workspace: str, task_id: str) -> str:
    """The commit task *task_id*'s branch is at, or ``""`` when it has none."""
    if not _safe_task_id(task_id):
        return ""
    ref = f"refs/heads/{branch_name(task_id)}"
    rc, out = _git(workspace, "rev-parse", "--verify", "--quiet", ref)
    return out.strip() if rc == 0 else ""


def branch_commits(workspace: str, task_id: str) -> list[str]:
    """The commits task *task_id*'s branch holds that the checked-out branch does not, newest
    first, each as its short hash and subject."""
    if not _safe_task_id(task_id):
        return []
    rc, out = _git(workspace, "log", "--format=%h %s", f"HEAD..{branch_name(task_id)}")
    return [ln for ln in out.splitlines() if ln.strip()] if rc == 0 else []


def commits_not_by(workspace: str, task_id: str, identity: tuple[str, str]) -> list[str]:
    """The commits task *task_id*'s branch holds that the checked-out branch does not and that
    were authored or committed as anyone but *identity* (name, email), newest first, each as its
    short hash and the name and email it carries. A branch git cannot list reads as one such
    commit, so work git cannot vouch for is never merged as its owner's."""
    if not _safe_task_id(task_id):
        return []
    rc, out = _git(
        workspace, "log", "--format=%h%x09%an%x09%ae%x09%cn%x09%ce", f"HEAD..{branch_name(task_id)}"
    )
    if rc != 0:
        return [f"{branch_name(task_id)} (git could not list its commits)"]
    other: list[str] = []
    for line in out.splitlines():
        sha, *people = line.split("\t")
        if len(people) != 4:
            continue
        author, committer = tuple(people[:2]), tuple(people[2:])
        for who in (author, committer):
            if who != identity:
                other.append(f"{sha} {who[0]} <{who[1]}>")
                break
    return other


#: The most of a pending merge's diff one review shows; the rest is named as left out.
REVIEW_DIFF_CHARS = 200_000


def merge_review(workspace: str, task_ids) -> dict:
    """What merging *task_ids*' branches into the workspace's checked-out branch would bring in:
    for each, its branch, the commit it is at, its commits (``HEAD..branch``) and its diff against
    the point it left the branch (``HEAD...branch``), the same text git shows. The diffs together
    are capped at :data:`REVIEW_DIFF_CHARS`, and a review that was cut says so."""
    into = base_branch(workspace)
    budget = REVIEW_DIFF_CHARS
    tasks: list[dict] = []
    cut = False
    for tid in dict.fromkeys(str(t) for t in task_ids):
        if not _safe_task_id(tid):
            continue
        branch = branch_name(tid)
        _rc, stat = _git(workspace, "diff", "--stat", f"HEAD...{branch}")
        rc, diff = _git(workspace, "diff", f"HEAD...{branch}")
        diff = diff if rc == 0 else ""
        if len(diff) > budget:
            diff, cut = diff[:budget], True
        budget -= len(diff)
        tasks.append(
            {
                "task_id": tid,
                "branch": branch,
                "tip": branch_tip(workspace, tid),
                "commits": branch_commits(workspace, tid),
                "stat": stat.strip(),
                "diff": diff,
            }
        )
    return {"into": into, "tasks": tasks, "cut": cut}


# ── what a loop's run has changed in its workspace (its stage gate reads it) ──

#: How a changed file differs from where the loop's run started.
CHANGE_ADDED = "added"
CHANGE_MODIFIED = "modified"
CHANGE_DELETED = "deleted"
#: A file git does not track yet (not added, not committed, and not ignored).
CHANGE_UNTRACKED = "new, not yet added to git"
_CHANGE_STATES = {"A": CHANGE_ADDED, "D": CHANGE_DELETED}

#: A file whose change is longer than this many lines is listed but its diff is not shown: a
#: lockfile or a generated bundle would spend a gate's whole budget on nothing a criterion turns on.
DIFF_LINES_CAP = 1500


class Change(NamedTuple):
    """One file of a workspace that differs from where a loop's run started."""

    path: str  # relative to the workspace
    state: str  # CHANGE_ADDED | CHANGE_MODIFIED | CHANGE_DELETED | CHANGE_UNTRACKED
    lines: int | None  # lines added and removed; None when git counts none (binary, untracked)


class Changes(NamedTuple):
    """What differs in a workspace from where a loop's run started (:func:`changes_since`)."""

    files: list[Change]
    diff: str  # git's diff of the files in ``shown``
    shown: list[str]
    error: str = ""  # why git could not say, or ""


def tracks(workspace: str) -> bool:
    """Whether git tracks *workspace*: it is inside a repository that does not ignore it. A loop's
    own folder in a home that sits inside a checkout is in that checkout's working tree and ignored
    by it, so nothing in it is a change git would show."""
    if not is_git_repo(workspace):
        return False
    rc, _ = _git(workspace, "check-ignore", "-q", ".")
    return rc == 1


def start_point(workspace: str) -> str:
    """Where a loop's run starts in *workspace*, to read what it changes there later
    (:func:`changes_since`): the commit its checked-out branch is at, or git's empty tree when the
    branch has no commit yet (every file the run adds is then a change). ``""`` when git does not
    track the folder (:func:`tracks`)."""
    if not tracks(workspace):
        return ""
    rc, out = _git(workspace, "rev-parse", "--verify", "--quiet", "HEAD^{commit}", errors=False)
    if rc == 0 and out.strip():
        return out.strip()
    rc, out = _git(workspace, "hash-object", "-t", "tree", os.devnull, errors=False)
    return out.strip() if rc == 0 else ""


def changes_since(workspace: str, base: str, *, diff_chars: int, max_files: int = 300) -> Changes:
    """Every file of *workspace* that differs from *base* (:func:`start_point`), committed or not,
    and every file git does not track yet, in path order; with git's diff of as many of them as
    *diff_chars* holds, each whole. Paths are relative to the workspace and the changes limited to
    it (a workspace may be one folder of a larger repository); a rename reads as a deletion and an
    addition. Read through :func:`_git`, so no program the repository names runs."""
    rc, _ = _git(workspace, "cat-file", "-e", base, errors=False)
    if rc != 0:
        return Changes([], "", [], f"the commit it started from ({base[:12]}) is gone")
    spec = ("--no-renames", "--relative", base, "--", ".")
    rc, numstat = _git(
        workspace, "--literal-pathspecs", "diff", "--numstat", "-z", *spec, errors=False
    )
    rc_status, status = _git(
        workspace, "--literal-pathspecs", "diff", "--name-status", "-z", *spec, errors=False
    )
    if rc != 0 or rc_status != 0:
        return Changes([], "", [], "git could not compare the folder with where the loop started")
    lines: dict[str, int | None] = {}
    for record in numstat.split("\0"):
        added, _, rest = record.partition("\t")
        removed, _, path = rest.partition("\t")
        if path:
            lines[path] = (
                int(added) + int(removed) if added.isdigit() and removed.isdigit() else None
            )
    tokens = status.split("\0")
    states = {
        path: _CHANGE_STATES.get(code[:1], CHANGE_MODIFIED)
        for code, path in zip(tokens[0::2], tokens[1::2])
        if path
    }
    files = [Change(p, states.get(p, CHANGE_MODIFIED), lines.get(p)) for p in sorted(states)]
    # A folder git does not track is one entry ("build/"), not every file in it.
    rc, others = _git(
        workspace,
        "--literal-pathspecs",
        "ls-files",
        "--others",
        "--exclude-standard",
        "--directory",
        "--no-empty-directory",
        "-z",
        "--",
        ".",
        errors=False,
    )
    if rc == 0:
        files += [
            Change(p, CHANGE_UNTRACKED, None) for p in sorted(filter(None, others.split("\0")))
        ]
    files = files[:max_files]
    diff, shown = "", []
    for change in files:
        if change.state == CHANGE_UNTRACKED or change.lines is None:
            continue
        if change.lines > DIFF_LINES_CAP:
            continue
        rc, text = _git(
            workspace,
            "--literal-pathspecs",
            "diff",
            "--no-color",
            "--no-ext-diff",
            "--no-textconv",
            *spec[:-1],
            change.path,
            errors=False,
        )
        if rc != 0 or not text.strip():
            continue
        if len(diff) + len(text) > diff_chars:
            if diff:
                break
            text = text[:diff_chars]
        diff += text if text.endswith("\n") else f"{text}\n"
        shown.append(change.path)
    return Changes(files, diff, shown)


def conflict_paths(workspace: str) -> list[str]:
    """Files with unmerged (conflict) entries in ``workspace``, or [] if none / not
    mid-merge. Used to tell a genuine merge CONFLICT apart from a non-conflict merge
    failure so the caller surfaces an accurate message + only aborts a real merge."""
    rc, out = _git(workspace, "diff", "--name-only", "--diff-filter=U")
    if rc != 0:
        return []
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def merge_conflicts(workspace: str, task_id: str) -> list[str] | None:
    """The files merging task *task_id*'s branch into the checked-out branch would conflict on,
    read without touching the working tree (``merge-tree --write-tree``): ``[]`` when it would merge
    cleanly, ``None`` when git cannot say (the branch is gone, or this git has no ``--write-tree``).
    """
    if not branch_exists(workspace, task_id):
        return None
    rc, out = _git(
        workspace,
        "merge-tree",
        "--write-tree",
        "--name-only",
        "--no-messages",
        "HEAD",
        branch_name(task_id),
    )
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    # Both answers open with the tree the merge would write; anything else is git saying it can't.
    if rc not in (0, 1) or not lines or not re.fullmatch(r"[0-9a-f]{40,64}", lines[0]):
        return None
    return lines[1:] if rc == 1 else []


def branch_exists(workspace: str, task_id: str) -> bool:
    """True iff this task's branch still exists in the repo. A done task whose
    branch lingers (its merge previously conflicted/failed and was aborted) still
    has unmerged work — the scheduler retries the merge on resume rather than
    skipping it past forever once its worker session is gone."""
    if not _safe_task_id(task_id):
        return False
    rc, _ = _git(
        workspace, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch_name(task_id)}"
    )
    return rc == 0


def remove_worktree(workspace: str, task_id: str, project_id: str = "") -> None:
    """Remove a task's worktree + delete its branch (best-effort cleanup), and the memory
    partition its worker kept: nothing runs in the worktree again, so nothing would read it."""
    if not _safe_task_id(task_id):
        return
    path = worktree_path(workspace, task_id, project_id)
    _git(workspace, "worktree", "remove", "--force", path)
    _git(workspace, "branch", "-D", branch_name(task_id))
    # if the worktree dir lingers (e.g. remove failed), drop it so it doesn't pile up
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)
    from personalclaw.memory_locality import drop_partition

    drop_partition(path)


class TaskWork(NamedTuple):
    """What one task's worktree and branch hold that its workspace does not.

    ``commits`` counts the commits on the branch whose change the workspace's checked-out branch
    lacks (:func:`new_commits`); ``changed`` counts the files changed in the worktree and not
    committed (untracked ones included). Either is ``None`` when git could not say, and a count
    it could not read is never taken for "nothing there": the work counts as unmerged and is
    kept."""

    task_id: str
    path: str  # the task's worktree, or "" when only its branch is left
    branch: str  # its branch, or "" when only its worktree folder is left
    commits: int | None
    changed: int | None

    @property
    def unmerged(self) -> bool:
        return self.commits != 0 or self.changed != 0


def _lines(rc: int, out: str) -> int | None:
    """The non-blank lines of a git command's output, or ``None`` when it failed."""
    if rc != 0:
        return None
    return sum(1 for ln in out.splitlines() if ln.strip())


def task_work(workspace: str, task_ids, project_id: str = "") -> list[TaskWork]:
    """The worktree and branch each of ``task_ids`` still has in ``workspace``, and what they
    hold that the workspace does not. A task with neither is left out.

    Scoped to the tasks named, never to everything under the worktrees root: two loops on one
    Project share that root, and one loop's ending must not read (or remove) the other's work."""
    if not workspace:
        return []
    rc, out = _git(
        workspace, "for-each-ref", "--format=%(refname:short)", f"refs/heads/{_BRANCH_PREFIX}*"
    )
    branches = {ln.strip() for ln in out.splitlines() if ln.strip()} if rc == 0 else set()
    found: list[TaskWork] = []
    for tid in dict.fromkeys(str(t) for t in task_ids):
        if not _safe_task_id(tid):
            continue
        path = worktree_path(workspace, tid, project_id)
        branch = branch_name(tid)
        has_dir, has_branch = os.path.isdir(path), branch in branches
        if not (has_dir or has_branch):
            continue
        commits = new_commits(workspace, branch) if has_branch else 0
        changed = (
            _lines(*_git(path, "status", "--porcelain", "--untracked-files=all")) if has_dir else 0
        )
        found.append(
            TaskWork(tid, path if has_dir else "", branch if has_branch else "", commits, changed)
        )
    return found


def sweep_finished(workspace: str, task_ids, project_id: str = "") -> list[TaskWork]:
    """At the end of a loop's run: remove each task's worktree and branch that hold nothing the
    workspace lacks (its work was merged, or it made none), and keep the rest. Returns the kept.

    The kept ones hold work nobody has merged — edits its owner may have approved among them —
    so only their owner discards them (:func:`remove_worktree`, from the loop's page)."""
    kept: list[TaskWork] = []
    for work in task_work(workspace, task_ids, project_id):
        if work.unmerged:
            kept.append(work)
        else:
            remove_worktree(workspace, work.task_id, project_id)
    if workspace:
        _git(workspace, "worktree", "prune")
    return kept


def discard(workspace: str, task_ids, project_id: str = "") -> None:
    """Remove each task's worktree and branch, whatever they hold: a deleted loop's, whose
    delete dialog says that work not yet merged goes with it. Only the tasks named are touched,
    so a sibling loop's worktrees under the same Project survive."""
    if not workspace:
        return
    for tid in dict.fromkeys(str(t) for t in task_ids):
        remove_worktree(workspace, tid, project_id)
    _git(workspace, "worktree", "prune")
