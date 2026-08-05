"""PersonalClaw snapshot and restore — portable state management."""

import argparse
import hashlib
import hmac
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

VALID_COMPONENTS = (
    "memory",
    "crons",
    "config",
    "skills",
    "workspace",
    "notifications",
    "security",
    # Criterion 1 names this invocation verbatim (`--components everything`) and the CLI
    # REJECTED it: "❌ Unknown component: everything". Covers every inventory entry the seven
    # named components do not, which is what makes a targeted restore expressible at all —
    # without it there is no way to ask for the task board.
    "everything",
)


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


def _extra_restore_paths_for_test_paths() -> list[str]:
    """Every inventory path the generic restore pass WOULD reach, independent of what exists on
    disk.

    `_extra_restore_paths` filters by existence, which is right at restore time and useless to a
    test
    asking "is this entry reachable at all". Kept beside it so the two cannot drift.
    """
    from personalclaw.durability import inventory as inv

    secret = inv.secret_paths()
    already = {f for files in CORE_FILES.values() for f in files}
    already |= {"workspace", "plan_memory", "skills"}
    out: list[str] = []
    for entry in inv.backup_entries():
        top = entry.path.split("/", 1)[0]
        if top in already or top in secret or entry.path in secret or entry.path in out:
            continue
        out.append(entry.path)
    return out


def _extra_restore_paths(snap: Path) -> list[str]:
    """Inventory entries a RESTORE must return, beyond the seven named components (S177).

    🔴 WHY THIS EXISTS. Capture is inventory-derived (:func:`_everything_paths`, which closed
    the "a full backup silently dropped the user's whole task board" gap); **both restore modes
    were hand-written seven-component lists**. So the archive held `tasks/`, `projects/`,
    `agents/`, `prompts/`, `workflows/`, `artifacts/`, `uploads/` and `entity_settings/` and
    neither `--mode merge` nor `--mode replace` returned any of them, while both printed a
    success line. The asymmetry is the defect: a snapshot is only as good as the restore, and
    widening only the capture side made the archive *look* complete.

    Mirrors :func:`_everything_paths` deliberately — same projection, same exclusions — but
    resolved against the SNAPSHOT rather than the live home, because that is where a restore
    reads. Keeping the two in one shape is the point: a store added to the inventory later is
    both captured and restored without touching either function.

    **Secrets are excluded, unlike capture.** ``backup_entries()`` includes them on purpose
    ("losing the credential store is exactly what a backup should prevent"), but restoring
    ``.env``/``credentials/``/``.local_secret`` generically would re-plant credential material
    into a home that may have deliberately rotated or removed it. Capture is a local 0600
    archive; restore writes into a live home, so the two directions do not warrant the same
    default. The named ``security`` component remains the deliberate path for key material,
    copy-if-missing and 0600 exactly as today.
    """
    from personalclaw.durability import inventory as inv

    secret = inv.secret_paths()
    already = {f for files in CORE_FILES.values() for f in files}
    already |= {"workspace", "plan_memory", "skills"}
    out: list[str] = []
    for entry in inv.backup_entries():
        top = entry.path.split("/", 1)[0]
        if top in already or top in secret or entry.path in secret or entry.path in out:
            continue
        if (snap / entry.path).exists():
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
    "everything": "every other store: tasks, projects, agents, prompts, workflows, uploads, …",
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
    """Is `name` selected? `everything` selects EVERY component, not just the un-named ones.

    🔴 Found by driving criterion 1's own drill (snapshot → wipe the home → restore) rather than
    trusting the component I had just added. `--components everything` restored the task board and
    dropped `config.json`, `memory.db`, `notifications.jsonl`, `workspace/` and `skills/` — because
    "everything" had been just another member of a list, so naming it DESELECTED the seven named
    components. A flag whose whole promise is completeness, silently narrowing the restore.

    So `everything` is a superset marker, not a peer. Reading it here rather than expanding it at
    the CLI keeps one definition for both restore modes and for any later caller.
    """
    if components is None:
        return True
    return name in components or "everything" in components


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


def _merge_json_collection(src: Path, dst: Path, *, wrapper: str | None, key: str) -> int:
    """Union an id-bearing JSON collection, live rows winning on a key collision (S181).

    Nine file-shaped entries declare `union_by_id` or `lww_by_updated_at` and none had an executor.
    S177 made them reachable, but reachably copy-if-missing — so a file the live home already had
    kept
    its own contents and dropped the snapshot's entirely. Driven: 8 of 8 lost the snapshot side.

    `wrapper` names the envelope key when the collection is nested (`{"hooks": [...]}`,
    `{"items": [...]}`) and is None for a bare top-level list (`tags.json`). Live rows win because
    merge mode's contract is that local state wins — the snapshot only fills gaps.
    """
    if not src.is_file() or not dst.is_file():
        return 0
    try:
        src_doc = json.loads(src.read_text(encoding="utf-8"))
        dst_doc = json.loads(dst.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # A hand-edited or truncated file. Leaving the live copy untouched is the safe direction:
        # the alternative is overwriting real state with a parse of something we do not understand.
        return 0

    def _rows(doc: object) -> list | None:
        if wrapper is None:
            return doc if isinstance(doc, list) else None
        if isinstance(doc, dict) and isinstance(doc.get(wrapper), list):
            return doc[wrapper]
        return None

    src_rows, dst_rows = _rows(src_doc), _rows(dst_doc)
    if src_rows is None or dst_rows is None:
        return 0
    seen = {r.get(key) for r in dst_rows if isinstance(r, dict)}
    added = [
        r for r in src_rows if isinstance(r, dict) and r.get(key) is not None and r[key] not in seen
    ]
    if not added:
        return 0
    merged = dst_rows + added
    if wrapper is None:
        out: object = merged
    else:
        out = dict(dst_doc)
        out[wrapper] = merged
    atomic_write(dst, json.dumps(out, indent=2))
    return len(added)


def _merge_json_map(src: Path, dst: Path, *, wrapper: str | None = None) -> int:
    """Union a JSON object keyed by entity, live values winning (S181).

    For the map-shaped entries whose top-level keys ARE the identity: `spend.json` (one key per
    `%Y-%m-%d`), `tool_usage.json` (per tool), `autonudge.json`'s `loops`, and tokenjuice's rows
    (keyed `"<month>|<model>|<compressor>"`).

    🔴 Live values win per key rather than being combined. `spend.json` is the counter a budget
    CEILING is compared against, so adding a snapshot's dollars to today's would move a real-money
    decision on the basis of spend that already happened on another machine or in another month. A
    key the live home does not have is pure recovery; a key it has is authoritative.
    """
    if not src.is_file() or not dst.is_file():
        return 0
    try:
        src_doc = json.loads(src.read_text(encoding="utf-8"))
        dst_doc = json.loads(dst.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    if not isinstance(src_doc, dict) or not isinstance(dst_doc, dict):
        return 0

    if wrapper is not None:
        src_map, dst_map = src_doc.get(wrapper), dst_doc.get(wrapper)
        if not isinstance(src_map, dict) or not isinstance(dst_map, dict):
            return 0
    else:
        src_map, dst_map = src_doc, dst_doc

    added = {k: v for k, v in src_map.items() if k not in dst_map}
    if not added:
        return 0
    if wrapper is not None:
        out = dict(dst_doc)
        out[wrapper] = {**dst_map, **added}
    else:
        out = {**dst_doc, **added}
    atomic_write(dst, json.dumps(out, indent=2))
    return len(added)


def _merge_sqlite_attach(src_db: Path, dst_db: Path, label: str) -> int:
    """Merge a declared sqlite store table-by-table with `INSERT OR IGNORE` (S180).

    🔴 WHY THIS EXISTS. Seven entries declare `merge=sqlite_attach_ignore` and only `memory.db` had
    an executor — a hand-written four-table allowlist. S177 made the other six REACHABLE, but
    reachably copy-if-missing, so a database the live home already had kept its own rows and dropped
    the snapshot's entirely. Driven across all six (`learning.db`, both `knowledge.db`,
    `loop/loops.db`, `workflows/runs.db`, `lexicon.db`): a snapshot row and a live row went in, only
    the live row came out — six stores silently half-restored.

    Generic rather than six allowlists, because the schemas said so: every real table in all six
    carries a primary key or unique index, so `INSERT OR IGNORE` deduplicates correctly and a
    repeated restore drill is a no-op. Measured against both a long-lived real home and the dev
    home.

    🔴 **FTS5 shadow tables are skipped and the index is REBUILT.** Merging them with the rest looks
    fine once and breaks on the second run: measured 40 documents indexed, then a repeated merge
    returned **80 rows for 40 documents** — every search result duplicated — because
    `items_fts_data`/`_idx`/`_docsize` carry segment state `INSERT OR IGNORE` cannot reconcile. A
    restore drill is exactly the thing a user runs twice.

    Of the two halves, the **rebuild is what fixes it** — verified by removing each independently:
    without the rebuild the index returns 0 hits for 40 documents, whereas without the skip the
    trailing rebuild still repairs the shadow tables. The skip is kept because it makes the import
    honest rather than repaired-after-the-fact: it avoids writing 160 rows of another database's
    segment state (measured) only to overwrite them, and it means a future caller that rebuilds
    conditionally cannot reintroduce the doubling.

    `memory.db` keeps its own executor and is NOT routed here: it filters `WHERE is_deleted=0`, so a
    generic all-tables merge would resurrect memories the user deleted. That filter is the reason
    the
    allowlist exists, not an accident of it.
    """
    try:
        check = sqlite3.connect(f"file:{src_db}?mode=ro", uri=True)
        try:
            if check.execute("PRAGMA integrity_check;").fetchone()[0] != "ok":
                print(f"  ⚠️  {label}: source integrity check failed — skipping merge")
                return 0
        finally:
            check.close()
    except Exception as exc:  # noqa: BLE001 — a corrupt source must not abort the restore
        print(f"  ⚠️  {label}: source unreadable ({exc}) — skipping merge")
        return 0

    conn = sqlite3.connect(str(dst_db))
    imported = 0
    try:
        conn.execute("BEGIN")
        conn.execute("ATTACH DATABASE ? AS src", (str(src_db),))
        virtual = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM src.sqlite_master WHERE sql LIKE '%VIRTUAL TABLE%'"
            )
        ]
        # A virtual table's shadow tables are `<name>_data`, `_idx`, `_docsize`, `_config`,
        # `_content`. Matching by prefix covers them without hardcoding FTS5's internals.
        skip = tuple(virtual) + tuple(v + "_" for v in virtual)
        local = {
            r[0] for r in conn.execute("SELECT name FROM main.sqlite_master WHERE type='table'")
        }
        for (table,) in conn.execute(
            "SELECT name FROM src.sqlite_master WHERE type='table'"
        ).fetchall():
            if table.startswith("sqlite_") or table in skip or table.startswith(skip):
                continue
            if table not in local:
                # A table the live schema does not have. Creating it here would import a shape this
                # build's code cannot read; the owning module creates its own tables on open.
                continue
            before = conn.total_changes
            try:
                conn.execute(f'INSERT OR IGNORE INTO main."{table}" SELECT * FROM src."{table}"')
            except sqlite3.Error as exc:
                # A column-set mismatch between snapshot and live schema. Skip the table, keep the
                # rest — the same call `_merge_memory` makes about its opportunistic `contributor`
                # column, for the same reason: a partial restore beats an aborted one.
                print(f"  ⚠️  {label}.{table}: {exc} — skipped")
                continue
            imported += conn.total_changes - before
        for view in virtual:
            if view in local:
                conn.execute(f'INSERT INTO main."{view}"("{view}") VALUES(\'rebuild\')')
        conn.execute("COMMIT")
    except Exception as exc:  # noqa: BLE001
        conn.execute("ROLLBACK")
        print(f"  ⚠️  {label}: merge failed ({exc}) — left unchanged")
        return 0
    finally:
        try:
            conn.execute("DETACH DATABASE src")
        except sqlite3.Error:
            pass
        conn.close()
    if imported:
        print(f"  {label} rows imported: {imported}")
    return imported


def _merge_keyed_jsonl(src: Path, dst: Path, key_field: str, label: str) -> int:
    """Append rows from `src` that `dst` lacks, deduping on `key_field` (S179).

    Extracted after a THIRD near-identical copy was needed (`model_calls.jsonl`, whose declared
    `append_dedup` the S178 ratchet demanded an executor for). Three hand-written loops differing
    only in a key name is how two of them start disagreeing — the duplication S175 deleted from the
    run store after finding one copy had silently reverted another.

    Deliberately NOT used for `security_events.jsonl`: that one carries an HMAC-key precondition,
    and folding a security gate into a generic helper is how the gate gets dropped by a later caller
    who only wanted the dedup.
    """
    if not src.is_file() or not dst.is_file():
        return 0
    existing: set[str] = set()
    with open(dst, encoding="utf-8") as f:
        for line in f:
            try:
                existing.add(json.loads(line).get(key_field) or line.strip())
            except (ValueError, TypeError):
                pass
    imported = 0
    with open(dst, "a", encoding="utf-8") as out, open(src, encoding="utf-8") as f:
        for line in f:
            try:
                key = json.loads(line).get(key_field) or line.strip()
            except (ValueError, TypeError):
                continue
            if key not in existing:
                out.write(line if line.endswith("\n") else line + "\n")
                existing.add(key)
                imported += 1
    if imported:
        print(f"  {label} imported: {imported}")
    return imported


def _merge_feedback(src: Path, dst: Path) -> None:
    """Merge `feedback.jsonl`, deduping on the record's own `id` (S178).

    The third `append_dedup` entry with no executor. Unlike the SEL log this carries no HMAC, so
    plain dedup is safe — and unlike the run history it is a single flat file, so there are no
    shards. Keyed on `FeedbackRecord.id` rather than the whole line for the reason
    `_merge_run_history` is: the same record round-trips through a serializer on both sides.

    Deliberately does NOT trim to `feedback._CAP`. That module owns its own retention ("atomic trim
    at 2x cap") and re-implementing the bound here is the duplication S175 deleted from the run
    store after finding one copy had silently reverted the other.
    """
    _merge_keyed_jsonl(src, dst, "id", "Feedback")


def _merge_security_events(snap: Path, pc: Path) -> None:
    """Merge the SEL audit log — but ONLY when the HMAC key that will verify it is the same (S178).

    🔴 WHY THE GUARD. `inventory.py` declares `security_events.jsonl` with `merge=append_dedup`, and
    a generic executor would have appended the snapshot's rows unconditionally. Driven: two homes
    with different `sel_hmac.key` files, 3 rows imported → `verify_integrity` reported
    **checked=5, valid=2**, logging "SEL HMAC mismatch" for every imported row. A restore would have
    made the tamper-evident audit log report tampering — turning the one surface a user consults to
    ask "was I compromised?" into a false positive they cannot clear except by rotating the chain.

    So the key decides, and it is knowable at restore time because both files are on disk. The
    `security` component restores `sel_hmac.key` **copy-if-missing**, so:

    * a WIPED home takes the snapshot's key → the snapshot's rows verify (measured 3/3 valid) and
      merging them recovers audit history that would otherwise be lost;
    * a LIVE home keeps its own key → the snapshot's rows could never verify under it, so importing
      them would only manufacture mismatches.

    Fail-CLOSED, unlike the other merges: when the keys differ (or either is unreadable) the rows
    are skipped and the reason is printed. The alternative failure — a silently importable row that
    reads as tampering — is strictly worse than a missing row, because an audit trail's value is
    that a mismatch means something.
    """
    src, dst = snap / "security_events.jsonl", pc / "security_events.jsonl"
    if not src.is_file():
        return

    def _key(p: Path) -> bytes | None:
        try:
            return (p / "sel_hmac.key").read_bytes()
        except OSError:
            return None

    snap_key, live_key = _key(snap), _key(pc)
    if not dst.exists():
        # Nothing to merge INTO. The generic store pass already copies a missing file; leaving it
        # to that path keeps one copy-if-missing implementation rather than two.
        return
    if snap_key is None or live_key is None or not hmac.compare_digest(snap_key, live_key):
        print("  Security events: skipped (HMAC key differs — imported rows could not verify)")
        return

    existing: set[str] = set()
    with open(dst, encoding="utf-8") as f:
        for line in f:
            try:
                existing.add(json.loads(line).get("event_id") or line.strip())
            except (ValueError, TypeError):
                pass
    imported = 0
    with open(dst, "a", encoding="utf-8") as out, open(src, encoding="utf-8") as f:
        for line in f:
            try:
                key = json.loads(line).get("event_id") or line.strip()
            except (ValueError, TypeError):
                continue
            if key not in existing:
                out.write(line if line.endswith("\n") else line + "\n")
                existing.add(key)
                imported += 1
    print(f"  Security events imported: {imported}")


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

    # 🔴 Every remaining inventory entry (S177) — see `_extra_restore_paths`. Replace mode moves
    # the live copy into the pre-restore backup FIRST, so the destructive half stays recoverable
    # exactly as it is for the named components.
    if _want(components, "everything"):
        for rel in _extra_restore_paths(snap):
            src, live = snap / rel, pc / rel
            if live.exists() and not live.is_symlink():
                (backup / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(live), str(backup / rel))
            if src.is_dir():
                _copytree_safe(src, live)
            elif src.is_file():
                live.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src), str(live))
        print("  ✅ stores")

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
        # `feedback.jsonl`, the third declared `append_dedup` with no executor. Grouped here rather
        # than given its own component: both are platform-domain append logs, and a new component
        # name is a CLI surface a user then has to know about.
        _merge_feedback(snap / "feedback.jsonl", pc / "feedback.jsonl")
        # `model_calls.jsonl`, the fourth declared `append_dedup` — demanded by S178's own ratchet
        # the moment S179 declared the entry. Keyed on `AttemptRecord.audit_id`.
        _merge_keyed_jsonl(
            snap / "model_calls.jsonl", pc / "model_calls.jsonl", "audit_id", "Model calls"
        )
        print("  ✅ notifications")

    if _want(components, "security"):
        for f in CORE_FILES["security"]:
            s, d = snap / f, pc / f
            if s.is_file() and not d.is_file():
                shutil.copy2(str(s), str(d))
                os.chmod(str(d), 0o600)
                print(f"  {f}: restored (was missing)")
        # The SEL audit log, whose declared `append_dedup` had no executor. Placed AFTER the key
        # copy above, because whether the imported rows can verify depends on which key won.
        _merge_security_events(snap, pc)
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

    # 🔴 Every remaining inventory entry (S177). The capture side stages these; neither restore
    # mode read them, so a merge recovered the automations and silently dropped the task board.
    # Gated on `everything` so a targeted `--components memory` stays targeted — but that is also
    # the default (`components is None`), which is the invocation a user in a recovery actually
    # types.
    # 🔴 The six declared sqlite stores whose `sqlite_attach_ignore` had no executor (S180). Runs
    # BEFORE the generic store pass so a DB the live home already holds is MERGED rather than left
    # alone; the pass below then copies any that are missing entirely.
    #
    # Driven off the inventory, not a hardcoded list, so a store declared later merges by default —
    # the same reason capture reads `backup_entries()`. `memory.db` is excluded: its own executor
    # filters `WHERE is_deleted=0`, and a generic all-tables merge would resurrect deleted memories.
    if _want(components, "everything"):
        try:
            from personalclaw.durability import inventory as _inv

            _attach = [
                e.path
                for e in _inv.sqlite_entries()
                if e.merge == _inv.MERGE_SQLITE_ATTACH_IGNORE and e.path != "memory.db"
            ]
        except Exception:  # noqa: BLE001 — a restore must work even if this import breaks
            _attach = []
        for rel in _attach:
            s_db, d_db = snap / rel, pc / rel
            if s_db.is_file() and d_db.is_file():
                _merge_sqlite_attach(s_db, d_db, rel)

    # 🔴 The nine file-shaped `union_by_id`/`lww_by_updated_at` entries with no executor (S181).
    # Runs BEFORE the generic store pass so a file the live home already holds is MERGED rather than
    # left alone; that pass then copies any missing outright.
    #
    # Per-file, not generic: the shapes genuinely differ (a wrapped list, a bare list, a map keyed
    # by
    # date/tool/composite) and so do the semantics. Read off the owning module's contract, measured
    # against a real home.
    if _want(components, "everything"):
        for rel, wrapper, key in (
            ("hooks.json", "hooks", "id"),
            ("inbox.json", "items", "id"),
            ("tags.json", None, "id"),
        ):
            n = _merge_json_collection(snap / rel, pc / rel, wrapper=wrapper, key=key)
            if n:
                print(f"  {rel}: {n} imported")
        for rel, wrapper in (
            # `spend.json` is date-keyed and `tool_usage.json` tool-keyed; tokenjuice's rows are
            # keyed "<month>|<model>|<compressor>"; autonudge's live loops sit under `loops`.
            ("spend.json", None),
            ("tool_usage.json", None),
            ("tokenjuice_savings.json", "rows"),
            ("autonudge.json", "loops"),
        ):
            n = _merge_json_map(snap / rel, pc / rel, wrapper=wrapper)
            if n:
                print(f"  {rel}: {n} imported")
        # `durability_state.json` is NOT merged. It holds the scheduler's own last-run marks, and
        # `_due()` compares them against an interval — driven, a stale snapshot's `last_snapshot`
        # reads as DUE while the live one does not, so importing it would re-trigger a snapshot
        # immediately. Copy-if-missing (the generic pass) is the correct semantic: a wiped home gets
        # its marks back, a live home keeps the ones that describe what actually ran.

    if _want(components, "everything"):
        restored = []
        for rel in _extra_restore_paths(snap):
            src = snap / rel
            dst = pc / rel
            if src.is_dir():
                dst.mkdir(parents=True, exist_ok=True)
                _copy_tree_no_overwrite(src, dst)
                restored.append(rel)
            elif src.is_file() and not dst.exists():
                # A file the live home does not have. An EXISTING file is left alone: merge
                # mode's contract is that local state wins, and these entries have no
                # field-level merge executor yet (their declared strategies are the 13 the
                # queue tracks) — so copy-if-missing is the honest half, not a silent overwrite.
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src), str(dst))
                restored.append(rel)
        if restored:
            print(f"  Stores: recovered {len(restored)} ({', '.join(sorted(restored)[:6])}…)")
        print("  ✅ stores")

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
