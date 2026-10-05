"""Turn-bound two-phase file checkpointing + ``/rewind-to-turn``.

The interactive-tier complement to the workflow journal's run-scoped checkpoints. Scope:
**chat/loop sessions and their tool-driven file edits on the host** — where today a wrong
``edit_file`` is simply gone. :mod:`personalclaw.dashboard.chat_undo` rolls back the
CONVERSATION and says in its own docstring that files written are NOT reverted; this module
is the other half, and the two stay deliberately separate (rewinding the transcript would
destroy the record of what happened).

**What a rewind puts back, and what it can only name.** A file's bytes are saved only before a
change PersonalClaw makes or answers for, because only then is there a moment before the bytes
change: its own file tools (``write_file``, ``edit_file``) save them before they write, and an
agent CLI's file edit is saved when PersonalClaw lets the call through (the CLI asks, and waits
for the answer, before it writes: :func:`back_up_named_files`). A shell command's change, and an
edit an agent CLI makes without asking, happen before anyone could know which file they touch,
so nothing can save those. They are named instead: :func:`preview_rewind` lists every file under
the turn's folder that changed with no backup, and says the rewind leaves it as it is.

**Two phases, per turn:**

1. :func:`begin_turn` — the *identity set*: the path, size and modification time of each file
   under the turn's folder as the turn begins, no bytes. It is what the preview compares the
   folder with to name the files that changed with no backup. It walks the folder (up to
   :data:`_IDENTITY_MAX_ENTRIES` files, for at most :data:`_IDENTITY_MAX_SECS`), so it is a
   worker thread's work and never the event loop's: the chat runner runs it in one. It is kept
   apart from the manifest and by its own digest (``identity/<sha256>.json.gz``), so a turn
   that finds the folder as the turn before it did keeps no second copy, and a backup taken in
   the turn rewrites only the small manifest.
2. :func:`capture_pre_edit` — the *pre-edit backup*: the file-writing tool handlers call
   this before the first mutation of a path in the current turn, so the bytes are copied
   while they still exist. Content-addressed (sha256) and deduped at the session level, via
   :func:`~personalclaw.atomic_write.atomic_write_bytes`. Only touched files cost bytes. The
   file's permission bits are recorded beside them, and a restore gives them back.

**Restore is two-phase too, and journaled**, because a half-restored working tree is worse
than no restore: :func:`apply_rewind` first *stages* every restored body as a sibling temp
file and writes a plan journal, then *commits* with :func:`os.replace` (atomic per file).
A process death between the per-file renames leaves the journal on disk;
:func:`resume_incomplete_rewind` finishes it on next access. Nothing is written before the
user has seen :func:`preview_rewind` and confirmed.

**Secrecy floor (:data:`NEVER_CAPTURE_GLOBS`).** ``.env`` and its siblings are never copied
into the store — not filtered on the way out, never written in the first place. This is the
restrictive reading of "``.env`` files are never captured": the store
lives under the home, is covered by snapshots and exports, and a captured credential would
outlive the file the user deleted. The consequence is stated rather than hidden: a skipped
path is recorded in the turn manifest as ``skipped="secret"`` (the *path*, never the bytes)
so :func:`preview_rewind` can warn "not captured" instead of silently restoring nothing.
``is_sensitive_path`` is *also* consulted, but it is home-anchored, so it does not see a
workspace ``.env`` — this floor is what closes that.

**Bounds.** Per-session byte cap and turn cap (``checkpoints.max_mb`` /
``checkpoints.max_turns``), enforced on the way in: adding a body that would exceed the cap
prunes the oldest turns until it fits, and a single body over ``checkpoints.max_file_mb`` is
recorded manifest-only (``skipped="too_large"``) so the preview can say "not captured". A
folder's record goes with the last turn that names it. Pruned with the session
(:func:`prune_session`).

Explicitly NOT git: it works in non-repos and never touches the user's index.
"""

from __future__ import annotations

import difflib
import gzip
import hashlib
import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
import zlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

from personalclaw.atomic_write import atomic_write, atomic_write_bytes, is_in_home

logger = logging.getLogger(__name__)

#: Store root, relative to ``config_dir()``. Declared in the durability inventory as
#: ``turn_checkpoints`` so :func:`~personalclaw.durability.inventory.audit_home` claims it.
CHECKPOINT_DIR_NAME = "checkpoints"

#: Filename globs whose CONTENT is never copied into the store, at any cap, under any
#: config. Matched case-insensitively against the basename. This is a floor, not a
#: preference: there is no config field that widens it.
NEVER_CAPTURE_GLOBS: tuple[str, ...] = (
    # dotenv, in every shape the ecosystem uses
    ".env",
    ".env.*",
    "*.env",
    ".envrc",
    # private keys / certs
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.jks",
    "*.keystore",
    "id_rsa*",
    "id_dsa*",
    "id_ecdsa*",
    "id_ed25519*",
    "*_rsa",
    "*_ed25519",
    # tool credential files
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".git-credentials",
    ".htpasswd",
    "credentials",
    "credentials.json",
    "service-account.json",
    "secrets.json",
    "secrets.yaml",
    "secrets.yml",
    # PersonalClaw's own gateway secret
    ".local_secret",
)

#: Directory names never walked for the identity set (phase 1). Keeps a `begin_turn` on a
#: real repo from stat-ing a 200k-file `node_modules`.
_IDENTITY_SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        "target",
        ".next",
        ".tox",
        ".cache",
        ".personalclaw",
    }
)

#: Ceiling on identity-set entries, and on how long one walk may take (a folder on a slow or
#: network drive). Phase 1 names what a rewind cannot put back; it is not the restore (phase 2
#: is), so a walk cut short costs the completeness of that list, never a restore. A record cut
#: short says so (``truncated``), and the preview says so in turn.
_IDENTITY_MAX_ENTRIES = 20_000
_IDENTITY_MAX_SECS = 2.0

#: The folder records, by digest, beside the turns that name them.
_IDENTITY_DIR_NAME = "identity"

#: What the preview's ``not_captured`` entries from the identity set say happened to a file that
#: changed with no backup.
UNBACKED_CHANGED, UNBACKED_CREATED, UNBACKED_DELETED = "changed", "created", "deleted"
UNBACKED_REASONS: frozenset[str] = frozenset({UNBACKED_CHANGED, UNBACKED_CREATED, UNBACKED_DELETED})
#: ...and on a restore or a delete, that something with no backup changed the file after the
#: rewind's turn and before its backup, so the rewind puts it back only as far as that backup.
CHANGED_BEFORE_BACKUP = "changed_before_backup"

#: Held by whatever writes a restore back to disk (:func:`apply_rewind`,
#: :func:`resume_incomplete_rewind`). A preview resumes a dead rewind in a worker thread, and two
#: replays of one journal at once would race each other's renames.
_REWIND_LOCK = threading.Lock()

_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")


# ── config ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Bounds:
    enabled: bool
    max_mb: int
    max_turns: int
    max_file_mb: int


def _bounds() -> _Bounds:
    """Read the caps from config.

    Fail-**open** on a corrupt/missing config, matching the shared convention for a
    convenience surface: a checkpoint store that refuses to record because config would not
    parse would silently remove the safety net a user believes they have. The defaults are
    200MB / 50 turns.
    """
    try:
        from personalclaw.config.loader import AppConfig

        c = AppConfig.load().checkpoints
        return _Bounds(
            enabled=bool(c.enabled),
            max_mb=max(0, int(c.max_mb)),
            max_turns=max(1, int(c.max_turns)),
            max_file_mb=max(0, int(c.max_file_mb)),
        )
    except Exception:  # noqa: BLE001 — see fail-open note above
        logger.debug("turn_checkpoints: config unreadable, using defaults", exc_info=True)
        return _Bounds(enabled=True, max_mb=200, max_turns=50, max_file_mb=8)


# ── paths ──────────────────────────────────────────────────────────────────────


def _home() -> Path:
    # Resolved per call, never bound at import: tests monkeypatch `config_dir`, and an
    # import-time capture would silently write to the real home.
    from personalclaw.config.loader import config_dir

    return Path(config_dir())


def session_slug(session_key: str) -> str:
    """A filesystem-safe, collision-free directory name for *session_key*.

    The readable prefix keeps the store greppable by a human; the hash suffix is what makes
    it injective, so two keys that sanitize to the same characters still get separate trees.
    """
    raw = (session_key or "unknown").strip()
    safe = _SLUG_RE.sub("-", raw).strip("-.")[:48] or "session"
    digest = hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:12]
    return f"{safe}-{digest}"


def store_root() -> Path:
    return _home() / CHECKPOINT_DIR_NAME


def session_dir(session_key: str) -> Path:
    return store_root() / session_slug(session_key)


def _blob_dir(session_key: str) -> Path:
    return session_dir(session_key) / "blobs"


def _identity_dir(session_key: str) -> Path:
    return session_dir(session_key) / _IDENTITY_DIR_NAME


def _turn_dir(session_key: str, turn: int) -> Path:
    return session_dir(session_key) / f"turn-{turn:06d}"


def _state_path(session_key: str) -> Path:
    return session_dir(session_key) / "state.json"


# ── small JSON helpers (reads tolerate missing/corrupt) ─────────────────────────


def _read_json(path: Path, default: dict) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(default)
    return data if isinstance(data, dict) else dict(default)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True))


# ── the secrecy floor ──────────────────────────────────────────────────────────


def is_never_captured(path: Path | str) -> bool:
    """Whether *path*'s CONTENT must never enter the store.

    Basename-glob match against :data:`NEVER_CAPTURE_GLOBS` (case-insensitive), plus the
    home-anchored :func:`~personalclaw.security.is_sensitive_path`. Both, not either: the
    globs catch a workspace ``.env`` the home-anchored check cannot see, and
    ``is_sensitive_path`` catches ``~/.aws/config``, which no basename glob would.

    **Both checks run against the LITERAL path and its RESOLVED target**, because a
    basename list is defeated by a symlink and this was measured, not theorized: on
    ``main``, ``ws/config.txt -> ws/.env`` returned ``"captured"`` and the dotenv body
    landed in a blob (the name ``config.txt`` matches no glob, and ``is_sensitive_path`` is
    ``$HOME``-anchored so a workspace file is invisible to it). ``read_bytes`` follows the
    link, so the only sound question is "what will actually be read?".
    """
    p = Path(path)
    candidates = [p]
    try:
        # `strict=False`: a dangling symlink still resolves to its TARGET name, which is the
        # name that matters — and a write through it would create that target.
        resolved = p.resolve()
        if resolved != p:
            candidates.append(resolved)
    except OSError:  # a path that cannot be resolved is not thereby safe
        logger.debug("turn_checkpoints: could not resolve %s", p, exc_info=True)
        return True
    for cand in candidates:
        if any(fnmatch(cand.name.lower(), g.lower()) for g in NEVER_CAPTURE_GLOBS):
            return True
    try:
        from personalclaw.security import is_sensitive_path

        return any(bool(is_sensitive_path(str(c))) for c in candidates)
    except Exception:  # noqa: BLE001 — a check that cannot run must not widen capture
        logger.debug("turn_checkpoints: sensitive-path check failed for %s", p, exc_info=True)
        return True


# ── phase 1: the identity set ──────────────────────────────────────────────────


def _inside(path: str, folder: str) -> bool:
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


def _kept_out(base: str) -> Callable[[str], bool]:
    """What a walk of *base* leaves out besides :data:`_IDENTITY_SKIP_DIRS`: PersonalClaw's own
    state, where the folder holds any of it.

    PersonalClaw writes there itself, during and between turns: its home's own records (the
    sessions, the logs, this store), and in the workspace in the home long-term memory (the daily
    history a consolidation adds to, each working folder's memory database and learning log) and
    its other stores (the knowledge library, the lexicon: ``file_scope.own_stores``). None of it is
    a file the chat changed, and memory keeps a history of its own changes, so listing it would put
    PersonalClaw's own writes on every preview. The home's records are left out only by a walk
    that reaches them (of a folder that holds the home, or of the home itself); a folder of the
    agent's work that lies in the home (its workspace, a checkout) is the agent's, and so is
    everything in it but memory and the stores. A folder that neither holds the home nor lies in it
    (a project's) leaves out nothing more."""
    try:
        from personalclaw import memory
        from personalclaw.config.loader import memory_root, resolve_config_dir
        from personalclaw.file_scope import own_stores

        home = os.path.realpath(str(resolve_config_dir()))
        if not (_inside(base, home) or _inside(home, base)):
            return lambda _path: False
        reaches_records = _inside(home, base)
        workspace = os.path.realpath(str(memory_root()))
        tops = [os.path.realpath(str(folder)) for folder in memory.memory_folders()]
        stores = own_stores(home)
    except Exception:  # noqa: BLE001 — a home that cannot be located leaves nothing more out
        logger.debug("turn_checkpoints: PersonalClaw's own state not located", exc_info=True)
        return lambda _path: False

    def kept_out(path: str) -> bool:
        # The home's records: in it, and neither in the workspace nor on the way to it.
        if (
            reaches_records
            and _inside(path, home)
            and not (_inside(path, workspace) or _inside(workspace, path))
        ):
            return True
        return any(_inside(path, top) for top in tops) or any(s.holds(path) for s in stores)

    return kept_out


def _identity_set(base: Path) -> tuple[dict[str, list[int]], bool]:
    """The files under *base* as ``{path relative to it: [size, mtime_ns]}``, no bytes, and
    whether the walk stopped short (:data:`_IDENTITY_MAX_ENTRIES` files, or
    :data:`_IDENTITY_MAX_SECS`).

    In name order, a folder's files before the folders in it, so two walks of one folder visit it
    alike and two walks stopped short cover the same files. A link is neither followed nor
    recorded: what it points at is recorded where that is, when that is in the folder. Leaves out
    :data:`_IDENTITY_SKIP_DIRS` and PersonalClaw's own state (:func:`_kept_out`)."""
    root = str(base)
    prefix = root.rstrip(os.sep) + os.sep
    kept_out = _kept_out(root)
    deadline = time.monotonic() + _IDENTITY_MAX_SECS
    out: dict[str, list[int]] = {}
    pending = [root]
    while pending:
        folder = pending.pop()
        try:
            with os.scandir(folder) as listing:
                entries = sorted(listing, key=lambda e: e.name)
        except OSError:
            continue
        below: list[str] = []
        for entry in entries:
            if len(out) >= _IDENTITY_MAX_ENTRIES or time.monotonic() > deadline:
                return out, True
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    name = entry.name
                    if not (
                        name in _IDENTITY_SKIP_DIRS
                        or name.startswith(".git")
                        or kept_out(entry.path)
                    ):
                        below.append(entry.path)
                    continue
                if not entry.is_file(follow_symlinks=False) or kept_out(entry.path):
                    continue
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            out[entry.path[len(prefix) :]] = [st.st_size, st.st_mtime_ns]
        pending.extend(reversed(below))
    return out, False


def _record_folder(session_key: str, base: Path) -> dict:
    """Record *base* as it stands (:func:`_identity_set`) and return how the turn's manifest names
    the record: ``{"record": <digest>, "cwd", "entries", "truncated"}``.

    Kept by the digest of its bytes, so a folder found as the turn before found it is not kept
    twice: a turn that changed nothing costs its own manifest, not another record."""
    files, truncated = _identity_set(base)
    body = gzip.compress(
        json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8"), mtime=0
    )
    digest = hashlib.sha256(body).hexdigest()
    path = _identity_dir(session_key) / f"{digest}.json.gz"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        # 0o600: it names the files in the user's folder, beside credentials in the home.
        atomic_write_bytes(path, body, mode=0o600)
    return {"record": digest, "cwd": str(base), "entries": len(files), "truncated": truncated}


def _folder_record(session_key: str, digest: str) -> dict[str, list[int]] | None:
    """The folder record named *digest*, or ``None`` when it is gone or unreadable."""
    try:
        body = (_identity_dir(session_key) / f"{digest}.json.gz").read_bytes()
        data = json.loads(gzip.decompress(body))
    except (OSError, EOFError, ValueError, zlib.error):
        return None
    return data if isinstance(data, dict) else None


def _open_turn(session_key: str, base: Path | None, identity: dict | None) -> int:
    """Number the next turn and write its manifest, which names *identity* when there is one."""
    session_dir(session_key).mkdir(parents=True, exist_ok=True)
    state = _read_json(_state_path(session_key), {"current_turn": 0})
    turn = int(state.get("current_turn") or 0) + 1
    state.update({"current_turn": turn, "session_key": session_key})
    _write_json(_state_path(session_key), state)
    manifest: dict = {
        "turn": turn,
        "session_key": session_key,
        "cwd": str(base) if base else "",
        "started_at": time.time(),
        "files": [],
    }
    if identity is not None:
        manifest["identity"] = identity
    _write_json(_turn_dir(session_key, turn) / "manifest.json", manifest)
    return turn


def begin_turn(session_key: str, *, cwd: Path | str | None = None) -> int:
    """Open the next turn for *session_key*; returns its number (1-based).

    Phase 1. Records *cwd* as it stands as the turn begins (:func:`_record_folder`), which the
    preview compares the folder with later; skipped when *cwd* is None or missing — a session
    with no workspace still gets a numbered turn so :func:`capture_pre_edit` has somewhere to put
    bytes. It walks the folder, so it is called from a worker thread and never on the event loop.
    Never raises: a checkpoint store that breaks a turn is worse than one that misses a turn.
    """
    b = _bounds()
    if not b.enabled:
        return 0
    try:
        base = Path(cwd).resolve() if cwd else None
        identity = None
        if base is not None and base.is_dir():
            identity = _record_folder(session_key, base)
        turn = _open_turn(session_key, base, identity)
        _enforce_turn_cap(session_key, b)
        return turn
    except Exception:  # noqa: BLE001
        logger.warning("turn_checkpoints: begin_turn failed for %s", session_key, exc_info=True)
        return 0


def current_turn(session_key: str) -> int:
    return int(_read_json(_state_path(session_key), {}).get("current_turn") or 0)


# ── phase 2: the pre-edit backup ───────────────────────────────────────────────


def capture_pre_edit(
    session_key: str,
    path: Path | str,
    *,
    cwd: Path | str | None = None,
    root: Path | str | None = None,
) -> str:
    """Back up *path*'s current bytes before this turn's first mutation of it.

    *root* is the folder the write was admitted through when that is not *cwd* (one of the
    owner's allowed working directories, ``file_scope``), recorded beside *cwd* so a rewind may
    restore the file there too.

    Returns a short status for logging/tests: ``"captured"``, ``"deduped"`` (already backed
    up in this turn), ``"absent"`` (the write creates the file — recorded so a rewind can
    delete it), ``"secret"``, ``"too_large"``, ``"disabled"``, or ``"error"``.

    Never raises. Called from the ``write_file``/``edit_file`` handlers, so an exception here
    would fail the agent's tool call — the store degrades instead.

    A session with no turn yet gets one here, with no record of its folder: it opens part way
    through whatever the session is doing, so a record taken now would not be the folder as the
    turn began, and taking it would walk the folder on the caller's thread (the event loop, for
    the native tools).
    """
    b = _bounds()
    if not b.enabled:
        return "disabled"
    try:
        target = Path(path)
        turn = current_turn(session_key)
        if turn <= 0:
            turn = _open_turn(session_key, Path(cwd).resolve() if cwd else None, None)
            _enforce_turn_cap(session_key, b)
        if turn <= 0:
            return "error"
        man_path = _turn_dir(session_key, turn) / "manifest.json"
        man = _read_json(man_path, {"turn": turn, "files": []})
        files = man.get("files")
        if not isinstance(files, list):
            files = []
        key = str(target)

        # Record the base this capture ran under, so a later rewind can confine its writes
        # to it (`session_roots`). It is recorded HERE and not only on the turn manifest
        # because `begin_turn` legitimately accepts `cwd=None` — a session with no workspace
        # still gets numbered turns — and the base the WRITE was governed by is the honest
        # root for restoring that write.
        roots = man.get("roots")
        if not isinstance(roots, list):
            roots = []
        for folder in (cwd, root):
            if not folder:
                continue
            try:
                base = str(Path(folder).resolve())
                if base not in roots:
                    roots.append(base)
            except OSError:
                logger.debug("turn_checkpoints: could not resolve %s", folder, exc_info=True)
        man["roots"] = roots

        if any(isinstance(f, dict) and f.get("path") == key for f in files):
            # Still persist a newly-learned root before returning: the second write of a
            # turn is deduped for BYTES, not for the confinement record.
            if roots:
                _write_json(man_path, man)
            return "deduped"

        entry: dict = {"path": key, "captured_at": time.time()}
        if is_never_captured(target):
            # The PATH is recorded (so the preview can warn "not captured"); the bytes are
            # not read at all — nothing to leak even if the store is later exported.
            entry.update({"skipped": "secret", "existed": target.is_file()})
            files.append(entry)
            man["files"] = files
            _write_json(man_path, man)
            return "secret"

        if not target.exists():
            entry.update({"existed": False})
            files.append(entry)
            man["files"] = files
            _write_json(man_path, man)
            return "absent"
        if target.is_dir():
            entry.update({"skipped": "directory", "existed": True})
            files.append(entry)
            man["files"] = files
            _write_json(man_path, man)
            return "error"

        try:
            # The modification time beside the bytes: what the preview compares with the folder's
            # record to tell whether something with no backup changed the file before this did.
            # And its permissions, which a restore gives back with the bytes.
            st = target.stat()
            data = target.read_bytes()
        except OSError:
            entry.update({"skipped": "unreadable", "existed": True})
            files.append(entry)
            man["files"] = files
            _write_json(man_path, man)
            return "error"

        if b.max_file_mb and len(data) > b.max_file_mb * 1024 * 1024:
            entry.update({"skipped": "too_large", "existed": True, "size": len(data)})
            files.append(entry)
            man["files"] = files
            _write_json(man_path, man)
            return "too_large"

        # Cap FIRST, then write: pruning after the write could evict the very blob we just
        # stored (its own turn is the newest, but a body larger than the whole cap would
        # otherwise land and then be swept, costing the write for nothing).
        if not _make_room(session_key, len(data), b, keep_turn=turn):
            entry.update({"skipped": "over_cap", "existed": True, "size": len(data)})
            files.append(entry)
            man["files"] = files
            _write_json(man_path, man)
            return "too_large"

        digest = hashlib.sha256(data).hexdigest()
        blob = _blob_dir(session_key) / f"{digest}.bin"
        if not blob.exists():
            blob.parent.mkdir(parents=True, exist_ok=True)
            # 0o600: the body is a copy of the user's file, and the store sits under the
            # home next to credentials — no reason for it to be group/world readable.
            atomic_write_bytes(blob, data, mode=0o600)
        entry.update(
            {
                "existed": True,
                "sha256": digest,
                "size": len(data),
                "mtime_ns": st.st_mtime_ns,
                # The permission bits only: a restore never sets a set-id or sticky bit.
                "mode": st.st_mode & 0o777,
            }
        )
        files.append(entry)
        man["files"] = files
        _write_json(man_path, man)
        return "captured"
    except Exception:  # noqa: BLE001
        logger.warning("turn_checkpoints: capture failed for %s", path, exc_info=True)
        return "error"


def back_up_named_files(
    session_key: str,
    paths: Iterable[str],
    *,
    cwd: Path | str,
    roots: Iterable[str | Path] = (),
) -> dict[str, str]:
    """Back up the files an agent CLI's call names, just before PersonalClaw lets the call through.

    An agent CLI writes files with its own tools, so the backup PersonalClaw's own file tools take
    before they write (:func:`capture_pre_edit`) never ran for it. But the CLI asks before an edit
    and waits for the answer, and its request names the files (the Agent Client Protocol's
    ``locations``, and the path of each ``diff`` it declares). So the chat runner calls this as it
    answers yes, the last moment the bytes are still the old ones, and a rewind restores them like
    any other backup.

    A file is backed up where a rewind may write it back: in the chat's folder (*cwd*), a folder
    its work was given (*roots*), or one of the owner's allowed working directories
    (``file_scope.FileScope.root_of``). One anywhere else is recorded as not captured
    (``skipped="outside"``), so the preview names it rather than offering a restore the rewind
    would then refuse (:func:`is_within_roots`). A relative path starts at *cwd*.

    Returns each path's :func:`capture_pre_edit` status, ``"outside"`` for one recorded that way.
    Never raises: the store degrades rather than holding up the answer the agent waits for.
    """
    statuses: dict[str, str] = {}
    try:
        from personalclaw.file_scope import FileScope

        base = os.path.realpath(str(cwd))
        scope = FileScope([base, *(str(r) for r in roots)])
        for raw in dict.fromkeys(str(p or "").strip() for p in paths):
            if not raw:
                continue
            real = os.path.realpath(os.path.join(base, os.path.expanduser(raw)))
            root = scope.root_of(real)
            if root:
                statuses[raw] = capture_pre_edit(session_key, real, cwd=base, root=root)
            else:
                statuses[raw] = _note_not_captured(session_key, Path(real), "outside", cwd=base)
    except Exception:  # noqa: BLE001
        logger.warning("turn_checkpoints: backing up %s failed", session_key, exc_info=True)
    return statuses


def _note_not_captured(session_key: str, target: Path, reason: str, *, cwd: str) -> str:
    """Record *target* in this turn's manifest as not captured for *reason* (its path, never its
    bytes), once per turn; returns *reason*, or ``"disabled"``/``"error"``."""
    b = _bounds()
    if not b.enabled:
        return "disabled"
    try:
        turn = current_turn(session_key)
        if turn <= 0:
            turn = _open_turn(session_key, Path(cwd), None)
            _enforce_turn_cap(session_key, b)
        man_path = _turn_dir(session_key, turn) / "manifest.json"
        man = _read_json(man_path, {"turn": turn, "files": []})
        recorded = man.get("files")
        files: list = recorded if isinstance(recorded, list) else []
        if not any(isinstance(f, dict) and f.get("path") == str(target) for f in files):
            files.append(
                {
                    "path": str(target),
                    "captured_at": time.time(),
                    "skipped": reason,
                    "existed": target.is_file(),
                }
            )
            man["files"] = files
            _write_json(man_path, man)
        return reason
    except Exception:  # noqa: BLE001
        logger.warning("turn_checkpoints: recording %s failed", target, exc_info=True)
        return "error"


# ── caps + pruning ─────────────────────────────────────────────────────────────


def _turn_numbers(session_key: str) -> list[int]:
    sd = session_dir(session_key)
    if not sd.is_dir():
        return []
    out: list[int] = []
    for child in sd.iterdir():
        if child.is_dir() and child.name.startswith("turn-"):
            try:
                out.append(int(child.name[5:]))
            except ValueError:
                continue
    return sorted(out)


def recorded_file_entries(session_key: str, *, max_turns: int = 20) -> list[dict]:
    """This session's recorded pre-edit file entries, oldest turn first.

    A read-only projection of the turn manifests, exposed because the resume account needs the one
    fact only this store holds: which files this session's own turns were about to mutate, and
    whether each ALREADY EXISTED at that moment. Bounded to the newest ``max_turns`` manifests —
    a months-old session must not make a resume walk its whole store.

    Each entry is the manifest's own dict (``path``, ``existed``, ``skipped``, ``sha256``, …),
    returned unchanged rather than reshaped: the account derives from recorded facts, and a
    reshaping pass here would be the first place to quietly reinterpret one.
    """
    out: list[dict] = []
    for t in _turn_numbers(session_key)[-max(1, max_turns) :]:
        man = _read_json(_turn_dir(session_key, t) / "manifest.json", {})
        for f in man.get("files") or []:
            if isinstance(f, dict) and f.get("path"):
                out.append({**f, "turn": t})
    return out


def store_bytes(session_key: str) -> int:
    """Total blob bytes held for *session_key* (the quantity the cap bounds)."""
    bd = _blob_dir(session_key)
    if not bd.is_dir():
        return 0
    total = 0
    for f in bd.iterdir():
        try:
            total += f.stat().st_size
        except OSError:
            continue
    return total


def _referenced(session_key: str) -> tuple[set[str], set[str]]:
    """The blob digests and the folder-record digests the surviving turn manifests name."""
    blobs: set[str] = set()
    records: set[str] = set()
    for t in _turn_numbers(session_key):
        man = _read_json(_turn_dir(session_key, t) / "manifest.json", {})
        for f in man.get("files") or []:
            if isinstance(f, dict) and f.get("sha256"):
                blobs.add(str(f["sha256"]))
        identity = man.get("identity")
        if isinstance(identity, dict) and identity.get("record"):
            records.add(str(identity["record"]))
    return blobs, records


def _gc_blobs(session_key: str) -> int:
    """Delete the blobs and the folder records no surviving turn manifest names. Returns the blob
    bytes freed (the quantity the cap bounds)."""
    keep_blobs, keep_records = _referenced(session_key)
    freed = 0
    bd = _blob_dir(session_key)
    for f in bd.iterdir() if bd.is_dir() else ():
        if f.stem in keep_blobs:
            continue
        try:
            freed += f.stat().st_size
            f.unlink()
        except OSError:
            continue
    rd = _identity_dir(session_key)
    for f in rd.iterdir() if rd.is_dir() else ():
        if f.name.removesuffix(".json.gz") in keep_records:
            continue
        try:
            f.unlink()
        except OSError:
            continue
    return freed


def _drop_turn(session_key: str, turn: int) -> None:
    shutil.rmtree(_turn_dir(session_key, turn), ignore_errors=True)


def _enforce_turn_cap(session_key: str, b: _Bounds) -> int:
    """Keep at most ``max_turns`` turns; drop the oldest. Returns turns dropped."""
    turns = _turn_numbers(session_key)
    dropped = 0
    while len(turns) > b.max_turns:
        _drop_turn(session_key, turns.pop(0))
        dropped += 1
    if dropped:
        _gc_blobs(session_key)
    return dropped


def _make_room(session_key: str, incoming: int, b: _Bounds, *, keep_turn: int) -> bool:
    """Prune oldest turns until ``incoming`` fits under ``max_mb``.

    Returns False when it cannot fit even with every prunable turn gone (i.e. the single
    body exceeds the whole cap) — the caller then records it manifest-only. ``keep_turn`` is
    never dropped: evicting the turn currently being written would discard the manifest the
    caller is about to update.
    """
    if b.max_mb <= 0:
        return True  # 0 = cap disabled
    cap = b.max_mb * 1024 * 1024
    if incoming > cap:
        return False
    while store_bytes(session_key) + incoming > cap:
        turns = [t for t in _turn_numbers(session_key) if t != keep_turn]
        if not turns:
            # Nothing left to evict but the live turn. Its own earlier blobs are still
            # referenced, so the incoming body genuinely does not fit.
            return store_bytes(session_key) + incoming <= cap
        _drop_turn(session_key, turns[0])
        _gc_blobs(session_key)
    return True


def prune_session(session_key: str) -> bool:
    """Delete the whole checkpoint tree for *session_key* (called on session delete)."""
    sd = session_dir(session_key)
    if not sd.exists():
        return False
    shutil.rmtree(sd, ignore_errors=True)
    return not sd.exists()


def prune_orphans(live_session_keys: list[str] | set[str]) -> int:
    """Delete checkpoint trees whose session no longer exists. Returns trees removed."""
    root = store_root()
    if not root.is_dir():
        return 0
    keep = {session_slug(k) for k in live_session_keys}
    removed = 0
    for child in root.iterdir():
        if child.is_dir() and child.name not in keep:
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
    return removed


# ── preview ────────────────────────────────────────────────────────────────────


_MAX_DIFF_BYTES = 256 * 1024  # a body larger than this is summarized, not diffed


@dataclass
class RewindFile:
    """One file a rewind would touch."""

    path: str
    action: str  # "restore" | "delete" | "unchanged" | "not_captured"
    turn: int
    #: Why, when action is "not_captured": a backup skipped (``secret``, ``too_large``, …) or a
    #: change with no backup at all (:data:`UNBACKED_REASONS`). On a restore or a delete,
    #: :data:`CHANGED_BEFORE_BACKUP` when something with no backup changed the file first.
    reason: str = ""
    current_size: int = -1  # -1 = absent
    restored_size: int = -1  # -1 = would be deleted
    current_sha256: str = ""
    restored_sha256: str = ""
    diff: str = ""
    #: The manifest entry the backup came from; the preview's own, never on the wire.
    backup: dict = field(default_factory=dict, repr=False)


@dataclass
class RewindPreview:
    session_key: str
    turn: int
    files: list[RewindFile] = field(default_factory=list)
    turns_affected: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "session": self.session_key,
            "turn": self.turn,
            "turns_affected": self.turns_affected,
            "warnings": self.warnings,
            "files": [
                {
                    "path": f.path,
                    "action": f.action,
                    "turn": f.turn,
                    "reason": f.reason,
                    "current_size": f.current_size,
                    "restored_size": f.restored_size,
                    "current_sha256": f.current_sha256,
                    "restored_sha256": f.restored_sha256,
                    "diff": f.diff,
                }
                for f in self.files
            ],
        }


def _sha_of(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _text_or_none(data: bytes) -> str | None:
    if len(data) > _MAX_DIFF_BYTES or b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _unified(old: bytes, new: bytes, label: str) -> str:
    a, b = _text_or_none(old), _text_or_none(new)
    if a is None or b is None:
        return ""  # binary/oversized: the size+hash columns carry the signal
    return "".join(
        difflib.unified_diff(
            a.splitlines(keepends=True),
            b.splitlines(keepends=True),
            fromfile=f"{label} (current)",
            tofile=f"{label} (restored)",
            n=3,
        )
    )


def preview_rewind(session_key: str, turn: int) -> RewindPreview:
    """What ``/rewind-to-turn <turn>`` would do. Reads only — writes nothing.

    Both halves of the answer: the files a backup lets the rewind put back
    (:func:`_backed_up_preview`), and every other file under the folder that changed after
    *turn* with no backup, which the rewind leaves as it is (:func:`_name_unbacked_changes`).
    The second half walks the folder, so this is a worker thread's work, never the event loop's.
    """
    pv = _backed_up_preview(session_key, turn)
    if pv.turns_affected:
        _name_unbacked_changes(session_key, pv)
    return pv


def _backed_up_preview(session_key: str, turn: int) -> RewindPreview:
    """The files the rewind puts back: every file backed up in a turn **greater than** *turn*.

    The EARLIEST backup wins per path: turn N+1's copy is the state as of the end of turn N,
    which is exactly what "rewind to N" means. A path first seen absent rewinds to deleted. Reads
    only the backed-up files and the manifests, never the rest of the folder, so it is what
    :func:`apply_rewind` acts on, at the moment it acts.
    """
    pv = RewindPreview(session_key=session_key, turn=turn)
    cur = current_turn(session_key)
    if cur <= 0:
        pv.warnings.append("no checkpoints recorded for this session")
        return pv
    if turn < 0:
        pv.warnings.append("turn must be >= 0")
        return pv
    if turn >= cur:
        pv.warnings.append(f"turn {turn} is not before the current turn ({cur}) — nothing to undo")
        return pv

    affected = [t for t in _turn_numbers(session_key) if t > turn]
    pv.turns_affected = affected
    if not affected:
        pv.warnings.append(f"no recorded turns after {turn}")
        return pv
    oldest = min(_turn_numbers(session_key), default=cur)
    if oldest > turn + 1:
        pv.warnings.append(
            f"turns {turn + 1}..{oldest - 1} were pruned (cap reached) — "
            "their file states are no longer recoverable"
        )

    seen: dict[str, RewindFile] = {}
    for t in affected:  # ascending, so the earliest backup lands first and is kept
        man = _read_json(_turn_dir(session_key, t) / "manifest.json", {})
        for f in man.get("files") or []:
            if not isinstance(f, dict):
                continue
            p = str(f.get("path") or "")
            if not p or p in seen:
                continue
            target = Path(p)
            cur_exists = target.is_file()
            cur_size = target.stat().st_size if cur_exists else -1
            skipped = str(f.get("skipped") or "")
            if skipped:
                seen[p] = RewindFile(
                    path=p,
                    action="not_captured",
                    turn=t,
                    reason=skipped,
                    current_size=cur_size,
                    current_sha256=_sha_of(target) if cur_exists else "",
                )
                continue
            if not f.get("existed"):
                seen[p] = RewindFile(
                    path=p,
                    action="delete" if cur_exists else "unchanged",
                    turn=t,
                    current_size=cur_size,
                    restored_size=-1,
                    current_sha256=_sha_of(target) if cur_exists else "",
                    backup=f,
                )
                continue
            digest = str(f.get("sha256") or "")
            blob = _blob_dir(session_key) / f"{digest}.bin"
            if not digest or not blob.is_file():
                seen[p] = RewindFile(
                    path=p,
                    action="not_captured",
                    turn=t,
                    reason="blob missing",
                    current_size=cur_size,
                    current_sha256=_sha_of(target) if cur_exists else "",
                )
                continue
            try:
                restored = blob.read_bytes()
            except OSError:
                seen[p] = RewindFile(
                    path=p, action="not_captured", turn=t, reason="blob unreadable"
                )
                continue
            current = target.read_bytes() if cur_exists else b""
            cur_sha = hashlib.sha256(current).hexdigest() if cur_exists else ""
            same = cur_exists and cur_sha == digest
            seen[p] = RewindFile(
                path=p,
                action="unchanged" if same else "restore",
                turn=t,
                current_size=cur_size,
                restored_size=len(restored),
                current_sha256=cur_sha,
                restored_sha256=digest,
                diff="" if same else _unified(current, restored, p),
                backup=f,
            )

    pv.files = sorted(seen.values(), key=lambda f: f.path)
    for f in pv.files:
        if f.action == "not_captured" and f.reason == "secret":
            pv.warnings.append(
                f"{f.path}: never captured (credential-shaped file) — it will NOT be restored"
            )
        elif f.action == "not_captured" and f.reason == "too_large":
            pv.warnings.append(f"{f.path}: not captured (over the per-file cap) — not restorable")
        elif f.action == "not_captured" and f.reason == "over_cap":
            pv.warnings.append(
                f"{f.path}: not captured (it did not fit under the store's cap) — not restorable"
            )
        elif f.action == "not_captured" and f.reason == "outside":
            pv.warnings.append(
                f"{f.path}: outside this chat's folders, so it was not backed up — it will NOT "
                "be restored"
            )
    return pv


def _name_unbacked_changes(session_key: str, pv: RewindPreview) -> None:
    """Add to *pv* every file that changed after its turn with no backup, which the rewind
    leaves as it is: a shell command's change, an agent CLI's edit made without asking.

    The folder as the first undone turn began (its identity set, :func:`begin_turn`) is the
    folder as the rewind's turn ended. Two kinds of file are named from it:

    * one no backup covers whose size or modification time differs now, or that is new or gone:
      a ``not_captured`` entry saying which (:data:`UNBACKED_REASONS`);
    * one whose backup was taken after something with no backup had already changed it (a
      command reformatted it, and the agent edited it later): the rewind puts it back only as far
      as that backup, which its entry says (:data:`CHANGED_BEFORE_BACKUP`), and one whose backup
      leaves it as it is now is a ``not_captured`` entry like the first kind.

    One warning says plainly that the rewind leaves these. A first undone turn with no record (a
    turn opened by its first backup, a store from before records) borrows the next turn's that
    has one, and says from when the list runs. A record cut short at the cap still names what
    changed or went among the files it holds, and says that the list may be incomplete. Walks the
    folder: a worker thread's work.
    """
    first: tuple[int, dict] | None = None
    for t in pv.turns_affected:
        identity = _read_json(_turn_dir(session_key, t) / "manifest.json", {}).get("identity")
        if isinstance(identity, dict) and identity.get("record") and identity.get("cwd"):
            first = (t, identity)
            break
    if first is None:
        pv.warnings.append(
            f"no record of the folder after turn {pv.turn}, so files changed with no backup "
            "(by a command, or by an agent CLI's edit it did not ask about) cannot be listed"
        )
        return
    t, identity = first
    if t != pv.turns_affected[0]:
        pv.warnings.append(
            f"no record of the folder as turn {pv.turns_affected[0]} began, so this list of "
            f"files changed with no backup starts at turn {t}"
        )
    then = _folder_record(session_key, str(identity["record"]))
    if then is None:
        pv.warnings.append(
            f"the record of the folder as turn {t} began is gone, so files changed with no "
            "backup cannot be listed"
        )
        return
    base = Path(str(identity["cwd"]))
    prefix = str(base).rstrip(os.sep) + os.sep
    then_short = bool(identity.get("truncated"))
    now, now_short = _identity_set(base) if base.is_dir() else ({}, False)
    kept_out = _kept_out(str(base))

    def recorded(real: str) -> bool:
        """Whether the record would hold *real* had it been there: so a file it does not hold
        was not there, rather than out of the walk's reach."""
        parts = real[len(prefix) :].split(os.sep)
        return not (
            then_short
            or any(part in _IDENTITY_SKIP_DIRS or part.startswith(".git") for part in parts[:-1])
            or kept_out(real)
        )

    named: list[RewindFile] = []
    covered: set[str] = set()
    for f in pv.files:
        real = os.path.realpath(f.path)
        covered.update((f.path, real))
        if f.turn < t or not f.backup or not real.startswith(prefix):
            continue
        backup = f.backup
        if backup.get("existed") and backup.get("mtime_ns") is None:
            continue  # a backup from before backups kept the file's time: nothing to compare
        was = then.get(real[len(prefix) :])
        at_backup = [backup.get("size"), backup.get("mtime_ns")] if backup.get("existed") else None
        if was == at_backup or (was is None and not recorded(real)):
            continue
        if f.action == "unchanged":
            # Its backup is how the file is now, and that is not how it was: left as it is.
            f.action = "not_captured"
            f.reason = (
                UNBACKED_CREATED
                if was is None
                else (UNBACKED_DELETED if f.current_size < 0 else UNBACKED_CHANGED)
            )
        else:
            f.reason = CHANGED_BEFORE_BACKUP
        named.append(f)

    unbacked: list[RewindFile] = []
    for rel in sorted(set(then) | set(now)):
        was, is_now = then.get(rel), now.get(rel)
        if was == is_now:
            continue
        path = base / rel
        if is_now is None and now_short:
            # Past the files this walk got to: ask the file itself.
            try:
                st = path.stat()
                is_now = [st.st_size, st.st_mtime_ns]
            except OSError:
                is_now = None
            if was == is_now:
                continue
        if was is None and then_short:
            continue  # beyond what the record holds: new, or only never recorded
        if str(path) in covered or os.path.realpath(path) in covered:
            continue
        how = (
            UNBACKED_CREATED
            if was is None
            else (UNBACKED_DELETED if is_now is None else UNBACKED_CHANGED)
        )
        unbacked.append(
            RewindFile(
                path=str(path),
                action="not_captured",
                turn=t,
                reason=how,
                current_size=int(is_now[0]) if is_now else -1,
            )
        )
    if unbacked:
        pv.files = sorted([*pv.files, *unbacked], key=lambda f: f.path)
    left = [f for f in pv.files if f.action == "not_captured" and f.reason in UNBACKED_REASONS]
    if left:
        one = len(left) == 1
        pv.warnings.append(
            f"{len(left)} file{'' if one else 's'} changed after turn {pv.turn} with no backup, "
            f"so the rewind leaves {'it as it is' if one else 'them as they are'}. Backed up are "
            "the agent's own file edits and the agent CLI edits PersonalClaw was asked about; a "
            "shell command's changes, and an agent CLI's edits made without asking, are not."
        )
    for f in named:
        if f.reason == CHANGED_BEFORE_BACKUP:
            pv.warnings.append(
                f"{f.path}: changed with no backup before its backup in turn {f.turn}, so the "
                "rewind puts it back only to how it was then"
            )
    if then_short or now_short:
        pv.warnings.append(
            f"the folder has more than {_IDENTITY_MAX_ENTRIES:,} files, or took too long to "
            "read in full, so files changed with no backup may be missing from this list"
        )


# ── the restore confinement floor ──────────────────────────────────────────────


def session_roots(session_key: str) -> list[Path]:
    """Every directory a rewind of *session_key* is allowed to write into.

    The union of the ``cwd`` recorded on each of the session's turn manifests and the
    configured workspace root. The workspace root is always included so the set is never
    empty — a session opened with no ``cwd`` (which :func:`begin_turn` supports) would
    otherwise have no root, and an empty root set would have to either refuse every restore
    or wave every path through. Neither is acceptable, so the floor is "the workspace".
    """
    roots: list[Path] = []
    seen: set[str] = set()

    def _add(raw: object) -> None:
        text = str(raw or "").strip()
        if not text:
            return
        try:
            r = Path(text).resolve()
        except OSError:
            return
        if str(r) not in seen:
            seen.add(str(r))
            roots.append(r)

    for t in _turn_numbers(session_key):
        man = _read_json(_turn_dir(session_key, t) / "manifest.json", {})
        _add(man.get("cwd"))
        recorded = man.get("roots")
        if isinstance(recorded, list):
            for entry in recorded:
                _add(entry)
    try:
        from personalclaw.config.loader import workspace_root

        _add(workspace_root())
    except Exception:  # noqa: BLE001 — a missing workspace root must not widen the set
        logger.debug("turn_checkpoints: workspace_root unreadable", exc_info=True)
    return roots


def is_within_roots(path: Path | str, roots: list[Path]) -> bool:
    """Whether *path* resolves inside one of *roots*.

    Compared on RESOLVED paths, so ``<root>/../../etc/hosts`` and a symlink out of the tree
    are both caught — a lexical ``startswith`` would pass the first and never see the second.
    A file that does not exist yet still normalizes, so a restore-to-create is checked too.
    """
    if not roots:
        return False
    try:
        target = Path(path).resolve()
    except OSError:
        return False
    for root in roots:
        try:
            target.relative_to(root)
            return True
        except ValueError:
            continue
    return False


# ── apply (two-phase, journaled) ───────────────────────────────────────────────


@dataclass
class RewindResult:
    ok: bool
    restored: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    #: Paths refused because they resolve OUTSIDE the session's roots. Reported separately
    #: from `errors` so a client can say *why* nothing was written to them.
    refused: list[str] = field(default_factory=list)
    journal: str = ""
    safety_turn: int = 0

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "restored": self.restored,
            "deleted": self.deleted,
            "skipped": self.skipped,
            "errors": self.errors,
            "refused": self.refused,
            "journal": self.journal,
            "safety_turn": self.safety_turn,
        }


_STAGE_SUFFIX = ".pclaw-rewind"


def _restored_mode(stage: Path, recorded: object) -> int | None:
    """The permissions a restored body is staged with, and so lands with: the ones its file had
    when it was backed up, so an executable stays executable and a file only its owner could read
    is not left readable by everyone. Under the home, the owner's bits alone, since no file there
    is written with a group or other bit (``atomic_write.private_mode_for``). ``None``, the
    writer's own default, for a backup that recorded none."""
    if not isinstance(recorded, int) or isinstance(recorded, bool):
        return None
    return recorded & (0o700 if is_in_home(stage) else 0o777)


def _journal_path(session_key: str, token: str) -> Path:
    return session_dir(session_key) / f"rewind-{token}.json"


def apply_rewind(
    session_key: str, turn: int, *, preview: RewindPreview | None = None
) -> RewindResult:
    """Restore the session's files to their state as of the end of *turn*.

    Two phases, so a death between them cannot leave a tree that is neither the old state
    nor the new one:

    * **stage** — every restored body is written to ``<target>.pclaw-rewind`` (a sibling, so
      the later rename stays on one filesystem and is therefore atomic), and the plan is
      journaled to ``rewind-<token>.json``. Nothing the user can see has changed yet.
    * **commit** — :func:`os.replace` each staged file onto its target, then unlink the
      journal. A crash mid-commit leaves the journal;
      :func:`resume_incomplete_rewind` replays it, and replay is idempotent because the
      journal carries the expected sha of every restored body.

    Before staging, the CURRENT bytes of every touched file are captured into a fresh
    "safety" turn, so the rewind itself is rewindable.

    Acts on the backups alone (:func:`_backed_up_preview` when no *preview* is given), read as
    it runs, so it never walks the folder: a caller on the event loop gets a restore no turn can
    start part way through.
    """
    with _REWIND_LOCK:
        return _apply_rewind(session_key, turn, preview)


def _apply_rewind(session_key: str, turn: int, preview: RewindPreview | None) -> RewindResult:
    pv = preview if preview is not None else _backed_up_preview(session_key, turn)
    res = RewindResult(ok=False)
    actionable = [f for f in pv.files if f.action in ("restore", "delete")]

    # Confinement BEFORE anything is staged. A rewind is the one path in the system that
    # writes an arbitrary recorded path back to disk, so it verifies the destination itself
    # rather than trusting the manifest that named it: a tampered store, a traversal
    # component, or a symlink planted out of the tree must not become a write outside the
    # session's roots. Refused paths are REPORTED (never silently dropped) — a rewind that
    # looked complete while one file stayed mangled is the defect this whole preview exists
    # to prevent.
    roots = session_roots(session_key)
    allowed: list[RewindFile] = []
    for f in actionable:
        if is_within_roots(f.path, roots):
            allowed.append(f)
        else:
            res.refused.append(f.path)
            res.errors.append(f"refused (outside the session's workspace roots): {f.path}")
    actionable = allowed

    if not actionable:
        # `ok` only when there was genuinely nothing to do. A refusal is NOT a no-op: the
        # user asked for a restore and did not get one, and reporting `ok=True` here would
        # be the swallowed-write shape — a confirm path that says it succeeded while the
        # file on disk stayed mangled.
        res.ok = not res.refused
        res.skipped = [f.path for f in pv.files]
        return res

    # A safety turn FIRST: `capture_pre_edit` reads current bytes, so it has to run before
    # anything is replaced. It also means the store's own secrecy floor applies — a
    # credential-shaped file is not copied here either.
    safety = begin_turn(session_key, cwd=None)
    res.safety_turn = safety
    for f in actionable:
        capture_pre_edit(session_key, f.path)

    token = uuid.uuid4().hex[:16]
    plan: list[dict] = []
    staged: list[Path] = []
    try:
        for f in actionable:
            target = Path(f.path)
            if f.action == "delete":
                plan.append({"path": f.path, "op": "delete"})
                continue
            blob = _blob_dir(session_key) / f"{f.restored_sha256}.bin"
            data = blob.read_bytes()
            stage = target.with_name(target.name + _STAGE_SUFFIX)
            stage.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_bytes(stage, data, mode=_restored_mode(stage, f.backup.get("mode")))
            staged.append(stage)
            plan.append(
                {
                    "path": f.path,
                    "op": "restore",
                    "stage": str(stage),
                    "sha256": f.restored_sha256,
                    "size": len(data),
                }
            )
        _write_json(
            _journal_path(session_key, token),
            {
                "token": token,
                "session_key": session_key,
                "to_turn": turn,
                "safety_turn": safety,
                "staged_at": time.time(),
                "plan": plan,
            },
        )
    except OSError as exc:
        # Staging failed: unwind every temp we made. The working tree is untouched, so the
        # honest outcome is "nothing happened", not a partial restore.
        for s in staged:
            try:
                s.unlink()
            except OSError:
                pass
        res.errors.append(f"staging failed, no files were modified: {exc}")
        return res

    res.journal = str(_journal_path(session_key, token))
    # EXTEND, never assign: a confinement refusal recorded before staging would otherwise be
    # erased here, and `ok` would flip back to True — a confirm path reporting success while a
    # file the user asked about was never written.
    restored, deleted, commit_errors = _commit_journal(session_key, token)
    res.restored, res.deleted = restored, deleted
    res.errors.extend(commit_errors)
    res.ok = not res.errors
    res.skipped = [f.path for f in pv.files if f.action not in ("restore", "delete")]
    return res


def _commit_journal(session_key: str, token: str) -> tuple[list[str], list[str], list[str]]:
    """Phase 2. Replay a staged plan onto the working tree; idempotent."""
    jp = _journal_path(session_key, token)
    j = _read_json(jp, {})
    restored: list[str] = []
    deleted: list[str] = []
    errors: list[str] = []
    for step in j.get("plan") or []:
        if not isinstance(step, dict):
            continue
        target = Path(str(step.get("path") or ""))
        op = str(step.get("op") or "")
        try:
            if op == "delete":
                if target.exists():
                    target.unlink()
                deleted.append(str(target))
                continue
            stage = Path(str(step.get("stage") or ""))
            want = str(step.get("sha256") or "")
            if not stage.exists():
                # Already committed on an earlier pass (replay), or the stage was lost.
                if want and _sha_of(target) == want:
                    restored.append(str(target))
                else:
                    errors.append(f"{target}: staged body missing and target does not match")
                continue
            os.replace(stage, target)
            restored.append(str(target))
        except OSError as exc:
            errors.append(f"{target}: {exc}")
    if not errors:
        try:
            jp.unlink()
        except OSError:
            pass
    return restored, deleted, errors


def pending_rewinds(session_key: str) -> list[str]:
    """Tokens of rewinds that staged but never finished committing."""
    sd = session_dir(session_key)
    if not sd.is_dir():
        return []
    out = []
    for f in sd.iterdir():
        if f.is_file() and f.name.startswith("rewind-") and f.name.endswith(".json"):
            out.append(f.name[len("rewind-") : -len(".json")])
    return sorted(out)


def resume_incomplete_rewind(session_key: str) -> dict:
    """Finish any rewind that died between staging and commit.

    This is the answer to "what happens if the process dies mid-restore": the tree is left
    with some targets replaced and some not, but the journal names every remaining step and
    the staged bodies are still on disk, so replaying completes it. Called on preview and on
    apply, so the ambiguity cannot outlive the next interaction with the store.
    """
    out: dict = {"resumed": [], "errors": []}
    with _REWIND_LOCK:
        for token in pending_rewinds(session_key):
            restored, deleted, errors = _commit_journal(session_key, token)
            out["resumed"].append(
                {"token": token, "restored": restored, "deleted": deleted, "errors": errors}
            )
            out["errors"].extend(errors)
    return out
