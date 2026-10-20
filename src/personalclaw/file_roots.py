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
from personalclaw.security import redact_for_display, system_subtrees

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
    """Where a captured screenshot lands, resolved AT CALL TIME.

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
    uploads, screenshots, a project's context, a loop's own folder where it works or keeps its
    deliverable). For a request the gateway scoped to an app (``permissions.request_app``), a
    root that CONTAINS the home, such as a loop or project workspace bound to ``~``, is left out
    as well, and so is every loop's own folder: it holds the brief the loop's worker reads every
    cycle, and steering a loop is the owner's. So is every project's own folder, its context
    among it: a chat in the project is given the overview and the ledgers kept there, and what a
    project's sessions are given is the owner's to write. The realpath checks in :func:`admit`
    refuse a symlink or ``..`` back out of a root.
    """
    roots = all_dashboard_roots()
    from personalclaw.apps.permissions import request_app

    if not request_app():
        return roots
    from personalclaw.config.loader import config_dir
    from personalclaw.loop.files import loops_root
    from personalclaw.tasks.hierarchy import projects_root

    home = os.path.realpath(str(config_dir()))
    owners_folders = tuple(
        os.path.realpath(str(root())) + os.sep for root in (loops_root, projects_root)
    )
    return [
        (label, r)
        for label, r in roots
        if not (home == r or home.startswith(r + os.sep) or r.startswith(owners_folders))
    ]


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
        from personalclaw.loop import kinds as _loop_kinds
        from personalclaw.loop import store as _loop_store

        _loop_kinds.ensure_loaded()
        for _lp in _loop_store.list_all():
            wsd = (_lp.workspace_dir or "").strip()
            # Named as the Loops page names the loop (`loop.store._redact_loop`), masked.
            shown = redact_for_display(_lp.name or "")[:24]
            if wsd:
                _add_workspace_root(f"Loop: {shown}", wsd)
            # A loop's OWN folder, in the home, when something the user opens lives there: a
            # greenfield code loop works in it (`effective_dir`), and a loop whose kind keeps a
            # document deliverable (REPORT.md, DESIGN.md) is told to maintain it there, which is
            # where its completion graduates it as a file-backed artifact
            # (`loop/watchdog._deliverable_file`). Without the root, that artifact's "Source file"
            # opened a path Files refused.
            namer = getattr(_loop_kinds.get_or_none(_lp.kind), "deliverable_name", None)
            if (not wsd and _lp.kind == "code") or (namer is not None and namer(_lp)):
                own = _loop_files.safe_loop_dir(_lp.id)
                if own is not None:
                    label = "Loop" if not wsd else "Loop folder"
                    candidates.append((f"{label}: {shown}", os.path.realpath(own)))
    except Exception:
        pass

    # Project workspaces — a Project is a first-class work
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


def within(canonical: str, roots: Iterable[str], *, home: str = "") -> bool:
    """Whether *canonical* (a real path) lies inside one of *roots* through a root that reaches it.

    🔴 A ROOT THAT CONTAINS THE HOME DOES NOT REACH INTO IT. A loop or project bound to ``~``, or
    a code worker whose folder is ``~``, is a root that contains the PersonalClaw home. Measured on
    `main`: through such a root the file explorer opened ``<home>/config.json``, and the native
    ``write_file`` of a worker in ``~`` wrote ``<home>/hooks/x-pre.sh`` — the files that say what
    runs as the owner (`owner_only`), reached through a root nobody bound to reach them. So a path
    inside the home is admitted only through a root that is itself inside it: the workspace,
    uploads, a project's context, a code loop's own folder. The home itself is never a root.

    *home* is the home's real path when the caller already resolved it (:class:`Admission`, once
    for a whole walk); empty resolves it here.
    """
    if not home:
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

#: C0 controls and DEL: the characters no file surface takes in a path or a name. The Files view
#: refuses them in a name it creates and in a path it reads or writes (``validation``'s file-path
#: pattern spells the same set), the agent's file tools refuse them in any path, and a file is not
#: sent to the user with one in its name. A newline in a name starts a line of its own in every
#: listing, log line and command that shows the path, and a name that ends in one is a file nobody
#: sees to clear.
CONTROL_CHARS = frozenset(chr(code) for code in (*range(0x20), 0x7F))


def control_character_in(text: str) -> str:
    """The first of :data:`CONTROL_CHARS` in *text*, written ``U+000A``; ``""`` for none."""
    for ch in text:
        if ch in CONTROL_CHARS:
            return f"U+{ord(ch):04X}"
    return ""


def admit(raw: str, roots: Iterable[str]) -> str | None:
    """The canonical path *raw* names when it lies inside one of *roots*, else ``None``.

    Two-layer check:
      1. Reject sensitive credential paths via ``personalclaw.hooks.validate_file_path``
         (e.g. ``~/.ssh``, ``~/.aws``).
      2. Restrict to *roots* (:func:`within`). The file explorer passes :func:`dashboard_roots`,
         which is never the home itself, and a root that contains the home does not reach into
         it. This constrains the path-traversal surface so a request like
         ``GET /api/file-read?path=/etc/passwd`` is rejected.

    Returns the canonical path or ``None`` if rejected. A caller asking about many paths (a
    listing, a search, an index) makes one :class:`Admission` and asks it instead: the same
    answer, with what does not depend on the path resolved once.
    """
    return Admission(roots)(raw)


#: Suffixes refused wherever they sit, alongside the blocked basenames (see :class:`Admission`).
_BLOCKED_SUFFIXES = (".key", ".pem", ".secret")


class Admission:
    """:func:`admit`, for a caller that asks about many paths: a listing, a search, an index.

    It IS :func:`admit`'s implementation, so the two cannot disagree. What does not depend on the
    path is resolved once, when it is made: the roots, the PersonalClaw home, the protected
    locations (a :class:`~personalclaw.security.SensitivePaths`, which is nearly all of one
    check's cost), and, per directory, which of its files carry a blocked name. Make one per walk
    and drop it after, as :class:`~personalclaw.security.SensitivePaths` says for itself.
    """

    def __init__(self, roots: Iterable[str]) -> None:
        from personalclaw.config.loader import resolve_config_dir
        from personalclaw.security import (
            HOME_SECRET_FILE_BASENAMES,
            OWN_SECRET_BASENAMES,
            SensitivePaths,
        )

        self._roots = [r for r in roots if r]
        self._home = os.path.realpath(str(resolve_config_dir()))
        self._sensitive = SensitivePaths()
        # Even within allowed roots, block known-sensitive filenames (e.g. HMAC
        # keys, telemetry salt, app secrets) to prevent credential disclosure
        # via /api/file-read. Extensionless names must be listed here explicitly —
        # the blocked suffixes cannot reach them.
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
        #     blocked basenames (a handful of ``stat()``s per DIRECTORY, remembered for
        #     the walk), so it stays cheap even when a listing asks per entry, and it is
        #     SKIPPED for a not-yet-existing target so create/write/move/upload of a
        #     fresh file is never rejected.
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
        self._blocked = tuple(sorted(set(OWN_SECRET_BASENAMES) | set(HOME_SECRET_FILE_BASENAMES)))
        self._blocked_cf = frozenset(name.casefold() for name in self._blocked)
        self._suffixes_cf = tuple(suffix.casefold() for suffix in _BLOCKED_SUFFIXES)
        self._blocked_ids: dict[str, frozenset[tuple[int, int]]] = {}

    def __call__(self, raw: str) -> str | None:
        """The canonical path *raw* names when it is admitted, else ``None`` (:func:`admit`)."""
        from personalclaw.hooks import validate_file_path

        canonical = validate_file_path(raw, sensitive=self._sensitive)
        if canonical is None:
            return None

        if not within(canonical, self._roots, home=self._home):
            return None

        # An over-long final component reaches the OS as `ENAMETOOLONG` and surfaced as a 500 from
        # `file-move` (over-long dest) and `create-dir`, which take a whole PATH rather than a name
        # and so never met the name rules (#652). Bounded here, at the one place every path-taking
        # endpoint already funnels through, rather than in each handler.
        name = os.path.basename(canonical)
        if len(name.encode("utf-8")) > MAX_NAME_BYTES:
            return None
        base_cf = name.casefold()
        if base_cf in self._blocked_cf or base_cf.endswith(self._suffixes_cf):
            return None

        # Layer 2 — identity. A missing target (create/write/move/upload validate
        # paths that need not exist yet) has no inode to compare, so fall through to
        # the layer-1 result rather than rejecting a legitimate new file.
        try:
            target_st = os.stat(canonical)
        except OSError:
            return canonical
        if (target_st.st_dev, target_st.st_ino) in self._blocked_in(os.path.dirname(canonical)):
            return None
        return canonical

    def _blocked_in(self, parent: str) -> frozenset[tuple[int, int]]:
        """The identities of the blocked-name files in *parent*, looked up once per directory."""
        ids = self._blocked_ids.get(parent)
        if ids is None:
            found: set[tuple[int, int]] = set()
            for blocked in self._blocked:
                try:
                    st = os.stat(os.path.join(parent, blocked))
                except OSError:
                    continue
                found.add((st.st_dev, st.st_ino))
            ids = self._blocked_ids[parent] = frozenset(found)
        return ids
