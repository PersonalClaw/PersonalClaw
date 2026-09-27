"""The places PersonalClaw's file surfaces may touch: the roots, and the one check against them.

The file explorer (``dashboard/handlers/files.py``) browses and edits only inside these roots. A
file-backed artifact (``artifacts/source_files.py``) points only at a file :func:`admit` accepts,
against these roots plus each loop's own folder. This module sits below both because the artifact
store is a provider, and a provider does not import the dashboard.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from pathlib import Path

from personalclaw.config import loader as config_loader
from personalclaw.security import system_subtrees

logger = logging.getLogger(__name__)

# System roots that the directory-picking / search surfaces refuse to browse,
# search, OR create under — picking a workspace/project folder is for the user's
# own code, never system internals. is_sensitive_path() only covers ~/credential
# dirs, so this is the complementary system-root guard.
#
# The subtree list is read through personalclaw.security.system_subtrees() (the single source
# of truth shared with the Code workspace validation, including its carve-out for the running
# account's own home) so the surfaces can't drift. NOTE: this is a SUBTREE-only check (no
# mount/temp PARENT blocking, unlike security.is_system_path) — the directory BROWSER +
# @-search must be able to navigate INTO /Volumes, /var, /tmp to reach a real workspace beneath
# them. create-dir/workspace-bind use the stricter full is_system_path (parents blocked) since
# you never create/bind AT a bare parent.


def is_system_root(path: str) -> bool:
    """True iff *path* is the filesystem root or sits under a protected system root
    (an already realpath'd absolute path is expected)."""
    return path == "/" or any(path == r or path.startswith(r + os.sep) for r in system_subtrees())


def screenshot_dir() -> Path:
    """Where a captured screenshot lands, resolved AT CALL TIME (CRE-8).

    This was a module-level ``_SCREENSHOT_DIR`` bound at import, and that is the whole
    defect: a constant is already a value, so no test fixture can redirect it and a
    ``$PERSONALCLAW_HOME`` set after first import is ignored for the life of the process.
    MEASURED in CI, which is where the difference shows: with no ``~/.personalclaw`` to
    begin with, the capture handler created the developer's real home just by resolving
    this path — the suite's real-home rail caught it as `dir-entries-changed screenshots`.
    """
    return config_loader.config_dir() / "screenshots"


def dashboard_roots() -> list[tuple[str, str]]:
    """Return the labeled root directories the dashboard is allowed to surface.

    Each entry is ``(label, realpath)``. These are the boundaries the file
    explorer browses and the allowlist :func:`admit`
    enforces: the workspace, the outbox, uploads, and the folders loops and
    projects work in. Roots that fail to resolve (e.g. not configured) are
    skipped. The order is user-facing-first (workspace) so the explorer can
    default to it.

    🔴 THE HOME ITSELF IS NEVER A ROOT. ``config.json``, ``mcp.json``, the automations, the
    agent files and every app's install are plain files under PERSONALCLAW_HOME, so editing one
    in Files could turn YOLO on or define an MCP command past every refusal the config PATCH,
    the MCP routes and the automation routes make, and reading one could show the MCP servers'
    credentials. Only folders inside it that hold work are roots (the workspace, outbox,
    uploads, screenshots, a project's context, a greenfield code loop's folder). For a request
    the gateway scoped to an app (``permissions.request_app``), a root that CONTAINS the home,
    such as a loop or project workspace bound to ``~``, is left out as well. The realpath checks
    in :func:`admit` refuse a symlink or ``..`` back out of a root.
    """
    roots = all_dashboard_roots()
    from personalclaw.apps.permissions import request_app

    if not request_app():
        return roots
    from personalclaw.config.loader import config_dir

    home = os.path.realpath(str(config_dir()))
    return [(label, r) for label, r in roots if not (home == r or home.startswith(r + os.sep))]


def all_dashboard_roots() -> list[tuple[str, str]]:
    """Every root :func:`dashboard_roots` may surface, before the app-scoped filter."""
    from personalclaw.config.loader import config_dir, outbox_dir

    candidates: list[tuple[str, str]] = []

    def _add(label: str, path_factory) -> None:
        try:
            candidates.append((label, os.path.realpath(str(path_factory()))))
        except Exception:
            pass

    # Default workspace root used by chat sessions and ACP agents — the
    # primary place users create/consume files, so list it first.
    try:
        from personalclaw.config.loader import workspace_root

        _add("Workspace", workspace_root)
    except Exception:
        pass
    _add("Outbox", outbox_dir)
    # Uploads must follow the ACTIVE home (config_dir()), NOT a hardcoded ~/.personalclaw: a
    # gateway on a custom PERSONALCLAW_HOME (every dev instance) would otherwise browse AND edit
    # the developer's REAL home via the write allowlist (#294). config_dir() re-reads
    # PERSONALCLAW_HOME live, and the factory is deferred so the home is resolved per request.
    _add("Uploads", lambda: os.path.join(config_dir(), "uploads"))
    # Where a native screen capture lands, so its chat chip's Open can show it.
    _add("Screenshots", screenshot_dir)

    # Loop workspaces — a Loop (typically a code kind, but any kind may) can bind an
    # arbitrary (brownfield) directory anywhere on disk; its cockpit (file tree +
    # editor) must be allowed to browse + edit it. Surface each existing loop's
    # workspace_dir as a root so the allowlist admits it. Best-effort: never let a
    # loop-store hiccup break the file explorer for the normal roots.
    # A user-bound workspace (loop OR project) may point anywhere — but a bound
    # workspace that IS (or sits under) a protected system root must NEVER become a
    # browsable root, or /etc, /usr, / etc. would leak via a workspace binding. The
    # allowlist check in :func:`admit` does not re-apply the system-root
    # guard, so we enforce it HERE at root derivation (the single admission point).
    def _add_workspace_root(label: str, wsd: str) -> None:
        real = os.path.realpath(os.path.expanduser(wsd))
        if is_system_root(real):
            logger.warning("dashboard: refusing system-root workspace %r as a browsable root", real)
            return
        candidates.append((label, real))

    try:
        from personalclaw.loop import files as _loop_files
        from personalclaw.loop import store as _loop_store

        for _lp in _loop_store.list_all():
            wsd = (_lp.workspace_dir or "").strip()
            if not wsd and _lp.kind == "code":
                # A greenfield code loop works in its own folder in the home (`effective_dir`),
                # and its cockpit says so with a link here.
                own = _loop_files.loop_dir(_lp.id)
                wsd = str(own) if own is not None else ""
            if wsd:
                _add_workspace_root(f"Loop: {_lp.name[:24]}", wsd)
    except Exception:
        pass

    # Project workspaces — a Project (projects-native-entity) is a first-class work
    # unit that MAY bind an arbitrary codebase dir on disk. Its detail view surfaces
    # that workspace as a "view contents" peek + "Open in Files", so the allowlist
    # must admit each bound Project.workspace_dir (exactly like a Loop's, above) —
    # otherwise a project workspace not coincidentally shared by a Loop 400s. Best-
    # effort: a project-store hiccup must never break the explorer for normal roots.
    try:
        from personalclaw.projects import _store as _project_store

        store = _project_store()
        for _pj in store.list_projects():
            wsd = (_pj.workspace_dir or "").strip()
            if wsd:
                _add_workspace_root(f"Project: {_pj.name[:24]}", wsd)
            # Its context folder, in the home: the notes its loops and chats share, which the
            # project page opens here.
            _add(f"Context: {_pj.name[:24]}", lambda pid=_pj.id: store.context_dir(pid))
    except Exception:
        pass

    # De-dupe by realpath while preserving order + first label.
    seen: set[str] = set()
    roots: list[tuple[str, str]] = []
    for label, rp in candidates:
        if rp and rp not in seen:
            seen.add(rp)
            roots.append((label, rp))
    return roots


def within(canonical: str, roots: Iterable[str]) -> bool:
    """Whether *canonical* (a real path) lies inside one of *roots* through a root that reaches it.

    🔴 A ROOT THAT CONTAINS THE HOME DOES NOT REACH INTO IT. A loop or project bound to ``~``, or
    a code worker whose folder is ``~``, is a root that contains the PersonalClaw home. Measured on
    `main`: through such a root the file explorer opened ``<home>/config.json``, and the native
    ``write_file`` of a worker in ``~`` wrote ``<home>/hooks/x-pre.sh`` — the files that say what
    runs as the owner (`owner_only`), reached through a root nobody bound to reach them. So a path
    inside the home is admitted only through a root that is itself inside it: the workspace,
    uploads, a project's context, a code loop's own folder. The home itself is never a root.
    """
    from personalclaw.config.loader import resolve_config_dir

    home = os.path.realpath(str(resolve_config_dir()))
    in_home = canonical == home or canonical.startswith(home + os.sep)
    for root in roots:
        if not root or not (canonical == root or canonical.startswith(root + os.sep)):
            continue
        if in_home and not root.startswith(home + os.sep):
            continue
        return True
    return False


#: The per-component byte limit essentially every filesystem enforces (ext4, APFS, NTFS).
#: BYTES, not characters: an emoji costs four, so a 90-character name can exceed it while
#: looking short. `len(name)` would have passed exactly the inputs the OS refuses.
MAX_NAME_BYTES = 255


def admit(raw: str, roots: Iterable[str]) -> str | None:
    """The canonical path *raw* names when it lies inside one of *roots*, else ``None``.

    Two-layer check:
      1. Reject sensitive credential paths via ``personalclaw.hooks.validate_file_path``
         (e.g. ``~/.ssh``, ``~/.aws``).
      2. Restrict to *roots* (:func:`within`). The file explorer passes :func:`dashboard_roots`,
         which is never the home itself, and a root that contains the home does not reach into
         it. This constrains the path-traversal surface so a request like
         ``GET /api/file-read?path=/etc/passwd`` is rejected.

    Returns the canonical path or ``None`` if rejected.
    """

    from personalclaw.hooks import validate_file_path

    canonical = validate_file_path(raw)
    if canonical is None:
        return None

    if not within(canonical, roots):
        return None

    # Even within allowed roots, block known-sensitive filenames (e.g. HMAC
    # keys, telemetry salt, app secrets) to prevent credential disclosure
    # via /api/file-read. Extensionless names must be listed here explicitly —
    # ``blocked_suffixes`` below cannot reach them.
    #
    # Two layers, because the host filesystem is often case-INSENSITIVE
    # (macOS/APFS, Windows/NTFS) while these comparisons used to be
    # case-SENSITIVE — so ``<home>/.LOCAL_SECRET`` sailed past the blocklist yet
    # resolved to the real ``.local_secret`` bytes (issue #690).
    #   Layer 1 — spelling: ``casefold()`` both sides so EVERY case variant of a
    #     blocked basename or suffix is refused, on any filesystem.
    #   Layer 2 — identity: on a case-insensitive (or hard-link-capable) volume
    #     the robust defence is file identity, not spelling. If the target
    #     resolves to the same inode as a blocked-name file in the same
    #     directory, refuse it whatever name reached it — this closes hard-link
    #     and short-name (8.3) aliases layer 1 cannot see. Bounded to the fixed
    #     blocked basenames (a handful of ``stat()``s), so it stays cheap even
    #     when file-list calls this per directory entry, and it is SKIPPED for a
    #     not-yet-existing target so create/write/move/upload of a fresh file is
    #     never rejected.
    # `OWN_SECRET_BASENAMES` is the SHARED definition (security.py), so this area and the
    # bash/terminal guards cannot disagree about what is secret. They did: every name here
    # was refused by `/api/file-read` and unknown to `is_sensitive_path`, which the terminal
    # cwd guard and the bash read hook both consult (#643).
    #
    # 🔴 DERIVED, NOT RE-LISTED (#354). Both halves come from `security.py`:
    #   `OWN_SECRET_BASENAMES`       — ours by NAME, wherever the file sits.
    #   `HOME_SECRET_FILE_BASENAMES` — ours by LOCATION (`.env`, `session_key`,
    #                                  `sessions.json`), applied here as a name rule because
    #                                  this tier is already scoped to the browsable roots.
    # Those three used to be spelled out here as literals, and that re-listing IS the mechanism
    # of the bug: `session_key` was documented as the session SIGNING KEY in `session_store.py`
    # and named a secret in `security.py`, and this list still did not have it — a hand-copied
    # list only ever knows what someone remembered to copy. The set is unchanged today; what
    # changes is that the NEXT name added to the one declaration is refused here without anyone
    # having to notice. `test_secret_file_blocklist_rail.py` asserts exactly that by adding a
    # synthetic name to the declaration and requiring this function to refuse it.
    #
    # The secret DIRECTORIES (`auth/`, `credentials/`, `governance/`) are deliberately NOT
    # folded in: a subtree is not a basename, and they are already refused one layer up by
    # `validate_file_path` → `is_sensitive_path`, which resolves them against the ACTIVE
    # `PERSONALCLAW_HOME`. Naming them here too would refuse a user's own `credentials/`
    # folder inside a workspace, which is an ordinary directory name.
    from personalclaw.security import HOME_SECRET_FILE_BASENAMES, OWN_SECRET_BASENAMES

    blocked_basenames = set(OWN_SECRET_BASENAMES) | set(HOME_SECRET_FILE_BASENAMES)
    # An over-long final component reaches the OS as `ENAMETOOLONG` and surfaced as a 500 from
    # `file-move` (over-long dest) and `create-dir`, which take a whole PATH rather than a name
    # and so never met the name rules (#652). Bounded here, at the one place every path-taking
    # endpoint already funnels through, rather than in each handler.
    if len(os.path.basename(canonical).encode("utf-8")) > MAX_NAME_BYTES:
        return None
    blocked_suffixes = (".key", ".pem", ".secret")
    base_cf = os.path.basename(canonical).casefold()
    if base_cf in {name.casefold() for name in blocked_basenames}:
        return None
    if any(base_cf.endswith(suffix.casefold()) for suffix in blocked_suffixes):
        return None

    # Layer 2 — identity. A missing target (create/write/move/upload validate
    # paths that need not exist yet) has no inode to compare, so fall through to
    # the layer-1 result rather than rejecting a legitimate new file.
    try:
        target_st = os.stat(canonical)
    except OSError:
        return canonical
    parent = os.path.dirname(canonical)
    for blocked in blocked_basenames:
        try:
            cand_st = os.stat(os.path.join(parent, blocked))
        except OSError:
            continue
        if cand_st.st_ino == target_st.st_ino and cand_st.st_dev == target_st.st_dev:
            return None
    return canonical
