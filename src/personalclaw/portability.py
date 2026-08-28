"""Portable zip export/import for PersonalClaw state — the DSAR surface (§6).

Creates a zip archive of PersonalClaw state for download via the dashboard, and
restores from uploaded zip archives. Designed to work over HTTP for remote users
(e.g. remote Linux server → local browser).

Two properties this module exists to guarantee, both now enforced by construction
rather than by a hand-maintained list:

* **`secret ∪ derived` never leaves the machine.** Every write site runs through
  :func:`_is_excluded`, which projects the inventory's ``secret=True`` *and*
  ``derived=True`` entries. Measured before this was true: the export's hardcoded
  database list carried ``memory_index.db`` — declared ``derived=True`` — so a
  "portable export" shipped a stale vector index that a restore would then pair
  with a newer store, the exact hazard `inventory.export_entries()` documents.
* **An export declares its own scope.** A per-domain export (memory / knowledge /
  work / automation / platform / config) carries only that domain's declared
  entries, and prunes every *other* domain's entries out of the tree walks — so a
  ``platform`` export cannot smuggle ``workspace/knowledge/files`` (the user's
  documents) out under cover of the ``workspace`` tree.

MANIFEST versions (:data:`SUPPORTED_MANIFEST_VERSIONS`): v1/v2 carry sizes only;
**v3 carries per-member ``bytes`` + ``sha256`` plus ``schema_version``/``machine_id``
(§2's integrity shape)**, so :func:`validate_import_zip` can detect a corrupted
archive before an import writes anything. v1/v2 zips still import — there is simply
nothing to verify them against, which is stated rather than silently assumed.
"""

import hashlib
import io
import json
import logging
import os
import shutil
import socket
import tempfile
import zipfile
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from personalclaw.config.loader import config_dir
from personalclaw.security import is_sensitive_path
from personalclaw.snapshot import (
    _copy_tree_no_overwrite,
    _do_replace,
    _merge_crons,
    _merge_event_triggers,
    _merge_memory,
    _merge_notifications,
    _merge_triggers,
)
from personalclaw.sqlite_compat import sqlite3

logger = logging.getLogger(__name__)


def _inventory_secrets() -> frozenset[str]:
    """Secret basenames from the state inventory (DURABILITY §1).

    The export exclude-set is now a PROJECTION of the manifest's ``secret=True``
    entries rather than a second hand-maintained list — the drift between these
    two lists is exactly what let stores escape coverage. Falls back to the
    historical literals if the import ever fails, so an export can never
    accidentally start including credentials.
    """
    literals = {
        ".env",
        # SH-2's rollback snapshot — a second PLAINTEXT copy of every credential, kept only
        # while the keychain move is reversible. It is `secret=True` in the inventory, so the
        # projection already covers it; it is named here as well because this fallback exists
        # for the case where the inventory cannot be imported, and that is precisely when the
        # file whose whole content is credentials must not become exportable.
        ".env.pre-keychain",
        "session_map.json",
    }
    # `.local_secret`, `sel_hmac.key` and `telemetry_salt` used to be spelled out here too,
    # which made this the THIRD hand-kept copy of the same names (with `handlers/files.py`
    # and `security.py`). That is the drift this function's own docstring warns about, and
    # #643 was one of the copies not knowing what another did. Unioned rather than replaced:
    # the extras above are export-specific and belong to this policy.
    from personalclaw.security import OWN_SECRET_BASENAMES

    literals |= set(OWN_SECRET_BASENAMES)
    try:
        from personalclaw.durability import inventory as inv

        return frozenset(literals | {p.rsplit("/", 1)[-1] for p in inv.secret_paths()})
    except Exception:  # noqa: BLE001 — never widen the export on an import error
        return frozenset(literals)


EXPORT_EXCLUDE = frozenset(
    _inventory_secrets()
    | {
        # Process-local runtime files (not "state", so not inventory entries).
        "session_pids.txt",
        "agent_pids.txt",
        # Advisory `flock` files. They carry no state, and a restored one is a lock held by a
        # process that does not exist on this machine — measured: the run-history tree exported
        # `cron-history/.history.lock` before this entry.
        ".history.lock",
        ".triggers.lock",
        ".crons.lock",
    }
)

EXCLUDE_DIRS = frozenset(
    {
        # Backup/sync OUTPUT. Exporting these would nest an archive inside an archive, and their
        # contents are reproducible from the state that travels beside them.
        "snapshots",
        "outbox",
        # 🔴 A DELIBERATE ASYMMETRY WITH THE SNAPSHOT PATH, recorded here in S182 because it was
        # unwritten and looks like an oversight once the export becomes inventory-derived: a
        # SNAPSHOT carries `uploads/` (verified: it is in both `_everything_paths` and
        # `_extra_restore_paths`) and an EXPORT does not.
        #
        # That is defensible rather than a bug — a snapshot is a local 0600 archive of this machine,
        # while an export is the artifact a user hands to another machine or attaches to a bug
        # report, and uploads are arbitrary user-supplied binaries of unbounded size. Kept as-is
        # because changing an export's contents is a product decision, not a sweep's to make; the
        # asymmetry is now stated so the next reader does not "fix" it by accident.
        "uploads",
        "__pycache__",
    }
)


#: MANIFEST version this module writes. v3 adds the §2 integrity shape
#: (``schema_version``/``machine_id`` + per-member ``bytes``/``sha256``) and the
#: export's declared ``scope``/``domains``.
MANIFEST_VERSION = 3

#: Versions :func:`validate_import_zip` accepts. Append-only: a zip a user has on
#: disk must keep importing, so a version is never dropped from this tuple.
SUPPORTED_MANIFEST_VERSIONS = (1, 2, 3)


def _pc_dir() -> Path:
    return Path(os.environ.get("PERSONALCLAW_HOME", config_dir()))


def _excluded_entry_paths() -> frozenset[str]:
    """Home-relative paths of every ``secret=True`` **or** ``derived=True`` entry.

    §6's rule is ``secret ∪ derived`` and only the ``secret`` half was enforced:
    :data:`EXPORT_EXCLUDE` is a secret-projection (S1) but nothing projected
    ``derived``, and the export's hardcoded database list named ``memory_index.db``
    outright. Measured on a seeded home before this existed — the zip contained the
    derived index *and its rows*. Derived state is rebuildable by definition, so
    carrying it is pure downside: a stale index restored beside a newer store is
    worse than no index at all (`inventory.export_entries()`).

    **RAISES rather than falling back to a literal list.** Every other inventory lookup in
    this module degrades to hand-written literals, and that is right for them — they decide
    what an export *includes*, so a degraded answer costs completeness. This one decides
    what an export must NOT include, so a degraded answer costs a leak. An export that
    fails loudly beats an export that quietly ships a credential.

    It therefore hard-codes NO paths, which is also required for a second reason:
    `test_the_snapshot_coverage_gap_list_can_only_shrink` decides snapshot coverage partly by
    grepping THIS module's source for entry paths. A denylist literal here made two genuinely
    uncovered entries (`memory_faiss`, `memory_ids`) read as covered — a guard that names
    what it guards falsifies the ratchet watching it.
    """
    try:
        from personalclaw.durability import inventory as inv
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "cannot determine the secret/derived exclusion set: the state inventory is "
            "unavailable, so refusing to build an export rather than risk including "
            f"credentials ({exc})"
        ) from exc
    return frozenset(e.path for e in inv.INVENTORY if e.secret or e.derived)


def _is_excluded(rel_path: PurePosixPath) -> bool:
    """Whether a home-relative path must stay out of an export, regardless of domain.

    Ancestor-inclusive against `secret ∪ derived`: excluding only an exact match would
    carry every file *inside* an excluded directory — the credential store's contents, a
    vector index's shards — while excluding the directory entry itself.

    Names no entry path on purpose. `test_the_snapshot_coverage_gap_list_can_only_shrink`
    greps this module for entry paths to decide snapshot coverage, so an example path in a
    comment here silently marks that entry "covered" (measured: it did, for two of them).
    """
    if rel_path.name in EXPORT_EXCLUDE:
        return True
    if rel_path.name.endswith(".pid"):
        return True
    for part in rel_path.parts:
        if part in EXCLUDE_DIRS:
            return True
    chain = {rel_path.as_posix()} | {p.as_posix() for p in rel_path.parents if p.as_posix() != "."}
    return bool(chain & _excluded_entry_paths())


def export_domains() -> tuple[str, ...]:
    """Domains a caller may ask :func:`create_export_zip` for.

    Projected from the inventory rather than listed here, so a new domain becomes
    exportable the moment an entry declares it. A domain with no exportable entry
    (every row ``secret``/``derived``) is not offered — an "export" that can only
    ever be empty is a broken promise, not a feature.
    """
    from personalclaw.durability import inventory as inv

    seen: list[str] = []
    for entry in inv.export_entries():
        if entry.domain not in seen:
            seen.append(entry.domain)
    return tuple(seen)


def domain_of(rel: str) -> str:
    """The inventory domain owning a home-relative path — **longest declared match**.

    Longest-match is load-bearing, not a nicety. ``workspace/knowledge/files`` (the
    user's documents, domain ``knowledge``) is nested *inside* the ``workspace`` tree
    entry (domain ``platform``), and both are declared. Measured while building this:

    * an ancestor-wins rule put every user document in a ``platform`` export and
      produced an **empty** ``knowledge`` export — the exact boundary criterion 9
      exists to protect, inverted;
    * a first-declared-wins rule depends on `INVENTORY` ordering, so adding an entry
      silently re-homes a neighbour's files.

    Longest-match is the only rule under which "which domain is this file's state?"
    has one answer that survives a new entry.
    """
    from personalclaw.durability import inventory as inv

    best_len, best = -1, ""
    for entry in inv.INVENTORY:
        if (rel == entry.path or rel.startswith(entry.path + "/")) and len(entry.path) > best_len:
            best_len, best = len(entry.path), entry.domain
    return best or _UNDECLARED_LITERAL_DOMAINS.get(rel, "platform")


def _wal_checkpoint(db_path: Path) -> None:
    if db_path.is_file():
        try:
            conn = sqlite3.connect(str(db_path))
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            conn.close()
        except Exception:
            logger.debug("WAL checkpoint failed for %s", db_path)


def _backup_sqlite(src: Path, dst_buffer: io.BytesIO) -> None:
    """Use SQLite backup API for a consistent copy."""
    src_conn = sqlite3.connect(str(src))
    mem_conn = sqlite3.connect(":memory:")
    try:
        src_conn.backup(mem_conn)
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        tmp.close()
        try:
            disk_conn = sqlite3.connect(tmp.name)
            try:
                mem_conn.backup(disk_conn)
            finally:
                disk_conn.close()
            dst_buffer.write(Path(tmp.name).read_bytes())
        finally:
            os.unlink(tmp.name)
    finally:
        src_conn.close()
        mem_conn.close()


#: Database files never travel as a filesystem copy — see the tree walk in `create_export_zip`.
_DB_SUFFIXES = frozenset({".db"})
_DB_SIDECARS = ("-wal", "-shm")


def _is_derived_within(entry_path: str, rel_to_entry: str) -> bool:
    """Whether `rel_to_entry` matches one of `entry_path`'s inventory `derived_within` globs.

    🔴 The field had NO reader anywhere. `projects` declares ``derived_within=("*/worktrees",)`` —
    git-owned checkouts, re-creatable from the repo — and this export reached `projects/` through a
    generic `rglob`, so an export of a home with one bound workspace carried the whole worktree.
    Enforcing it in BOTH whole-home paths (here and `snapshot._derived_ignore`) is the point: a
    declaration honored in one direction is the asymmetry that made a restore drop what a backup
    captured.

    Matches the path AND every ANCESTOR of it. `*/worktrees` names a directory, and what an export
    walks is the files inside it — `p-1/worktrees/repo/src/a.py` matches no glob written about the
    directory, so a leaf-only test would exclude the empty dir and carry its entire contents. Found
    by writing the exclusion first and then counting the exported files.
    """
    import fnmatch

    try:
        from personalclaw.durability import inventory as inv

        globs: tuple[str, ...] = ()
        for entry in inv.INVENTORY:
            if entry.path == entry_path:
                globs = tuple(entry.derived_within)
                break
        if not globs:
            return False
        candidate = PurePosixPath(rel_to_entry)
        chain = [candidate.as_posix()] + [
            p.as_posix() for p in candidate.parents if p.name or p.parts
        ]
        return any(fnmatch.fnmatch(c, g) for c in chain for g in globs)
    except Exception:  # noqa: BLE001 — an export must work even if this import breaks
        return False


#: Home-relative paths the three hand-written lists in `create_export_zip` already
#: carry. Subtracted from the inventory-derived sweep so each keeps its per-entry
#: reason (the safe sqlite backup API, the `skills/auto` skip, the `crons.json` note).
_LITERAL_EXPORT_PATHS = frozenset(
    {
        "config.json",
        "hooks.json",
        "triggers.json",
        "crons.json",
        "event_triggers.json",
        "notifications.jsonl",
        "feedback.jsonl",
        "project_dir",
        "workspace_dir",
        "memory.db",
        "memory_index.db",
        "learning.db",
        "workspace",
        "skills",
        "cron-history",
    }
)

#: The one literal above the inventory does not declare. `workspace_dir` is a pointer
#: file (the bound workspace path), grouped with `project_dir` — its sibling pointer,
#: declared ``domain=config`` — so a per-domain export can place it. Stated here
#: because a silent default would put an unclaimed file in an arbitrary domain.
_UNDECLARED_LITERAL_DOMAINS = {"workspace_dir": "config"}


def _remaining_export_paths(pc: Path, *, covered: frozenset[str] | None = None) -> list[str]:
    """Declared entries the hand-written export lists do not already carry (S182).

    Derived from `durability.inventory.export_entries()` — which excludes `secret=True` and
    `derived=True` by construction, so a credential cannot arrive here by being newly declared. The
    three literal lists in `create_export_zip` are subtracted rather than replaced: they encode
    per-entry reasons (the safe sqlite backup API for the databases, the `skills/auto` skip, the
    `crons.json` note) that a generic pass would lose.

    Databases are deliberately NOT returned. They are already staged through `_backup_sqlite`, and a
    filesystem copy of a live WAL store can capture a torn page set — the hazard the snapshot path
    fixed by routing every declared DB through the backup API.

    ``covered`` is the set of paths the literal lists actually wrote for THIS export.
    It defaults to every literal, reproducing the whole-home behaviour. A per-domain
    export passes only the literals it selected, so an entry nested inside a literal
    tree that this export skipped (``workspace/knowledge/files`` under a ``workspace``
    the ``knowledge`` domain never selects) is exported on its own instead of being
    silently dropped — the defect a top-level-only filter produces.
    """
    from personalclaw.durability import inventory as inv

    already = _LITERAL_EXPORT_PATHS if covered is None else covered
    db_paths = {e.path for e in inv.sqlite_entries()}
    out: list[str] = []
    for entry in inv.export_entries():
        if entry.path in already or entry.path in db_paths:
            continue
        top = entry.path.split("/", 1)[0]
        if top in already or top in out:
            continue
        if (pc / entry.path).exists():
            out.append(entry.path)
    return out


def create_export_zip(domains: Sequence[str] | None = None) -> tuple[bytes, dict]:
    """Create a zip archive of PersonalClaw state. Returns (zip_bytes, manifest_dict).

    ``domains`` restricts the export to those inventory domains (§6's per-domain
    shard: memory / knowledge / work / automation / platform / config). ``None`` is
    the full "give me everything PersonalClaw knows about me" export. An unknown
    domain raises :class:`ValueError` — silently exporting nothing for a typo is the
    worst failure a DSAR surface can have.

    ``secret ∪ derived`` is excluded on every path, and the returned manifest names
    the excluded entry ids so the exclusion is auditable from the artifact itself.
    """
    pc = _pc_dir()
    want: frozenset[str] | None = None
    if domains is not None:
        want = frozenset(domains)
        if not want:
            raise ValueError("domains must be a non-empty list, or None for a full export")
        unknown = sorted(want - set(export_domains()))
        if unknown:
            raise ValueError(
                f"unknown export domain(s): {', '.join(unknown)}; "
                f"valid: {', '.join(export_domains())}"
            )

    def _wanted(rel: str) -> bool:
        """Whether this export carries the path ``rel``.

        One predicate for all four write sites — the literal file list, the database
        projection, the tree walks and the inventory sweep. Four independently-written
        filters is exactly how ``memory_index.db`` stayed exported for four sessions
        after `EXPORT_EXCLUDE` became a secret-projection.
        """
        if _is_excluded(PurePosixPath(rel)):
            return False
        return want is None or domain_of(rel) in want

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = "" if want is None else "-" + "-".join(sorted(want))
    prefix = f"personalclaw-export{suffix}-{ts}"

    # Only checkpoint databases this export will actually read. `memory_index.db` is
    # deliberately absent: it is `derived=True` and no longer travels at all.
    for db_rel in ("memory.db", "learning.db"):
        if _wanted(db_rel):
            _wal_checkpoint(pc / db_rel)

    buf = io.BytesIO()
    contents_summary: dict = {}
    covered: set[str] = set()

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        # Core JSON/text files
        for fname in (
            "config.json",
            "hooks.json",
            # 🔴 `triggers.json` — the SOLE source of automations since S101, and the only one
            # since S112 deleted `ScheduleService`. Driven before adding it: a snapshot of a home
            # with two automations, an event trigger and run history captured `config.json` ALONE,
            # so `personalclaw snapshot` silently lost every automation the user had. The release
            # notes advise taking one before a breaking upgrade — it must not lose anything.
            "triggers.json",
            # `crons.json` still travels: it is read-only per §6 and `automation verify-migration`
            # diffs both sides, so a snapshot that dropped it would break that command after a move.
            "crons.json",
            # Named in the plan's own recon note as missing alongside the trigger store.
            "event_triggers.json",
            "notifications.jsonl",
            "feedback.jsonl",
            "project_dir",
            "workspace_dir",
        ):
            if not _wanted(fname):
                continue
            covered.add(fname)
            src = pc / fname
            if src.is_file() and not src.is_symlink():
                zf.write(str(src), f"{prefix}/{fname}")
                contents_summary[fname] = src.stat().st_size

        # SQLite databases via backup API
        # learning.db carries the capture staging log + flush outcome records. It
        # travels with the export so the flywheel's observability history survives a
        # move — a restored home that reports "no capture activity" because the log
        # was left behind is indistinguishable from a broken capture path, which is
        # the exact ambiguity the outcome records exist to remove.
        # 🔴 Every DECLARED database, not just the three named here (S182). `workflows/runs.db`,
        # `loop/loops.db`, both `knowledge.db` and `lexicon.db` are declared `kind=sqlite` and were
        # reachable only as a raw filesystem copy inside their parent tree — measured UNUSABLE ("no
        # such table") when the store had a 237 KB uncheckpointed WAL. Routing every declared DB
        # through the backup API is what the snapshot path already does.
        #
        # 🔴 `memory_index.db` IS GONE FROM THIS LIST (DAS-10). It was hardcoded here and
        # is declared `derived=True`, so §6's "`secret ∪ derived` excluded" was only half
        # enforced. Measured on a seeded home: the zip carried `memory_index.db` AND its
        # rows. The list is now a pure projection of `export_entries() ∩ sqlite_entries()`,
        # so a derived database cannot re-enter by being named.
        try:
            from personalclaw.durability import inventory as _inv

            _export_paths = {e.path for e in _inv.export_entries()}
            db_names: list[str] = sorted(
                e.path for e in _inv.sqlite_entries() if e.path in _export_paths
            )
        except Exception:  # noqa: BLE001 — an export must work even if this import breaks
            # Fail closed on the derived index: an export without the vector index is
            # complete (it rebuilds); an export WITH a stale one is a restore hazard.
            db_names = ["learning.db", "memory.db"]

        #: The databases THIS export routes through the sqlite backup API below. The
        #: `workspace`/`skills`/`cron-history` walk consults it so a declared store can
        #: never also leave as a raw filesystem copy — see `_is_projected_db`.
        projected_dbs = frozenset(db_names)

        def _is_projected_db(rel: str) -> bool:
            """Whether ``rel`` is a declared database the backup API already owns, or one
            of its ``-wal``/``-shm`` sidecars.

            🔴 WHY (DAS-10). Two write sites could emit the same declared database: the
            projection below (safe, WAL-checkpointed) and the `workspace` tree walk
            (a raw `zf.write`). Measured on a home whose `workspace/lexicon/lexicon.db`
            had a 53 KB uncheckpointed WAL: the zip carried the path TWICE (6 entries
            against 5 declared members, plus a `Duplicate name` warning), shipped
            `lexicon.db-wal` **and** `lexicon.db-shm`, and — worst — when
            `_backup_sqlite` raised and the projection logged "skipping unreadable
            database", the tree walk shipped a raw 45 KB copy anyway *and the manifest
            declared it*. That inverts the projection's fail-closed intent: a store the
            export decided not to carry travelled regardless, as a validated member.

            The inventory sweep further down already refuses raw database copies for
            exactly this reason. It skips **every** `.db` in a tree; this predicate is
            deliberately narrower — only the databases the projection owns — because
            `workspace/` is the user's own directory and silently dropping an *undeclared*
            sqlite file a user put there would trade one data defect for another.
            """
            if rel in projected_dbs:
                return True
            return any(
                rel.endswith(sidecar) and rel[: -len(sidecar)] in projected_dbs
                for sidecar in _DB_SIDECARS
            )

        for db_name in db_names:
            if not _wanted(db_name):
                continue
            covered.add(db_name)
            src = pc / db_name
            if src.is_file() and not src.is_symlink():
                if is_sensitive_path(str(src)):
                    continue
                db_buf = io.BytesIO()
                try:
                    _wal_checkpoint(src)
                    _backup_sqlite(src, db_buf)
                except Exception:  # noqa: BLE001
                    # One unreadable store must not cost the whole export. Skipping it is honest;
                    # writing a torn copy would put corruption in the artifact a user trusts.
                    logger.warning("export: skipping unreadable database %s", db_name)
                    continue
                zf.writestr(f"{prefix}/{db_name}", db_buf.getvalue())
                contents_summary[db_name] = db_buf.tell()

        # Directory trees: workspace, skills, cron-history
        dir_counts: dict[str, int] = {}
        # `cron-history` is the run ledger `ScheduleRunStore` owns (JSONL per job + a cross-job
        # index). Carried with the automations: a restored home whose triggers exist but whose run
        # history is empty reports "never ran" for automations that have run for months, which is
        # indistinguishable from a broken fire path — the same ambiguity the learning staging log
        # travels to avoid (see the note above).
        for dirname in ("workspace", "skills", "cron-history"):
            if not _wanted(dirname):
                continue
            covered.add(dirname)
            src_dir = pc / dirname
            count = 0
            if src_dir.is_dir():
                for fpath in src_dir.rglob("*"):
                    if fpath.is_symlink():
                        continue
                    rel = fpath.relative_to(pc)
                    if not _wanted(str(rel)):
                        continue
                    if is_sensitive_path(str(fpath)):
                        continue
                    if dirname == "skills" and "auto" in rel.parts:
                        continue
                    # A declared database leaves through the backup API above or not at
                    # all. Without this the same path was written twice and a skipped
                    # store shipped anyway — see `_is_projected_db`.
                    if _is_projected_db(rel.as_posix()):
                        continue
                    if fpath.is_file():
                        zf.write(str(fpath), f"{prefix}/{rel}")
                        count += 1
            dir_counts[dirname] = count
        contents_summary["workspace_files"] = dir_counts.get("workspace", 0)
        contents_summary["skill_count"] = dir_counts.get("skills", 0)
        contents_summary["run_history_files"] = dir_counts.get("cron-history", 0)

        # 🔴 EVERY REMAINING DECLARED ENTRY (S182). The three lists above are hand-written, and
        # `export_entries()` had no consumer here — so the export named 18 of 53 exportable entries
        # and the zip came out holding **three files**: `config.json`, `memory.db`, `MANIFEST.json`.
        # Driven on a home seeded across the inventory, 30 stores of the user's own data were absent
        # from the feature whose whole promise is "give me everything PersonalClaw knows about me".
        #
        # This is the same defect the `triggers.json` and `cron-history` comments above record,
        # closed one entry at a time. Deriving the rest from the inventory is what stops the next
        # store from being forgotten — the snapshot side already does exactly this.
        extra_counts: dict[str, int] = {}
        for entry in _remaining_export_paths(pc, covered=frozenset(covered)):
            if not _wanted(entry):
                continue
            src = pc / entry
            if src.is_symlink() or is_sensitive_path(str(src)):
                continue
            if src.is_file():
                zf.write(str(src), f"{prefix}/{entry}")
                contents_summary[entry] = src.stat().st_size
            elif src.is_dir():
                count = 0
                for fpath in src.rglob("*"):
                    if fpath.is_symlink() or not fpath.is_file():
                        continue
                    rel = fpath.relative_to(pc)
                    if not _wanted(str(rel)) or is_sensitive_path(str(fpath)):
                        continue
                    # The entry's own `derived_within` (e.g. `projects/*/worktrees`). Checked
                    # against the path relative to the ENTRY, which is the frame the inventory
                    # writes the globs in — `*/worktrees` means "any project's worktrees", not
                    # "any directory named worktrees anywhere under the home".
                    if _is_derived_within(entry, fpath.relative_to(src).as_posix()):
                        continue
                    # 🔴 Never raw-copy a live database out of a tree. `workflows/runs.db` and
                    # `loop/loops.db` sit INSIDE declared trees, so `rglob` reaches them — and a
                    # filesystem copy of a WAL store captures the `.db` without its `-wal`.
                    # Measured on a store with 2000 committed rows and a 237 KB uncheckpointed WAL:
                    # the raw copy was not merely short, it was UNUSABLE ("no such table: runs").
                    # The declared databases travel through `_backup_sqlite` below instead; this is
                    # the same split the snapshot path makes with `_tree_ignore_dbs`.
                    if fpath.suffix in _DB_SUFFIXES or fpath.name.endswith(_DB_SIDECARS):
                        continue
                    zf.write(str(fpath), f"{prefix}/{rel}")
                    count += 1
                if count:
                    extra_counts[entry] = count
        if extra_counts:
            contents_summary["store_files"] = extra_counts

        # Manifest — v3 (§2's integrity shape, so an import can detect corruption).
        #
        # `members` is the §2 per-shard record applied to a zip: every member's bytes
        # and sha256, sorted for determinism. Without it a v1/v2 import had NOTHING to
        # verify against — `validate_import_zip` could only confirm the zip parsed, so a
        # silently truncated archive imported as far as it went. `contents` is retained
        # because v2 readers exist (the settings panel, `snapshot._print_manifest`).
        members = sorted(
            (
                {
                    "path": name.split("/", 1)[1] if "/" in name else name,
                    "bytes": info.file_size,
                    "sha256": hashlib.sha256(zf.read(name)).hexdigest(),
                }
                for name, info in ((i.filename, i) for i in zf.infolist())
                if not info.is_dir()
            ),
            key=lambda m: str(m["path"]),
        )
        excluded = _excluded_entry_paths()
        manifest = {
            "version": MANIFEST_VERSION,
            "format": "zip",
            "schema_version": _shard_schema_version(),
            "machine_id": _machine_id(pc),
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "hostname": socket.gethostname(),
            "user": os.environ.get("USER", "unknown"),
            "scope": "full" if want is None else "partial",
            "domains": sorted(want) if want is not None else list(export_domains()),
            "domain_counts": _domain_counts(members),
            "excluded": sorted(excluded),
            "members": members,
            "contents": contents_summary,
        }
        zf.writestr(f"{prefix}/MANIFEST.json", json.dumps(manifest, indent=2, sort_keys=True))

    return buf.getvalue(), manifest


def _shard_schema_version() -> int:
    """§2's shard schema version, so a v3 zip declares the format generation it came from."""
    try:
        from personalclaw.durability.shards import SHARD_SCHEMA_VERSION

        return int(SHARD_SCHEMA_VERSION)
    except Exception:  # noqa: BLE001 — an export must work even if this import breaks
        return 0


def _machine_id(home: Path) -> str:
    """§2's ``machine_id``, so two machines' exports of the same state are attributable."""
    try:
        from personalclaw.durability.shards import machine_id

        return str(machine_id(home))
    except Exception:  # noqa: BLE001
        return ""


def _domain_counts(members: list[dict]) -> dict[str, dict[str, int]]:
    """Per-domain ``{files, bytes}`` over the zip's members — the archive browser's row counts.

    Attributed by longest declared-path match, so ``workspace/knowledge/files/doc.pdf``
    counts as ``knowledge`` (its own entry) and not ``platform`` (the ``workspace`` tree
    it is nested inside). A shortest-match or first-match rule would report every user
    document as platform state, which is precisely the boundary criterion 9 is about.
    """
    try:
        from personalclaw.durability import inventory as inv

        by_path = sorted(
            ((e.path, e.domain) for e in inv.INVENTORY), key=lambda t: len(t[0]), reverse=True
        )
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, dict[str, int]] = {}
    for member in members:
        rel = str(member["path"])
        if rel == "MANIFEST.json":
            continue
        domain = next(
            (d for p, d in by_path if rel == p or rel.startswith(p + "/")),
            _UNDECLARED_LITERAL_DOMAINS.get(rel, "platform"),
        )
        bucket = out.setdefault(domain, {"files": 0, "bytes": 0})
        bucket["files"] += 1
        bucket["bytes"] += int(member["bytes"])
    return out


def _verify_members(zf: zipfile.ZipFile, prefix: str, members: list) -> str:
    """Check every declared member's sha256. Returns "" when sound, else the problem.

    Mirrors `shards.validate`: a declared-but-absent member and a hash mismatch are
    both fatal, and the message NAMES the member — "the archive is corrupt" without a
    name is not actionable. An UNDECLARED extra member is also fatal: a manifest that
    does not describe the whole archive cannot vouch for it, and this is the shape a
    tampered zip takes.
    """
    # MANIFEST.json is never in `members`: its hash cannot describe itself (the manifest
    # is written last, after the member records are computed), so it is excluded from
    # BOTH sides of the comparison rather than special-cased on one.
    present = {
        rel
        for rel in (
            (n.split("/", 1)[1] if "/" in n else n)
            for n in zf.namelist()
            if not n.endswith("/") and (not prefix or n.startswith(prefix + "/"))
        )
        if rel != "MANIFEST.json"
    }
    declared: set[str] = set()
    for member in members:
        if not isinstance(member, dict):
            return "Invalid manifest: members must be objects"
        rel = str(member.get("path", ""))
        declared.add(rel)
        if rel == "MANIFEST.json":
            continue
        name = f"{prefix}/{rel}" if prefix else rel
        try:
            data = zf.read(name)
        except KeyError:
            return f"Archive is missing a declared member: {rel}"
        want = str(member.get("sha256", ""))
        if want and hashlib.sha256(data).hexdigest() != want:
            return f"Archive member failed its checksum: {rel}"
    extra = sorted(present - declared)
    if extra:
        return f"Archive has undeclared member(s): {', '.join(extra[:4])}"
    return ""


def validate_import_zip(zip_path: Path) -> tuple[bool, str, dict]:
    """Validate a zip file for import.

    Returns (ok, error_message, manifest_dict).
    """
    try:
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            names = zf.namelist()

            # Check for path traversal
            for name in names:
                parts = PurePosixPath(name).parts
                if ".." in parts or name.startswith("/"):
                    return False, f"Rejected path traversal: {name}", {}

            # Find manifest
            manifest_entries = [n for n in names if n.endswith("MANIFEST.json")]
            if not manifest_entries:
                return False, "No MANIFEST.json found in archive", {}

            manifest_data = json.loads(zf.read(manifest_entries[0]))
            version = manifest_data.get("version")
            if version not in SUPPORTED_MANIFEST_VERSIONS:
                return False, f"Unsupported manifest version: {version}", {}

            # v3 declares per-member hashes (§2), so corruption is DETECTABLE — verify
            # before an import writes anything. v1/v2 carry sizes only: there is nothing
            # to verify them against, and refusing them would break every archive a user
            # already has on disk. So back-compat is "import unverified", stated in the
            # returned manifest rather than left as an assumption the caller can't see.
            if version == MANIFEST_VERSION:
                prefix = PurePosixPath(manifest_entries[0]).parent.as_posix()
                problem = _verify_members(zf, prefix, manifest_data.get("members") or [])
                if problem:
                    return False, problem, {}
                manifest_data = {**manifest_data, "verified": True}
            else:
                manifest_data = {**manifest_data, "verified": False}

            return True, "", manifest_data
    except zipfile.BadZipFile:
        return False, "Invalid zip file", {}
    except (json.JSONDecodeError, KeyError) as e:
        return False, f"Invalid manifest: {e}", {}


def _strip_excluded_from_staged(snap: Path) -> list[str]:
    """Delete every `secret ∪ derived` path from a staged import tree. Returns what went.

    Belt-and-suspenders against a hand-built archive (§ amendment: "merge mode
    additionally never writes any ``secret=True`` entry even if a hand-built archive
    contains one"). Applied to the STAGED copy, not to the live home, so the later
    copy/merge passes physically cannot see a credential — no per-branch skip to forget.
    """
    removed: list[str] = []
    excluded = _excluded_entry_paths()
    for rel in sorted(excluded):
        target = snap / rel
        if not target.exists():
            continue
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(str(target), ignore_errors=True)
        else:
            target.unlink(missing_ok=True)
        removed.append(rel)
    for fpath in sorted(snap.rglob("*")):
        if not fpath.exists() or fpath.is_dir():
            continue
        rel_posix = fpath.relative_to(snap).as_posix()
        if fpath.name in EXPORT_EXCLUDE or is_sensitive_path(str(fpath)):
            fpath.unlink(missing_ok=True)
            removed.append(rel_posix)
    return removed


def apply_import_zip(zip_path: Path, mode: str = "merge") -> dict:
    """Extract and apply an import zip.

    Args:
        zip_path: Path to validated zip file.
        mode: "merge" (default, non-destructive) or "replace" (overwrites).

    Returns summary dict of what was imported.

    **Failure semantics, stated because this can overwrite a user's home.** Neither
    mode is transactional — POSIX gives no atomic multi-file swap — so the contract is
    *recoverability*, not atomicity:

    * ``merge`` is **copy-if-missing on every path**. A failure part-way leaves the home
      with some of the archive's absent stores present and the rest not; nothing the
      home already had is ever touched, so a partial merge is a *subset* of a complete
      one and re-running it is safe and idempotent. There is no hybrid to be left in.
    * ``replace`` moves each live path into ``pre-restore-<ts>/`` **before** writing the
      incoming one (`snapshot._do_replace`), so a failure part-way leaves the displaced
      originals on disk under that directory and the summary reports its path. That
      directory is the recovery: it is removed only when it ends up empty, i.e. only
      when nothing was displaced. A replace that dies mid-way is therefore recoverable
      by hand, which is the honest guarantee — a "hybrid home" with no trace of what it
      replaced is what this ordering exists to prevent.
    """
    pc = _pc_dir()
    summary: dict = {"mode": mode, "items": []}

    with tempfile.TemporaryDirectory() as work_str:
        work = Path(work_str)

        with zipfile.ZipFile(str(zip_path), "r") as zf:
            for info in zf.infolist():
                parts = PurePosixPath(info.filename).parts
                if ".." in parts or info.filename.startswith("/"):
                    continue
                zf.extract(info, work)

        snap_dirs = [d for d in work.iterdir() if d.is_dir()]
        if len(snap_dirs) != 1:
            raise ValueError(f"Expected 1 top-level directory in zip, found {len(snap_dirs)}")
        snap = snap_dirs[0]

        # 🔴 STRIP `secret ∪ derived` FROM THE STAGED ARCHIVE, IN BOTH MODES, BEFORE
        # ANYTHING READS IT. Our own exports cannot contain either — but an import zip is
        # attacker-or-accident-supplied, and the amendment's belt-and-suspenders rule is
        # explicit: merge mode must never write a `secret=True` entry "even if a
        # hand-built archive contains one". Doing it once here rather than per-branch is
        # what makes it true of the FOURTH hand-written list too, which is where the
        # per-branch version of this rule kept being forgotten.
        stripped = _strip_excluded_from_staged(snap)
        if stripped:
            logger.warning(
                "import: refused %d secret/derived path(s) present in the archive: %s",
                len(stripped),
                ", ".join(stripped[:6]),
            )
            summary["refused"] = stripped

        if mode == "replace":
            # Strip skills/auto/ from snapshot before replace (secret/derived already gone).
            auto_dir = snap / "skills" / "auto"
            if auto_dir.is_dir():
                shutil.rmtree(str(auto_dir))
            before = {p.name for p in pc.glob("pre-restore-*") if p.is_dir()}
            try:
                _do_replace(snap, pc, None)
            finally:
                # Report the escape hatch whether the replace finished or raised — a
                # half-done replace is exactly when the user needs to be told where
                # their displaced state went.
                new = sorted(
                    p.name for p in pc.glob("pre-restore-*") if p.is_dir() and p.name not in before
                )
                if new:
                    summary["pre_restore"] = new[-1]
            summary["items"].append("full replace")
        else:
            # Merge mode
            # `memory_index.db` is NOT copied alongside: it is `derived=True`, so
            # `_strip_excluded_from_staged` has already removed it from the archive. The
            # index rebuilds from `memory.db`; restoring a *stale* one beside a newer
            # store is the failure mode the inventory's derived flag exists to prevent.
            if (snap / "memory.db").is_file():
                if not (pc / "memory.db").is_file():
                    shutil.copy2(str(snap / "memory.db"), str(pc / "memory.db"))
                    summary["items"].append("memory (copied)")
                else:
                    _merge_memory(snap / "memory.db", pc / "memory.db")
                    summary["items"].append("memory (merged)")

            # Staging is append-only and prunable: copy it when absent, never merge.
            # Merging two capture logs would double-count evidence occurrences, and
            # the evidence floor (`learning.min_evidence`) is what decides whether a
            # pattern is real — inflating it would manufacture proposals from a
            # restore rather than from the user's actual behaviour.
            if (snap / "learning.db").is_file() and not (pc / "learning.db").is_file():
                shutil.copy2(str(snap / "learning.db"), str(pc / "learning.db"))
                summary["items"].append("learning staging (copied)")

            if (snap / "triggers.json").is_file():
                if (pc / "triggers.json").is_file():
                    _merge_triggers(snap / "triggers.json", pc / "triggers.json")
                    summary["items"].append("automations (merged)")
                else:
                    shutil.copy2(str(snap / "triggers.json"), str(pc / "triggers.json"))
                    summary["items"].append("automations (copied)")

            if (snap / "event_triggers.json").is_file():
                if (pc / "event_triggers.json").is_file():
                    _merge_event_triggers(snap / "event_triggers.json", pc / "event_triggers.json")
                    summary["items"].append("event triggers (merged)")
                else:
                    shutil.copy2(str(snap / "event_triggers.json"), str(pc / "event_triggers.json"))
                    summary["items"].append("event triggers (copied)")

            if (snap / "crons.json").is_file():
                if (pc / "crons.json").is_file():
                    _merge_crons(snap / "crons.json", pc / "crons.json")
                    summary["items"].append("crons (merged)")
                else:
                    shutil.copy2(str(snap / "crons.json"), str(pc / "crons.json"))
                    summary["items"].append("crons (copied)")

            if (snap / "hooks.json").is_file():
                if not (pc / "hooks.json").is_file():
                    shutil.copy2(str(snap / "hooks.json"), str(pc / "hooks.json"))
                    summary["items"].append("hooks (copied)")
                else:
                    summary["items"].append("hooks (skipped, already exists)")

            if (snap / "config.json").is_file() and not (pc / "config.json").is_file():
                shutil.copy2(str(snap / "config.json"), str(pc / "config.json"))
                summary["items"].append("config (restored)")

            if (snap / "notifications.jsonl").is_file():
                if (pc / "notifications.jsonl").is_file():
                    _merge_notifications(snap / "notifications.jsonl", pc / "notifications.jsonl")
                    summary["items"].append("notifications (merged)")
                else:
                    shutil.copy2(str(snap / "notifications.jsonl"), str(pc / "notifications.jsonl"))
                    summary["items"].append("notifications (copied)")

            # Feedback records (append-only; supersede-by-target makes ordering
            # forgiving): restore only when absent — merging two instances'
            # verdict streams is not meaningful.
            if (snap / "feedback.jsonl").is_file() and not (pc / "feedback.jsonl").is_file():
                shutil.copy2(str(snap / "feedback.jsonl"), str(pc / "feedback.jsonl"))
                summary["items"].append("feedback (restored)")

            # `cron-history` joins the merged trees (S113). Export carried it and import ignored it
            # — driven on a round trip, so a restored home showed its automations with an EMPTY
            # run history: "never ran" for automations that have run for months, which is
            # indistinguishable from a broken fire path.
            #
            # NO-OVERWRITE is the right merge for this ledger, not append: each file is one job's
            # JSONL, and concatenating two homes' rows would double-count runs that
            # `_last_run_status` and the autopause counters read. A job this home already has keeps
            # its own history; a job arriving from the snapshot brings its own.
            for dirname in ("workspace", "cron-history"):
                sd = snap / dirname
                if sd.is_dir():
                    dd = pc / dirname
                    dd.mkdir(parents=True, exist_ok=True)
                    _copy_tree_no_overwrite(sd, dd)
                    summary["items"].append(f"{dirname} (merged)")

            if (snap / "skills").is_dir():
                (pc / "skills").mkdir(parents=True, exist_ok=True)
                # Skip skills/auto/ — those must go through SkillsLoader APIs
                for item in (snap / "skills").iterdir():
                    if item.name == "auto":
                        continue
                    target = pc / "skills" / item.name
                    if item.is_dir() and not target.exists():
                        shutil.copytree(str(item), str(target))
                    elif item.is_file() and not target.exists():
                        shutil.copy2(str(item), str(target))

            # 🔴 EVERY REMAINING DECLARED STORE (S182). Widening the EXPORT is only half a round
            # trip: driven end to end, an export carrying `tasks/`, `projects/` and `inbox.json`
            # imported **none of them**, because this branch is a fourth hand-written list — the
            # same
            # shape as the export's own, and the same defect. The blocks above are kept as-is: each
            # encodes a per-entry decision (learning.db copy-only so evidence is not double-counted,
            # feedback copy-only, cron-history no-overwrite) that a generic pass would erase.
            #
            # Copy-if-missing, matching `_copy_tree_no_overwrite` above: an import must not
            # overwrite
            # state the receiving home already has. The snapshot restore path owns the richer
            # per-store merges; an import is the conservative direction because the archive came
            # from
            # somewhere else.
            imported_stores = 0
            for entry in _remaining_export_paths(snap):
                sp, dp = snap / entry, pc / entry
                if sp.is_dir():
                    dp.mkdir(parents=True, exist_ok=True)
                    _copy_tree_no_overwrite(sp, dp)
                    imported_stores += 1
                elif sp.is_file() and not dp.exists():
                    dp.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(sp), str(dp))
                    imported_stores += 1
            if imported_stores:
                summary["items"].append(f"{imported_stores} stores (merged)")
                summary["items"].append("skills (merged, auto/ skipped)")

    return summary
