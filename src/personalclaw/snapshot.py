"""PersonalClaw snapshot and restore — portable state management."""

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from personalclaw.atomic_write import atomic_write

try:
    import pysqlite3 as sqlite3
except ImportError:
    import sqlite3

VALID_COMPONENTS = ("memory", "crons", "config", "skills", "workspace", "notifications", "security")


def _data_filter(info: tarfile.TarInfo, _dest: str = "") -> tarfile.TarInfo | None:
    """Equivalent to tarfile ``"data"`` filter (Python 3.12+), with 3.10 fallback.

    Also rejects path traversal, symlinks, and hardlinks to eliminate TOCTOU
    race between pre-scan and extraction.
    """
    # Reject path traversal
    if ".." in PurePosixPath(info.name).parts or info.name.startswith("/"):
        print(f"⚠️  Rejecting path traversal entry: {info.name}")
        return None
    # Reject symlinks and hardlinks
    if info.issym() or info.islnk():
        print(f"⚠️  Rejecting symlink/hardlink entry: {info.name}")
        return None
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.mode = 0o755 if info.isdir() else 0o644
    return info


def _default_snapshot_dir() -> str:
    """The snapshot output directory: config's ``snapshot_dir``, else
    ``<home>/snapshots``.

    The fallback resolves the ACTIVE home (``PERSONALCLAW_HOME`` when set), not a
    hardcoded ``~/.personalclaw``. Without that, snapshotting an isolated home
    wrote its archive into the real one — surprising, and it mixes two installs'
    backups in the directory that retention pruning then walks.
    """
    try:
        from personalclaw.config.loader import AppConfig

        d = AppConfig.load().snapshot_dir
        if d:
            return str(Path(d).expanduser())
    except Exception:
        pass
    return str(_pc_dir() / "snapshots")


def _audit(event_type: str, resources: str) -> None:
    """Emit a SEL audit event for snapshot/restore operations."""
    try:
        from personalclaw.sel import SecurityEvent, sel

        sel().log(
            SecurityEvent(
                event_id=os.urandom(8).hex(),
                timestamp=datetime.now(timezone.utc).isoformat(),
                event_type=event_type,
                caller_identity=os.environ.get("USER", "unknown"),
                agent="personalclaw",
                source="cli",
                operation=event_type,
                outcome="completed",
                resources=resources,
            )
        )
    except Exception as e:
        import logging

        logging.getLogger(__name__).warning("SEL audit event '%s' failed: %s", event_type, e)


CORE_FILES: dict[str, tuple[str, ...]] = {
    "memory": ("memory.db", "memory_index.db"),
    # 🔴 `triggers.json` + `event_triggers.json` (S113). This component held `crons.json` ALONE —
    # the legacy file, which nothing has written since S108 and which S112's deletion left as a
    # read-only migration source. So `personalclaw snapshot` backed up an empty relic and dropped
    # every automation the user actually had. `crons.json` still travels because §6 keeps it
    # read-only for `automation verify-migration` to diff.
    "crons": ("crons.json", "triggers.json", "event_triggers.json"),
    "config": ("config.json", "session_map.json", "hooks.json", "project_dir", "workspace_dir"),
    "notifications": ("notifications.jsonl",),
    "security": ("sel_hmac.key", "telemetry_salt"),
}


def _declared_db_paths() -> tuple[str, ...]:
    """Home-relative paths of every database the inventory declares.

    A live sqlite database must be copied with the backup API, never by a
    filesystem copy: the gateway holds these open in WAL mode, so `copy2`/
    `copytree` can capture a torn page set. Before the inventory, only the two
    files in ``CORE_FILES["memory"]`` got the safe path — `knowledge.db`,
    `lexicon.db`, and `loops.db` were inside tree copies and got raw-copied.
    """
    try:
        from personalclaw.durability import inventory as inv

        return tuple(e.path for e in inv.sqlite_entries())
    except Exception:  # noqa: BLE001 — snapshot must work even if this import breaks
        return ("memory.db", "memory_index.db")


def _safe_copy_db(src: Path, dst: Path) -> bool:
    """Copy one sqlite file consistently via the backup API. False if it isn't a
    readable database (caller falls back to a plain copy)."""
    from contextlib import closing

    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        with (
            closing(sqlite3.connect(str(src))) as src_conn,
            closing(sqlite3.connect(str(dst))) as dst_conn,
        ):
            src_conn.backup(dst_conn)
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️  sqlite backup failed for {src.name} ({exc}); falling back to a file copy")
        return False


def _tree_ignore_dbs(db_names: set[str]):
    """A copytree `ignore` that skips database files, so a tree copy never
    raw-copies a live DB — the safe backup-API pass handles those separately."""

    def _ignore(directory: str, contents: list[str]) -> set[str]:
        return {n for n in contents if n in db_names or n.endswith((".db-wal", ".db-shm"))}

    return _ignore


def _everything_paths(pc: Path) -> list[str]:
    """Home-relative paths the ``everything`` component adds (DURABILITY §1).

    Derived from :mod:`personalclaw.durability.inventory` — the single manifest of
    what PersonalClaw's state IS — minus what the named components already stage
    and minus rebuildable indexes. Before the inventory existed, nine real store
    directories (tasks, projects, loop, artifacts, prompts, workflows, agents,
    apps, entity_settings) were covered by NEITHER snapshot nor export; this is
    the projection that closes that gap without a second hand-written allowlist.
    """
    from personalclaw.durability import inventory as inv

    already = {f for files in CORE_FILES.values() for f in files}
    already |= {"workspace", "plan_memory", "skills"}  # staged as trees below
    out: list[str] = []
    for entry in inv.backup_entries():
        top = entry.path.split("/", 1)[0]
        if top in already or entry.path in out:
            continue
        if (pc / entry.path).exists():
            out.append(entry.path)
    return out


COMPONENT_HELP = {
    "memory": "memory.db, memory_index.db (semantic, episodic, knowledge graph)",
    "crons": "triggers.json + event_triggers.json + crons.json (automations)",
    "config": "config.json, session_map.json, hooks.json, project_dir, workspace_dir",
    "skills": "skills/ directory",
    "workspace": "workspace/, plan_memory/ directories",
    "notifications": "notifications.jsonl (notification history)",
    "security": "sel_hmac.key, telemetry_salt",
}


def _pc_dir() -> Path:
    from .config.loader import config_dir

    return Path(os.environ.get("PERSONALCLAW_HOME", config_dir()))


def _fsize(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def _want(components: list[str] | None, name: str) -> bool:
    return components is None or name in components


def _list_components() -> None:
    print("Available components:")
    for k, v in COMPONENT_HELP.items():
        print(f"  {k:16s} {v}")
    print("\nCombine with commas: --components memory,crons,skills")


def _copytree_safe(src: Path, dst: Path, **kwargs) -> None:
    """copytree that skips symlinks to prevent sensitive file leakage."""
    outer_ignore = kwargs.pop("ignore", None)

    def _ignore_symlinks(directory, contents):
        skipped = {name for name in contents if os.path.islink(os.path.join(directory, name))}
        for name in skipped:
            print(f"⚠️  Skipping symlink in source tree: {os.path.join(directory, name)}")
        if outer_ignore:
            skipped |= set(outer_ignore(directory, contents))
        return skipped

    shutil.copytree(str(src), str(dst), ignore=_ignore_symlinks, **kwargs)


def _copy_tree_no_overwrite(src: Path, dst: Path) -> None:
    for item in src.rglob("*"):
        if item.is_symlink():
            continue
        target = dst / item.relative_to(src)
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif item.is_file() and not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(item), str(target))


# ── Snapshot ──────────────────────────────────────────────────────────────────


def snapshot_main(
    argv: list[str] | None = None, *, parsed: argparse.Namespace | None = None
) -> int:
    if parsed is None:
        p = argparse.ArgumentParser(
            prog="personalclaw-snapshot",
            description="Create a portable .tar.gz snapshot of PersonalClaw state.",
        )
        p.add_argument("output_dir", nargs="?", default=_default_snapshot_dir())
        p.add_argument("--keep", type=int, default=7)
        p.add_argument("--list", action="store_true", dest="list_snapshots")
        parsed = p.parse_args(argv)
    args = parsed

    if args.keep <= 0:
        print(f"❌ --keep value must be a positive integer, got: {args.keep}")
        return 1

    out = Path(args.output_dir or _default_snapshot_dir())

    if args.list_snapshots:
        if not out.is_dir():
            print(f"No snapshots found in {out}")
            return 0
        snaps = sorted(
            out.glob("personalclaw-snapshot-*.tar.gz"),
            key=lambda x: x.stat().st_mtime,
            reverse=True,
        )
        for s in snaps:
            print(s)
        if not snaps:
            print(f"No snapshots found in {out}")
        return 0

    pc = _pc_dir()
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"personalclaw-snapshot-{ts}"

    # Pre-flight size estimate
    if pc.is_dir():
        total_bytes = sum(
            f.stat().st_size for f in pc.rglob("*") if f.is_file() and not f.is_symlink()
        )
        total_mb = total_bytes / (1024 * 1024)
        if total_mb > 500:
            print(f"⚠️  ~/.personalclaw is {total_mb:.0f} MB — snapshot may be large and slow")

    # WAL checkpoint every DECLARED database, not just memory.db. The inventory is
    # the source of truth for which files are databases, so a store added later is
    # checkpointed automatically instead of being missed.
    for _db in _declared_db_paths():
        if (pc / _db).is_file():
            try:
                from contextlib import closing

                with closing(sqlite3.connect(str(pc / _db))) as c:
                    c.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            except Exception:
                print(
                    f"⚠️  WAL checkpoint failed for {_db} (may be locked by the "
                    "gateway). The backup API still produces a consistent copy."
                )

    with tempfile.TemporaryDirectory() as work:
        stage = Path(work) / name
        for d in ("workspace", "skills", "plan_memory"):
            (stage / d).mkdir(parents=True, exist_ok=True)

        # Core files
        for files in CORE_FILES.values():
            for f in files:
                src = pc / f
                if src.is_file():
                    if os.path.islink(src):
                        print(f"⚠️  Skipping symlinked core file: {src}")
                        continue
                    if f.endswith(".db"):
                        from contextlib import closing

                        with (
                            closing(sqlite3.connect(str(src))) as src_conn,
                            closing(sqlite3.connect(str(stage / f))) as dst_conn,
                        ):
                            src_conn.backup(dst_conn)
                    else:
                        shutil.copy2(str(src), str(stage / f))

        # Every DECLARED database, copied consistently via the sqlite backup API.
        # This runs BEFORE the tree copies (which skip *.db, see _tree_ignore_dbs)
        # so a live database is never captured as a raw file. Fixes the
        # knowledge.db / lexicon.db / loops.db raw-copy hazard.
        _db_paths = _declared_db_paths()
        _db_names = {PurePosixPath(p).name for p in _db_paths}
        for _db in _db_paths:
            _src_db = pc / _db
            if not _src_db.is_file() or os.path.islink(_src_db):
                continue
            if not _safe_copy_db(_src_db, stage / _db):
                (stage / _db).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(_src_db), str(stage / _db))

        # Workspace (exclude hygiene_data, insert_facts*.py, and any database —
        # those were staged above through the backup API).
        if (pc / "workspace").is_dir():
            _pattern_ignore = shutil.ignore_patterns("hygiene_data", "insert_facts*.py")

            def _ws_ignore(directory: str, contents: list[str]) -> set[str]:
                return set(_pattern_ignore(directory, contents)) | _tree_ignore_dbs(_db_names)(
                    directory, contents
                )

            _copytree_safe(
                pc / "workspace",
                stage / "workspace",
                dirs_exist_ok=True,
                ignore=_ws_ignore,
            )

        # Plan memory
        if (pc / "plan_memory").is_dir():
            _copytree_safe(pc / "plan_memory", stage / "plan_memory", dirs_exist_ok=True)

        # Skills
        if (pc / "skills").is_dir():
            _copytree_safe(pc / "skills", stage / "skills", dirs_exist_ok=True)

        # THE GAP CLOSURE (DURABILITY §1): every remaining inventory entry. Before
        # this, tasks/, projects/, loop/, artifacts/, prompts/, workflows/,
        # agents/, apps/ and entity_settings/ were in NEITHER the snapshot nor the
        # export — a "full backup" that silently dropped a user's whole task board.
        # Driven off the inventory so a store added later is captured by default.
        staged_extra: list[str] = []
        for rel in _everything_paths(pc):
            src = pc / rel
            if os.path.islink(src):
                print(f"⚠️  Skipping symlinked state path: {rel}")
                continue
            if src.is_dir():
                _copytree_safe(
                    src, stage / rel, dirs_exist_ok=True, ignore=_tree_ignore_dbs(_db_names)
                )
                staged_extra.append(rel)
            elif src.is_file():
                (stage / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src), str(stage / rel))
                staged_extra.append(rel)

        # Manifest
        ws_files = sum(1 for _ in (stage / "workspace").rglob("*") if _.is_file())
        pm_files = sum(1 for _ in (stage / "plan_memory").rglob("*") if _.is_file())
        sk_count = sum(1 for _ in (stage / "skills").iterdir() if _.is_dir())
        manifest = {
            "version": 2,
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "hostname": socket.gethostname(),
            "user": os.environ.get("USER", "unknown"),
            "personalclaw_dir": str(pc),
            "contents": {
                "memory_db": _fsize(stage / "memory.db"),
                "memory_index_db": _fsize(stage / "memory_index.db"),
                "crons_json": _fsize(stage / "crons.json"),
                "triggers_json": _fsize(stage / "triggers.json"),
                "event_triggers_json": _fsize(stage / "event_triggers.json"),
                "config_json": _fsize(stage / "config.json"),
                "notifications_jsonl": _fsize(stage / "notifications.jsonl"),
                "workspace_files": ws_files,
                "plan_memory_files": pm_files,
                "skill_count": sk_count,
            },
        }
        (stage / "MANIFEST.json").write_text(json.dumps(manifest, indent=2))

        # Tarball — write to temp file and rename atomically to avoid corrupt partials
        out.mkdir(parents=True, exist_ok=True)
        outfile = out / f"{name}.tar.gz"
        tmp_tar = outfile.with_suffix(".tar.gz.tmp")
        try:
            with tarfile.open(str(tmp_tar), "w:gz") as tar:
                tar.add(str(stage), arcname=name, filter=_data_filter)
            tmp_tar.rename(outfile)
        except BaseException:
            tmp_tar.unlink(missing_ok=True)
            raise

        has_hmac_key = (stage / "sel_hmac.key").exists()

    sz = outfile.stat().st_size
    os.chmod(str(outfile), 0o600)  # contains sel_hmac.key — restrict access
    human = f"{sz // 1024}K" if sz < 1024 * 1024 else f"{sz / 1024 / 1024:.1f}M"
    print(f"✅ Snapshot created: {outfile} ({human})")
    if has_hmac_key:
        print(
            "⚠️  Snapshot contains sel_hmac.key — treat this file as sensitive. "
            "An attacker with access to it could forge SEL audit entries."
        )

    _audit("snapshot_created", f"{outfile} ({human})")

    # Prune
    snaps = sorted(
        out.glob("personalclaw-snapshot-*.tar.gz"), key=lambda x: x.stat().st_mtime, reverse=True
    )
    for old in snaps[args.keep :]:
        old.unlink()
        print(f"🗑  Pruned: {old.name}")

    remaining = len(list(out.glob("personalclaw-snapshot-*.tar.gz")))
    print(f"📦 Snapshots in {out}: {remaining} (keep={args.keep})")
    return 0


# ── Restore ───────────────────────────────────────────────────────────────────


def _print_manifest(snap: Path) -> None:
    mf = snap / "MANIFEST.json"
    if not mf.is_file():
        return
    try:
        m = json.loads(mf.read_text())
        print("📋 Snapshot info:")
        print(f"  Created: {m.get('created_at', 'unknown')}")
        print(f"  From: {m.get('user', 'unknown')}@{m.get('hostname', 'unknown')}")
        c = m.get("contents", {})
        print(f"  Memory DB: {c.get('memory_db', 0) // 1024} KB")
        _auto_kb = (
            c.get("triggers_json", 0) + c.get("event_triggers_json", 0) + c.get("crons_json", 0)
        ) // 1024
        print(f"  Automations: {_auto_kb} KB")
        print(f"  Workspace files: {c.get('workspace_files', 0)}")
        print(f"  Skills: {c.get('skill_count', 0)}")
        print(f"  Notifications: {c.get('notifications_jsonl', 0) // 1024} KB")
        print(f"  Plan memory files: {c.get('plan_memory_files', 0)}")
    except Exception as e:
        print(f"  (Could not read manifest: {e})")


_MERGE_ALLOWED_TABLES = frozenset(
    {
        "semantic_memory",
        "episodic_memories",
        "knowledge_facts",
        "knowledge_edges",
    }
)
_SAFE_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _both_have(conn: "sqlite3.Connection", table: str, column: str) -> bool:
    """True when *column* exists on *table* in BOTH the destination and attached source.

    A snapshot taken before a column was added genuinely does not have it, and a merge
    that names a column either side lacks fails the whole table. Checked rather than
    assumed so restoring an older snapshot keeps working.
    """
    for prefix in ("", "src."):
        try:
            cols = {r[1] for r in conn.execute(f"PRAGMA {prefix}table_info({table})").fetchall()}
        except sqlite3.Error:
            return False
        if column not in cols:
            return False
    return True


def _validate_identifier(name: str) -> str:
    """Validate a SQL identifier against allowlist pattern. Raises ValueError if invalid."""
    if not _SAFE_IDENTIFIER_RE.match(name):
        raise ValueError(f"Invalid SQL identifier: {name!r}")
    return name


def _merge_memory(src_db: Path, dst_db: Path) -> None:
    # Integrity check on source DB before ATTACH
    try:
        with sqlite3.connect(str(src_db)) as check_conn:
            result = check_conn.execute("PRAGMA integrity_check;").fetchone()[0]
        if result != "ok":
            print(f"  ⚠️  Source DB integrity check failed: {result} — skipping merge")
            return
    except Exception as e:
        print(f"  ⚠️  Source DB unreadable: {e} — skipping merge")
        return

    conn = sqlite3.connect(str(dst_db))
    conn.execute("BEGIN")
    attached = False
    try:
        conn.execute("ATTACH DATABASE ? AS src", (str(src_db),))
        attached = True

        # `contributor` (TEAM-SHARED-ENTITIES §2.3) is included only when BOTH databases
        # carry it. Naming it unconditionally made every pre-v9 snapshot fail its whole
        # memory merge — SQLite raises on the missing source column, the handler below
        # logs and SKIPS the table, and the restore reported "imported: 0" while looking
        # like it worked. A restore that silently drops all memory is far worse than one
        # that drops a provenance column, so the column is opportunistic, not required.
        def _with_contributor(base: str, table: str) -> str:
            return f"{base}, contributor" if _both_have(conn, table, "contributor") else base

        for table, cols, where in [
            (
                "semantic_memory",
                _with_contributor(
                    "key, value_json, confidence, source, created_at, updated_at, embedding",
                    "semantic_memory",
                ),
                "WHERE is_deleted=0",
            ),
            (
                "episodic_memories",
                _with_contributor(
                    "id, conversation_id, text, embedding, tags, importance, created_at, "
                    "last_accessed_at",
                    "episodic_memories",
                ),
                "WHERE is_deleted=0",
            ),
            ("knowledge_facts", "subject, predicate, object, episode_id, created_at", ""),
            (
                "knowledge_edges",
                "source_key, target_key, relation, weight, metadata, created_at",
                "",
            ),
        ]:
            if table not in _MERGE_ALLOWED_TABLES:
                raise ValueError(f"Table {table!r} not in merge allowlist")
            for col in cols.split(", "):
                _validate_identifier(col.strip())
            try:
                before = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                conn.execute(
                    f"INSERT OR IGNORE INTO {table} ({cols}) "
                    f"SELECT {cols} FROM src.{table} {where}"
                )
                after = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                label = table.replace("_", " ").title()
                print(f"  {label} imported: {after - before}")
            except sqlite3.OperationalError as e:
                import logging

                logging.getLogger(__name__).warning("Skipping table %s: %s", table, e)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        if attached:
            try:
                conn.execute("DETACH DATABASE src")
            except Exception:
                pass
        conn.close()


def _merge_crons(src_path: Path, dst_path: Path) -> None:
    src = json.loads(src_path.read_text())
    dst = json.loads(dst_path.read_text())
    existing = {j.get("name") for j in dst.get("jobs", [])}
    imported = 0
    for job in src.get("jobs", []):
        name = job.get("name")
        if not name or name in existing:
            continue
        job["id"] = hashlib.md5(f"{name}-imported".encode(), usedforsecurity=False).hexdigest()[:8]
        dst.setdefault("jobs", []).append(job)
        imported += 1
    atomic_write(dst_path, json.dumps(dst, indent=2))
    total = len(src.get("jobs", []))
    print(f"  Cron jobs imported: {imported} (skipped {total - imported} duplicates)")


def _merge_triggers(src_path: Path, dst_path: Path) -> None:
    """Merge an imported `triggers.json` into the live one, skipping duplicates by NAME.

    🔴 THE DEFECT THIS CLOSES (S113). `create_export_zip` carried `crons.json` and `hooks.json` and
    NOT `triggers.json` — the store that has been the sole source of automations since S101, and
    the only one since S112 deleted `ScheduleService`. Driven against a home holding two
    automations, an event trigger and run history, the snapshot captured **`config.json` alone**.
    So `personalclaw snapshot` silently lost every automation the user had — and the release notes
    advise taking one before a breaking upgrade, the one moment it must not lose anything.

    Skip-by-NAME with a fresh id, mirroring `_merge_crons`: an id collision between two homes is
    meaningless (ids are slugs), while a name collision means the user already has that automation
    and a second copy would fire it twice.

    🔴 RUNTIME STATE IS DROPPED, deliberately. `next_fire_at` from another machine is a fire that was
    already scheduled elsewhere, and `run_count`/`last_success_at`/health describe runs this home
    never performed. An imported trigger arrives UNARMED and the boot sweep arms it here — which is
    also why importing cannot resurrect a fire that should have happened during the move.
    """
    from personalclaw.triggers.store import RUNTIME_FIELDS

    src = json.loads(src_path.read_text())
    dst = json.loads(dst_path.read_text())
    existing_names = {str(t.get("name") or "") for t in dst.get("triggers", [])}
    existing_ids = {str(t.get("id") or "") for t in dst.get("triggers", [])}
    imported = 0
    for trigger in src.get("triggers", []):
        name = str(trigger.get("name") or "")
        if not name or name in existing_names:
            continue
        row = dict(trigger)
        for field in RUNTIME_FIELDS:
            row.pop(field, None)
        base = str(row.get("id") or "") or "imported"
        candidate = base
        n = 2
        while candidate in existing_ids:
            candidate = f"{base}-{n}"
            n += 1
        row["id"] = candidate
        # An imported automation must not fire until this home has armed it.
        row["enabled"] = False
        existing_ids.add(candidate)
        existing_names.add(name)
        dst.setdefault("triggers", []).append(row)
        imported += 1
    atomic_write(dst_path, json.dumps(dst, indent=2))
    total = len(src.get("triggers", []))
    print(
        f"  Automations imported: {imported} (skipped {total - imported} duplicates) "
        f"— imported rows arrive PAUSED; review and enable them"
    )


def _merge_event_triggers(src_path: Path, dst_path: Path) -> None:
    """Merge `event_triggers.json`, skipping duplicates by PATTERN.

    Carried for the same reason as the trigger store, and named in the plan's own recon note
    ("today snapshot covers crons.json/hooks.json but NOT event_triggers.json"). An event trigger
    has no name field, so the pattern is its identity.
    """
    src = json.loads(src_path.read_text())
    dst = json.loads(dst_path.read_text())
    src_rows = src if isinstance(src, list) else src.get("triggers", [])
    dst_rows = dst if isinstance(dst, list) else dst.get("triggers", [])
    existing = {str(t.get("pattern") or "") for t in dst_rows}
    imported = 0
    for trigger in src_rows:
        pattern = str(trigger.get("pattern") or "")
        if not pattern or pattern in existing:
            continue
        row = dict(trigger)
        row["enabled"] = False
        dst_rows.append(row)
        existing.add(pattern)
        imported += 1
    payload = dst_rows if isinstance(dst, list) else {**dst, "triggers": dst_rows}
    atomic_write(dst_path, json.dumps(payload, indent=2))
    total = len(src_rows)
    print(f"  Event triggers imported: {imported} (skipped {total - imported} duplicates)")


def _merge_notifications(src_path: Path, dst_path: Path) -> None:
    existing: set[str] = set()
    with open(dst_path) as f:
        for line in f:
            try:
                existing.add(json.loads(line).get("ts") or line.strip())
            except (ValueError, TypeError):
                pass
    imported = 0
    with open(dst_path, "a") as out, open(src_path) as f:
        for line in f:
            try:
                key = json.loads(line).get("ts") or line.strip()
                if key not in existing:
                    out.write(line)
                    existing.add(key)
                    imported += 1
            except (ValueError, TypeError):
                pass
    print(f"  Notifications imported: {imported}")


def _merge_run_history(src_dir: Path, dst_dir: Path) -> None:
    """Merge `cron-history/` shard-by-shard, deduping on `run_id` (S176).

    🔴 WHY THIS EXISTS. `inventory.py` declares `cron_history` with `merge=append_dedup`, and
    `_do_merge` had no branch for it — so a merge restore printed "✅ Merge complete" while
    recovering **no run history at all**. Driven: a snapshot holding `FROM-SNAPSHOT` merged into a
    home holding `LIVE-run` left only `LIVE-run`. The declared strategy had no executor, which is
    this program's signature defect in the durability layer.

    Deduped on `run_id` rather than a whole-line compare: the same run round-trips through
    `to_dict()`, so key ordering or a re-serialised float could make an identical run look new and
    double it. Mirrors `_merge_notifications`, which dedupes on `ts` for the same reason.

    Per-shard, because the store is one file per job (`clock:backup.jsonl`) plus a cross-job
    `_index.jsonl`. A shard present only in the snapshot is copied whole; one present in both is
    appended-and-deduped, so the live home never loses a row it already had.

    Deliberately does NOT rotate afterwards. `ScheduleRunStore.rotate_all()` runs at gateway boot
    (S175) and owns that policy; trimming here would apply retention twice with a second copy of the
    rule — the duplication S175 just removed.
    """
    if not src_dir.is_dir():
        return
    dst_dir.mkdir(parents=True, exist_ok=True)
    shards = imported = 0
    for src in sorted(src_dir.glob("*.jsonl")):
        dst = dst_dir / src.name
        if not dst.is_file():
            shutil.copy2(str(src), str(dst))
            shards += 1
            continue
        existing: set[str] = set()
        with open(dst) as f:
            for line in f:
                try:
                    existing.add(str(json.loads(line).get("run_id") or line.strip()))
                except (ValueError, TypeError):
                    pass
        with open(dst, "a") as out, open(src) as f:
            for line in f:
                try:
                    key = str(json.loads(line).get("run_id") or line.strip())
                except (ValueError, TypeError):
                    continue
                if key not in existing:
                    out.write(line)
                    existing.add(key)
                    imported += 1
        shards += 1
    print(f"  Run history: {shards} shard(s), {imported} row(s) imported")


def _backup_and_copy(pc: Path, backup: Path, snap: Path, component: str) -> None:
    for f in CORE_FILES.get(component, ()):
        if (pc / f).is_file():
            if os.path.islink(pc / f):
                print(f"⚠️  Skipping symlinked core file during backup: {pc / f}")
                continue
            shutil.move(str(pc / f), str(backup / f))
        if (snap / f).is_file():
            if os.path.islink(snap / f):
                print(f"⚠️  Skipping symlinked file from snapshot: {snap / f}")
                continue
            shutil.copy2(str(snap / f), str(pc / f))
            if component == "security":
                os.chmod(str(pc / f), 0o600)


def _do_replace(snap: Path, pc: Path, components: list[str] | None) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = pc / f"pre-restore-{ts}"
    backup.mkdir(exist_ok=True)
    print("🔄 Replace mode — backing up current state...")

    for comp in ("memory", "crons", "config", "notifications", "security"):
        if _want(components, comp):
            _backup_and_copy(pc, backup, snap, comp)
            print(f"  ✅ {comp}")

    if _want(components, "workspace"):
        for dirname in ("workspace", "plan_memory"):
            d = pc / dirname
            if d.is_dir():
                _copytree_safe(d, backup / dirname, dirs_exist_ok=True)
            sd = snap / dirname
            if sd.is_dir():
                if d.is_dir():
                    shutil.rmtree(str(d))
                _copytree_safe(sd, d)
        print("  ✅ workspace")

    if _want(components, "skills"):
        sk = pc / "skills"
        if sk.is_dir():
            _copytree_safe(sk, backup / "skills", dirs_exist_ok=True)
        snap_sk = snap / "skills"
        if snap_sk.is_dir():
            if sk.is_dir():
                shutil.rmtree(str(sk))
            _copytree_safe(snap_sk, sk)
        print("  ✅ skills")

    try:
        backup.rmdir()
    except OSError:
        print(f"  Previous state saved to: {backup}/")
    print("✅ Replace complete.")


def _do_merge(snap: Path, pc: Path, components: list[str] | None) -> None:
    print("🔀 Merge mode — importing...")

    if _want(components, "memory") and (snap / "memory.db").is_file():
        if not (pc / "memory.db").is_file():
            shutil.copy2(str(snap / "memory.db"), str(pc / "memory.db"))
            if (snap / "memory_index.db").is_file():
                shutil.copy2(str(snap / "memory_index.db"), str(pc / "memory_index.db"))
            print("  Memory: copied (no existing memory.db)")
        else:
            _merge_memory(snap / "memory.db", pc / "memory.db")
        print("  ✅ memory")

    if _want(components, "crons"):
        st, dt = snap / "triggers.json", pc / "triggers.json"
        if st.is_file():
            if dt.is_file():
                _merge_triggers(st, dt)
            else:
                shutil.copy2(str(st), str(dt))
                print("  Automations: copied (no existing store)")
        se, de = snap / "event_triggers.json", pc / "event_triggers.json"
        if se.is_file():
            if de.is_file():
                _merge_event_triggers(se, de)
            else:
                shutil.copy2(str(se), str(de))
                print("  Event triggers: copied (none existing)")
        sc, dc = snap / "crons.json", pc / "crons.json"
        if sc.is_file():
            if dc.is_file():
                _merge_crons(sc, dc)
            else:
                shutil.copy2(str(sc), str(dc))
                print("  Legacy crons: copied (no existing crons)")
        print("  ✅ automations")

    if _want(components, "config"):
        for f in CORE_FILES["config"]:
            s, d = snap / f, pc / f
            if s.is_file() and not d.is_file():
                shutil.copy2(str(s), str(d))
                print(f"  {f}: restored (was missing)")
        print("  ✅ config")

    # 🔴 The run history, whose declared `append_dedup` had no executor (S176). Grouped with
    # `crons` because it IS the crons' history: a merge restore that recovered the triggers but not
    # their runs leaves a user with automations and no record of what they ever did.
    if _want(components, "crons"):
        _merge_run_history(snap / "cron-history", pc / "cron-history")

    if _want(components, "notifications"):
        sn, dn = snap / "notifications.jsonl", pc / "notifications.jsonl"
        if sn.is_file():
            if dn.is_file():
                _merge_notifications(sn, dn)
            else:
                shutil.copy2(str(sn), str(dn))
                print("  Notifications: copied")
        print("  ✅ notifications")

    if _want(components, "security"):
        for f in CORE_FILES["security"]:
            s, d = snap / f, pc / f
            if s.is_file() and not d.is_file():
                shutil.copy2(str(s), str(d))
                os.chmod(str(d), 0o600)
                print(f"  {f}: restored (was missing)")
        print("  ✅ security")

    if _want(components, "workspace"):
        for dirname in ("workspace", "plan_memory"):
            sd = snap / dirname
            if sd.is_dir():
                dd = pc / dirname
                dd.mkdir(parents=True, exist_ok=True)
                _copy_tree_no_overwrite(sd, dd)
        print("  ✅ workspace")

    if _want(components, "skills"):
        if (snap / "skills").is_dir():
            (pc / "skills").mkdir(parents=True, exist_ok=True)
            _copy_tree_no_overwrite(snap / "skills", pc / "skills")
        print("  ✅ skills")

    print("✅ Merge complete.")


def _is_gateway_running() -> bool:
    """Check if the PersonalClaw gateway is listening on its dashboard port."""
    # DASHBOARD_PORT already resolves PERSONALCLAW_PORT → _DEFAULT_PORT, so this
    # is the single source of truth for the gateway port.
    from personalclaw.config.loader import DASHBOARD_PORT

    port = DASHBOARD_PORT
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def restore_main(argv: list[str] | None = None, *, parsed: argparse.Namespace | None = None) -> int:
    if parsed is None:
        p = argparse.ArgumentParser(
            prog="personalclaw-restore", description="Restore PersonalClaw state from a snapshot."
        )
        p.add_argument("snapshot", nargs="?")
        p.add_argument("--mode", choices=("replace", "merge"))
        p.add_argument("--dry-run", action="store_true")
        p.add_argument(
            "--force", action="store_true", help="Allow restore even if gateway is running"
        )
        p.add_argument("--components")
        p.add_argument("--list-components", action="store_true")
        parsed = p.parse_args(argv)
    args = parsed

    if args.list_components:
        _list_components()
        return 0

    if not args.snapshot:
        print("❌ snapshot file is required (unless --list-components is given)")
        return 1

    force = getattr(args, "force", False)
    if not force and _is_gateway_running():
        _audit("state_restore_rejected", "reason=gateway_running")
        print("❌ Gateway is running. Stop it first (personalclaw stop) or use --force.")
        return 1

    snap_path = Path(args.snapshot)
    if not snap_path.is_file():
        print(f"❌ File not found: {snap_path}")
        return 1

    # Parse components
    components: list[str] | None = None
    if args.components:
        components = [c.strip() for c in args.components.split(",")]
        for c in components:
            if c not in VALID_COMPONENTS:
                print(f"❌ Unknown component: {c}\n")
                _list_components()
                return 1

    pc = _pc_dir()
    mode = args.mode or ("merge" if (pc / "memory.db").is_file() else "replace")

    with tempfile.TemporaryDirectory() as work_str:
        work = Path(work_str)

        # Security checks are enforced inside _data_filter (no TOCTOU gap)
        with tarfile.open(str(snap_path), "r:gz") as tar:
            try:
                tar.extractall(work, filter=_data_filter)
            except TypeError:
                # Python < 3.11.4: filter param not supported, apply manually
                members = [m for m in tar.getmembers() if _data_filter(m) is not None]
                tar.extractall(work, members=members)

        snap_dirs = [
            d for d in work.iterdir() if d.is_dir() and d.name.startswith("personalclaw-snapshot-")
        ]
        if not snap_dirs:
            print("❌ Invalid snapshot format")
            return 1
        snap = snap_dirs[0]

        _print_manifest(snap)
        if components:
            print(f"🔧 Components: {','.join(components)}")

        if args.dry_run:
            print(f"\n🔍 Dry run — would restore to {pc} in {mode} mode")
            print("Files in snapshot:")
            for f in sorted(snap.rglob("*")):
                if f.is_file():
                    print(f"  {f.relative_to(snap)}")
            return 0

        pc.mkdir(parents=True, exist_ok=True)
        if mode == "replace":
            _do_replace(snap, pc, components)
        else:
            _do_merge(snap, pc, components)

    # Integrity check
    if _want(components, "memory") and (pc / "memory.db").is_file():
        try:
            with sqlite3.connect(str(pc / "memory.db")) as conn:
                result = conn.execute("PRAGMA integrity_check;").fetchone()[0]
        except Exception as e:
            result = str(e)
        if result == "ok":
            print("🔍 memory.db integrity: OK")
        else:
            print(f"⚠️  memory.db integrity check failed: {result}")
            _audit("state_restore_rejected", f"reason=integrity_check_failed from={snap_path.name}")
            return 1
        if not (pc / "memory_index.db").is_file():
            print(
                "⚠️  memory_index.db is missing — full-text search may not "
                "work until the FTS index is rebuilt."
            )

    comp_str = ",".join(components) if components else "all"
    _audit("state_restored", f"mode={mode} components={comp_str} from={snap_path.name}")

    print("\n⚠️  Restart personalclaw gateway to pick up changes: personalclaw restart")
    return 0
