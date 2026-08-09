"""Portable zip export/import for PersonalClaw state (dashboard endpoint).

Creates a zip archive of all PersonalClaw settings and memory for download
via the dashboard, and restores from uploaded zip archives. Designed to
work over HTTP for remote users (e.g. remote Linux server → local browser).

Credentials (.env, session secrets) are always excluded from exports.
"""

import io
import json
import logging
import os
import shutil
import socket
import tempfile
import zipfile
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
        ".local_secret",
        "sel_hmac.key",
        "telemetry_salt",
        "session_map.json",
    }
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


def _pc_dir() -> Path:
    return Path(os.environ.get("PERSONALCLAW_HOME", config_dir()))


def _is_excluded(rel_path: PurePosixPath) -> bool:
    if rel_path.name in EXPORT_EXCLUDE:
        return True
    if rel_path.name.endswith(".pid"):
        return True
    for part in rel_path.parts:
        if part in EXCLUDE_DIRS:
            return True
    return False


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


def _remaining_export_paths(pc: Path) -> list[str]:
    """Declared entries the hand-written export lists do not already carry (S182).

    Derived from `durability.inventory.export_entries()` — which excludes `secret=True` and
    `derived=True` by construction, so a credential cannot arrive here by being newly declared. The
    three literal lists in `create_export_zip` are subtracted rather than replaced: they encode
    per-entry reasons (the safe sqlite backup API for the databases, the `skills/auto` skip, the
    `crons.json` note) that a generic pass would lose.

    Databases are deliberately NOT returned. They are already staged through `_backup_sqlite`, and a
    filesystem copy of a live WAL store can capture a torn page set — the hazard the snapshot path
    fixed by routing every declared DB through the backup API.
    """
    from personalclaw.durability import inventory as inv

    already = {
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


def create_export_zip() -> tuple[bytes, dict]:
    """Create a zip archive of PersonalClaw state. Returns (zip_bytes, manifest_dict)."""
    pc = _pc_dir()
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    prefix = f"personalclaw-export-{ts}"

    _wal_checkpoint(pc / "memory.db")
    _wal_checkpoint(pc / "memory_index.db")
    _wal_checkpoint(pc / "learning.db")

    buf = io.BytesIO()
    contents_summary: dict = {}

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
        try:
            from personalclaw.durability import inventory as _inv

            _export_paths = {e.path for e in _inv.export_entries()}
            db_names: list[str] = ["memory.db", "memory_index.db", "learning.db"]
            db_names += sorted(
                e.path
                for e in _inv.sqlite_entries()
                if e.path in _export_paths and e.path not in db_names
            )
        except Exception:  # noqa: BLE001 — an export must work even if this import breaks
            db_names = ["memory.db", "memory_index.db", "learning.db"]
        for db_name in db_names:
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
            src_dir = pc / dirname
            count = 0
            if src_dir.is_dir():
                for fpath in src_dir.rglob("*"):
                    if fpath.is_symlink():
                        continue
                    rel = fpath.relative_to(pc)
                    if _is_excluded(PurePosixPath(str(rel))):
                        continue
                    if is_sensitive_path(str(fpath)):
                        continue
                    if dirname == "skills" and "auto" in rel.parts:
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
        for entry in _remaining_export_paths(pc):
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
                    if _is_excluded(PurePosixPath(str(rel))) or is_sensitive_path(str(fpath)):
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

        # Manifest
        manifest = {
            "version": 2,
            "format": "zip",
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "hostname": socket.gethostname(),
            "user": os.environ.get("USER", "unknown"),
            "contents": contents_summary,
        }
        zf.writestr(f"{prefix}/MANIFEST.json", json.dumps(manifest, indent=2))

    return buf.getvalue(), manifest


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
            if version not in (1, 2):
                return False, f"Unsupported manifest version: {version}", {}

            return True, "", manifest_data
    except zipfile.BadZipFile:
        return False, "Invalid zip file", {}
    except (json.JSONDecodeError, KeyError) as e:
        return False, f"Invalid manifest: {e}", {}


def apply_import_zip(zip_path: Path, mode: str = "merge") -> dict:
    """Extract and apply an import zip.

    Args:
        zip_path: Path to validated zip file.
        mode: "merge" (default, non-destructive) or "replace" (overwrites).

    Returns summary dict of what was imported.
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

        if mode == "replace":
            # Strip sensitive files and skills/auto/ from snapshot before replace
            for excluded_name in EXPORT_EXCLUDE:
                excluded_file = snap / excluded_name
                if excluded_file.exists():
                    excluded_file.unlink()
            for fpath in snap.rglob("*"):
                if fpath.is_file() and is_sensitive_path(str(fpath)):
                    fpath.unlink()
            auto_dir = snap / "skills" / "auto"
            if auto_dir.is_dir():
                shutil.rmtree(str(auto_dir))
            _do_replace(snap, pc, None)
            summary["items"].append("full replace")
        else:
            # Merge mode
            if (snap / "memory.db").is_file():
                if not (pc / "memory.db").is_file():
                    shutil.copy2(str(snap / "memory.db"), str(pc / "memory.db"))
                    if (snap / "memory_index.db").is_file():
                        shutil.copy2(str(snap / "memory_index.db"), str(pc / "memory_index.db"))
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
