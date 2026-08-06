"""Deterministic shard export — JSONL + SHA manifest (DURABILITY §2).

A tar snapshot is opaque: you cannot diff it, review it, or sync it. Shards are the
*other* representation of the same state — canonical JSONL, one directory per
inventory entry, byte-identical for identical input. That determinism is what makes
the export reviewable (``git diff`` over shards answers "what did the assistant
learn this week?") and, later, syncable.

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

import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from personalclaw.atomic_write import atomic_write, atomic_write_bytes
from personalclaw.durability import inventory as inv

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
    ATTACH it. Only written when ``export_shards(include_databases=True)`` — the hourly
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
    blobs: int = 0
    databases: list[DbCopy] = field(default_factory=list)  # sync-only whole-DB copies

    @property
    def rows(self) -> int:
        return sum(s.rows for s in self.shards)


def _json_rows_from_entity_dir(root: Path) -> list[dict]:
    """One row per entity JSON file, id = filename stem, sorted by id."""
    rows: list[dict] = []
    for path in sorted(root.rglob("*.json")):
        rel = path.relative_to(root).as_posix()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.debug("shards: unreadable entity file %s — skipped", path, exc_info=True)
            continue
        rows.append({"id": rel[:-5] if rel.endswith(".json") else rel, "data": data})
    rows.sort(key=lambda r: r["id"])
    return rows


def _json_rows_from_file(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [{"id": path.name, "data": data}]


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
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
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
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
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
        with sqlite3.connect(str(src)) as src_conn, sqlite3.connect(str(dst)) as dst_conn:
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


def _export_blobs(root: Path, src_dir: Path) -> int:
    """Content-addressed blob dir for binary originals, deduplicated by sha256."""
    count = 0
    blob_root = root / "blobs"
    for path in sorted(src_dir.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        digest = _sha256(data)
        dest = blob_root / digest[:2] / digest
        if dest.exists():  # dedup: identical content is stored once
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(dest, data)
        count += 1
    return count


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
    include_databases: bool = False,
) -> ExportResult:
    """Export state to deterministic shards under ``out_dir``.

    ``entries`` optionally restricts to specific inventory entry ids (the hourly
    incremental path exports only dirty entries). Secrets and derived data are
    never exported.

    ``include_databases`` (sync only) additionally stages a consistent whole-DB copy for
    each ``KIND_SQLITE`` entry under ``db/<entry_id>.db``, because the diffable row shards
    store embedding/byte columns as placeholders and can't rebuild a DB losslessly. The
    hourly incremental backup leaves this False, so its byte-for-byte determinism (and its
    tests) are unaffected — DB copies are not byte-identical across runs by nature.
    """
    result = ExportResult()
    out_dir.mkdir(parents=True, exist_ok=True)
    wanted = set(entries) if entries else None

    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        for entry in inv.export_entries():  # excludes secret + derived
            if wanted is not None and entry.id not in wanted:
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
                if include_databases:
                    # Sync also needs the real DB (embeddings/blobs the row shards drop).
                    staged = _stage_db_copy(out_dir, entry.id, copy)
                    if staged is not None:
                        result.databases.append(staged)
            elif entry.kind == inv.KIND_JSON_ENTITY_DIR:
                rows = _json_rows_from_entity_dir(src) if src.is_dir() else []
                if entry.tombstones and src.is_dir():
                    # Fold the hard-delete side-log so a deleted row's marker rides the
                    # export (DAS-6c-iii). Only for tombstone entries; a no-op otherwise.
                    from personalclaw.durability.tombstones import merge_into_rows

                    rows = merge_into_rows(src, rows)
                result.shards.extend(_write_shard(out_dir, f"{entry.id}/entities.jsonl", rows))
            elif entry.kind == inv.KIND_JSON_FILE:
                rows = _json_rows_from_file(src) if src.is_file() else []
                result.shards.extend(_write_shard(out_dir, f"{entry.id}/value.jsonl", rows))
            elif entry.kind == inv.KIND_JSONL_APPEND:
                files = [src] if src.is_file() else sorted(src.rglob("*.jsonl"))
                buckets: dict[str, list[dict]] = {}
                for path in files:
                    for year, rows in _jsonl_rows_by_year(path).items():
                        buckets.setdefault(year, []).extend(rows)
                for year in sorted(buckets):
                    result.shards.extend(
                        _write_shard(out_dir, f"{entry.id}/{year}.jsonl", buckets[year])
                    )
            else:  # KIND_TREE — text-ish trees ride the tar; binaries go to blobs
                if src.is_dir():
                    result.blobs += _export_blobs(out_dir / entry.id, src)
                else:
                    result.blobs += _export_blobs(out_dir / entry.id, src.parent)

    result.shards.sort(key=lambda s: s.path)
    # An INCREMENTAL export rewrote only the changed entries' shards, but the
    # manifest must still describe the WHOLE export — otherwise the untouched
    # shards become "present on disk but not declared" and validation fails on a
    # perfectly good export. Carry forward the previous manifest's records for
    # every entry this run didn't touch.
    if entries is not None:
        result.shards = _merged_shard_records(out_dir, result.shards, touched=set(entries))
    _write_manifest(home, out_dir, result)
    return result


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
        "blobs": result.blobs,
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

    @property
    def ok(self) -> bool:
        return not self.problems


def validate(shard_dir: Path) -> ValidationResult:
    """Verify an export end to end: a backup nobody has verified is a hope.

    Checks the manifest parses and is well-formed, every declared shard exists,
    its byte length / row count / sha256 all re-derive to the recorded values, and
    every row re-parses as JSON. Any mismatch is reported (not raised) so a caller
    can print all problems at once.
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
    """Export then immediately verify — the restore-drill core (§3)."""
    exported = export_shards(home, out_dir)
    return exported, validate(out_dir)


# ── import (the read side; DURABILITY-AND-SYNC DAS-6) ────────────────────────
# S2k shipped the export half write-only: shards could be produced + validated but
# nothing read them back. The sync cycle (DAS-6c) merges shards from a remote into
# the local state, and a restore reconstructs a home from shards — both need to turn
# a shard directory back into rows, keyed by the inventory entry that owns them. This
# is that read side: the exact inverse of `export_shards`' row extraction, so an
# export→import round-trip returns every non-secret, non-derived row unchanged.


@dataclass
class ImportResult:
    """What :func:`import_shards` read back from a shard directory.

    ``rows`` maps an inventory entry id → its rows, reassembled across year buckets,
    sqlite tables, and ``part-NNNN`` splits (so the caller sees one flat list per
    entry, exactly what `export_shards` was handed). ``blobs`` lists the content-addressed
    blob paths present under ``blobs/`` (KIND_TREE payloads), relative to the shard dir.
    ``problems`` carries any non-fatal read issue; a structurally broken export raises.
    """

    rows: dict[str, list[dict]] = field(default_factory=dict)
    blobs: list[str] = field(default_factory=list)
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

    The inverse of :func:`export_shards`. Runs :func:`validate` first — a shard whose
    bytes/sha/row-count drifted from the manifest is not trustworthy input for a merge
    or restore, so a failed validation raises :class:`ValueError` rather than importing
    silently corrupt data. ``entries`` optionally restricts to specific entry ids (the
    sync cycle imports only the entries a remote actually changed).

    Rows for an entry are reassembled across every shape the exporter splits into —
    sqlite tables (``<entry>/<table>.jsonl``), year buckets (``<entry>/2026.jsonl``),
    and deterministic ``part-NNNN`` files — into one flat, order-preserving list, so a
    round-trip yields exactly the rows that were exported.
    """
    report = validate(shard_dir)
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

    # KIND_TREE payloads live under blobs/<sha[:2]>/<sha>; enumerate them so a
    # restore/merge can rehydrate the tree. Content-addressed, so listing is enough.
    blob_root = shard_dir / "blobs"
    if blob_root.is_dir():
        result.blobs = sorted(
            p.relative_to(shard_dir).as_posix()
            for p in blob_root.rglob("*")
            if p.is_file() and not p.is_symlink()
        )

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
    for entry in inv.export_entries():
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
    """Remove a previous export so a full re-export cannot leave stale shards
    behind (which would then show up as undeclared files in validation)."""
    if out_dir.is_dir():
        shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(out_dir, exist_ok=True)


# ── CLI ─────────────────────────────────────────────────────────────────────


def backup_cmd(args) -> int:
    """``personalclaw backup export|validate`` — the operator entry point.

    ``validate`` is designed for CI/cron use: it prints every problem it found and
    returns non-zero, so a scheduled verification fails loudly instead of quietly
    reporting success over a corrupt export.
    """
    from personalclaw.concurrency import single_flight

    home = _home()
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
                clear_shards(out_dir)
            result = export_shards(home, out_dir, entries=entries)
        print(
            f"✅ Exported {result.entries} entr"
            f"{'y' if result.entries == 1 else 'ies'} → "
            f"{len(result.shards)} shard(s), {result.rows:,} row(s)"
            + (f", {result.blobs:,} blob(s)" if result.blobs else "")
        )
        print(f"📁 {out_dir}")
        for entry_id, reason in sorted(result.skipped.items()):
            print(f"⚠️  skipped {entry_id}: {reason}")
        return 0

    if command == "validate":
        shard_dir = Path(args.shard_dir).expanduser() if args.shard_dir else default_shard_dir(home)
        if not shard_dir.is_dir():
            print(f"❌ No shard export at {shard_dir} — run `personalclaw backup export` first.")
            return 1
        report = validate(shard_dir)
        if report.ok:
            print(
                f"✅ Export valid: {report.shards_checked} shard(s), "
                f"{report.rows_checked:,} row(s) verified (bytes + rows + sha256 + parse)."
            )
            return 0
        print(f"❌ Export INVALID — {len(report.problems)} problem(s):")
        for problem in report.problems[:50]:
            print(f"  - {problem}")
        if len(report.problems) > 50:
            print(f"  … and {len(report.problems) - 50} more")
        return 1

    print("Usage: personalclaw backup {export|validate}")
    return 2


def _home() -> Path:
    from personalclaw.config.loader import config_dir

    return Path(os.environ.get("PERSONALCLAW_HOME", config_dir()))
