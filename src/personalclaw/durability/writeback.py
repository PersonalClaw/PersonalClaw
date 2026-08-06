"""Apply merged rows back to the live on-disk store (DURABILITY-AND-SYNC §4.1, DAS-6c-ii-c).

The sync cycle's last pure primitive: the inverse of ``shards.py``'s row *extraction*.
``shards.export_shards`` turned each inventory entry's on-disk form into a flat row list;
after :mod:`durability.merge` reconciles a peer's rows with the local ones, this writes the
merged set back into the live store in that entry's native shape, dispatched by its
inventory ``kind``. It is the exact mirror of ``export_shards``' per-kind dispatch, so a
round-trip (extract → merge with empty remote → apply) reproduces the same files.

Row shapes, matching the extractors verbatim:

* ``json_entity_dir`` — rows are ``{"id": "<rel-stem>", "data": {...}}`` → one JSON file
  per row at ``<dest>/<id>.json``. A **tombstone** row (a ``deleted_at`` marker) removes
  that file instead of writing it, so a delete synced from a peer propagates to the live
  store rather than resurrecting the entity.
* ``json_file`` — a single row ``{"id": "<name>", "data": {...}}`` → its ``data`` written
  to the file at ``dest``; a tombstone removes the file.
* ``jsonl_append`` — rows are the raw event dicts → written as canonical JSONL. If ``dest``
  is (or would be) a directory the stream is re-bucketed by year exactly as the exporter
  shards it (``<dest>/<year>.jsonl``); a single-file stream is rewritten at ``dest``.

``sqlite`` and ``tree`` are NOT handled here — the cycle merges DBs via the ATTACH-OR-IGNORE
path (``snapshot.py``) and rehydrates ``tree`` payloads from the content-addressed blob
store — so routing one through :func:`apply_rows` is a caller bug, raised loudly, mirroring
:func:`merge.merge_rows`. Writes are atomic (temp-file + rename); the caller owns the merge
and the choice of which entries to apply.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from personalclaw.atomic_write import atomic_write
from personalclaw.durability import inventory as inv
from personalclaw.durability.shards import _year_of, canonical_json

logger = logging.getLogger(__name__)

_TOMBSTONE_FIELD = "deleted_at"


@dataclass
class ApplyResult:
    """A reviewable tally of what an apply did to the live store."""

    written: int = 0  # entities/files/streams written
    removed: int = 0  # entities removed by a tombstone row
    skipped: int = 0  # rows that couldn't be applied (malformed) — logged, never fatal


def _is_tombstone(row: dict) -> bool:
    # A JSONL row carries deleted_at at the top level; an entity-dir row nests it under the
    # exporter's {"id", "data"} wrapper. Check both so a synced delete removes the file for
    # either shape (mirrors merge._field).
    if row.get(_TOMBSTONE_FIELD):
        return True
    data = row.get("data")
    return bool(isinstance(data, dict) and data.get(_TOMBSTONE_FIELD))


def apply_rows(kind: str, dest: Path, rows: list[dict]) -> ApplyResult:
    """Write ``rows`` back to ``dest`` in the on-disk shape for inventory ``kind``.

    Atomic per file. Returns an :class:`ApplyResult`. Raises ``ValueError`` for
    ``sqlite``/``tree`` (handled by other paths) and any unknown kind.
    """
    dest = Path(dest)
    if kind == inv.KIND_JSON_ENTITY_DIR:
        return _apply_entity_dir(dest, rows)
    if kind == inv.KIND_JSON_FILE:
        return _apply_json_file(dest, rows)
    if kind == inv.KIND_JSONL_APPEND:
        return _apply_jsonl(dest, rows)
    if kind in (inv.KIND_SQLITE, inv.KIND_TREE):
        raise ValueError(
            f"{kind!r} is not row-applied — the cycle merges sqlite via ATTACH-OR-IGNORE and "
            "rehydrates tree payloads from the blob store; do not route it through apply_rows"
        )
    raise ValueError(f"unknown inventory kind {kind!r}")


def _apply_entity_dir(root: Path, rows: list[dict]) -> ApplyResult:
    """One JSON file per row at ``root/<id>.json``; a tombstone removes the file."""
    result = ApplyResult()
    for row in rows:
        rid = row.get("id")
        if not isinstance(rid, str) or not rid:
            result.skipped += 1
            logger.debug("apply_rows(entity_dir): row without a string id — skipped")
            continue
        target = root / f"{rid}.json"
        if _is_tombstone(row):
            if target.exists():
                target.unlink()
                result.removed += 1
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(target, canonical_json(row.get("data", {})) + "\n")
        result.written += 1
    return result


def _apply_json_file(dest: Path, rows: list[dict]) -> ApplyResult:
    """A single-document store: write the one row's ``data`` to ``dest`` (or remove it on a
    tombstone). More than one row is a merge bug (a json_file has exactly one id) — the last
    row wins, logged, rather than a silent half-write."""
    result = ApplyResult()
    if not rows:
        return result
    if len(rows) > 1:
        logger.warning("apply_rows(json_file): %d rows for a single-document store", len(rows))
    row = rows[-1]
    if _is_tombstone(row):
        if dest.exists():
            dest.unlink()
            result.removed += 1
        return result
    dest.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(dest, canonical_json(row.get("data", {})) + "\n")
    result.written += 1
    return result


def _apply_jsonl(dest: Path, rows: list[dict]) -> ApplyResult:
    """Rewrite an append-only stream as canonical JSONL. A ``dest`` that is a directory (or
    already exists as one) is year-sharded exactly as the exporter shards it; otherwise the
    whole stream is one file at ``dest``. The merged rows are the full, deduped stream, so a
    rewrite is the correct inverse — order preserved."""
    result = ApplyResult()
    if dest.is_dir() or (not dest.suffix and not dest.exists()):
        # Directory-shaped stream (e.g. sessions/) → one file per year, mirroring export.
        buckets: dict[str, list[dict]] = {}
        for row in rows:
            buckets.setdefault(_year_of(row), []).append(row)
        dest.mkdir(parents=True, exist_ok=True)
        for year in sorted(buckets):
            body = "".join(canonical_json(r) + "\n" for r in buckets[year])
            atomic_write(dest / f"{year}.jsonl", body)
            result.written += len(buckets[year])
        return result
    # Single-file stream (e.g. notifications.jsonl).
    dest.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(canonical_json(r) + "\n" for r in rows)
    atomic_write(dest, body)
    result.written = len(rows)
    return result
