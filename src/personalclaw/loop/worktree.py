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
import time
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
    # Resource ceiling (PHF-1): loop worktree git steps are agent-influenced (a loop
    # run drives them). Deliver the ``build`` ceiling (raised NOFILE for many file
    # handles + OOM bias) via the post-exec shim, prepended to argv. Synchronous run
    # off no event loop, so no fork-wedge hazard; the shim applies the limit after exec.
    from personalclaw.sandbox import PROFILE_BUILD, spawn_shim_argv

    try:
        p = subprocess.run(
            spawn_shim_argv(["git", *args], PROFILE_BUILD),
            cwd=workspace,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        out = (p.stdout or b"").decode("utf-8", "replace") + (p.stderr or b"").decode(
            "utf-8", "replace"
        )
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


# ── creation-cost instrumentation (HARNESS-CRAFT §1.1 "measure first", HC-1) ──
#
# §1 is explicitly a MEASURED-bottleneck plan: the hydration tuning in §1.2 (sparse
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
#   ``created`` is the cost §1.2 would attack; ``reused`` is add_worktree's idempotent
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
#: benchmark case §1.1 names — a 10K-file repo — sits exactly on the ``large`` floor
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


# ── sparse hydration, pooled creation, reuse-reset (HARNESS-CRAFT §1.2, HC-2) ──
#
# HC-1's measurement opened the gate: a fan-out of 4 on a 10K-file repo cost 5216 ms
# mean per worktree against a 286 ms ambient floor, so ~2.7 s of even the CHEAPEST
# sample is hydration. §1.2's three levers all attack that hydration, and each has a
# different failure mode, so each is a separate seam here:
#
# * SPARSE — hydrate only the paths a task names. Scope is a HINT (§Risks): it is
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

#: Ceiling on concurrent ``git worktree add`` calls (§1.2 "bounded by os.cpu_count(),
#: ceiling 4"). Matches ``sdlc._POOL_CAP`` — a phase never has more than that many
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
#: is the whole reason §Risks calls task scope a HINT rather than a contract.
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
    the fallback §1.2 requires whenever scope is "absent/unreliable"."""
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
    logger.debug("sparse-checkout set failed in %s: %s", wt_path, out.strip()[:200])
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
        logger.debug("sparse-checkout add failed in %s: %s", wt_path, out.strip()[:200])
        return False
    return True


def widen_for_pending(wt_path: str) -> list[str]:
    """Widen the cone to cover every out-of-cone change present in ``wt_path``;
    return the directories added (``[]`` when nothing needed widening).

    This is the auto-widen of §1.2: an out-of-scope write must SUCCEED, and in git's
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

    Bounded on BOTH sides deliberately. The ceiling is §1.2's ("bounded by
    os.cpu_count(), ceiling 4"): ``git worktree add`` is I/O-bound and each briefly
    takes the repo lock, so more threads than 4 buys contention, not throughput. The
    cpu_count leg keeps a 2-core box from being asked for 4."""
    size = min(os.cpu_count() or 1, POOL_CEILING)
    if n_items is not None:
        size = min(size, n_items)
    return max(1, size)


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
    from concurrent.futures import ThreadPoolExecutor

    results: dict[str, str | None] = {}
    with ThreadPoolExecutor(max_workers=pool_size(len(specs))) as pool:
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

    §1.2's reuse pool: where hydration dominates, resetting an existing checkout beats
    remove + re-add. False means the caller must tear down (remove + add fresh) — a
    half-reset worktree is worse than none, because it silently hands the next run the
    previous one's leftovers.

    **The recipe is three commands, not the plan's two.** §1.2 specifies
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
        logger.debug("worktree reset checkout failed for %s: %s", task_id, out.strip()[:200])
        return False
    rc, out = _git(path, "reset", "--hard", base)
    if rc != 0:
        logger.debug("worktree reset --hard failed for %s: %s", task_id, out.strip()[:200])
        return False
    rc, out = _git(path, "clean", "-fdx")
    if rc != 0:
        logger.debug("worktree reset clean failed for %s: %s", task_id, out.strip()[:200])
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
    The reuse reset belongs at the phase/redo boundary where the work is finished with —
    see :func:`reset_worktree` and its caller in ``sdlc._reap_merge_done``.

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
    # ORDER IS THE WHOLE SAVING. With ``scope`` we add ``--no-checkout`` first, record
    # the cone, and only then hydrate — so the out-of-scope files are never written at
    # all. Doing it the obvious way round (full ``worktree add``, then
    # ``sparse-checkout set``) is measurably WORSE than today: it pays the entire
    # hydration cost and then pays again to delete what it just wrote.
    args = ["worktree", "add", "-f", "-B", branch]
    if scope:
        args.append("--no-checkout")
    rc, out = _git(workspace, *args, path, "HEAD")
    if rc == 0 and scope:
        # Both steps stay INSIDE the timed window: hydration is what the HC-1 log line
        # measures, and with sparse on, the ``checkout`` below IS the hydration. The
        # checkout runs whether or not the cone was recorded — a failed
        # ``sparse-checkout set`` must still leave a populated (just full) worktree, not
        # the empty one ``--no-checkout`` created.
        set_sparse_scope(path, scope)
        rc, out = _git(path, "checkout")
    elapsed = time.perf_counter() - started
    if rc != 0:
        _log_creation(workspace, task_id, elapsed, OUTCOME_FAILED)
        logger.debug("worktree add failed for %s: %s", task_id, out.strip()[:200])
        return None
    _log_creation(workspace, task_id, elapsed, OUTCOME_CREATED)
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
        workspace,
        "-c",
        "user.name=PersonalClaw",
        "-c",
        "user.email=code@personalclaw.local",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "Initial commit (PersonalClaw Code)",
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
        # AUTO-WIDEN FIRST (HC-2). On a sparse worktree, ``add -A`` silently declines to
        # stage out-of-cone paths — exit 0, no error, work gone. Widening the cone to
        # cover whatever the task actually wrote is what makes an out-of-scope write
        # succeed; skip it and a scoped task's stray file vanishes at merge-back.
        widen_for_pending(wt)
        _git(wt, "add", "-A")
        _git(
            wt,
            "-c",
            "user.name=PersonalClaw",
            "-c",
            "user.email=code@personalclaw.local",
            "commit",
            "-q",
            "-m",
            f"task {task_id}: work",
        )
    # merge into base from the main workspace checkout. A non-fast-forward merge
    # (the common case — multiple task branches diverge from base) creates a MERGE
    # COMMIT, which needs a committer identity; supply the same isolated identity
    # used elsewhere so a freshly git-init'd workspace with no user/email configured
    # (e.g. a clean container) doesn't fail the merge + falsely wedge as a conflict.
    rc, out = _git(
        workspace,
        "-c",
        "user.name=PersonalClaw",
        "-c",
        "user.email=code@personalclaw.local",
        "merge",
        "--no-edit",
        branch,
    )
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
            out.strip()[:200],
        )
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
    rc, _ = _git(
        workspace, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch_name(task_id)}"
    )
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
        rc, out = _git(
            workspace, "for-each-ref", "--format=%(refname:short)", f"refs/heads/{_BRANCH_PREFIX}*"
        )
        if rc == 0:
            for ref in (ln.strip() for ln in out.splitlines() if ln.strip()):
                _git(workspace, "branch", "-D", ref)
    if os.path.isdir(root):
        shutil.rmtree(root, ignore_errors=True)
