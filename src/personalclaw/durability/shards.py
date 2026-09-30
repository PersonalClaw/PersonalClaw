"""Deterministic shard export — JSONL + SHA manifest (DURABILITY §2).

A tar snapshot is opaque: you cannot diff it, review it, or sync it. Shards are the
*other* representation of the records — canonical JSONL, one directory per inventory
entry, byte-identical for identical input. That determinism is what makes the export
reviewable (``git diff`` over shards answers "what did the assistant learn this week?")
and syncable.

**The snapshot is the backup; the shards are not one.** A restore reads a snapshot
(``snapshot.py``): it holds every store whole — a folder with every file at its path, a
database through the backup API. The shards hold the stores whose content is records (the
entity folders, the one-file stores, the append-only streams) and each database as rows, and
nothing restores a home from them. They cannot become a backup without giving up what they are
for: a database copied whole is not byte-identical run to run (its embedding and byte columns
are placeholders in the rows), which is why only a sync's export stages one. And a folder of
files — skills, scripts, uploads, the workspace, installed apps — is not in them at all: it
used to be copied in as blobs named by their content, with no path, which nothing could put
back, and which every sync cycle uploaded again.

Three properties are load-bearing, and each is tested:

* **Byte-identical for identical state.** Rows sorted by id, JSON with sorted keys
  and no incidental whitespace, LF endings, UTF-8. Two exports of an unchanged home
  produce the same bytes and therefore the same sha256 — a sync that re-uploads
  unchanged data, or a git history full of no-op commits, is the failure this
  prevents.
* **Every shard is verifiable.** ``manifest.json`` records ``{bytes, rows, sha256}``
  per shard; :func:`validate` re-derives all three and re-parses every row, so a
  truncated or corrupted export is detected rather than trusted.
* **Secrets never shard.** Shards are the representation that *leaves the machine*
  (§2), so ``secret=True`` entries are excluded unconditionally — unlike a local
  snapshot tar, which keeps them because a same-machine restore needs them.

Databases are read through the sqlite backup API into a scratch copy first, so a
live WAL store is exported consistently — the same hazard Session 1 closed for tars.
Table discovery reads the schema rather than a hand-written allowlist: the previous
allowlist in ``snapshot._merge_memory`` names two tables (``knowledge_facts``,
``knowledge_edges``) that do not exist in ``memory.db`` at all.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import uuid
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from personalclaw.atomic_write import atomic_write, atomic_write_bytes
from personalclaw.durability import inventory as inv
from personalclaw.record_ids import is_path_in_store, is_safe_relative_path
from personalclaw.sqlite_compat import sqlite3

logger = logging.getLogger(__name__)

SHARD_SCHEMA_VERSION = 1

# Split a shard beyond this many bytes into deterministic `part-NNNN` files, so a
# git transport never needs LFS. Rows are assigned to parts by cumulative size, so
# the split points are a pure function of the content.
PART_SPLIT_BYTES = 48 * 1024 * 1024

_MANIFEST = "manifest.json"
_MACHINE_ID_FILE = "machine_id"
# Rows whose timestamp can't be parsed go here rather than being silently
# back-dated into a year they didn't happen in.
_UNKNOWN_YEAR = "unknown"

_YEAR_RE = re.compile(r"(19|20)\d{2}")


# ── canonical encoding ──────────────────────────────────────────────────────


def canonical_json(value: Any) -> str:
    """One line of canonical JSON: sorted keys, compact separators, UTF-8 text.

    ``ensure_ascii=False`` keeps real characters readable in a diff instead of
    escaping them; ``sort_keys`` plus fixed separators is what makes two exports of
    the same state byte-identical.
    """
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def machine_id(home: Path) -> str:
    """A stable, non-secret per-machine id, created on first use.

    Deliberately NOT ``telemetry_salt``: that is marked ``secret=True`` and must
    never leave the machine, while this id is written into every manifest so a sync
    can tell "which machine produced this export".
    """
    path = home / _MACHINE_ID_FILE
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except (FileNotFoundError, OSError):
        pass
    fresh = uuid.uuid4().hex
    try:
        atomic_write(path, fresh + "\n")
    except OSError:
        logger.debug("could not persist machine_id", exc_info=True)
    return fresh


# ── row extraction per entry kind ───────────────────────────────────────────


@dataclass
class ShardFile:
    """One written shard file and its verification triple."""

    path: str  # relative to the shards root
    bytes: int
    rows: int
    sha256: str


@dataclass
class DbCopy:
    """A consistent whole-database copy staged for sync (DAS-6c-ii-g).

    The diffable row shards store byte/embedding columns as size placeholders, so they
    can't rebuild a DB losslessly; a sync export additionally stages the real DB file
    (backup-API copy, WAL-checkpointed) under ``db/<entry_id>.db`` so the merger can
    ATTACH it. Only written for a sync (``export_shards(for_sync=True)``) — the hourly
    incremental backup never carries these, so its determinism is untouched.
    """

    path: str  # relative to the shards root (db/<entry_id>.db)
    entry_id: str
    bytes: int
    sha256: str


@dataclass
class ExportResult:
    entries: int = 0
    shards: list[ShardFile] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)  # entry id -> reason
    databases: list[DbCopy] = field(default_factory=list)  # sync-only whole-DB copies
    #: A store's files this export could not carry, by home-relative path, with why (:class:`Read`).
    left_out: dict[str, str] = field(default_factory=dict)

    @property
    def rows(self) -> int:
        return sum(s.rows for s in self.shards)


# ── a store's files, as rows ──────────────────────────────────────────────────
#
# A row of an entity directory, or of a ``json_file``, is one file of the store: a JSON file as
# its parsed ``data`` (an entity directory names it by its path without ``.json``), any other file
# as its ``text`` when it is UTF-8 and as ``base64`` otherwise (named by its path). A tombstone is
# ``{"id", "deleted_at"}`` for a JSON entity.

#: The largest file a row carries. A row is never split across the parts of a shard, and a file
#: carried as ``base64`` is 4/3 its size, so a file this size still fits one part.
LARGEST_FILE_BYTES = PART_SPLIT_BYTES * 3 // 4


@dataclass
class Read:
    """What a store's files held when they were read: its rows; the sha256 of each row's file as
    it was then, by row id, which a write compares against before it replaces the file
    (:mod:`durability.writeback`); and the files it could not carry, by home-relative path with
    why, which an export names rather than dropping."""

    rows: list[dict] = field(default_factory=list)
    shas: dict[str, str] = field(default_factory=dict)
    left_out: dict[str, str] = field(default_factory=dict)


def _outside_the_store(entry: inv.StateEntry, rel: str) -> bool:
    """Whether *rel*, a path inside *entry*'s directory, is not the store's to carry: runtime
    scratch the inventory ignores (a lock, a temp file, a sqlite sidecar), what *entry* declares
    derived, or a store another entry claims (``workflows/runs``). True of every path under one
    that is."""
    from personalclaw.portability import _is_derived_within

    if inv.is_ignored(f"{entry.path}/{rel}") or _is_derived_within(entry.path, rel):
        return True
    owner = inv.claim_for(f"{entry.path}/{rel}")
    return owner is not None and owner.path.startswith(f"{entry.path}/")


def store_file(entry: inv.StateEntry, rel: str) -> bool:
    """Whether *rel*, a path inside *entry*'s directory, names a file of that store: a plain
    relative path that stays inside it (``record_ids.is_safe_relative_path``), not a database, not
    the tombstone side-log, and not outside the store (:func:`_outside_the_store`). The exporter
    reads these files and no others, and a sync writes no others: a row another machine names by
    any other path is not one of the store's."""
    from personalclaw.durability.tombstones import TOMBSTONE_FILE

    if not is_safe_relative_path(rel):
        return False
    if rel == TOMBSTONE_FILE or PurePosixPath(rel).suffix in (".db", ".db-journal"):
        return False
    return not _outside_the_store(entry, rel)


def row_file(row: dict) -> str:
    """The path, inside its store's directory, of the file *row* stands for."""
    rid = str(row.get("id", ""))
    return rid if ("text" in row or "base64" in row) else f"{rid}.json"


def row_bytes(row: dict) -> bytes:
    """The content of the file *row* stands for, as the sync and a restore write it."""
    if "text" in row:
        return str(row["text"]).encode("utf-8")
    if "base64" in row:
        return base64.b64decode(str(row["base64"]))
    return (canonical_json(row.get("data", {})) + "\n").encode("utf-8")


def _json_of(raw: bytes) -> Any:
    """*raw* parsed as JSON, or :data:`_NOT_JSON`."""
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _NOT_JSON


_NOT_JSON = object()


def _file_row(rid: str, raw: bytes) -> dict:
    """A row that carries a file as it is: its ``text`` when it is UTF-8, its ``base64`` else."""
    try:
        return {"id": rid, "text": raw.decode("utf-8")}
    except UnicodeDecodeError:
        return {"id": rid, "base64": base64.b64encode(raw).decode("ascii")}


def _file_bytes(path: Path) -> tuple[bytes | None, str]:
    """*path*'s content, or ``None`` with why a row cannot carry it."""
    try:
        if path.stat().st_size > LARGEST_FILE_BYTES:
            mib = 1024 * 1024
            limit = (
                f"{LARGEST_FILE_BYTES // mib} MiB"
                if LARGEST_FILE_BYTES >= mib
                else f"{LARGEST_FILE_BYTES} bytes"
            )
            return None, f"larger than {limit}"
        return path.read_bytes(), ""
    except OSError as exc:
        return None, f"unreadable ({exc.strerror or exc})"


def read_entity_dir(entry: inv.StateEntry, root: Path) -> Read:
    """One row per file of *entry*'s directory at *root* (:func:`store_file`), sorted by id.

    🔴 This read ``*.json`` and nothing else, so a backup and a sync carried none of a store's
    other files, and said nothing: saved prompts and prompt snippets are YAML, an agent's prompt
    assets Markdown, a voice profile's reference clip and consent recording audio. A file it cannot
    carry — JSON that does not parse, one too large for a row, or one named as another's row would
    be — is in ``left_out``, with why. Symlinks are not followed, and a folder that is not the
    store's is not walked.
    """
    out = Read()
    if not root.is_dir():
        return out
    found: list[tuple[str, Path]] = []
    for directory, dirs, files in os.walk(root, followlinks=False):
        here = Path(directory).relative_to(root)
        dirs[:] = sorted(d for d in dirs if not _outside_the_store(entry, (here / d).as_posix()))
        for name in files:
            path = Path(directory) / name
            if not path.is_symlink():
                found.append(((here / name).as_posix(), path))
    rows: dict[str, dict] = {}
    for rel, path in sorted(found):
        if not store_file(entry, rel):
            continue
        raw, why = _file_bytes(path)
        row: dict | None = None
        if raw is not None and rel.endswith(".json"):
            data = _json_of(raw)
            row = {"id": rel[:-5], "data": data} if data is not _NOT_JSON else None
            why = "" if row is not None else "not valid JSON"
        elif raw is not None:
            row = _file_row(rel, raw)
        if row is not None and row["id"] in rows:
            why = f"named as the row of {row_file(rows[row['id']])}"
            row = None
        if row is None or raw is None:
            logger.warning("shards: %s/%s could not be carried: %s", entry.path, rel, why)
            out.left_out[f"{entry.path}/{rel}"] = why
            continue
        rows[row["id"]] = row
        out.shas[row["id"]] = _sha256(raw)
    out.rows = [rows[rid] for rid in sorted(rows)]
    return out


def read_json_file(entry: inv.StateEntry, path: Path) -> Read:
    """The one row of a ``json_file`` store at *path*: its JSON ``data``.

    A store restored whole or not at all (``replace_only``), which a sync never merges, is carried
    whatever it holds, as its ``text`` or ``base64`` when that is not JSON: the workspace pointer
    is a bare path, and a backup did not hold it. Any other store's file that does not parse is
    left out and named (``left_out``): taken in by a sync, it would be written over the other
    machine's store as an edit.
    """
    out = Read()
    if not path.is_file() or path.is_symlink():
        return out
    raw, why = _file_bytes(path)
    data = _NOT_JSON if raw is None else _json_of(raw)
    if raw is not None and data is _NOT_JSON and entry.merge != inv.MERGE_REPLACE_ONLY:
        why = "not valid JSON"
    if raw is None or why:
        logger.warning("shards: %s could not be carried: %s", entry.path, why)
        out.left_out[entry.path] = why
        return out
    out.rows = [_file_row(path.name, raw) if data is _NOT_JSON else {"id": path.name, "data": data}]
    out.shas[path.name] = _sha256(raw)
    return out


def left_out_sentence(left_out: dict[str, str], *, what: str = "exported") -> str:
    """The words that name the files an export could not carry (:class:`Read`): how many, and the
    first few with why."""
    shown = ", ".join(f"{path} ({why})" for path, why in sorted(left_out.items())[:3])
    more = f" and {len(left_out) - 3} more" if len(left_out) > 3 else ""
    return f"{len(left_out)} file(s) could not be {what}: {shown}{more}"


def refused_sentence(refused: dict[str, str]) -> str:
    """The words that name what a pull refused (``pull_engine.PullReport.refused``): the paths
    another machine named outside what a sync may write, the first few with why."""
    shown = ", ".join(f"{path} ({why})" for path, why in sorted(refused.items())[:3])
    more = f" and {len(refused) - 3} more" if len(refused) > 3 else ""
    return (
        f"refused {len(refused)} path(s) another machine named outside what a sync may write: "
        f"{shown}{more}"
    )


def _year_of(row: dict) -> str:
    """Best-effort year for an append-only row, for year sharding.

    Looks at the usual timestamp fields; anything unparseable lands in
    ``unknown`` rather than being assigned a plausible-looking year.
    """
    for key in ("ts", "timestamp", "created_at", "started_at", "at"):
        raw = row.get(key)
        if raw is None:
            continue
        if isinstance(raw, (int, float)):
            try:
                return str(datetime.fromtimestamp(float(raw), timezone.utc).year)
            except (OverflowError, OSError, ValueError):
                continue
        match = _YEAR_RE.search(str(raw))
        if match:
            return match.group(0)
    return _UNKNOWN_YEAR


def _jsonl_rows_by_year(path: Path) -> dict[str, list[dict]]:
    """Parse an append-only JSONL file into ``{year: rows}``, order preserved."""
    buckets: dict[str, list[dict]] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return buckets
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        buckets.setdefault(_year_of(row), []).append(row)
    return buckets


def _sqlite_tables(db: Path) -> list[str]:
    """User tables in a database, discovered from the schema.

    Discovery rather than an allowlist: the pre-existing merge allowlist in
    ``snapshot.py`` names ``knowledge_facts``/``knowledge_edges``, which do not
    exist in ``memory.db`` — a hand-written list drifts, a schema read cannot.
    Internal sqlite bookkeeping and FTS shadow tables are excluded (the latter are
    derived data, rebuilt on import).
    """
    try:
        # closing(), not a bare `with` on the connection: sqlite3's connection context
        # manager ends the TRANSACTION and leaves the connection OPEN, so every call here
        # leaked one handle until gc got to it. Measured — this is why the suite still
        # printed unclosed-database warnings after the fixture fix, and in a long-lived
        # gateway the hourly incremental export leaks one per table per entry. Read-only,
        # so there is no transaction to commit. (`snapshot.py` already does this.)
        with closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True)) as conn:
            names = [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
            ]
    except sqlite3.Error:
        logger.debug("shards: cannot read schema of %s", db, exc_info=True)
        return []
    out = []
    for name in names:
        if name.startswith("sqlite_"):
            continue
        # FTS shadow tables (…_data/_idx/_docsize/_config/_content) are derived.
        if re.search(r"_(data|idx|docsize|config|content)$", name):
            continue
        out.append(name)
    return out


def _sqlite_rows(db: Path, table: str) -> list[dict]:
    """Every row of one table as dicts, stably ordered.

    Ordered by the table's own ``id``/``key`` when it has one, else by ``rowid`` —
    so the same database always dumps in the same order.
    """
    try:
        with closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            cols = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
            order = "id" if "id" in cols else ("key" if "key" in cols else "rowid")
            rows = conn.execute(f'SELECT * FROM "{table}" ORDER BY "{order}"').fetchall()
    except sqlite3.Error:
        logger.debug("shards: cannot read %s.%s", db, table, exc_info=True)
        return []
    out: list[dict] = []
    for row in rows:
        record = {}
        for key in row.keys():
            value = row[key]
            if isinstance(value, (bytes, bytearray, memoryview)):
                # Embeddings/blobs are derived or opaque; record presence + size
                # instead of base64-bloating a human-diffable shard.
                record[key] = {"__bytes__": len(bytes(value))}
            else:
                record[key] = value
        out.append(record)
    return out


def _consistent_db_copy(src: Path, workdir: Path) -> Path | None:
    """A consistent scratch copy of a live database via the backup API."""
    dst = workdir / src.name
    try:
        with (
            closing(sqlite3.connect(str(src))) as src_conn,
            closing(sqlite3.connect(str(dst))) as dst_conn,
        ):
            src_conn.backup(dst_conn)
        return dst
    except sqlite3.Error:
        logger.debug("shards: backup-API copy failed for %s", src, exc_info=True)
        return None


# ── writing ─────────────────────────────────────────────────────────────────


def _write_shard(root: Path, rel: str, rows: list[dict]) -> list[ShardFile]:
    """Write rows as canonical JSONL, splitting deterministically past the cap."""
    lines = [canonical_json(r).encode("utf-8") + b"\n" for r in rows]
    total = sum(len(b) for b in lines)
    if total <= PART_SPLIT_BYTES:
        body = b"".join(lines)
        out = root / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(out, body)
        return [ShardFile(path=rel, bytes=len(body), rows=len(rows), sha256=_sha256(body))]

    # Deterministic split: fill each part until adding the next row would exceed
    # the cap, so part boundaries are a pure function of the content.
    parts: list[list[bytes]] = [[]]
    size = 0
    for line in lines:
        if size + len(line) > PART_SPLIT_BYTES and parts[-1]:
            parts.append([])
            size = 0
        parts[-1].append(line)
        size += len(line)
    written: list[ShardFile] = []
    stem = rel[:-6] if rel.endswith(".jsonl") else rel
    for index, chunk in enumerate(parts):
        body = b"".join(chunk)
        part_rel = f"{stem}.part-{index:04d}.jsonl"
        out = root / part_rel
        out.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(out, body)
        written.append(
            ShardFile(path=part_rel, bytes=len(body), rows=len(chunk), sha256=_sha256(body))
        )
    return written


def _stage_db_copy(out_dir: Path, entry_id: str, src_copy: Path) -> DbCopy | None:
    """Stage a consistent whole-DB copy under ``db/<entry_id>.db`` for the sync merger.

    ``src_copy`` is the already-consistent backup-API copy the exporter made for row
    extraction, so this is a plain byte copy (no second live-DB read). Returns the
    :class:`DbCopy` record, or None if the copy can't be read."""
    try:
        data = src_copy.read_bytes()
    except OSError:
        logger.debug("shards: cannot stage db copy for %s", entry_id, exc_info=True)
        return None
    rel = f"db/{entry_id}.db"
    dest = out_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(dest, data)
    return DbCopy(path=rel, entry_id=entry_id, bytes=len(data), sha256=_sha256(data))


def export_shards(
    home: Path,
    out_dir: Path,
    *,
    entries: list[str] | None = None,
    for_sync: bool = False,
) -> ExportResult:
    """Export the records to deterministic shards under ``out_dir``.

    ``entries`` optionally restricts to specific inventory entry ids (the hourly
    incremental path exports only dirty entries). Secrets and derived data are
    never exported, and neither is a folder of files (``inventory.shard_entries``): the
    snapshot is what holds those, and what a restore reads.

    ``for_sync`` is a sync's export, for another machine. It leaves out what stays on this one
    (``StateEntry.machine_local`` and ``machine_local_within``) and the append-only stores that are
    folders of files, which a sync does not carry (``inventory.append_only_folder``), and stages a
    consistent whole-DB copy for each ``KIND_SQLITE`` entry under ``db/<entry_id>.db``, because
    the diffable row shards store embedding/byte columns as placeholders and can't rebuild a DB
    losslessly. The hourly export leaves it False, so it carries this machine's own stores, and
    its byte-for-byte determinism (and its tests) are unaffected — DB copies are not
    byte-identical across runs by nature.
    """
    result = ExportResult()
    out_dir.mkdir(parents=True, exist_ok=True)
    wanted = set(entries) if entries else None
    temporary: frozenset[Path] | None = None

    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        for entry in inv.shard_entries():  # excludes secret, derived and folders of files
            if wanted is not None and entry.id not in wanted:
                continue
            if for_sync and (entry.machine_local or inv.append_only_folder(entry)):
                continue
            src = home / entry.path
            if not src.exists():
                continue
            result.entries += 1

            if entry.kind == inv.KIND_SQLITE:
                copy = _consistent_db_copy(src, workdir)
                if copy is None:
                    result.skipped[entry.id] = "database unreadable"
                    continue
                tables = _sqlite_tables(copy)
                if not tables:
                    result.skipped[entry.id] = "no tables"
                    continue
                for table in tables:
                    rows = _sqlite_rows(copy, table)
                    result.shards.extend(_write_shard(out_dir, f"{entry.id}/{table}.jsonl", rows))
                if for_sync:
                    # Sync also needs the real DB (embeddings/blobs the row shards drop).
                    staged = _stage_db_copy(out_dir, entry.id, copy)
                    if staged is not None:
                        result.databases.append(staged)
            elif entry.kind == inv.KIND_JSON_ENTITY_DIR:
                read = read_entity_dir(entry, src)
                result.left_out.update(read.left_out)
                rows = read.rows
                if for_sync and entry.machine_local_within:
                    rows = [r for r in rows if not inv.stays_here(entry, str(r.get("id", "")))]
                if entry.tombstones and src.is_dir():
                    # Fold the hard-delete side-log so a deleted row's marker rides the
                    # export. Only for tombstone entries; a no-op otherwise.
                    from personalclaw.durability.tombstones import merge_into_rows

                    rows = merge_into_rows(src, rows)
                result.shards.extend(_write_shard(out_dir, f"{entry.id}/entities.jsonl", rows))
            elif entry.kind == inv.KIND_JSON_FILE:
                read = read_json_file(entry, src)
                result.left_out.update(read.left_out)
                result.shards.extend(_write_shard(out_dir, f"{entry.id}/value.jsonl", read.rows))
            elif entry.kind == inv.KIND_JSONL_APPEND:
                files = [src] if src.is_file() else sorted(src.rglob("*.jsonl"))
                # A running Temporary chat's transcript is never copied out: the chat is forgotten
                # when its session ends, and a shard would outlive it.
                if temporary is None:
                    from personalclaw.chat_traces import kept_by_temporary_chats

                    temporary = kept_by_temporary_chats(home)
                if temporary:
                    files = [f for f in files if f.resolve() not in temporary]
                buckets: dict[str, list[dict]] = {}
                for path in files:
                    for year, rows in _jsonl_rows_by_year(path).items():
                        buckets.setdefault(year, []).extend(rows)
                for year in sorted(buckets):
                    result.shards.extend(
                        _write_shard(out_dir, f"{entry.id}/{year}.jsonl", buckets[year])
                    )

    result.shards.sort(key=lambda s: s.path)
    # An INCREMENTAL export rewrote only the changed entries' shards, but the
    # manifest must still describe the WHOLE export — otherwise the untouched
    # shards become "present on disk but not declared" and validation fails on a
    # perfectly good export. Carry forward the previous manifest's records for
    # every entry this run didn't touch.
    if entries is not None:
        result.shards = _merged_shard_records(out_dir, result.shards, touched=set(entries))
    _drop_earlier_folder_copies(out_dir)
    _write_manifest(home, out_dir, result)
    return result


def _is_an_export(directory: Path) -> bool:
    """Whether *directory* holds a shard export: a manifest an export wrote."""
    try:
        manifest = json.loads((directory / _MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(manifest, dict) and "schema_version" in manifest and "shards" in manifest


def _drop_earlier_folder_copies(out_dir: Path) -> None:
    """Remove what an export before this one copied of a folder store into *out_dir*: the
    store's files as blobs named by their content (``<entry>/blobs/``), which nothing could put
    back at their paths. Only in a folder that holds an export, and only that layout."""
    if not _is_an_export(out_dir):
        return
    for entry in inv.INVENTORY:
        if entry.kind != inv.KIND_TREE:
            continue
        folder = out_dir / entry.id
        blobs = folder / "blobs"
        if blobs.is_dir() and not blobs.is_symlink() and not folder.is_symlink():
            shutil.rmtree(blobs, ignore_errors=True)
            try:
                folder.rmdir()
            except OSError:
                pass


def _merged_shard_records(
    out_dir: Path, fresh: list[ShardFile], *, touched: set[str]
) -> list[ShardFile]:
    """Fresh records for re-exported entries + carried-forward records for the rest.

    A shard's entry id is its first path segment, which is how a carried record is
    matched to the entry that owns it. Carried records whose file has since vanished
    are dropped rather than kept as a phantom declaration.
    """
    merged = {s.path: s for s in fresh}
    try:
        previous = json.loads((out_dir / _MANIFEST).read_text(encoding="utf-8"))
        records = previous.get("shards") or []
    except (OSError, json.JSONDecodeError):
        records = []
    for record in records:
        rel = str(record.get("path", ""))
        if not rel or rel in merged:
            continue
        if rel.split("/", 1)[0] in touched:
            continue  # this entry was re-exported; its fresh records are authoritative
        if not (out_dir / rel).is_file():
            continue  # the file is gone — don't declare it
        merged[rel] = ShardFile(
            path=rel,
            bytes=int(record.get("bytes", 0)),
            rows=int(record.get("rows", 0)),
            sha256=str(record.get("sha256", "")),
        )
    return sorted(merged.values(), key=lambda s: s.path)


def _write_manifest(home: Path, out_dir: Path, result: ExportResult) -> None:
    manifest = {
        "schema_version": SHARD_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "machine_id": machine_id(home),
        "entries": result.entries,
        "skipped": result.skipped,
        "shards": [
            {"path": s.path, "bytes": s.bytes, "rows": s.rows, "sha256": s.sha256}
            for s in result.shards
        ],
        "databases": [
            {"path": d.path, "entry_id": d.entry_id, "bytes": d.bytes, "sha256": d.sha256}
            for d in result.databases
        ],
    }
    atomic_write(out_dir / _MANIFEST, json.dumps(manifest, indent=2, sort_keys=True) + "\n")


# ── validation ──────────────────────────────────────────────────────────────


@dataclass
class ValidationResult:
    """What :func:`validate` found. ``ok`` is the CI/cron exit signal."""

    problems: list[str] = field(default_factory=list)
    shards_checked: int = 0
    rows_checked: int = 0
    #: The paths the manifest names outside the export, never read (:func:`validate`).
    outside: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


class OutsideTheExport(ValueError):
    """An export whose manifest names a path outside it: refused whole, and none of it read.

    What another machine sends is read from where its manifest says, and ``Path``'s ``/`` takes
    an absolute right side as the whole path, so a manifest could name any file of this machine
    to be read as its rows. ``paths`` are the paths it named, for the sync report."""

    def __init__(self, paths: list[str]) -> None:
        self.paths = list(paths)
        super().__init__(
            "the export's manifest names a path outside it, so none of it is read: "
            + ", ".join(self.paths[:5])
        )


def _outside_the_export(result: ValidationResult, shard_dir: Path, rel: str) -> bool:
    """Whether *rel*, a path the manifest declares, is outside the export at *shard_dir*
    (``record_ids.is_path_in_store``); recorded in *result* when it is, and never read. A record
    with no path names nothing, and is reported missing."""
    if not rel or is_path_in_store(shard_dir, rel):
        return False
    result.outside.append(rel)
    result.problems.append(f"{rel}: names a path outside the export — not read")
    return True


def validate(shard_dir: Path) -> ValidationResult:
    """Verify an export end to end: a backup nobody has verified is a hope.

    Checks the manifest parses and is well-formed, every declared shard exists,
    its byte length / row count / sha256 all re-derive to the recorded values, and
    every row re-parses as JSON. Any mismatch is reported (not raised) so a caller
    can print all problems at once. A declared path outside the export is a problem too, and
    is never read (:class:`OutsideTheExport`).
    """
    result = ValidationResult()
    manifest_path = shard_dir / _MANIFEST
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        result.problems.append(f"missing {_MANIFEST}")
        return result
    except json.JSONDecodeError as exc:
        result.problems.append(f"{_MANIFEST} is not valid JSON: {exc}")
        return result

    if manifest.get("schema_version") != SHARD_SCHEMA_VERSION:
        result.problems.append(
            f"unsupported schema_version {manifest.get('schema_version')!r} "
            f"(expected {SHARD_SCHEMA_VERSION})"
        )
    if not manifest.get("machine_id"):
        result.problems.append("manifest has no machine_id")
    shards = manifest.get("shards")
    if not isinstance(shards, list):
        result.problems.append("manifest has no shards list")
        return result

    declared: set[str] = set()
    for record in shards:
        rel = str(record.get("path", ""))
        declared.add(rel)
        if _outside_the_export(result, shard_dir, rel):
            continue
        path = shard_dir / rel
        if not path.is_file():
            result.problems.append(f"{rel}: declared in manifest but missing on disk")
            continue
        data = path.read_bytes()
        result.shards_checked += 1
        if len(data) != record.get("bytes"):
            result.problems.append(f"{rel}: size {len(data)} != manifest {record.get('bytes')}")
        actual_sha = _sha256(data)
        if actual_sha != record.get("sha256"):
            result.problems.append(f"{rel}: sha256 mismatch (content changed)")
        lines = [ln for ln in data.decode("utf-8", errors="replace").splitlines() if ln.strip()]
        if len(lines) != record.get("rows"):
            result.problems.append(f"{rel}: {len(lines)} rows != manifest {record.get('rows')}")
        for number, line in enumerate(lines, start=1):
            try:
                json.loads(line)
            except json.JSONDecodeError as exc:
                result.problems.append(f"{rel}:{number}: unparseable row ({exc})")
                break
        result.rows_checked += len(lines)

    # An undeclared shard file means the manifest and the export disagree, which
    # would let a corrupted or partial write pass unnoticed.
    for path in sorted(shard_dir.rglob("*.jsonl")):
        rel = path.relative_to(shard_dir).as_posix()
        if rel not in declared:
            result.problems.append(f"{rel}: present on disk but not declared in the manifest")

    # Sync-only whole-DB copies (DAS-6c-ii-g): verify each declared db/ file's bytes + sha.
    # A manifest with no `databases` key (an incremental/row-only export) is valid — the
    # field is absent, not empty-and-wrong.
    for record in manifest.get("databases", []) or []:
        rel = str(record.get("path", ""))
        if _outside_the_export(result, shard_dir, rel):
            continue
        path = shard_dir / rel
        if not path.is_file():
            result.problems.append(f"{rel}: declared database missing on disk")
            continue
        data = path.read_bytes()
        if len(data) != record.get("bytes"):
            result.problems.append(f"{rel}: db size {len(data)} != manifest {record.get('bytes')}")
        if _sha256(data) != record.get("sha256"):
            result.problems.append(f"{rel}: db sha256 mismatch (content changed)")
    return result


def export_and_validate(home: Path, out_dir: Path) -> tuple[ExportResult, ValidationResult]:
    """Export then immediately verify. (The restore drill checks a snapshot, not shards.)"""
    exported = export_shards(home, out_dir)
    return exported, validate(out_dir)


# ── import (the read side) ───────────────────────────────────────────────────
# The sync cycle merges another machine's shards into this home's stores, so it has to turn a
# shard directory back into rows, keyed by the inventory entry that owns them. This is that read
# side: the exact inverse of `export_shards`' row extraction, so an export→import round-trip
# returns every row the export carried unchanged. A restore does not read shards: it reads a
# snapshot, which holds what they do not (a folder of files, a database whole).


@dataclass
class ImportResult:
    """What :func:`import_shards` read back from a shard directory.

    ``rows`` maps an inventory entry id → its rows, reassembled across year buckets,
    sqlite tables, and ``part-NNNN`` splits (so the caller sees one flat list per
    entry, exactly what `export_shards` was handed). There is nothing of a folder store
    (``KIND_TREE``): the shards do not carry one. ``problems`` carries any non-fatal read
    issue; a structurally broken export raises.
    """

    rows: dict[str, list[dict]] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    machine_id: str = ""
    # entry id -> shard-dir-relative path of its whole-DB copy (sync-only, DAS-6c-ii-g).
    databases: dict[str, str] = field(default_factory=dict)

    @property
    def entries(self) -> int:
        return len(self.rows)

    @property
    def total_rows(self) -> int:
        return sum(len(v) for v in self.rows.values())


def _entry_id_of(rel: str) -> str:
    """The inventory entry id that owns a shard — its first path segment.

    Mirrors ``_merged_shard_records``' matching rule: every shard `export_shards`
    writes is rooted at ``<entry_id>/…`` (``tasks/entities.jsonl``,
    ``memory_db/semantic_memory.jsonl``, ``sessions/2026.jsonl``, possibly with a
    ``.part-0001`` suffix), so the leading segment is the entry that produced it.
    """
    return rel.split("/", 1)[0]


def _rows_of_shard(shard_dir: Path, rel: str) -> list[dict]:
    """Parse one shard file's canonical-JSONL rows (blank lines skipped)."""
    data = (shard_dir / rel).read_bytes()
    out: list[dict] = []
    for line in data.decode("utf-8").splitlines():
        if not line.strip():
            continue
        out.append(json.loads(line))
    return out


def import_shards(shard_dir: Path, *, entries: list[str] | None = None) -> ImportResult:
    """Read a shard directory back into rows keyed by inventory entry id.

    The inverse of :func:`export_shards`, which a sync's pull reads another machine's
    export with. Runs :func:`validate` first — a shard whose bytes/sha/row-count drifted
    from the manifest is not trustworthy input for a merge, so a failed validation raises
    :class:`ValueError` rather than importing silently corrupt data. ``entries`` optionally
    restricts to specific entry ids (the sync cycle imports only the entries a remote
    actually changed).

    Rows for an entry are reassembled across every shape the exporter splits into —
    sqlite tables (``<entry>/<table>.jsonl``), year buckets (``<entry>/2026.jsonl``),
    and deterministic ``part-NNNN`` files — into one flat, order-preserving list, so a
    round-trip yields exactly the rows that were exported.

    What it returns is the records, and a sync's whole-database copies: never a folder store
    (skills, scripts, uploads, the workspace, installed apps), which the shards do not carry
    and a restore brings back from a snapshot. It used to list the shards' ``blobs/`` "so a
    restore can rehydrate the tree"; they were named by their content, with no path, so
    nothing ever could — and nothing read the list.
    """
    report = validate(shard_dir)
    if report.outside:
        raise OutsideTheExport(report.outside)
    if not report.ok:
        raise ValueError(
            "refusing to import an invalid shard export:\n" + "\n".join(report.problems)
        )

    manifest = json.loads((shard_dir / _MANIFEST).read_text(encoding="utf-8"))
    result = ImportResult(machine_id=str(manifest.get("machine_id", "")))
    wanted = set(entries) if entries else None

    # Read declared shards in manifest order so part-NNNN splits reassemble in the
    # same order they were written (the manifest's `shards` list is path-sorted).
    for record in manifest.get("shards", []):
        rel = str(record.get("path", ""))
        if not rel:
            continue
        entry_id = _entry_id_of(rel)
        if wanted is not None and entry_id not in wanted:
            continue
        try:
            result.rows.setdefault(entry_id, []).extend(_rows_of_shard(shard_dir, rel))
        except (OSError, json.JSONDecodeError) as exc:
            # validate() already re-parsed every row, so this is unreachable in
            # practice; kept as a non-fatal guard rather than a crash on a race.
            result.problems.append(f"{rel}: unreadable during import ({exc})")

    # Sync-only whole-DB copies (DAS-6c-ii-g): map each entry id to its db/ file so the
    # DB merger can ATTACH the real database (validate() already verified its bytes/sha).
    for record in manifest.get("databases", []) or []:
        rel = str(record.get("path", ""))
        entry_id = str(record.get("entry_id", ""))
        if not rel or not entry_id:
            continue
        if wanted is not None and entry_id not in wanted:
            continue
        result.databases[entry_id] = rel
    return result


def dirty_entries(home: Path, state_path: Path) -> list[str]:
    """Inventory entry ids whose content changed since the last export.

    Uses an mtime fingerprint per entry so the hourly incremental export writes
    only what moved. A missing/corrupt state file means "everything is dirty",
    which is the safe direction — a needless full export costs time, a missed one
    costs data.
    """
    try:
        previous = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(previous, dict):
            previous = {}
    except (OSError, json.JSONDecodeError):
        previous = {}

    current: dict[str, str] = {}
    dirty: list[str] = []
    for entry in inv.shard_entries():
        src = home / entry.path
        if not src.exists():
            continue
        fingerprint = _fingerprint(src)
        current[entry.id] = fingerprint
        if previous.get(entry.id) != fingerprint:
            dirty.append(entry.id)
    try:
        atomic_write(state_path, json.dumps(current, indent=2, sort_keys=True) + "\n")
    except OSError:
        logger.debug("shards: could not persist the dirty-state fingerprint", exc_info=True)
    return dirty


def _fold_wal(db_path: Path) -> None:
    """Fold committed WAL frames into the main DB file with a passive checkpoint.

    Best-effort and non-destructive: ``PASSIVE`` never blocks on a writer and never
    truncates, so it cannot itself become the volatile event the fingerprint is trying to
    avoid. A failure (locked store, missing sidecar, not a database) is swallowed — the
    caller then fingerprints whatever the main file currently is, which for an actively
    written store has already moved on its last commit.
    """
    if not db_path.with_name(db_path.name + "-wal").exists():
        return  # no sidecar → nothing to fold, and no need to open a connection
    try:
        conn = sqlite3.connect(str(db_path), timeout=0.5)
        try:
            conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        finally:
            conn.close()
    except sqlite3.Error:
        logger.debug("shards: passive checkpoint skipped for %s", db_path, exc_info=True)


def _fingerprint(path: Path) -> str:
    """A cheap change fingerprint: newest mtime + total size beneath ``path``.

    For a SQLite file the committed WAL frames are folded into the MAIN FILE with a
    passive checkpoint first, and only the main file's ``(mtime, size)`` is fingerprinted.
    The sidecars are NOT stat'd. Every store here runs in WAL mode, where a committed
    write lands in the ``-wal`` and may not touch the main file for a long time — so
    fingerprinting the ``.db`` alone once reported "unchanged" through an entire session
    of writes, and the incremental export silently backed up nothing (found by writing a
    fact and watching the fingerprint not move). A passive checkpoint makes that committed
    content durable in the main file, so the main-file mtime moves exactly when — and only
    when — durable data changed.

    Neither sidecar is folded in, and that is the fix, not an optimization. Both are
    VOLATILE at moments the writer does not control:

    * ``-shm`` is the WAL index — pure ephemeral shared memory, mmap'd ``MAP_SHARED``, its
      mtime advancing at page-writeback time rather than at store time.
    * ``-wal`` is truncated to zero by a checkpoint (autocheckpoint at 1000 pages, or the
      last connection closing, or any other connection running ``wal_checkpoint``). That
      moves the sidecar's mtime AND size with **no data change at all** — reproduced
      directly: a ``wal_checkpoint(TRUNCATE)`` shifts an unchanged store's fingerprint.

    Folding either one made the fingerprint report change where no data had changed. The
    ``-shm`` fold produced an idle home re-exporting ``memory.db`` forever; the ``-wal``
    fold produced a load-dependent CI flake in the "dirty detection settles completely"
    test — under load a checkpoint lands between two ``dirty_entries`` calls and the second
    reports the store dirty though nothing was written. A change fingerprint must key on
    durable content the writer controls; the passive-checkpoint-then-main-file rule does.

    The checkpoint is best-effort: if the store is locked by an active writer it is a
    no-op, which is the safe direction — an actively-written store is genuinely dirty, and
    the main-file mtime will have moved on its last commit regardless.
    """
    if path.is_file():
        if path.suffix == ".db":
            _fold_wal(path)
        stat = path.stat()
        return f"{stat.st_mtime_ns}:{stat.st_size}"
    newest = 0
    total = 0
    count = 0
    for child in path.rglob("*"):
        try:
            if child.is_file() and not child.is_symlink():
                stat = child.stat()
                newest = max(newest, stat.st_mtime_ns)
                total += stat.st_size
                count += 1
        except OSError:
            continue
    return f"{newest}:{total}:{count}"


def default_shard_dir(home: Path) -> Path:
    return home / "shards"


def clear_shards(out_dir: Path) -> None:
    """Remove an earlier export from *out_dir*, so a full re-export cannot leave stale shards
    behind (which would then show up as undeclared files in validation). What an export wrote
    goes — its manifest, the folder it keeps for each store, the database copies — and nothing
    else: a git history the shards are kept in, or a note beside them, stays.

    🔴 This deleted *out_dir* whole, whatever it held, so ``personalclaw backup export ~/Documents``
    deleted the folder. A folder that holds files and no export is now refused (``ValueError``),
    with nothing in it touched.
    """
    if out_dir.is_symlink() or (out_dir.exists() and not out_dir.is_dir()):
        raise ValueError(f"{out_dir} is not a folder")
    out_dir.mkdir(parents=True, exist_ok=True)
    if not _is_an_export(out_dir):
        if any(out_dir.iterdir()):
            raise ValueError(
                f"{out_dir} holds files and no shard export; choose an empty folder, or the "
                "folder of an earlier export"
            )
        return
    ours = {entry.id for entry in inv.INVENTORY} | {"db"}
    for child in out_dir.iterdir():
        if child.is_symlink():
            continue
        if child.name == _MANIFEST and child.is_file():
            child.unlink()
        elif child.name in ours and child.is_dir():
            shutil.rmtree(child)


# ── CLI ─────────────────────────────────────────────────────────────────────


#: What every surface of the shard export says it is, and is not.
NOT_A_BACKUP = (
    "Shards are a copy of your records to review and diff, and what sync carries between your "
    "machines. They hold no folder of files, and nothing restores from them. The backup is a "
    "snapshot, which holds everything, your skills, scripts and uploads included: "
    "`personalclaw snapshot`, and `personalclaw restore` to bring one back."
)


def backup_cmd(args) -> int:
    """``personalclaw backup export|validate`` — the operator entry point.

    ``validate`` is designed for CI/cron use: it prints every problem it found and
    returns non-zero, so a scheduled verification fails loudly instead of quietly
    reporting success over a corrupt export.
    """
    from personalclaw.concurrency import single_flight
    from personalclaw.config.loader import config_dir

    home = config_dir()
    command = getattr(args, "backup_command", None)

    if command == "export":
        out_dir = Path(args.out_dir).expanduser() if args.out_dir else default_shard_dir(home)
        incremental = bool(getattr(args, "incremental", False))
        # Two exports racing would interleave partial writes into one manifest.
        with single_flight("shard-export") as acquired:
            if not acquired:
                print("⏭  Another shard export is already running — skipping.")
                return 0
            entries = None
            if incremental:
                entries = dirty_entries(home, home / ".shard-state.json")
                if not entries:
                    print("✅ Nothing changed since the last export.")
                    return 0
                print(
                    f"↻ Incremental export: {len(entries)} changed entr"
                    f"{'y' if len(entries) == 1 else 'ies'}"
                )
            else:
                try:
                    clear_shards(out_dir)
                except ValueError as exc:
                    print(f"❌ {exc}.", file=sys.stderr)
                    return 1
            result = export_shards(home, out_dir, entries=entries)
        print(
            f"✅ Exported {result.entries} store(s) → "
            f"{len(result.shards)} shard(s), {result.rows:,} row(s)"
        )
        print(f"📁 {out_dir}")
        for entry_id, reason in sorted(result.skipped.items()):
            print(f"⚠️  skipped {entry_id}: {reason}")
        for path, why in sorted(result.left_out.items()):
            print(f"⚠️  could not export {path}: {why}")
        print(f"ℹ️  {NOT_A_BACKUP}")
        return 1 if result.left_out else 0

    # `validate`: the parser allows no other command.
    shard_dir = Path(args.shard_dir).expanduser() if args.shard_dir else default_shard_dir(home)
    if not shard_dir.is_dir():
        print(
            f"❌ No shard export at {shard_dir} — run `personalclaw backup export` first.",
            file=sys.stderr,
        )
        return 1
    report = validate(shard_dir)
    if report.ok:
        print(
            f"✅ Export valid: {report.shards_checked} shard(s), "
            f"{report.rows_checked:,} row(s) verified (bytes + rows + sha256 + parse)."
        )
        print(f"ℹ️  {NOT_A_BACKUP}")
        return 0
    print(f"❌ Export INVALID — {len(report.problems)} problem(s):")
    for problem in report.problems[:50]:
        print(f"  - {problem}")
    if len(report.problems) > 50:
        print(f"  … and {len(report.problems) - 50} more")
    return 1
