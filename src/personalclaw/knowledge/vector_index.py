"""ANN index over chunk vectors — ``sqlite-vec`` living inside the knowledge DB (H1.4).

WHY THIS EXISTS. KL-10 made the vector arm score CHUNK vectors as well as whole-item
vectors, which is what lets retrieval reach page 12 of a long document. It also made the
arm's row count grow from N items to roughly N·(1 + content_chars/1500), and that cost was
measured: at 384 dimensions the pure-Python cosine scan runs ~21 µs/row, so 300 rows cost
6.4 ms/query, 1,800 rows cost 39.1 ms/query, and a 5,000-item library (~30,000 rows) costs
roughly 650 ms/query — user-visible latency on every search. KL-10's execution log raised
this task from optimization to required follow-on.

WHAT THIS IS. A ``vec0`` virtual table (from the ``sqlite-vec`` extension) over the chunk
vectors, in the SAME database file as the ``chunks`` rows it indexes — the shape the owner's
dependency ruling asked for, so a chunk write and its vector write cannot drift into a
sidecar's split brain. The index is a **candidate generator only**: it narrows the corpus to
the k nearest chunk vectors, and ``HybridRetriever`` then re-scores those candidates with the
unchanged Python cosine, the unchanged dimension guard, and the unchanged
``_VECTOR_MIN_SIMILARITY`` floor. Scoring identity is deliberate — it means the only way ANN
can differ from the exact scan is by truncating candidates, which is one testable failure mode
instead of a second scoring implementation that can silently disagree in the last decimal.

HONEST NAMING. ``sqlite-vec`` 0.1.x's ``vec0`` KNN is an exhaustive SIMD scan in C, not a
graph/IVF index, so the request path is still linear in row count — just with a ~280x smaller
constant (measured: 1,800 rows 37.7 ms → 0.16 ms; 30,000 rows 633 ms → 2.3 ms). It is called
an ANN index here because that is the plan's term and the seam is the one an approximate index
would occupy; when ``sqlite-vec`` gains a partitioned/graph index the seam does not move.

STALENESS. Silent staleness is the same silent-recall defect in another costume, so it is
handled in three layers rather than assumed away:

1. **Write-through.** The store's three chunk write sites (``replace_chunks``,
   ``clear_chunks``, ``_delete_item_cascade``) maintain the index on the same connection,
   immediately adjacent to the chunk write.
2. **Reconciliation, once per process.** The first search compares the index's row count
   against the live count of embedded chunks at that dimension and rebuilds on a mismatch
   (measured at 182 ms for 30,000 rows), logging the rebuild. This repairs a database written
   before this index existed, or written by a process where the extension would not load.
3. **Harmless extras.** A candidate id that no longer joins to a live, active chunk row is
   simply dropped by the reader, and the reader escalates ``k`` when the surviving candidate
   set is too small — so stale-EXTRA entries cost nothing but a candidate slot, and even that
   is compensated.

The residual gap is stated rather than hidden: equal row counts with different *contents*
(an unmaintained delete paired with an unmaintained insert) would pass the reconciliation.
Detecting that needs a full content scan, which is the exact cost this index removes. The
Doctor probe therefore reports index coverage so drift is visible, not inferred.

DIMENSION. One index table per embedding dimension (``chunk_vec_384``), so a half-re-embedded
library keeps one self-consistent index per model instead of one index that lies about both.
This matches the reader's dimension guard exactly: a vector whose dimension differs from the
active query vector is unscoreable either way.

FAIL SOFT, NEVER CLOSED. SQLite extension loading depends on how the interpreter's SQLite was
built. Every entry point here degrades to "no index" and lets the caller keep its existing
exact scan; nothing in this module raises into a search. The unavailability reason is logged
ONCE at INFO (the probe is cached) and surfaced as a Doctor capability line.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache

from personalclaw.sqlite_compat import sqlite3

logger = logging.getLogger(__name__)

__all__ = [
    "VecCapability",
    "ChunkVectorIndex",
    "index_table_name",
    "probe",
    "reset_probe",
]

# Table-name prefix; the dimension is the suffix (``chunk_vec_384``). Encoding the dimension
# in the NAME rather than a metadata row means the index's dimension can never drift from the
# vectors inside it — there is no second place to keep in step.
_TABLE_PREFIX = "chunk_vec_"

# The remedy line a user sees when the extension cannot load. Names the concrete fix in the
# same spirit as ``sqlite_compat.FTS5_REMEDY`` rather than leaving a bare capability=false.
VEC_REMEDY = (
    "Knowledge vector search is running the exact scan, which is correct but slower on a "
    "large library. The 'sqlite-vec' SQLite extension could not be loaded by this Python's "
    "SQLite build. Install the 'sqlite-vec' wheel (pip install sqlite-vec) and use a Python "
    "whose SQLite was built with loadable-extension support."
)


def index_table_name(dim: int) -> str:
    """The index table for vectors of ``dim`` dimensions."""
    return f"{_TABLE_PREFIX}{int(dim)}"


@dataclass(frozen=True)
class VecCapability:
    """Whether this interpreter can load ``sqlite-vec`` at all (probed once, memoized)."""

    available: bool
    reason: str = ""  # empty when available; otherwise WHY not, for the log + Doctor
    version: str = ""  # ``vec_version()`` when available


def _load_extension(conn: "sqlite3.Connection") -> str:
    """Load ``sqlite-vec`` into ``conn`` and return its version.

    Split out as the single seam every load goes through: the availability probe, the store's
    live connection, and the force-disabled test all funnel here, so one patch point exercises
    the real degradation path rather than a test-only flag.
    """
    import sqlite_vec

    # enable_load_extension is absent when CPython was built --disable-loadable-sqlite-
    # extensions (and raises on some hardened builds), which is exactly the case the
    # runtime-availability clause exists for.
    conn.enable_load_extension(True)
    try:
        sqlite_vec.load(conn)
    finally:
        # Re-close the door immediately: leaving extension loading enabled on a long-lived
        # connection widens what a SQL-injection bug could reach.
        conn.enable_load_extension(False)
    row = conn.execute("SELECT vec_version()").fetchone()
    return str(row[0]) if row else "unknown"


# Set once, so the degradation is announced exactly one time per process however many
# searches run. Module-global (one gateway per process) with a reset for tests.
_logged_degraded = False


def _log_degraded_once(reason: str) -> None:
    global _logged_degraded
    if _logged_degraded:
        return
    _logged_degraded = True
    logger.info(
        "knowledge vector search: ANN index unavailable (%s) — falling back to the exact "
        "scan, which is correct but linear in library size. %s",
        reason,
        VEC_REMEDY,
    )


@lru_cache(maxsize=1)
def probe() -> VecCapability:
    """Can this interpreter load ``sqlite-vec`` and create a ``vec0`` table? (memoized)

    Runs against a throwaway in-memory connection so the probe has no side effects on the
    real database, and never raises: any failure reports the capability as absent with the
    reason, and logs that reason once at INFO.
    """
    conn = None
    try:
        conn = sqlite3.connect(":memory:")
        version = _load_extension(conn)
        # Loading the extension is necessary but not sufficient — the vec0 module has to
        # actually register, so probe the thing the index needs rather than a proxy for it.
        # Column names mirror the real table's: vec0 reserves `k` (the KNN limit), so a probe
        # using it would report the capability absent on a perfectly good build.
        conn.execute(
            "CREATE VIRTUAL TABLE _probe_vec USING "
            "vec0(chunk_id text primary key, embedding float[4] distance_metric=cosine)"
        )
        return VecCapability(available=True, version=version)
    except ImportError as exc:
        reason = f"the sqlite-vec package is not installed ({exc})"
    except AttributeError:
        reason = "this Python's sqlite3 has no enable_load_extension (built without it)"
    except Exception as exc:  # noqa: BLE001 — a capability probe must never raise
        reason = f"{type(exc).__name__}: {exc}"
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001,S110 — closing a probe conn is best-effort
                pass
    _log_degraded_once(reason)
    return VecCapability(available=False, reason=reason)


def reset_probe() -> None:
    """Clear the memoized probe + the one-time-log latch (test isolation only)."""
    global _logged_degraded
    probe.cache_clear()
    _logged_degraded = False


class ChunkVectorIndex:
    """The ``vec0`` index over ``chunks.embedding``, bound to one store connection.

    Every method is a no-op-and-return rather than a raise when the extension is unavailable,
    so a caller can call unconditionally and keep its own exact-scan path for the ``None``
    case. Construction is free: nothing is loaded or created until first use, so opening a
    knowledge store on a build without the extension costs nothing.
    """

    def __init__(self, db: "sqlite3.Connection"):
        self.db = db
        self._loaded: bool | None = None  # None = not attempted yet
        self._synced: set[int] = set()  # dimensions reconciled in this process
        self._last_rebuild: dict[str, int] = {}  # dim -> rows rebuilt (Doctor evidence)

    # ── availability ─────────────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        """Is the extension loaded on this connection? Loads it on first ask."""
        if self._loaded is None:
            cap = probe()
            if not cap.available:
                self._loaded = False
            else:
                try:
                    _load_extension(self.db)
                    self._loaded = True
                except Exception as exc:  # noqa: BLE001 — never raise into a search/write
                    # The probe's throwaway connection succeeded but this one did not; that
                    # is still a degradation the user should see once.
                    _log_degraded_once(f"loading into the knowledge DB failed: {exc}")
                    self._loaded = False
        return self._loaded

    # ── write-through maintenance ────────────────────────────────────────────

    def sync_item(self, item_id: str, rows: list[tuple[str, bytes | None]]) -> None:
        """Replace the index entries for ``item_id`` with ``rows`` (``(chunk_id, blob)``).

        Called immediately after the store rewrites an item's chunk rows, on the same
        connection. Chunks with no embedding are skipped — an un-embedded chunk is not
        scoreable by either path, so indexing a NULL would only invent a candidate that the
        reader has to drop again.
        """
        if not self.enabled:
            return
        self._delete_keys([cid for cid, _ in rows])
        for chunk_id, blob in rows:
            if not blob:
                continue
            dim = len(blob) // 4
            if dim <= 0 or len(blob) % 4:
                continue
            try:
                self._ensure_table(dim)
                self.db.execute(
                    f"INSERT INTO {index_table_name(dim)} (chunk_id, embedding) VALUES (?, ?)",  # noqa: S608,E501
                    (chunk_id, blob),
                )
            except Exception as exc:  # noqa: BLE001 — an index write never fails a chunk write
                logger.debug("chunk vector index: insert failed for %s: %s", chunk_id, exc)
                # The row count now disagrees with the live count, so the next process's
                # reconciliation rebuilds. Forget the sync mark so THIS process re-checks too.
                self._synced.discard(dim)

    def drop_item(self, item_id: str) -> None:
        """Drop an item's index entries. **Call before deleting its ``chunks`` rows** — the
        chunk ids are read from that table (a stale extra entry is harmless to the reader, so
        calling late degrades to "reconcile later" rather than to wrong results)."""
        if not self.enabled:
            return
        try:
            ids = [
                r[0] for r in self.db.execute("SELECT id FROM chunks WHERE item_id = ?", (item_id,))
            ]  # noqa: E501
        except Exception as exc:  # noqa: BLE001
            logger.debug("chunk vector index: id lookup failed for %s: %s", item_id, exc)
            return
        self._delete_keys(ids)

    def _delete_keys(self, chunk_ids: list[str]) -> None:
        """Remove these keys from every dimension's index table (a re-chunk can move a
        chunk between dimensions after a model change, so all tables are swept)."""
        if not chunk_ids:
            return
        for table in self._tables():
            for chunk_id in chunk_ids:
                try:
                    self.db.execute(
                        f"DELETE FROM {table} WHERE chunk_id = ?", (chunk_id,)
                    )  # noqa: S608,E501
                except Exception as exc:  # noqa: BLE001
                    logger.debug("chunk vector index: delete failed for %s: %s", chunk_id, exc)

    # ── read path ────────────────────────────────────────────────────────────

    def candidate_chunk_ids(self, query_blob: bytes, dim: int, k: int) -> list[str] | None:
        """The ``k`` nearest indexed chunk ids to ``query_blob``, or ``None`` when the index
        cannot serve this query (extension unavailable, no table at this dimension, or any
        SQLite error) — ``None`` is the caller's signal to run its exact scan.
        """
        if not self.enabled or k <= 0:
            return None
        if not self.ensure_synced(dim):
            return None
        table = index_table_name(dim)
        try:
            return [
                r[0]
                for r in self.db.execute(
                    f"SELECT chunk_id FROM {table} WHERE embedding MATCH ? AND k = ?",  # noqa: S608
                    (query_blob, int(k)),
                )
            ]
        except Exception as exc:  # noqa: BLE001 — a broken index means slow, never broken
            logger.debug("chunk vector index: KNN query failed: %s", exc)
            self._synced.discard(dim)
            return None

    def ensure_synced(self, dim: int) -> bool:
        """Reconcile this dimension's index against the live chunk rows, once per process.

        Returns False when no usable index exists for ``dim`` (including "there are no
        embedded chunks at this dimension", where an ANN query would be pointless).
        """
        if not self.enabled:
            return False
        if dim in self._synced:
            return True
        try:
            live = self.db.execute(
                "SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL AND length(embedding) = ?",
                (dim * 4,),
            ).fetchone()[0]
            if not live:
                return False
            self._ensure_table(dim)
            table = index_table_name(dim)
            indexed = self.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
            if indexed != live:
                self._rebuild(dim, live=live, indexed=indexed)
            self._synced.add(dim)
            return True
        except Exception as exc:  # noqa: BLE001 — reconciliation failure means exact scan
            logger.debug("chunk vector index: reconciliation failed at dim %s: %s", dim, exc)
            return False

    # ── build / rebuild ──────────────────────────────────────────────────────

    def _tables(self) -> list[str]:
        """Existing index tables (any dimension).

        The exact-suffix match matters: vec0 creates SHADOW tables beside each virtual table
        (``chunk_vec_384_chunks``, ``_info``, ``_rowids``, …), and a bare ``LIKE 'chunk_vec_%'``
        sweeps those in — which turned every delete into a failed statement against a shadow
        table and made the Doctor's coverage read throw on parsing ``"384_chunks"`` as a
        dimension. Only ``<prefix><digits>`` is one of ours.
        """
        try:
            return [
                r[0]
                for r in self.db.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE ?",
                    (_TABLE_PREFIX + "%",),
                )
                if r[0][len(_TABLE_PREFIX) :].isdigit()
            ]
        except Exception:  # noqa: BLE001
            return []

    def _ensure_table(self, dim: int) -> None:
        """Create this dimension's ``vec0`` table if absent.

        ``distance_metric=cosine`` is load-bearing, not decoration: the reader ranks by
        cosine, and vec0's default L2 ordering only agrees with cosine for unit-length
        vectors. Nothing guarantees the embedder normalizes, so an L2-ordered candidate set
        would hand the reader the wrong candidates for a non-normalized model — a silent
        recall regression of exactly the kind this task exists to prevent.
        """
        table = index_table_name(dim)
        if table in self._tables():
            return
        self.db.execute(
            f"CREATE VIRTUAL TABLE {table} USING "  # noqa: S608 — dim is int-coerced
            f"vec0(chunk_id text primary key, embedding float[{int(dim)}] distance_metric=cosine)"
        )

    def _rebuild(self, dim: int, *, live: int, indexed: int) -> None:
        """Rebuild this dimension's index from the live chunk rows.

        DROP + CREATE + INSERT..SELECT rather than an incremental diff: the whole point of a
        rebuild is that we do not trust the current contents, and at measured cost (182 ms for
        30,000 rows, once per process, only on a mismatch) a diff would buy complexity and a
        second thing to get wrong.
        """
        table = index_table_name(dim)
        logger.info(
            "knowledge vector search: rebuilding the chunk ANN index at %d dimensions "
            "(index had %d rows, %d embedded chunks live) — a database written before the "
            "index existed, or by a process that could not load sqlite-vec.",
            dim,
            indexed,
            live,
        )
        self.db.execute(f"DROP TABLE IF EXISTS {table}")  # noqa: S608
        self._ensure_table(dim)
        self.db.execute(
            f"INSERT INTO {table} (chunk_id, embedding) "  # noqa: S608
            "SELECT id, embedding FROM chunks "
            "WHERE embedding IS NOT NULL AND length(embedding) = ?",
            (dim * 4,),
        )
        self._last_rebuild[str(dim)] = live

    # ── introspection (Doctor) ───────────────────────────────────────────────

    def coverage(self) -> dict:
        """What the Doctor reports: availability, per-dimension indexed-vs-live counts, and
        any rebuild this process performed. Read-only and exception-safe — it must never be
        the reason a health report fails."""
        cap = probe()
        out: dict = {
            "extension_available": cap.available,
            "loaded": bool(self._loaded),
            "version": cap.version,
            "dimensions": {},
            "rebuilt": dict(self._last_rebuild),
        }
        if cap.reason:
            out["reason"] = cap.reason
        if not self.enabled:
            return out
        try:
            for table in sorted(self._tables()):
                dim = int(table[len(_TABLE_PREFIX) :])
                indexed = self.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[
                    0
                ]  # noqa: S608,E501
                live = self.db.execute(
                    "SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL "
                    "AND length(embedding) = ?",
                    (dim * 4,),
                ).fetchone()[0]
                out["dimensions"][str(dim)] = {"indexed": indexed, "live": live}
        except Exception as exc:  # noqa: BLE001
            out["error"] = str(exc)
        return out
