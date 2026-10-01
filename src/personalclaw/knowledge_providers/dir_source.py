"""Watched local directories — the dir-source provider (am.5).

A watched directory is the one source kind whose upstream is MUTABLE: a feed appends,
but a folder of notes is edited in place, renamed, and deleted. So this provider is not a
fetcher, it is an OBSERVER: each poll takes a cheap ``(mtime, size)`` signature of every
matching file, diffs it against the signature map persisted in the source's cursor, and
reports what changed as ``created`` / ``modified`` / ``deleted`` sightings. The engine owns
what persisting each kind means — which is what keeps a filesystem event from ever
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

**The first scan brings in what is already there, within a stated bound.** A person who
adds a folder of notes to her library expects its notes in the library; a first pass that
only recorded a baseline left a healthy-looking folder that brought nothing in, with no
word about why. So the first scan takes the files already in the folder, the most recently
changed first, up to :data:`FIRST_SCAN_MAX_FILES` files and :data:`FIRST_SCAN_MAX_BYTES` of
them in all. What it leaves out (a folder of 4000 notes) goes into the baseline and comes
in when it next changes, and the cursor records how many that was, so the sources page can
say it rather than let a partial import pass for a complete one
(:meth:`DirSourceProvider.first_scan_status`). This is a knowledge library, not a `file`
trigger: a trigger must not fire for every file that already exists (its
``WatchState.seeded`` rule), while a library that shows none of them is the defect.

**Nothing a poll observes is lost to the engine's per-poll cap.** The engine indexes at
most ``max_items`` sightings of one poll, and this provider's baseline is what it has
emitted, so emitting more than the engine takes would move the baseline past files that
never reached the library. The poll therefore stops at the cap; a change it did not emit stays
uncommitted, exactly like one still settling, and is emitted by the next poll. A first scan
bigger than one poll arrives over several, and the cursor says how many are still to come.

**A folder takes in what is inside it, and nothing a link reaches outside it.** A link in the
folder names a file or folder somewhere else; she shared the folder, not where its links point.
So every path the scan meets is taken in under the path it really is (:func:`resolve_in`, links
and ``..`` resolved first): one that resolves outside the folder is left out, and the cursor
records how many, so the sources page can say it (:meth:`DirSourceProvider.links_outside`); one
that resolves to a file inside is that file, which the walk meets under its own name, so it
comes in once. The agent's file tools (``file_scope``) ask the same two questions of a path in a
watched folder, :func:`resolve_in` and :func:`takes`, so what the library takes from a folder and
what the agent may read in it cannot differ. A note an earlier scan took in through a link out
of the folder is removed outright at the next scan, with its sighting
(:meth:`DirSourceProvider._withdraw`): what it holds was never in the folder, and an archived
item keeps its text.

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
from pathlib import Path, PurePosixPath
from typing import Any, NamedTuple

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

#: Files read as HTML and stored as their words (see ``DirSourceProvider._read``).
HTML_SUFFIXES = frozenset({".html", ".htm"})

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

#: Hard ceiling on files tracked per source (the path-cap). A directory pointed at
#: ``/`` must degrade to a refusal, not a multi-hour stat walk that starves the loop.
MAX_FILES_PER_SOURCE = 5000

#: Per-file content ceiling. A file larger than this is tracked (its deletion still
#: archives) but truncated on read — one pathological file cannot blow the poll's memory.
MAX_FILE_BYTES = 2 * 1024 * 1024

#: How many reported deletions a source remembers, so a restored file revives its archived
#: item instead of being dropped by the engine's novelty gate. Bounded like the seen-set.
MAX_TOMBSTONES = 1000

#: The most files a folder's first scan brings in, the most recently changed first. Past it
#: the rest of the folder is baselined and comes in when it changes. Sized for a notes
#: folder: every file becomes a library item that is enriched in the background, and a
#: whole-home or whole-drive folder must not queue tens of thousands of them.
FIRST_SCAN_MAX_FILES = 1000

#: The most bytes of files a first scan brings in, all of them together (each counted at
#: most :data:`MAX_FILE_BYTES`, what is read of it). The file bound is what a notes folder
#: meets; this one is what a folder of large text exports or logs meets first.
FIRST_SCAN_MAX_BYTES = 100 * 1024 * 1024


@dataclass
class _DirCursor:
    """The source's persisted observation state (§3.2 cursor, opaque to the engine).

    ``sigs`` is the committed baseline — the last signature actually re-indexed for each
    relative path. A file whose change is still settling, or that a poll left for the next
    one at the engine's cap, is deliberately NOT written into it, which is what makes the
    change re-observable next pass without a timer. ``gone`` times the debounce window for
    deletions (``rel -> first_missing_at``), the one change kind with no mtime of its own,
    and ``tombstones`` remembers the deletions already REPORTED so a restored file revives
    its archived item (see :meth:`remember_deleted`).

    ``first_scan`` records that the first scan has happened and what it found:
    ``{"found": files in the folder then, "left_out": files past its bound}``. ``None``
    means it has not — a new source, a cursor that could not be read, or one written before
    the first scan brought files in, whose baseline listed every file while none of them
    reached the library. ``waiting`` is how many settled changes the last poll left for the
    next one at the engine's per-poll cap. ``outside`` is how many links the last scan left out
    because they resolve outside the folder.
    """

    first_scan: dict[str, int] | None = None
    sigs: dict[str, list] = field(default_factory=dict)
    gone: dict[str, float] = field(default_factory=dict)
    tombstones: dict[str, float] = field(default_factory=dict)
    waiting: int = 0
    outside: int = 0

    @classmethod
    def parse(cls, raw: str) -> _DirCursor:
        """Revive a cursor; a missing or corrupt one degrades to not yet scanned.

        Not yet scanned re-runs the first scan, and that is safe: every file it brings in
        whose guid this source already has an item for is refused by the engine's novelty
        gate, so a lost cursor costs a re-read of the folder, never a duplicate item.
        """
        try:
            data = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        out = cls()
        scan = data.get("first_scan")
        if isinstance(scan, dict):
            try:
                out.first_scan = {
                    "found": int(scan.get("found") or 0),
                    "left_out": int(scan.get("left_out") or 0),
                }
            except (TypeError, ValueError):
                out.first_scan = None
        sigs = data.get("sigs")
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
        for attr in ("waiting", "outside"):
            try:
                setattr(out, attr, max(0, int(data.get(attr) or 0)))
            except (TypeError, ValueError):
                setattr(out, attr, 0)
        return out

    def dump(self) -> str:
        return json.dumps(
            {
                "first_scan": self.first_scan,
                "sigs": self.sigs,
                "gone": self.gone,
                "tombstones": self.tombstones,
                "waiting": self.waiting,
                "outside": self.outside,
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


def _open_no_link(path: str, flags: int) -> int:
    """``open``'s opener for a file the scan took in: it does not follow a link put in its place
    since."""
    return os.open(path, flags | getattr(os, "O_NOFOLLOW", 0))


class FolderScan(NamedTuple):
    """What one scan found in a watched folder."""

    #: ``{relative_path: [mtime, size]}`` of every file the folder takes in.
    sigs: dict[str, list]
    #: How many of those files could not be stat'ed.
    unreadable: int
    #: The paths (files and folders) it left out because they resolve outside the folder.
    outside: tuple[str, ...]


def matchers(spec: dict) -> tuple[str, ...]:
    """The file-name patterns a folder's *spec* takes in: its ``include``, else
    :data:`DEFAULT_INCLUDE`."""
    include = (spec or {}).get("include") or DEFAULT_INCLUDE
    if isinstance(include, str):
        include = [include]
    pats = tuple(str(p) for p in include if str(p).strip())
    return pats or DEFAULT_INCLUDE


def resolve_in(root: str, path: str | os.PathLike) -> str | None:
    """Where *path* really is in the watched folder whose real path is *root*: its real path
    relative to the folder (``"."`` for the folder itself), or ``None`` when it resolves outside.

    Links and ``..`` are resolved first, so a link out of the folder is outside it, and a link
    to a file inside is that file's own path. The scan and the agent's file tools
    (``file_scope``) both ask this and then :func:`takes` of the path it gives."""
    real = os.path.realpath(path)
    if real != root and not real.startswith(root.rstrip(os.sep) + os.sep):
        return None
    return Path(os.path.relpath(real, root)).as_posix()


def takes(spec: dict, rel: str, *, is_dir: bool) -> bool:
    """Whether the folder *spec* watches takes in *rel*, a path relative to that folder.

    A folder (*is_dir*) is one :meth:`DirSourceProvider.scan` walks into, and a file one it
    brings in: no part of the path hidden or a :data:`SKIP_DIRS` name, nothing below the top
    when the spec is not recursive, and a file's name matching the spec's patterns. The scan
    and the agent's file tools (``file_scope``) both ask this of where a path really is
    (:func:`resolve_in`), so what the library takes from a folder and what the agent may read in
    it are one rule."""
    parts = [part for part in PurePosixPath(rel).parts if part not in ("", ".")]
    folders = parts if is_dir else parts[:-1]
    if any(part in SKIP_DIRS or part.startswith(".") for part in folders):
        return False
    if folders and not bool((spec or {}).get("recursive", True)):
        return False
    if is_dir:
        return True
    name = parts[-1] if parts else ""
    return (
        bool(name)
        and not name.startswith(".")
        and any(fnmatch.fnmatch(name, pat) for pat in matchers(spec))
    )


def note_path(store: Any, item: dict) -> str:
    """The file a library item came from, as the owner wrote its folder (``~/Notes/a.md``), when
    it came from a watched folder that is still watched; ``""`` for any other item."""
    source_id = item.get("source_id")
    source = store.get_source(source_id) if source_id else None
    if not source or source.get("provider") != "watched-dir" or not source.get("enabled"):
        return ""
    folder = str((source.get("spec") or {}).get("path") or "").rstrip("/")
    guid = str(item.get("guid") or "")
    return f"{folder}/{guid}" if folder and guid else ""


def note_file(store: Any, rel: str) -> str:
    """The file of the one note a watched folder took from *rel* (its path inside the folder, or
    its tail down to the file's name, as a chat names a note it read), written as the owner wrote
    the folder; ``""`` when no note answers to it, or more than one does."""
    paths = {
        path for item in store.find_active_by_source_file(rel) if (path := note_path(store, item))
    }
    return paths.pop() if len(paths) == 1 else ""


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

    def scan(self, spec: dict) -> FolderScan:
        """What the folder holds now (:class:`FolderScan`). Sorted-and-capped so the cap bites
        deterministically rather than depending on directory iteration order.

        Every path is taken in under the path it really is (:func:`resolve_in`). ``os.walk``
        lists a link to a folder without entering it and a link to a file as a file: a link that
        resolves outside the folder is left out and named in ``outside``, and one that resolves
        inside is left to the walk, which meets what it names under that name when the folder
        takes it in."""
        canonical = self._resolved_path(spec)
        if not canonical:
            return FolderScan({}, 0, ())
        root = Path(canonical)
        cap = min(int((spec or {}).get("max_files") or MAX_FILES_PER_SOURCE), MAX_FILES_PER_SOURCE)
        found: list[tuple[str, Path]] = []
        outside: list[str] = []

        def really(dirpath: str, name: str, rel: str) -> bool:
            full = os.path.join(dirpath, name)
            # The walk starts at the folder's real path and enters no link, so an entry that is
            # not a link is where it says it is, and only a link costs the resolution.
            if not os.path.islink(full):
                return True
            at = resolve_in(canonical, full)
            if at is None:
                outside.append(rel)
            return at == rel

        for dirpath, dirnames, filenames in os.walk(root):
            here = Path(dirpath).relative_to(root)
            # Prune in place so os.walk never descends into the noise directories at all.
            dirnames[:] = [
                d
                for d in sorted(dirnames)
                if takes(spec, (rel := (here / d).as_posix()), is_dir=True)
                and really(dirpath, d, rel)
            ]
            for fname in sorted(filenames):
                rel = (here / fname).as_posix()
                if takes(spec, rel, is_dir=False) and really(dirpath, fname, rel):
                    found.append((rel, Path(dirpath) / fname))
        found.sort()
        sigs: dict[str, list] = {}
        errors = 0
        for rel, full in found[:cap]:
            sig = _signature(full)
            if sig is None:
                errors += 1
                continue
            sigs[rel] = sig
        return FolderScan(sigs, errors, tuple(outside))

    def _resolved_path(self, spec: dict) -> str:
        from personalclaw.triggers.pathguard import canonicalize

        return canonicalize(str((spec or {}).get("path") or ""))

    def _read(self, spec: dict, rel: str) -> str | None:
        """File text, or None when it cannot be read (fail-open per file). Read only at
        EMIT time — never while a change is still settling — so a half-written file is
        not what gets indexed.

        An HTML file (a folder the spec widens to ``*.html``) is stored as its words, through
        the same conversion an uploaded ``.html`` takes; every other file is text already."""
        root = self._resolved_path(spec)
        if not root or resolve_in(root, os.path.join(root, rel)) != rel:
            # Gone, or swapped for a link since the scan: what a link names is not read.
            return None
        try:
            with open(os.path.join(root, rel), "rb", opener=_open_no_link) as fh:
                raw = fh.read(MAX_FILE_BYTES)
        except OSError:
            return None
        text = raw.decode("utf-8", errors="replace")
        if Path(rel).suffix.lower() in HTML_SUFFIXES:
            from personalclaw.knowledge.readers import html_to_prose

            return html_to_prose(text)
        return text

    @staticmethod
    def _first_scan(sigs: dict[str, list], prior: _DirCursor) -> _DirCursor:
        """The state the first scan starts from: which files it brings in, and which not.

        The files it brings in are simply left OUT of the baseline, so the ordinary diff
        below sees them as created — with the same debounce, the same per-poll cap and the
        same emit path as a file added later, and no second way in. The files past the
        bound go INTO the baseline: they are in the folder, not in the library, and come in
        when they change. The order is newest first and the cut is a prefix of it, so what
        was left out is always "the files changed longest ago", which is a sentence the
        page can say. ``prior``'s tombstones are kept, so a file deleted and restored
        across the first scan still revives its archived item.
        """
        newest_first = sorted(sigs, key=lambda rel: (-float(sigs[rel][0]), rel))
        taken = spent = 0
        for rel in newest_first:
            size = min(int(sigs[rel][1]), MAX_FILE_BYTES)
            if taken >= FIRST_SCAN_MAX_FILES or spent + size > FIRST_SCAN_MAX_BYTES:
                break
            taken += 1
            spent += size
        left_out = {rel: sigs[rel] for rel in newest_first[taken:]}
        return _DirCursor(
            first_scan={"found": len(sigs), "left_out": len(left_out)},
            sigs=left_out,
            tombstones=dict(prior.tombstones),
        )

    @staticmethod
    def links_outside(cursor: str) -> int:
        """How many links the folder's last scan left out because they resolve outside it, for
        the sources page."""
        return _DirCursor.parse(cursor).outside

    @staticmethod
    def first_scan_status(cursor: str) -> dict[str, int] | None:
        """What a folder's first scan did, for the sources page: ``found`` (files in the
        folder then), ``left_out`` (files past the bound, which come in when they change)
        and ``waiting`` (files still to come at the engine's per-poll cap). ``None`` before
        the first scan has run."""
        state = _DirCursor.parse(cursor)
        if state.first_scan is None:
            return None
        return {**state.first_scan, "waiting": state.waiting}

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

    async def poll(
        self, source_id: str, cursor: str = "", *, max_items: int | None = None
    ) -> SourcePollResult:
        """One observation pass: scan, diff, debounce, emit the settled changes.

        ``max_items`` is the engine's per-poll cap (``ENGINE_POLL_KWARGS``). At most that
        many sightings are emitted; the rest stay uncommitted and come next poll, so none
        is lost to the cap. ``None`` (a direct call) emits every settled change.

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
            scan = self.scan(spec)
        except OSError as exc:
            return SourcePollResult(error=f"scan failed: {exc}"[:200])
        sigs, read_errors = scan.sigs, scan.unreadable

        state = _DirCursor.parse(cursor)
        if state.first_scan is None:
            state = self._first_scan(sigs, state)
        for rel in scan.outside:
            self._withdraw(source_id, rel, state)
        state.outside = len(scan.outside)

        now = float(self._now_fn())
        window = float(spec.get("debounce_secs") or DEFAULT_DEBOUNCE_SECS)
        changes = self.diff(sigs, state.sigs)
        gone: dict[str, float] = {}
        settled: list[tuple[str, str]] = []

        for rel, change in changes.items():
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
            settled.append((rel, change))

        # Deletions first (an archive, never enqueued, and what keeps a moved file from
        # showing twice), then files newest first, so a first scan that spans several
        # polls brings in the notes she touched last before the ones she has not opened in
        # years. Ties by path, so the order is stable.
        settled.sort(
            key=lambda pair: (
                (0, 0.0, pair[0])
                if pair[1] == CHANGE_DELETED
                else (1, -float(sigs[pair[0]][0]), pair[0])
            )
        )
        limit = len(settled) if max_items is None else max(1, int(max_items))
        items: list[SourceItem] = []
        waiting = 0
        for rel, change in settled:
            if len(items) >= limit:
                # Left for the next poll at the engine's cap: NOT committed, so it is seen
                # again next pass, already settled. A deletion keeps its window's start.
                waiting += 1
                if change == CHANGE_DELETED:
                    gone[rel] = state.gone.get(rel, now)
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
        state.waiting = waiting
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

    def _withdraw(self, source_id: str, rel: str, state: _DirCursor) -> None:
        """Remove what an earlier scan took in through *rel*, a link out of the folder: its note
        and its sighting, outright, and *rel* from the baseline.

        Not an archive, which keeps a note's text: what this one holds was never in the folder
        she shared. The sighting goes with it, so a file she later saves under that name comes in
        as new. A link left in place costs one lookup at each scan after the first."""
        state.sigs.pop(rel, None)
        state.gone.pop(rel, None)
        state.tombstones.pop(rel, None)
        if self._store.find_source_item(source_id, rel) is not None:
            self._store.forget_source_item(source_id, rel)

    def _emit(self, spec: dict, rel: str, change: str, now: float) -> SourceItem | None:
        """Build the sighting for a settled change (content read only for a live file)."""
        from personalclaw.instants import utc_iso

        if change == CHANGE_DELETED:
            return SourceItem(
                guid=rel,
                title=Path(rel).name,
                change=CHANGE_DELETED,
                metadata={"source_deleted_at": utc_iso(now)},
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
