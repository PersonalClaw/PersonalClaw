"""Workspace time-travel — local git history over the state a human edits (§5).

This is the "undo what just happened" half of durability, deliberately distinct
from the scheduled snapshots (§3), which are for disasters. The user edits a
skill, the agent rewrites a memory note, a config toggle turns out to be wrong —
none of that is a disaster, and none of it should require restoring a tarball.

Shape of the mechanism:

* **Roots.** A small set of state trees get their own git repository: the config
  document + entity settings, ``skills/``, the memory markdown workspace, the
  prompt surfaces, and per-project context. Each root's repo is a *separate git
  directory* under ``<home>/state-history/``, with the tracked tree as its work
  tree. No ``.git`` ever appears inside the user's home or workspace, so nothing
  else in PersonalClaw (or the user's own tooling) trips over one.

* **Deny-by-default ignore.** Every root's ``info/exclude`` starts by excluding
  everything and then re-includes exactly the declared paths. That is what makes
  "secrets are gitignored" a structural property rather than a denylist someone
  has to keep current: a root whose work tree is the home directory cannot commit
  ``.local_secret`` or ``security/`` because it never re-includes them. A second,
  pattern-based layer catches secret-shaped files that could appear *inside* a
  tracked tree (a stray ``.env`` under ``skills/``).

* **Rollback vs revert.** ``rollback`` is a hard reset with the prior HEAD saved
  into a service-owned ref, so the commits you rolled away stay listable and
  forward travel is possible. ``revert`` is git's inverse commit, so
  non-overlapping later edits survive; an overlap fails loudly naming the
  blocking files instead of leaving a half-merged tree.

* **Preview first.** Both operations have a ``preview_*`` sibling that returns
  the affected files and per-file diffs, and neither destructive call is reachable
  from the API without the user confirming that preview.

Time-travel history is **local only**. It never enters an export, a snapshot or a
sync transport: ``state-history`` is in the inventory's ignored set, which is the
one place that decides what travels.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
import os
import re
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

#: Directory under the home that holds one git directory per root.
HISTORY_DIR_NAME = "state-history"

#: Namespace for service-owned refs. A rollback parks the prior HEAD here so the
#: commits it reset away remain reachable (git would otherwise garbage-collect
#: them) and therefore listable for forward travel.
REF_PREFIX = "refs/personalclaw/history"

#: Identity used for history commits. Deliberately not the user's git identity:
#: these commits are made *by* PersonalClaw, and borrowing the user's name would
#: make the history lie about who edited what.
COMMIT_NAME = "PersonalClaw"
COMMIT_EMAIL = "history@personalclaw.local"

#: Diffs larger than this are listed, not rendered (§5: "diffs >1MB listed not
#: rendered") — a preview must stay a preview, not a memory event.
MAX_DIFF_BYTES = 1_000_000

#: Secret-shaped and machine-local files that must never enter a commit even when
#: they appear inside an otherwise-tracked tree. This is the SECOND layer; the
#: first is the deny-by-default allowlist each root generates. Databases are here
#: too: a binary blob per hourly commit would bloat the repo and diff to nothing,
#: and the sqlite stores have their own shard/snapshot path.
SECRET_EXCLUDE: tuple[str, ...] = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "id_rsa*",
    "id_ed25519*",
    "credentials.json",
    ".local_secret",
    "session_key",
    "sessions.json",
    "*.db",
    "*.db-wal",
    "*.db-shm",
    "*.sqlite",
    "*.sqlite3",
)

#: Noise that would otherwise produce commits nobody wants to read.
NOISE_EXCLUDE: tuple[str, ...] = (
    "__pycache__/",
    "*.pyc",
    ".DS_Store",
    "*.tmp",
    "*.log",
    "*.lock",
    "*.pid",
    ".git/",
)

# ── writing surface ────────────────────────────────────────────────────────
#
# "What changed while I slept" needs each commit to name the surface that caused
# the write. The surface is captured in the WRITER's context (that is where the
# knowledge lives) and carried to the commit, which may happen later on the
# debouncer's thread — so a ContextVar, not a global.
SURFACE_INTERACTIVE = "interactive"
SURFACE_SCHEDULED = "scheduled"
SURFACE_BACKGROUND = "background"

_surface: contextvars.ContextVar[str] = contextvars.ContextVar(
    "personalclaw_history_surface", default=SURFACE_INTERACTIVE
)


def current_surface() -> str:
    return _surface.get()


@contextlib.contextmanager
def writing_surface(surface: str) -> Iterator[None]:
    """Mark writes made in this context as coming from *surface*."""
    token = _surface.set(surface)
    try:
        yield
    finally:
        _surface.reset(token)


def is_unattended_surface(surface: str) -> bool:
    """Whether *surface* is the "while I slept" kind — anything not interactive."""
    return surface != SURFACE_INTERACTIVE


# ── errors ─────────────────────────────────────────────────────────────────


class HistoryError(RuntimeError):
    """A time-travel operation could not be completed."""


class OverlapError(HistoryError):
    """A revert would collide with later edits. Names the blocking files.

    Loud on purpose: silently taking one side of a conflict is how a "safe undo"
    destroys work the user never asked to lose.
    """

    def __init__(self, files: list[str]) -> None:
        self.files = files
        listed = ", ".join(files) or "unknown files"
        super().__init__(
            "revert overlaps later edits and was aborted; conflicting files: " + listed
        )


# ── roots ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class HistoryRoot:
    """One tracked state tree.

    ``include`` holds work-tree-relative allowlist specs. A spec is either a
    top-level name (``config.json``, ``skills``) or a one-level-deep wildcard
    (``*/context``); everything not named is excluded, which is what keeps
    secrets out structurally.
    """

    id: str
    label: str
    worktree: Path
    include: tuple[str, ...]
    #: Part of the hourly memory-tree commit (§3's deferred piece).
    memory: bool = False

    @property
    def git_dir_name(self) -> str:
        return f"{self.id}.git"


def history_dir(home: Path | None = None) -> Path:
    """Where the git directories live. Never inside a tracked tree."""
    return _home(home) / HISTORY_DIR_NAME


def _home(home: Path | None = None) -> Path:
    if home is not None:
        return Path(home)
    from personalclaw.durability.service import active_home

    return active_home()


def is_history_path(path: Path | str, *, home: Path | None = None) -> bool:
    """Whether *path* belongs to time-travel's own storage.

    Used to keep history out of every transport and to stop the post-write
    notifier from chasing its own tail.
    """
    text = str(path).replace("\\", "/")
    if HISTORY_DIR_NAME + "/" in text + "/":
        parts = [p for p in text.split("/") if p]
        if HISTORY_DIR_NAME in parts:
            return True
    try:
        Path(path).resolve().relative_to(history_dir(home).resolve())
    except (ValueError, OSError):
        return False
    return True


def roots(home: Path | None = None, workspace: Path | None = None) -> tuple[HistoryRoot, ...]:
    """The tracked roots, resolved against the ACTIVE home and workspace.

    Resolved per call rather than cached at import: an isolated test/dev home is
    set through the environment, and a module-level constant frozen at import
    time is exactly how a "safe" subsystem ends up writing to the real home.
    """
    h = _home(home)
    if workspace is None:
        from personalclaw.config.loader import workspace_root

        workspace = workspace_root()
    ws = Path(workspace)
    return (
        HistoryRoot(
            id="config",
            label="Configuration",
            worktree=h,
            # The work tree is the HOME, which holds the credential store and the
            # gateway secret. Naming only these two is what makes a secret leak
            # impossible here rather than unlikely.
            include=("config.json", "entity_settings"),
        ),
        HistoryRoot(id="skills", label="Skills", worktree=h / "skills", include=()),
        HistoryRoot(
            id="prompts",
            label="Prompts",
            worktree=h,
            include=("prompts", "prompt_snippets"),
        ),
        HistoryRoot(
            id="projects",
            label="Project context",
            worktree=h / "projects",
            include=("*/context",),
        ),
        HistoryRoot(
            id="memory",
            label="Memory",
            worktree=ws,
            # `memory` is the default tree; `_ext` holds the per-external-cwd
            # trees, so both the plain and the project-scoped memory are covered.
            include=("memory", "_ext"),
            memory=True,
        ),
    )


def root_by_id(root_id: str, *, home: Path | None = None) -> HistoryRoot | None:
    return next((r for r in roots(home) if r.id == root_id), None)


def root_for_path(path: Path | str, *, home: Path | None = None) -> HistoryRoot | None:
    """The root that would track *path*, or None.

    Longest work-tree match wins so the memory root claims a workspace path even
    when the workspace happens to sit inside the home.
    """
    try:
        target = Path(path).resolve()
    except OSError:
        return None
    best: HistoryRoot | None = None
    best_len = -1
    for root in roots(home):
        try:
            rel = target.relative_to(root.worktree.resolve())
        except (ValueError, OSError):
            continue
        if not _included(root, rel):
            continue
        depth = len(root.worktree.resolve().parts)
        if depth > best_len:
            best, best_len = root, depth
    return best


def _included(root: HistoryRoot, rel: Path) -> bool:
    """Whether a work-tree-relative path falls inside the root's allowlist."""
    parts = rel.parts
    if not parts:
        return False
    if not root.include:
        return True
    for spec in root.include:
        spec_parts = tuple(spec.split("/"))
        if len(parts) < len(spec_parts):
            continue
        if all(
            sp == "*" or sp == p
            for sp, p in zip(spec_parts, parts[: len(spec_parts)], strict=False)
        ):
            return True
    return False


# ── git plumbing ───────────────────────────────────────────────────────────


def _exclude_lines(root: HistoryRoot) -> list[str]:
    """The root's ``info/exclude`` body: deny-by-default, then the denylist.

    Order is load-bearing. Git applies the LAST matching pattern, so the secret
    patterns come after the allowlist negations and therefore win: re-including
    ``skills/`` cannot re-include ``skills/.env``.
    """
    lines = [
        "# Generated by PersonalClaw time-travel. Deny-by-default: nothing is",
        "# tracked unless re-included below, and secrets are re-excluded last.",
    ]
    if root.include:
        depth1 = [s for s in root.include if "/" not in s]
        depth2 = [s for s in root.include if "/" in s]
        lines.append("/*")
        for spec in depth1:
            lines.append(f"!/{spec}")
            lines.append(f"!/{spec}/")
        if depth2:
            # Re-include top-level directories so git descends, then exclude what
            # is inside them, then re-include the named second level.
            lines.append("!/*/")
            lines.append("/*/*")
            for spec in depth2:
                tail = spec.split("/", 1)[1]
                lines.append(f"!/*/{tail}/")
    lines.append("")
    lines.append("# secrets and machine-local material — never committed")
    lines.extend(SECRET_EXCLUDE)
    lines.append("")
    lines.append("# noise")
    lines.extend(NOISE_EXCLUDE)
    return lines


def git_dir(root: HistoryRoot, *, home: Path | None = None) -> Path:
    return history_dir(home) / root.git_dir_name


def _assert_service_git_dir(gd: Path, *, home: Path | None = None) -> None:
    """Refuse to run git against anything but our own history repos.

    This module runs ``reset --hard`` for a living. The one failure mode that
    could not be walked back is pointing it at a repository that is not ours —
    the user's own project, or the PersonalClaw checkout itself. So every call
    asserts the git directory sits under ``<home>/state-history`` and carries our
    naming, and there is no code path that takes a caller-supplied git dir.
    """
    root_dir = history_dir(home)
    try:
        gd.resolve().relative_to(root_dir.resolve())
    except (ValueError, OSError) as exc:
        raise HistoryError(f"refusing to operate on a git dir outside {root_dir}: {gd}") from exc
    if gd.name.endswith(".git") is False:
        raise HistoryError(f"not a PersonalClaw history git dir: {gd}")


def _git_env() -> dict[str, str]:
    env = dict(os.environ)
    # The user's global/system git config must not reach these repos: a global
    # `core.hooksPath`, `commit.gpgsign`, or a template dir would either run
    # third-party code on every history commit or block it behind a signing key.
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return env


def _git(
    root: HistoryRoot,
    *args: str,
    home: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    gd = git_dir(root, home=home)
    _assert_service_git_dir(gd, home=home)
    cmd = [
        "git",
        f"--git-dir={gd}",
        f"--work-tree={root.worktree}",
        "-c",
        f"user.name={COMMIT_NAME}",
        "-c",
        f"user.email={COMMIT_EMAIL}",
        "-c",
        "commit.gpgsign=false",
        "-c",
        "core.hooksPath=",
        "-c",
        "advice.detachedHead=false",
        *args,
    ]
    proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
        cmd,
        cwd=str(root.worktree),
        env=_git_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    if check and proc.returncode != 0:
        raise HistoryError(
            f"git {' '.join(args[:2])} failed for root {root.id}: "
            f"{(proc.stderr or proc.stdout).strip()}"
        )
    return proc


def git_available() -> bool:
    """Whether a usable ``git`` exists. Time-travel degrades to off without one."""
    try:
        proc = subprocess.run(  # noqa: S603
            ["git", "--version"],  # noqa: S607 — PATH lookup is the point
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return False
    return proc.returncode == 0


def ensure_repo(root: HistoryRoot, *, home: Path | None = None) -> Path:
    """Create (or refresh) the root's repo and return its git dir. Idempotent.

    The exclude file is rewritten every call so a changed allowlist takes effect
    without a migration — the alternative is a repo that keeps committing a path
    a later release removed from the allowlist.
    """
    gd = git_dir(root, home=home)
    _assert_service_git_dir(gd, home=home)
    root.worktree.mkdir(parents=True, exist_ok=True)
    if not (gd / "HEAD").is_file():
        gd.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(  # noqa: S603
            ["git", "init", "--bare", "--quiet", str(gd)],  # noqa: S607
            env=_git_env(),
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise HistoryError(f"could not init history repo for {root.id}: {proc.stderr.strip()}")
        # 0700: the history holds the user's memory notes and configuration.
        with contextlib.suppress(OSError):
            gd.chmod(0o700)
        _git(root, "config", "core.bare", "false", home=home)
    info = gd / "info"
    info.mkdir(parents=True, exist_ok=True)
    (info / "exclude").write_text("\n".join(_exclude_lines(root)) + "\n", encoding="utf-8")
    return gd


def repo_exists(root: HistoryRoot, *, home: Path | None = None) -> bool:
    return (git_dir(root, home=home) / "HEAD").is_file()


def has_head(root: HistoryRoot, *, home: Path | None = None) -> bool:
    if not repo_exists(root, home=home):
        return False
    return _git(root, "rev-parse", "--verify", "-q", "HEAD", home=home, check=False).returncode == 0


def commit_count(root: HistoryRoot, *, home: Path | None = None) -> int:
    """How many commits the root has. The vacuity floor for every history rail."""
    if not has_head(root, home=home):
        return 0
    out = _git(root, "rev-list", "--count", "HEAD", home=home).stdout.strip()
    return int(out or 0)


# ── committing ─────────────────────────────────────────────────────────────


def commit(
    root: HistoryRoot,
    *,
    surface: str | None = None,
    reason: str = "",
    home: Path | None = None,
) -> str | None:
    """Commit whatever changed in *root*. Returns the sha, or None if nothing did.

    ``None`` is the common case and is not an error: the debouncer fires after a
    write that may have produced identical bytes, and an empty commit per write
    would make ``git log`` useless.
    """
    ensure_repo(root, home=home)
    surface = surface or current_surface()
    _git(root, "add", "-A", "--", ".", home=home)
    staged = _git(root, "diff", "--cached", "--name-only", home=home).stdout.strip()
    if not staged:
        return None
    count = len(staged.splitlines())
    subject = f"{root.label}: {count} file{'s' if count != 1 else ''} changed"
    if reason:
        subject = f"{subject} ({reason})"
    body = f"Surface: {surface}\nRoot: {root.id}\n"
    _git(root, "commit", "--quiet", "-m", subject, "-m", body, home=home)
    return _git(root, "rev-parse", "HEAD", home=home).stdout.strip()


_TRAILER_RE = re.compile(r"^Surface:\s*(\S+)", re.MULTILINE)


def timeline(
    root: HistoryRoot,
    *,
    limit: int = 50,
    unattended_only: bool = False,
    home: Path | None = None,
) -> list[dict]:
    """Commits newest-first: sha, unix time, subject, surface, file count.

    ``unattended_only`` is the panel's "what changed while I slept" filter — the
    commits whose writes did not come from someone sitting at the dashboard.
    """
    if not has_head(root, home=home):
        return []
    sep = "\x1e"
    fmt = f"%H{sep}%at{sep}%s{sep}%b{sep}"
    proc = _git(root, "log", f"--max-count={max(1, limit)}", f"--format={fmt}%x1f", home=home)
    entries: list[dict] = []
    for chunk in proc.stdout.split("\x1f"):
        chunk = chunk.strip("\n")
        if not chunk.strip():
            continue
        parts = chunk.split(sep)
        if len(parts) < 3:
            continue
        sha, at, subject = parts[0].strip(), parts[1].strip(), parts[2]
        body = parts[3] if len(parts) > 3 else ""
        m = _TRAILER_RE.search(body)
        surface = m.group(1) if m else SURFACE_INTERACTIVE
        if unattended_only and not is_unattended_surface(surface):
            continue
        entries.append(
            {
                "sha": sha,
                "short": sha[:8],
                "at": int(at or 0),
                "subject": subject,
                "surface": surface,
                "unattended": is_unattended_surface(surface),
            }
        )
    return entries


# ── preview ────────────────────────────────────────────────────────────────


def _resolve(root: HistoryRoot, sha: str, *, home: Path | None = None) -> str:
    """Resolve *sha* to a full commit id inside this repo, or refuse.

    Anything the caller hands us goes through here: a rollback target is a
    destructive argument, and `rev-parse` on an arbitrary string would happily
    accept `HEAD@{upstream}` or a path.
    """
    if not re.fullmatch(r"[0-9a-fA-F]{4,64}", sha or ""):
        raise HistoryError(f"not a commit id: {sha!r}")
    proc = _git(root, "rev-parse", "--verify", "-q", f"{sha}^{{commit}}", home=home, check=False)
    resolved = proc.stdout.strip()
    if proc.returncode != 0 or not resolved:
        raise HistoryError(f"unknown commit {sha} in root {root.id}")
    return resolved


def _diff_preview(root: HistoryRoot, frm: str, to: str, *, home: Path | None = None) -> list[dict]:
    """Per-file name/status plus the patch, with the >1MB render cutoff."""
    name_status = _git(root, "diff", "--name-status", "-z", frm, to, home=home).stdout
    fields = [f for f in name_status.split("\0") if f]
    files: list[dict] = []
    i = 0
    while i < len(fields):
        status = fields[i]
        if status.startswith(("R", "C")) and i + 2 < len(fields):
            path = fields[i + 2]
            i += 3
        else:
            path = fields[i + 1] if i + 1 < len(fields) else ""
            i += 2
        if not path:
            continue
        patch = _git(root, "diff", frm, to, "--", path, home=home).stdout
        size = len(patch.encode("utf-8", "replace"))
        files.append(
            {
                "path": path,
                "status": status[:1],
                "bytes": size,
                "rendered": size <= MAX_DIFF_BYTES,
                "diff": patch if size <= MAX_DIFF_BYTES else "",
            }
        )
    return files


def preview_rollback(root: HistoryRoot, sha: str, *, home: Path | None = None) -> dict:
    """What a hard reset to *sha* would change in the work tree."""
    target = _resolve(root, sha, home=home)
    head = _git(root, "rev-parse", "HEAD", home=home).stdout.strip()
    files = _diff_preview(root, head, target, home=home)
    dropped = _git(root, "rev-list", "--count", f"{target}..{head}", home=home).stdout.strip()
    return {
        "operation": "rollback",
        "root": root.id,
        "target": target,
        "head": head,
        "files": files,
        "commits_rolled_away": int(dropped or 0),
        "reversible": True,
    }


def preview_revert(root: HistoryRoot, sha: str, *, home: Path | None = None) -> dict:
    """What an inverse commit of *sha* would change.

    The inverse patch of a commit is the diff from that commit back to its
    parent, which is exactly what ``git revert`` will try to apply.
    """
    target = _resolve(root, sha, home=home)
    parents = _git(root, "rev-list", "--parents", "-n", "1", target, home=home).stdout.split()
    if len(parents) < 2:
        # The root commit has no parent; reverting it would empty the tree.
        empty = _git(root, "hash-object", "-t", "tree", "/dev/null", home=home).stdout.strip()
        files = _diff_preview(root, target, empty, home=home)
    else:
        files = _diff_preview(root, target, parents[1], home=home)
    return {
        "operation": "revert",
        "root": root.id,
        "target": target,
        "head": _git(root, "rev-parse", "HEAD", home=home).stdout.strip(),
        "files": files,
        "commits_rolled_away": 0,
        "reversible": True,
    }


def preview(root: HistoryRoot, sha: str, *, operation: str, home: Path | None = None) -> dict:
    if operation == "rollback":
        return preview_rollback(root, sha, home=home)
    if operation == "revert":
        return preview_revert(root, sha, home=home)
    raise HistoryError(f"unknown time-travel operation {operation!r}")


# ── destructive operations ─────────────────────────────────────────────────


def rollback(root: HistoryRoot, sha: str, *, home: Path | None = None) -> dict:
    """Hard-reset *root* to *sha*, parking the prior HEAD in a service ref.

    Ignored files — the credential store, ``.env``, anything the allowlist never
    admitted — are untouched: ``reset --hard`` only rewrites tracked paths, and
    this module never runs ``git clean``. That is the whole reason secrets can be
    both gitignored and preserved across a rollback.
    """
    target = _resolve(root, sha, home=home)
    prior = _git(root, "rev-parse", "HEAD", home=home).stdout.strip()
    ref = f"{REF_PREFIX}/rollback-{int(time.time())}-{prior[:8]}"
    _git(root, "update-ref", ref, prior, home=home)
    _git(root, "reset", "--hard", "--quiet", target, home=home)
    logger.info(
        "time-travel: rolled %s back to %s (prior HEAD kept at %s)", root.id, target[:8], ref
    )
    return {
        "operation": "rollback",
        "root": root.id,
        "head": target,
        "prior_head": prior,
        "prior_ref": ref,
    }


def revert(root: HistoryRoot, sha: str, *, home: Path | None = None) -> dict:
    """Apply the inverse of *sha* as a new commit, keeping later edits.

    Raises :class:`OverlapError` naming the blocking files when a later edit
    touched the same lines, after aborting so the work tree is left exactly as it
    was. Never resolves a conflict on the user's behalf.
    """
    target = _resolve(root, sha, home=home)
    proc = _git(root, "revert", "--no-edit", target, home=home, check=False)
    if proc.returncode != 0:
        conflicted = _git(
            root, "diff", "--name-only", "--diff-filter=U", home=home, check=False
        ).stdout.split()
        _git(root, "revert", "--abort", home=home, check=False)
        raise OverlapError(sorted(set(conflicted)))
    return {
        "operation": "revert",
        "root": root.id,
        "head": _git(root, "rev-parse", "HEAD", home=home).stdout.strip(),
        "reverted": target,
    }


def forward_refs(root: HistoryRoot, *, home: Path | None = None) -> list[dict]:
    """Service refs parked by past rollbacks — the forward-travel entry points."""
    if not repo_exists(root, home=home):
        return []
    # \x1f (unit separator), NOT \x1e: Python's `str.splitlines` treats \x1e
    # (record separator) as a LINE boundary, so an \x1e-delimited record read back
    # with splitlines shatters into single fields and every row is silently
    # dropped. That is how this function returned an empty list over a repo that
    # demonstrably held the ref.
    proc = _git(
        root,
        "for-each-ref",
        "--format=%(refname)\x1f%(objectname)\x1f%(creatordate:unix)",
        REF_PREFIX,
        home=home,
        check=False,
    )
    out: list[dict] = []
    for line in proc.stdout.split("\n"):
        parts = line.split("\x1f")
        if len(parts) < 3:
            continue
        out.append({"ref": parts[0], "sha": parts[1], "at": int(parts[2] or 0)})
    return sorted(out, key=lambda r: r["at"], reverse=True)


def status(*, home: Path | None = None) -> dict:
    """Per-root summary for the panel and the durability status card."""
    out = []
    for root in roots(home):
        exists = repo_exists(root, home=home)
        out.append(
            {
                "id": root.id,
                "label": root.label,
                "worktree": str(root.worktree),
                "exists": exists,
                "commits": commit_count(root, home=home) if exists else 0,
                "memory": root.memory,
            }
        )
    return {"git": git_available(), "dir": str(history_dir(home)), "roots": out}


def commit_memory_roots(*, home: Path | None = None, reason: str = "hourly") -> list[dict]:
    """Commit the memory-tree roots — §3's deferred hourly git commit.

    Marked ``scheduled`` so the panel's "what changed while I slept" filter has
    something real to show: these are precisely the commits nobody was watching.
    """
    results: list[dict] = []
    with writing_surface(SURFACE_SCHEDULED):
        for root in roots(home):
            if not root.memory:
                continue
            try:
                sha = commit(root, reason=reason, home=home)
            except HistoryError as exc:
                logger.warning("time-travel: hourly commit failed for %s: %s", root.id, exc)
                results.append({"root": root.id, "ok": False, "error": str(exc)})
                continue
            results.append({"root": root.id, "ok": True, "sha": sha, "changed": bool(sha)})
    return results
