"""Watched local directories — the dir-source provider (WATCHED-SOURCES §4, am.5).

A watched directory is the one source kind whose upstream is MUTABLE: a feed appends,
but a folder of notes is edited in place, renamed, and deleted. So this provider is not a
fetcher, it is an OBSERVER: each poll takes a cheap ``(mtime, size)`` signature of every
matching file, diffs it against the signature map persisted in the source's cursor, and
reports what changed as ``created`` / ``modified`` / ``deleted`` sightings. The engine owns
what persisting each kind means (§1.1) — which is what keeps a filesystem event from ever
being able to hard-delete a library item.

**Why signatures and not ``watchdog``.** A dependency-free poll is the same trade
``triggers/file_poll.py`` already made for `file` triggers: an OS-level watcher adds a
platform-specific dependency and a per-directory thread, and still needs the signature map
for the restart case (events that fired while the process was down are simply lost). A
minute of latency on "a note changed" is invisible; a missed edit is not. ``fs_watch.py``
and ``triggers/file_watch.py`` are untouched — this is the knowledge-library path, they are
the chat/automation paths.

**Debounce is the point of the design.** An editor writes a file several times per save
(and a formatter/sync client several more), so re-indexing on first sighting would re-embed
the same note three times per keystroke burst. A changed file is therefore only emitted
once it has been QUIET for ``debounce_secs`` — and the quiet clock is the file's own
``mtime``, not the moment this loop happened to notice it. That choice matters twice: a
further edit inside the window moves ``mtime`` and so restarts the window by construction
(no timer state to keep, nothing to lose across a restart), and the file's baseline
signature is left uncommitted while it settles, so the change is simply re-observed next
pass. Three edits to one file in one window collapse to exactly one re-index; three
different files edited in one window produce exactly three — one each, never one per
intermediate signature. A vanished file has no mtime, so a delete's window is timed from
when it was first observed missing (the one piece of state the cursor carries for it).

**The first pass seeds and emits nothing.** Enrolling a directory of 4000 existing notes
must not ingest 4000 items — that is the startup storm ``WatchState.seeded`` exists to
prevent for `file` triggers, and the same rule holds here: pass one records the baseline,
and only changes AFTER it are library events.

**Save-time validation runs at POLL time too** (:meth:`validate_spec`). The spec is data in
a SQLite row that an MCP tool, an app, or a hand-edit can change after the fact, so a guard
that only ran on the create path would be one edit away from being bypassed — the same
reasoning ``pathguard`` applies to the `paths` capability. A sensitive path (credential
store, key material) is refused outright, and the file cap bounds one poll's work.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from personalclaw.knowledge_providers.base import (
    CHANGE_CREATED,
    CHANGE_DELETED,
    CHANGE_MODIFIED,
    KnowledgeItem,
    KnowledgeSource,
    KnowledgeSourceProvider,
    SourceItem,
    SourcePollResult,
)

logger = logging.getLogger(__name__)

#: Seconds a changed file must hold one signature before it is re-indexed. Five seconds
#: covers an editor's multi-write save and a formatter-on-save round trip while keeping
#: "I edited a note" to one poll interval of latency. Per-source overridable via the
#: spec's ``debounce_secs`` — a directory synced by a slow client wants a longer window.
DEFAULT_DEBOUNCE_SECS = 5.0

#: Files considered when the spec names no ``include`` globs. Text the library can
#: actually index; a watched directory is a notes/docs folder, not a binary drop.
DEFAULT_INCLUDE = ("*.md", "*.markdown", "*.txt", "*.rst", "*.org")

#: Never walked, whatever the globs say: VCS/dependency/build noise a user never means to
#: index, and the churn that would dominate every diff.
SKIP_DIRS = frozenset(
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
        ".DS_Store",
        "dist",
        "build",
        ".personalclaw",
    }
)

#: Hard ceiling on files tracked per source (the §4 path-cap). A directory pointed at
#: ``/`` must degrade to a refusal, not a multi-hour stat walk that starves the loop.
MAX_FILES_PER_SOURCE = 5000

#: Per-file content ceiling. A file larger than this is tracked (its deletion still
#: archives) but truncated on read — one pathological file cannot blow the poll's memory.
MAX_FILE_BYTES = 2 * 1024 * 1024

#: How many reported deletions a source remembers, so a restored file revives its archived
#: item instead of being dropped by the engine's novelty gate. Bounded like the seen-set.
MAX_TOMBSTONES = 1000


@dataclass
class _DirCursor:
    """The source's persisted observation state (§3.2 cursor, opaque to the engine).

    ``sigs`` is the committed baseline — the last signature actually re-indexed for each
    relative path. A file whose change is still settling is deliberately NOT written into
    it, which is what makes the change re-observable next pass without a timer. ``gone``
    times the debounce window for deletions (``rel -> first_missing_at``), the one change
    kind with no mtime of its own, and ``tombstones`` remembers the deletions already
    REPORTED so a restored file revives its archived item (see :meth:`remember_deleted`).
    ``seeded`` records that the baseline pass has happened, so a restart never re-ingests
    the whole directory.
    """

    seeded: bool = False
    sigs: dict[str, list] = field(default_factory=dict)
    gone: dict[str, float] = field(default_factory=dict)
    tombstones: dict[str, float] = field(default_factory=dict)

    @classmethod
    def parse(cls, raw: str) -> _DirCursor:
        """Revive a cursor; a missing or corrupt one degrades to unseeded.

        Unseeded is the SAFE degradation: the next poll re-records the baseline and emits
        nothing, so a truncated cursor costs one skipped change rather than re-ingesting
        (and re-embedding) every file in the directory.
        """
        try:
            data = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        sigs = data.get("sigs")
        out = cls(seeded=bool(data.get("seeded")))
        if isinstance(sigs, dict):
            out.sigs = {str(k): list(v) for k, v in sigs.items() if isinstance(v, (list, tuple))}
        for attr in ("gone", "tombstones"):
            raw_map = data.get(attr)
            if not isinstance(raw_map, dict):
                continue
            target = getattr(out, attr)
            for k, v in raw_map.items():
                try:
                    target[str(k)] = float(v)
                except (TypeError, ValueError):
                    continue
        return out

    def dump(self) -> str:
        return json.dumps(
            {
                "seeded": self.seeded,
                "sigs": self.sigs,
                "gone": self.gone,
                "tombstones": self.tombstones,
            },
            sort_keys=True,
        )

    def remember_deleted(self, rel: str, at: float) -> None:
        """Record that a delete was REPORTED for ``rel``, so a later re-appearance is a
        modification of the (archived) item rather than a create the engine's novelty gate
        would silently drop — the guid has already been seen, so a create can never write
        it again. Kept in the cursor, not inferred from the store, so the append-only
        storm guard stays exactly as strict for feed providers."""
        self.sigs.pop(rel, None)
        self.tombstones[rel] = at
        while len(self.tombstones) > MAX_TOMBSTONES:
            # Insertion-ordered: drop the oldest. A very old tombstone falling off means a
            # long-deleted file that reappears re-indexes as a create instead of a revive,
            # which is the same bounded trade the seen-set's FIFO cap makes.
            self.tombstones.pop(next(iter(self.tombstones)))


def _signature(path: Path) -> list | None:
    """``[mtime, size]`` for one file, or None when it cannot be stat'ed.

    Fail-open per file: a vanished/permission-denied entry is skipped so the rest of the
    directory still polls. Aborting the cycle on one unreadable file would let a single
    root-owned file stop every other note in the folder from ever being indexed.
    """
    try:
        st = path.stat()
    except OSError:
        return None
    return [float(st.st_mtime), int(st.st_size)]


class DirSourceProvider(KnowledgeSourceProvider):
    """Poll-capable provider over a watched local directory (§4).

    Reads its per-source configuration from the WatchedSource row's ``spec``:

    ``path``          the directory to watch (required)
    ``include``       glob patterns for filenames (defaults to :data:`DEFAULT_INCLUDE`)
    ``recursive``     walk subdirectories (default true)
    ``debounce_secs`` quiet window before a change is re-indexed
    ``max_files``     per-source file cap, clamped to :data:`MAX_FILES_PER_SOURCE`

    ``now_fn`` is injected so a test drives the debounce window at an exact instant
    instead of sleeping on the wall clock — the same seam the engine exposes.
    """

    poll_interval_seconds = 300

    def __init__(self, store: Any, *, now_fn=None) -> None:
        self._store = store
        import time

        self._now_fn = now_fn or time.time

    @property
    def name(self) -> str:
        return "watched-dir"

    @property
    def display_name(self) -> str:
        return "Watched Directory"

    # ── corpus contract (the library itself owns search/get) ────────────────────────

    async def list_sources(self) -> list[KnowledgeSource]:
        return [
            KnowledgeSource(
                id=s["id"],
                name=s["name"],
                source_type="dir",
                provider=self.name,
            )
            for s in self._store.list_sources()
            if s.get("provider") == self.name
        ]

    async def search(self, query: str, limit: int = 10) -> list[KnowledgeItem]:
        # Items land in the library on ingest, so the library's own search covers them —
        # a second search path here would be a divergent ranking of the same rows.
        return []

    async def get_item(self, item_id: str) -> KnowledgeItem | None:
        return None

    # ── save-time (and poll-time) spec validation — §4 path guard + cap ─────────────

    def validate_spec(self, spec: dict) -> tuple[bool, str]:
        """Validate a dir-source spec: real directory, not sensitive, within the cap.

        Called by the create/edit path AND at the top of every :meth:`poll`, because the
        spec is mutable data — a guard that only ran at save time would be one out-of-band
        row edit away from watching ``~/.ssh``. Fail-CLOSED (an unresolvable path is
        refused), matching ``pathguard``'s asymmetry: a stuck-closed watch is a visibly
        broken source, a stuck-open one hands the indexer credential material.
        """
        from personalclaw.security import is_sensitive_path
        from personalclaw.triggers.pathguard import canonicalize

        raw = str((spec or {}).get("path") or "").strip()
        if not raw:
            return False, "dir source requires a 'path'"
        resolved = canonicalize(raw)
        if not resolved:
            return False, f"path could not be resolved: {raw!r}"
        if is_sensitive_path(resolved):
            # Refused even if an operator explicitly configured it — decision 7's
            # bypass-immune class. An entry naming a credential location is far likelier
            # to be a mistake (or an injected edit) than an intention.
            return False, "path is a sensitive location and cannot be watched"
        p = Path(resolved)
        if not p.exists():
            return False, f"path does not exist: {resolved}"
        if not p.is_dir():
            return False, f"path is not a directory: {resolved}"
        cap = int((spec or {}).get("max_files") or MAX_FILES_PER_SOURCE)
        if cap < 1 or cap > MAX_FILES_PER_SOURCE:
            return False, f"max_files must be between 1 and {MAX_FILES_PER_SOURCE}"
        return True, ""

    # ── the signature-diff observation ──────────────────────────────────────────────

    def _matchers(self, spec: dict) -> tuple[str, ...]:
        include = (spec or {}).get("include") or DEFAULT_INCLUDE
        if isinstance(include, str):
            include = [include]
        pats = tuple(str(p) for p in include if str(p).strip())
        return pats or DEFAULT_INCLUDE

    def scan(self, spec: dict) -> tuple[dict[str, list], int]:
        """Signature map ``{relative_path: [mtime, size]}`` plus the count of files that
        could not be read. Sorted-and-capped so the cap bites deterministically rather
        than depending on directory iteration order."""
        root = Path(canonical) if (canonical := self._resolved_path(spec)) else None
        if root is None:
            return {}, 0
        pats = self._matchers(spec)
        recursive = bool((spec or {}).get("recursive", True))
        cap = min(int((spec or {}).get("max_files") or MAX_FILES_PER_SOURCE), MAX_FILES_PER_SOURCE)
        found: list[tuple[str, Path]] = []
        for dirpath, dirnames, filenames in os.walk(root):
            # Prune in place so os.walk never descends into the noise directories at all.
            dirnames[:] = sorted(
                d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")
            )
            if not recursive:
                dirnames[:] = []
            for fname in sorted(filenames):
                if fname.startswith("."):
                    continue
                if not any(fnmatch.fnmatch(fname, pat) for pat in pats):
                    continue
                full = Path(dirpath) / fname
                found.append((full.relative_to(root).as_posix(), full))
        found.sort()
        sigs: dict[str, list] = {}
        errors = 0
        for rel, full in found[:cap]:
            sig = _signature(full)
            if sig is None:
                errors += 1
                continue
            sigs[rel] = sig
        return sigs, errors

    def _resolved_path(self, spec: dict) -> str:
        from personalclaw.triggers.pathguard import canonicalize

        return canonicalize(str((spec or {}).get("path") or ""))

    def _read(self, spec: dict, rel: str) -> str | None:
        """File text, or None when it cannot be read (fail-open per file). Read only at
        EMIT time — never while a change is still settling — so a half-written file is
        not what gets indexed."""
        root = self._resolved_path(spec)
        if not root:
            return None
        try:
            with open(Path(root) / rel, "rb") as fh:
                raw = fh.read(MAX_FILE_BYTES)
        except OSError:
            return None
        return raw.decode("utf-8", errors="replace")

    def diff(self, sigs: dict[str, list], baseline: dict[str, list]) -> dict[str, str]:
        """``{relative_path: change}`` for everything that differs from the baseline."""
        out: dict[str, str] = {}
        for rel, sig in sigs.items():
            if rel not in baseline:
                out[rel] = CHANGE_CREATED
            elif baseline[rel] != sig:
                out[rel] = CHANGE_MODIFIED
        for rel in baseline:
            if rel not in sigs:
                out[rel] = CHANGE_DELETED
        return out

    async def poll(self, source_id: str, cursor: str = "") -> SourcePollResult:
        """One observation pass: scan, diff, debounce, emit the settled changes.

        Never raises to the engine (§1.1) — a bad spec or an unwalkable tree is reported
        as a soft error so the source degrades rather than killing the loop.
        """
        source = self._store.get_source(source_id)
        if source is None:
            return SourcePollResult(error=f"source {source_id} no longer exists")
        spec = source.get("spec") or {}
        ok, err = self.validate_spec(spec)
        if not ok:
            # Cursor untouched: the baseline must survive a transient misconfiguration
            # (an unmounted volume), or remounting would archive every item at once.
            return SourcePollResult(error=err)
        try:
            sigs, read_errors = self.scan(spec)
        except OSError as exc:
            return SourcePollResult(error=f"scan failed: {exc}"[:200])

        state = _DirCursor.parse(cursor)
        if not state.seeded:
            # SEED ONLY (no startup ingestion storm): record the baseline, emit nothing.
            return SourcePollResult(items=[], cursor=_DirCursor(True, sigs, {}).dump())

        now = float(self._now_fn())
        window = float(spec.get("debounce_secs") or DEFAULT_DEBOUNCE_SECS)
        changes = self.diff(sigs, state.sigs)
        gone: dict[str, float] = {}
        items: list[SourceItem] = []

        for rel, change in sorted(changes.items()):
            if change == CHANGE_CREATED and rel in state.tombstones:
                # This path was reported deleted before: its item still exists (archived),
                # and its guid is already in the engine's seen-set, so a create would be
                # dropped as a repeat. A restored file is a MODIFICATION of that item.
                change = CHANGE_MODIFIED
            if change == CHANGE_DELETED:
                # A delete has no mtime, so its window runs from when it was first seen
                # missing — which also absorbs an editor that saves by replacing the file.
                first_missing = state.gone.get(rel, now)
                if now - first_missing < window:
                    gone[rel] = first_missing
                    continue
            else:
                observed = sigs[rel]
                # The quiet clock is the file's OWN mtime: another save inside the window
                # moves it and restarts the window with no timer to keep. A future-dated
                # mtime (a synced volume with clock skew) cannot be "settling", so it is
                # treated as settled rather than held forever.
                elapsed = now - float(observed[0])
                if 0.0 <= elapsed < window:
                    # Baseline deliberately NOT advanced — the change is re-observed next
                    # pass, and the intermediate signature never becomes an index event.
                    continue
            emitted = self._emit(spec, rel, change, now)
            if emitted is None:
                # Unreadable at emit time: skip the file but ADVANCE its baseline so the
                # poll does not spin on it forever, and keep processing the others.
                read_errors += 1
                state.sigs[rel] = sigs[rel]
                continue
            items.append(emitted)
            if change == CHANGE_DELETED:
                state.remember_deleted(rel, now)
            else:
                state.sigs[rel] = sigs[rel]
                state.tombstones.pop(rel, None)

        state.gone = gone
        result = SourcePollResult(items=items, cursor=state.dump())
        if read_errors:
            # Surfaced as a soft error ONLY when nothing else happened, so a partially
            # unreadable directory still delivers the files it could read (the engine
            # treats a result with `error` set as a no-item degraded poll).
            if not items:
                result.error = f"{read_errors} file(s) could not be read"
            else:
                logger.debug("dir source %s: %d file(s) unreadable", source_id, read_errors)
        return result

    def _emit(self, spec: dict, rel: str, change: str, now: float) -> SourceItem | None:
        """Build the sighting for a settled change (content read only for a live file)."""
        from datetime import datetime

        if change == CHANGE_DELETED:
            return SourceItem(
                guid=rel,
                title=Path(rel).name,
                change=CHANGE_DELETED,
                metadata={"source_deleted_at": datetime.fromtimestamp(now).isoformat()},
            )
        content = self._read(spec, rel)
        if content is None:
            return None
        return SourceItem(
            guid=rel,
            title=Path(rel).name,
            content=content,
            change=change,
            metadata={"relative_path": rel},
        )
