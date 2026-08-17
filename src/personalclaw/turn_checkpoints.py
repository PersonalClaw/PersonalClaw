"""Turn-bound two-phase file checkpointing + ``/rewind-to-turn`` (EXECUTION-ISOLATION §6).

The interactive-tier complement to the workflow journal's run-scoped checkpoints. Scope:
**chat/loop sessions and their tool-driven file edits on the host** — where today a wrong
``edit_file`` is simply gone. :mod:`personalclaw.dashboard.chat_undo` rolls back the
CONVERSATION and says in its own docstring that files written are NOT reverted; this module
is the other half, and the two stay deliberately separate (rewinding the transcript would
destroy the record of what happened).

**Two phases, per turn:**

1. :func:`begin_turn` — the *identity set*: paths + mtime + size of the files under the
   session's cwd scope. A cheap manifest, no copies, so a turn that writes nothing costs
   only a few KB of JSON. Its job is to tell a later rewind which files the turn *created*
   (present now, absent from the identity set) versus *modified*.
2. :func:`capture_pre_edit` — the *pre-edit backup*: the file-writing tool handlers call
   this before the first mutation of a path in the current turn, so the bytes are copied
   while they still exist. Content-addressed (sha256) and deduped at the session level, via
   :func:`~personalclaw.atomic_write.atomic_write_bytes`. Only touched files cost bytes.

**Restore is two-phase too, and journaled**, because a half-restored working tree is worse
than no restore: :func:`apply_rewind` first *stages* every restored body as a sibling temp
file and writes a plan journal, then *commits* with :func:`os.replace` (atomic per file).
A process death between the per-file renames leaves the journal on disk;
:func:`resume_incomplete_rewind` finishes it on next access. Nothing is written before the
user has seen :func:`preview_rewind` and confirmed.

**Secrecy floor (:data:`NEVER_CAPTURE_GLOBS`).** ``.env`` and its siblings are never copied
into the store — not filtered on the way out, never written in the first place. This is the
restrictive reading of a plan that says only "``.env`` files were never captured": the store
lives under the home, is covered by snapshots and exports, and a captured credential would
outlive the file the user deleted. The consequence is stated rather than hidden: a skipped
path is recorded in the turn manifest as ``skipped="secret"`` (the *path*, never the bytes)
so :func:`preview_rewind` can warn "not captured" instead of silently restoring nothing.
``is_sensitive_path`` is *also* consulted, but it is home-anchored, so it does not see a
workspace ``.env`` — this floor is what closes that.

**Bounds.** Per-session byte cap and turn cap (``checkpoints.max_mb`` /
``checkpoints.max_turns``), enforced on the way in: adding a body that would exceed the cap
prunes the oldest turns until it fits, and a single body over ``checkpoints.max_file_mb`` is
recorded manifest-only (``skipped="too_large"``) so the preview can say "not captured".
Pruned with the session (:func:`prune_session`).

Explicitly NOT git: it works in non-repos and never touches the user's index.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

from personalclaw.atomic_write import atomic_write, atomic_write_bytes

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

#: Ceiling on identity-set entries. Phase 1 is a legibility aid, not the restore mechanism
#: (phase 2 is), so truncating it degrades the "created vs modified" label on a huge tree
#: rather than the restore itself. Recorded as ``identity_truncated`` when hit.
_IDENTITY_MAX_ENTRIES = 20_000

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
    the plan's (200MB / 50 turns).
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
    """
    p = Path(path)
    name = p.name.lower()
    if any(fnmatch(name, g.lower()) for g in NEVER_CAPTURE_GLOBS):
        return True
    try:
        from personalclaw.security import is_sensitive_path

        return bool(is_sensitive_path(str(p)))
    except Exception:  # noqa: BLE001 — a check that cannot run must not widen capture
        logger.debug("turn_checkpoints: sensitive-path check failed for %s", p, exc_info=True)
        return True


# ── phase 1: the identity set ──────────────────────────────────────────────────


def _identity_set(cwd: Path) -> tuple[list[dict], bool]:
    """paths+mtime+size under *cwd* — no copies. Returns (entries, truncated)."""
    out: list[dict] = []
    truncated = False
    base = cwd.resolve()
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in _IDENTITY_SKIP_DIRS and not d.startswith(".git")]
        for fn in files:
            if len(out) >= _IDENTITY_MAX_ENTRIES:
                truncated = True
                return out, truncated
            fp = Path(root) / fn
            try:
                st = fp.stat()
            except OSError:
                continue
            try:
                rel = str(fp.relative_to(base))
            except ValueError:
                continue
            out.append({"path": rel, "size": st.st_size, "mtime": round(st.st_mtime, 3)})
    return out, truncated


def begin_turn(session_key: str, *, cwd: Path | str | None = None) -> int:
    """Open the next turn for *session_key*; returns its number (1-based).

    Phase 1. Writes the identity-set manifest for *cwd* (skipped when *cwd* is None or
    missing — a session with no workspace still gets a numbered turn so
    :func:`capture_pre_edit` has somewhere to put bytes). Never raises: a checkpoint store
    that breaks a turn is worse than one that misses a turn.
    """
    b = _bounds()
    if not b.enabled:
        return 0
    try:
        sd = session_dir(session_key)
        sd.mkdir(parents=True, exist_ok=True)
        state = _read_json(_state_path(session_key), {"current_turn": 0})
        turn = int(state.get("current_turn") or 0) + 1
        state.update({"current_turn": turn, "session_key": session_key})
        _write_json(_state_path(session_key), state)

        identity: list[dict] = []
        truncated = False
        base = Path(cwd).resolve() if cwd else None
        if base is not None and base.is_dir():
            identity, truncated = _identity_set(base)
        _write_json(
            _turn_dir(session_key, turn) / "manifest.json",
            {
                "turn": turn,
                "session_key": session_key,
                "cwd": str(base) if base else "",
                "started_at": time.time(),
                "identity": identity,
                "identity_truncated": truncated,
                "files": [],
            },
        )
        _enforce_turn_cap(session_key, b)
        return turn
    except Exception:  # noqa: BLE001
        logger.warning("turn_checkpoints: begin_turn failed for %s", session_key, exc_info=True)
        return 0


def current_turn(session_key: str) -> int:
    return int(_read_json(_state_path(session_key), {}).get("current_turn") or 0)


# ── phase 2: the pre-edit backup ───────────────────────────────────────────────


def capture_pre_edit(session_key: str, path: Path | str, *, cwd: Path | str | None = None) -> str:
    """Back up *path*'s current bytes before this turn's first mutation of it.

    Returns a short status for logging/tests: ``"captured"``, ``"deduped"`` (already backed
    up in this turn), ``"absent"`` (the write creates the file — recorded so a rewind can
    delete it), ``"secret"``, ``"too_large"``, ``"disabled"``, or ``"error"``.

    Never raises. Called from the ``write_file``/``edit_file`` handlers, so an exception here
    would fail the agent's tool call — the store degrades instead.
    """
    b = _bounds()
    if not b.enabled:
        return "disabled"
    try:
        target = Path(path)
        turn = current_turn(session_key) or begin_turn(session_key, cwd=cwd)
        if turn <= 0:
            return "error"
        man_path = _turn_dir(session_key, turn) / "manifest.json"
        man = _read_json(man_path, {"turn": turn, "files": []})
        files = man.get("files")
        if not isinstance(files, list):
            files = []
        key = str(target)
        if any(isinstance(f, dict) and f.get("path") == key for f in files):
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
        entry.update({"existed": True, "sha256": digest, "size": len(data)})
        files.append(entry)
        man["files"] = files
        _write_json(man_path, man)
        return "captured"
    except Exception:  # noqa: BLE001
        logger.warning("turn_checkpoints: capture failed for %s", path, exc_info=True)
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


def _referenced_shas(session_key: str) -> set[str]:
    out: set[str] = set()
    for t in _turn_numbers(session_key):
        man = _read_json(_turn_dir(session_key, t) / "manifest.json", {})
        for f in man.get("files") or []:
            if isinstance(f, dict) and f.get("sha256"):
                out.add(str(f["sha256"]))
    return out


def _gc_blobs(session_key: str) -> int:
    """Delete blobs no surviving turn manifest references. Returns bytes freed."""
    bd = _blob_dir(session_key)
    if not bd.is_dir():
        return 0
    keep = _referenced_shas(session_key)
    freed = 0
    for f in bd.iterdir():
        if f.stem in keep:
            continue
        try:
            freed += f.stat().st_size
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
    reason: str = ""  # why, when action is "not_captured"
    current_size: int = -1  # -1 = absent
    restored_size: int = -1  # -1 = would be deleted
    current_sha256: str = ""
    restored_sha256: str = ""
    diff: str = ""


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

    Every file backed up in a turn **greater than** *turn* is reported. The EARLIEST
    backup wins per path: turn N+1's copy is the state as of the end of turn N, which is
    exactly what "rewind to N" means. A path first seen absent rewinds to deleted.
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
            )

    pv.files = sorted(seen.values(), key=lambda f: f.path)
    for f in pv.files:
        if f.action == "not_captured" and f.reason == "secret":
            pv.warnings.append(
                f"{f.path}: never captured (credential-shaped file) — it will NOT be restored"
            )
        elif f.action == "not_captured" and f.reason == "too_large":
            pv.warnings.append(f"{f.path}: not captured (over the per-file cap) — not restorable")
    return pv


# ── apply (two-phase, journaled) ───────────────────────────────────────────────


@dataclass
class RewindResult:
    ok: bool
    restored: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    journal: str = ""
    safety_turn: int = 0

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "restored": self.restored,
            "deleted": self.deleted,
            "skipped": self.skipped,
            "errors": self.errors,
            "journal": self.journal,
            "safety_turn": self.safety_turn,
        }


_STAGE_SUFFIX = ".pclaw-rewind"


def _journal_path(session_key: str, token: str) -> Path:
    return session_dir(session_key) / f"rewind-{token}.json"


def apply_rewind(session_key: str, turn: int, *, preview: RewindPreview | None = None):
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
    """
    pv = preview if preview is not None else preview_rewind(session_key, turn)
    res = RewindResult(ok=False)
    actionable = [f for f in pv.files if f.action in ("restore", "delete")]
    if not actionable:
        res.ok = True
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
            atomic_write_bytes(stage, data)
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
    commit = _commit_journal(session_key, token)
    res.restored, res.deleted, res.errors = commit
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
    for token in pending_rewinds(session_key):
        restored, deleted, errors = _commit_journal(session_key, token)
        out["resumed"].append(
            {"token": token, "restored": restored, "deleted": deleted, "errors": errors}
        )
        out["errors"].extend(errors)
    return out
