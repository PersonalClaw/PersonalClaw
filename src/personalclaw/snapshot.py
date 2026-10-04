"""PersonalClaw snapshot and restore — portable state management."""

import argparse
import functools
import hashlib
import hmac
import json
import os
import re
import shlex
import shutil
import socket
import sys
import tarfile
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, TextIO

from personalclaw import bounded_log
from personalclaw.atomic_write import atomic_write, atomic_write_bytes, private_file
from personalclaw.durability import home_paths, restore_items, sqlite_files
from personalclaw.sqlite_compat import sqlite3

if TYPE_CHECKING:  # pragma: no cover - typing only
    from personalclaw.durability.inventory import StateEntry
    from personalclaw.gateway_base import LiveGateway

VALID_COMPONENTS = (
    "memory",
    "crons",
    "config",
    "skills",
    "workspace",
    "notifications",
    "security",
    # 🔴 `projects` was reachable ONLY through `everything` (via the inventory projection), so
    # there was no way to ask for the projects and nothing else — and a project is the unit a user
    # moves between machines, which is exactly the targeted restore the flag exists for. Naming it
    # also gives the project ARCHIVE format (`workflows/project_archive.py`) a component to be the
    # whole-home counterpart of: one project travels as a manifest ZIP, every project travels as
    # this component. Worktrees stay excluded either way — the inventory declares them
    # `derived_within`, being git-owned checkouts re-creatable from the repo.
    "projects",
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

    Every member is owned by its reader alone: a file 0600, a folder 0700, both when a snapshot is
    written and when one is extracted. A snapshot holds the audit log's signing key and every
    memory, and the mode a member carries is the mode a restore's staging copy, the files a restore
    puts back, and a ``tar -x`` by hand all make.
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
    info.mode = 0o700 if info.isdir() else 0o600
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
    # 🔴 `triggers.json` + `event_triggers.json`. This component held `crons.json` ALONE —
    # the legacy file, which nothing has written since S108 and which the deletion left as a
    # read-only migration source. So `personalclaw snapshot` backed up an empty relic and dropped
    # every automation the user actually had. `crons.json` still travels because §6 keeps it
    # read-only for `automation verify-migration` to diff.
    "crons": ("crons.json", "triggers.json", "event_triggers.json"),
    # `autonomy_rungs.json` rides here: it holds the rungs the
    # user has explicitly granted per action type plus the demotion history. Losing it
    # is not catastrophic (every type falls back to its declared floor) but it IS a
    # decision the user made by hand, so it travels with the other decisions.
    "config": (
        "config.json",
        "session_map.json",
        "hooks.json",
        "project_dir",
        "workspace_dir",
        "autonomy_rungs.json",
        # `routing_policy.json` rides here for the same reason as `autonomy_rungs.json`
        # (MODEL-ROUTING-TELEMETRY §7): it holds DECISIONS — the per-use-case routing mode,
        # the pin, and any manual reorder. Losing it is not catastrophic (routing falls back
        # to off/heuristic) but it is a choice the user made by hand, so it travels.
        "routing_policy.json",
    ),
    "notifications": ("notifications.jsonl",),
    # Integrity material only: the audit log's HMAC key (so a restore into a wiped home can
    # verify the rows it imports — `_merge_security_events`) and the telemetry salt. NO
    # credential value is captured: `credentials.json` rode here (#2217) so a restore returned
    # provider keys, and that is reversed — an archive gets copied off the machine, and a key
    # inside it leaves with it. The settings that use a key travel as `{{secret:…}}`
    # references; the credential store itself stays on this machine (`inventory.credential`).
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


def _partition_paths(root: Path, *, of: str = "") -> list[str]:
    """The store partitions under *root* (a home, or an unpacked snapshot), home-relative — see
    `inventory.partition_paths` — or only those of the entry with the id *of*. Empty when the
    inventory cannot be read, like its siblings: a snapshot must work even if that import breaks."""
    try:
        from personalclaw.durability import inventory as inv

        return [
            rel
            for rel in inv.partition_paths(root)
            if not of or getattr(inv.partition_entry(rel), "id", "") == of
        ]
    except Exception:  # noqa: BLE001 — snapshot must work even if this import breaks
        return []


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
    already |= {"workspace", "skills"}  # staged as trees below
    out: list[str] = []
    for entry in inv.backup_entries():
        top = entry.path.split("/", 1)[0]
        if top in already or entry.path in out:
            continue
        if (pc / entry.path).exists():
            out.append(entry.path)
    return out


def _derived_within(entry_path: str) -> tuple[str, ...]:
    """The inventory's `derived_within` globs for one entry, or ().

    🔴 THIS FIELD HAD NO READER. `projects` declares ``derived_within=("*/worktrees",)`` — "git-owned
    checkouts, re-creatable from the repo" — and every capture path reached `projects/` through a
    plain `_copytree_safe`, so a snapshot of a home with one bound workspace copied the entire
    worktree. The declaration was true and unenforced, which is the worst shape for state policy: it
    reads as a decision and behaves as an omission. Scoped to the entry it is asked about, not
    applied globally, because `workspace`'s `knowledge` and `skills`' embeddings file are separate
    decisions with their own consumers.
    """
    try:
        from personalclaw.durability import inventory as inv

        for entry in inv.INVENTORY:
            if entry.path == entry_path:
                return tuple(entry.derived_within)
    except Exception:  # noqa: BLE001 — a snapshot must work even if this import breaks
        return ()
    return ()


def _derived_ignore(entry_path: str, root: Path):
    """A copytree `ignore` that skips whatever `entry_path` declares `derived_within`.

    Matches the glob against the path RELATIVE TO the entry root, which is the frame the inventory
    writes them in (`*/worktrees` means "any project's worktrees dir", not "a dir called worktrees
    anywhere"). Matching the absolute path would make the pattern depend on where the home lives.
    """
    import fnmatch

    globs = _derived_within(entry_path)

    def _ignore(directory: str, contents: list[str]) -> set[str]:
        if not globs:
            return set()
        skipped: set[str] = set()
        for name in contents:
            try:
                rel = (Path(directory) / name).relative_to(root).as_posix()
            except ValueError:
                continue
            if any(fnmatch.fnmatch(rel, g) for g in globs):
                skipped.add(name)
        return skipped

    return _ignore


def _restore_ignore(entry_path: str, root: Path):
    """A copytree ``ignore`` that skips what ``restore_items.left_out_of_restore`` leaves out, and
    every sidecar of a database, which no restore puts into the home (``durability.sqlite_files``).
    """
    leaves = bool(_derived_within(entry_path))

    def _ignore(directory: str, contents: list[str]) -> set[str]:
        skipped = sqlite_files.sidecars_in(directory, contents)
        if not leaves:
            return skipped
        base = Path(directory).relative_to(root)
        return skipped | {
            n
            for n in contents
            if restore_items.left_out_of_restore(entry_path, (base / n).as_posix())
        }

    return _ignore


def _engines_not_here(snap: Path, components: list[str] | None) -> list[tuple[str, bool]]:
    """The apps this restore brought back whose engine is not installed here: ``(display name,
    whether the app has an engine here at all)``.

    An engine lives in the app's own ``venv/``, which a snapshot leaves out, so an app that declares
    one comes back without it and offers Install engine, unless this home had it. A merge keeps the
    live ``venv/`` and a replace hands it back to the app (:func:`_keep_app_engines`); an engine
    kept that way was installed for the version this home had, so an app is named with ``True``
    when the version that came back does not match it, and Install engine then brings that
    environment up to date. An app whose engine is installed is not named, nor is an app the
    restore did not touch.
    """
    if not _store_selected(components, "apps") or not (snap / "apps").is_dir():
        return []
    try:
        from personalclaw.apps.manager import (
            APP_MANIFEST_FILENAME,
            APP_VENV_DIRNAME,
            app_dir,
            engine_not_installed,
        )
        from personalclaw.apps.manifest import AppManifest

        names: list[tuple[str, bool]] = []
        for tree in sorted((snap / "apps").iterdir()):
            if tree.name.startswith(".") or not (tree / APP_MANIFEST_FILENAME).is_file():
                continue
            if not engine_not_installed(tree.name):
                continue
            here = app_dir(tree.name)
            manifest = AppManifest.from_json_file(here / APP_MANIFEST_FILENAME)
            names.append((manifest.displayName or tree.name, (here / APP_VENV_DIRNAME).is_dir()))
        return names
    except Exception:  # noqa: BLE001 — a note after the restore must never fail the restore
        import logging

        logging.getLogger(__name__).debug("engine census after restore failed", exc_info=True)
        return []


def _app_display_name(folder: Path) -> str:
    """The display name in the app manifest *folder* holds, else the folder's name."""
    from personalclaw.apps.manager import APP_MANIFEST_FILENAME
    from personalclaw.apps.manifest import AppManifest

    try:
        return AppManifest.from_json_file(folder / APP_MANIFEST_FILENAME).displayName or folder.name
    except Exception:  # noqa: BLE001 — a name for a sentence, never a reason to fail a restore
        return folder.name


def _keep_app_engines(displaced: Path, restored: Path) -> list[str]:
    """Hand each app's engine back to the app of the same name the restore brought back.

    An engine (``apps/<app>/venv``) is built for this machine: capture leaves it out
    (``derived_within``) and a restore never plants one, so a replace has nothing to put in its
    place. Moving it into ``pre-restore-<ts>/`` with the rest of the app left gigabytes of working
    engine there while the restored app offered to install the same engine again. An update keeps
    an app's engine the same way (``apps.app_manager._carry_state``: a rename on one filesystem,
    whatever the size), and the app's own check (``SidecarInstall.installed``: the interpreter, and
    the packages the restored manifest declares) says whether it fits the version that came back.
    Everything else of the app, its ``data/`` included, stays displaced, so undoing the restore
    still has it. Returns the folder names of the apps whose engine stayed.
    """
    from personalclaw.apps.app_manager import _carry_state
    from personalclaw.apps.manager import APP_MANIFEST_FILENAME, APP_VENV_DIRNAME

    if not displaced.is_dir() or not restored.is_dir():
        return []
    kept: list[str] = []
    for old in sorted(displaced.iterdir()):
        new = restored / old.name
        if old.name.startswith(".") or not (new / APP_MANIFEST_FILENAME).is_file():
            continue
        if _carry_state(old, new, names=(APP_VENV_DIRNAME,)):
            kept.append(old.name)
    return kept


def _engines_set_aside(displaced: Path) -> list[tuple[Path, int]]:
    """The engines still in the backup: those of apps the restore did not bring back, with their
    size. Each stays with its app, as removing an app takes its engine along, so moving the folder
    back undoes the restore whole; the backup is the user's to delete."""
    from personalclaw.apps.manager import APP_VENV_DIRNAME
    from personalclaw.durability.footprint import _tree_bytes

    if not displaced.is_dir():
        return []
    out: list[tuple[Path, int]] = []
    for old in sorted(displaced.iterdir()):
        venv = old / APP_VENV_DIRNAME
        if old.name.startswith(".") or venv.is_symlink() or not venv.is_dir():
            continue
        out.append((old, _tree_bytes(venv, skip_files=set(), skip_dirs=set())))
    return out


def _projects_component_paths(base: Path) -> list[str]:
    """Home-relative per-project paths the named `projects` component covers.

    Per-project rather than the `projects/` root so the component can be reported and restored one
    project at a time — the unit a user actually moves. Mirrors
    `workflows.project_archive.project_component_paths`, which is the single-project archive's view
    of the same tree.
    """
    root = base / "projects"
    if not root.is_dir():
        return []
    out: list[str] = []
    for d in sorted(root.iterdir()):
        try:
            if not d.is_dir() or d.is_symlink():
                continue
        except OSError:
            continue
        out.append(f"projects/{d.name}")
    return out


def _store_selected(components: list[str] | None, rel: str) -> bool:
    """Whether the generic store pass should restore `rel` for this component selection.

    `everything` still selects every store. A NAMED store component (today: `projects`) additionally
    selects its own subtree, so `--components projects` restores the projects and nothing else. Both
    are asked here rather than at each call site so the two restore modes cannot drift — the exact
    asymmetry `_extra_restore_paths` exists to record.
    """
    if _want(components, "everything"):
        return True
    top = rel.split("/", 1)[0]
    return top in VALID_COMPONENTS and _want(components, top)


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
    already |= {"workspace", "skills"}
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

    **Secrets are excluded.** Credential values are not even captured (``backup_entries()``
    drops ``credential=True`` entries — ``.env``, ``credentials.json``, ``.local_secret``), and
    the secrets a snapshot does carry are not re-planted generically either: restore writes into
    a live home that may have deliberately rotated or removed them. The named ``security``
    component remains the deliberate path for the audit key material it carries. The owner's
    grants (``grants/``, ``owner_grants``) are one of these secrets: a yes is given on the machine
    where the owner was shown what runs, so the MCP servers, hooks and tasks a restore brings back
    wait for a yes here.
    """
    from personalclaw.durability import inventory as inv

    secret = inv.secret_paths()
    already = {f for files in CORE_FILES.values() for f in files}
    already |= {"workspace", "skills"}
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
    "workspace": "workspace/ directory",
    "notifications": "notifications.jsonl (notification history)",
    "security": "sel_hmac.key, telemetry_salt (no credential is ever captured)",
    "projects": "projects/ — briefs, context ledgers, templates (worktrees excluded, git-owned)",
    "everything": "every other store: tasks, projects, agents, prompts, workflows, uploads, …",
}


def _pc_dir() -> Path:
    from .config.loader import config_dir

    return config_dir()


def _home_id(pc: Path) -> str:
    """The id of the home at *pc* (`durability.shards.machine_id`), or ``""`` for a home that is
    not there, or whose id is behind a link it holds (which is never read): what a snapshot's
    manifest names, and what a restore compares it with. A snapshot that names no home is read as
    another home's."""
    if not pc.is_dir():
        return ""
    from personalclaw.durability.shards import machine_id

    try:
        return machine_id(pc)
    except home_paths.LinkInTheWay:
        return ""


def _snapshot_home(snap: Path) -> str:
    """The home an unpacked snapshot or export archive names in its manifest, or ``""`` when it
    names none (one taken before manifests did) or the manifest cannot be read."""
    try:
        manifest = json.loads((snap / "MANIFEST.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    value = manifest.get("machine_id") if isinstance(manifest, dict) else None
    return value if isinstance(value, str) else ""


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


def _list_components(stream: TextIO | None = None) -> None:
    """The components ``restore --components`` takes: on stdout (``None``, the stdout of the
    moment) when asked for with ``--list-components``, and on stderr after the refusal of one
    named that is not one."""
    print("Available components:", file=stream)
    for k, v in COMPONENT_HELP.items():
        print(f"  {k:16s} {v}", file=stream)
    print("\nCombine with commas: --components memory,crons,skills", file=stream)


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


# ── Snapshot ──────────────────────────────────────────────────────────────────


def _write_archive(stage: Path, outfile: Path) -> Path:
    """Write the staged snapshot ``stage`` to ``outfile`` as a gzipped tar; return ``outfile``.

    Through the private writer (``atomic_write.private_file``): the archive holds the audit log's
    signing key and every memory, so it is 0600 from its first byte, in a folder made 0700, and it
    appears under its name only once it is whole. The tar is named for the archive, so the gzip
    header records ``<name>.tar`` rather than the temp file's name.
    """
    with private_file(outfile) as fh:
        with tarfile.open(name=str(outfile), mode="w:gz", fileobj=fh) as tar:
            tar.add(str(stage), arcname=stage.name, filter=_data_filter)
    return outfile


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
        print(f"❌ --keep value must be a positive integer, got: {args.keep}", file=sys.stderr)
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
        for d in ("workspace", "skills"):
            (stage / d).mkdir(parents=True, exist_ok=True)

        # The core files, then every DECLARED database: each store's own path and each of its
        # partitions (`StateEntry.partitions`: a memory partition's databases, an installed app's).
        # Each is copied once, a database through the sqlite backup API (`sqlite_files.copy_file`).
        # `captured` names them by their path in the home, and the folder copies below leave out
        # exactly those paths, never a file merely NAMED like one (a `runs.db` of the user's own in
        # the workspace is not the run ledger), and copy any other database they meet through the
        # backup API too. Leaving them out by name dropped the user's files, and so did the folder
        # pass's copying of a declared database again, as bytes, over the consistent copy: with a
        # reader holding the log, that copy of `learning.db` had not even its tables.
        captured: set[str] = set()
        for files in CORE_FILES.values():
            for f in files:
                if (pc / f).is_file() and os.path.islink(pc / f):
                    print(f"⚠️  Skipping symlinked core file: {pc / f}")
                elif (pc / f).is_file():
                    sqlite_files.copy_file(pc / f, stage / f)
                    captured.add(f)
        for _db in [*_declared_db_paths(), *_partition_paths(pc)]:
            _src_db = pc / _db
            if _db not in captured and _src_db.is_file() and not os.path.islink(_src_db):
                sqlite_files.copy_file(_src_db, stage / _db)
                captured.add(_db)
        _db_ignore = sqlite_files.tree_ignore(pc, captured)
        _copy = sqlite_files.copy_file

        # Workspace, without hygiene_data and insert_facts*.py.
        if (pc / "workspace").is_dir():
            _pattern_ignore = shutil.ignore_patterns("hygiene_data", "insert_facts*.py")

            def _ws_ignore(directory: str, contents: list[str]) -> set[str]:
                return set(_pattern_ignore(directory, contents)) | _db_ignore(directory, contents)

            _copytree_safe(
                pc / "workspace",
                stage / "workspace",
                dirs_exist_ok=True,
                ignore=_ws_ignore,
                copy_function=_copy,
            )

        # Skills
        if (pc / "skills").is_dir():
            _copytree_safe(
                pc / "skills",
                stage / "skills",
                dirs_exist_ok=True,
                ignore=_db_ignore,
                copy_function=_copy,
            )

        # THE GAP CLOSURE (DURABILITY §1): every remaining inventory entry. Before
        # this, tasks/, projects/, loop/, artifacts/, prompts/, workflows/,
        # agents/, apps/ and entity_settings/ were in NEITHER the snapshot nor the
        # export — a "full backup" that silently dropped a user's whole task board.
        # Driven off the inventory so a store added later is captured by default.
        # What a running Temporary chat keeps (its transcript, working folder and attached files)
        # stays out: the chat is forgotten when its session ends, and a snapshot would outlive it.
        from personalclaw.chat_traces import kept_by_temporary_chats

        _temporary = kept_by_temporary_chats(pc)
        for rel in _everything_paths(pc):
            src = pc / rel
            if rel in captured:
                continue
            if os.path.islink(src):
                print(f"⚠️  Skipping symlinked state path: {rel}")
                continue
            if src.is_dir():
                # 🔴 `derived_within` is honored HERE, where the tree is actually copied. `projects`
                # declares `*/worktrees` derived; without this the copy carried every git worktree a
                # bound workspace had produced, which is the difference between a megabyte archive
                # and a multi-gigabyte one.
                _derived = _derived_ignore(rel, src)

                def _ignore(directory: str, contents: list[str], _d=_derived) -> set[str]:
                    held = (
                        {n for n in contents if (Path(directory) / n).resolve() in _temporary}
                        if _temporary
                        else set()
                    )
                    return _db_ignore(directory, contents) | _d(directory, contents) | held

                _copytree_safe(
                    src, stage / rel, dirs_exist_ok=True, ignore=_ignore, copy_function=_copy
                )
            elif src.is_file():
                (stage / rel).parent.mkdir(parents=True, exist_ok=True)
                _copy(src, stage / rel)

        # Manifest
        ws_files = sum(1 for _ in (stage / "workspace").rglob("*") if _.is_file())
        # The loader's enumeration, not a directory count: `iterdir()` counted the `auto/`
        # NAMESPACE as one skill however many live under it, and missed `.proposals/` not being
        # a skill at all (#302's third site). A manifest that miscounts what it archived is a
        # restore the user cannot check.
        from personalclaw.skills.loader import iter_skill_files

        sk_count = len(iter_skill_files(stage / "skills"))
        manifest = {
            # v3 adds `domains` — the per-domain counts the archive browser shows. The
            # `contents` block is unchanged: `_print_manifest` and the settings panel
            # read it, and a browser gaining a column is no reason to break a printer.
            "version": 3,
            "domains": _domain_counts(stage),
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "hostname": socket.gethostname(),
            "user": os.environ.get("USER", "unknown"),
            "personalclaw_dir": str(pc),
            # The home that took it, so a replace restore can tell this home's own snapshot from
            # another's, whose automations may still be running there (`triggers.restore_hold`).
            "machine_id": _home_id(pc),
            "contents": {
                "memory_db": _fsize(stage / "memory.db"),
                "memory_index_db": _fsize(stage / "memory_index.db"),
                "crons_json": _fsize(stage / "crons.json"),
                "triggers_json": _fsize(stage / "triggers.json"),
                "event_triggers_json": _fsize(stage / "event_triggers.json"),
                "config_json": _fsize(stage / "config.json"),
                "notifications_jsonl": _fsize(stage / "notifications.jsonl"),
                "workspace_files": ws_files,
                "skill_count": sk_count,
            },
        }
        atomic_write(stage / "MANIFEST.json", json.dumps(manifest, indent=2))

        outfile = _write_archive(stage, out / f"{name}.tar.gz")

        # Manifest sidecar, so the archive browser can show this snapshot's per-domain
        # counts without streaming-decompressing the whole tarball to find one member.
        from personalclaw.durability.archive import write_sidecar

        write_sidecar(outfile, manifest)

        has_hmac_key = (stage / "sel_hmac.key").exists()

    sz = outfile.stat().st_size
    human = f"{sz // 1024}K" if sz < 1024 * 1024 else f"{sz / 1024 / 1024:.1f}M"
    print(f"✅ Snapshot created: {outfile} ({human})")
    if has_hmac_key:
        print(
            "⚠️  Snapshot contains sel_hmac.key — treat this file as sensitive. "
            "An attacker with access to it could forge SEL audit entries."
        )

    _audit("snapshot_created", f"{outfile} ({human})")

    # Prune to the N newest by the time in each name, through retention's own plan: the newest
    # snapshot a restore drill verified stays too while every newer one is unverified.
    from personalclaw.durability import retention
    from personalclaw.durability.service import last_verified

    plan = retention.plan_newest(
        retention.list_snapshots(out), keep=args.keep, verified=last_verified()["archive"]
    )
    for name in retention.remove(plan.prune)[0]:
        print(f"🗑  Pruned: {name} ({plan.reasons[name]})")
    if plan.held is not None:
        print(f"🛡  Kept {plan.held.name} as well: {retention.HELD}.")

    remaining = len(list(out.glob("personalclaw-snapshot-*.tar.gz")))
    print(f"📦 Snapshots in {out}: {remaining} (keep={args.keep})")
    return 0


# ── Restore ───────────────────────────────────────────────────────────────────


def _domain_counts(stage: Path) -> dict[str, dict[str, int]]:
    """Per-domain ``{files, bytes, rows}`` over a staged snapshot tree (MANIFEST v3).

    ``rows`` is the count §6 asks the archive browser to show, and it is a real row
    count, not a file count: SQLite entries are summed across their tables and JSONL
    streams by line. A tree entry has no rows, so it contributes ``files``/``bytes``
    only — reporting 0 rows for a directory of documents would read as "empty".

    Domains are attributed by :func:`portability.domain_of` (longest declared match), so
    this and the export's ``domain_counts`` cannot disagree about which domain a nested
    entry belongs to. One rule, two consumers.
    """
    from personalclaw.durability import inventory as inv
    from personalclaw.portability import domain_of

    out: dict[str, dict[str, int]] = {}

    def _bucket(domain: str) -> dict[str, int]:
        return out.setdefault(domain, {"files": 0, "bytes": 0, "rows": 0})

    for path in sorted(stage.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(stage).as_posix()
        if rel == "MANIFEST.json":
            continue
        bucket = _bucket(domain_of(rel))
        bucket["files"] += 1
        try:
            bucket["bytes"] += path.stat().st_size
        except OSError:
            pass

    for entry in inv.backup_entries():
        staged = stage / entry.path
        if not staged.exists():
            continue
        bucket = _bucket(entry.domain)
        if entry.kind == inv.KIND_SQLITE and staged.is_file():
            bucket["rows"] += _sqlite_row_total(staged)
        elif entry.kind == inv.KIND_JSONL_APPEND:
            files = [staged] if staged.is_file() else sorted(staged.rglob("*.jsonl"))
            for fpath in files:
                try:
                    bucket["rows"] += sum(
                        1 for line in fpath.read_text(encoding="utf-8").splitlines() if line.strip()
                    )
                except (OSError, UnicodeDecodeError):
                    continue
        elif entry.kind == inv.KIND_JSON_ENTITY_DIR and staged.is_dir():
            bucket["rows"] += sum(1 for _ in staged.rglob("*.json"))
    return out


def _sqlite_row_total(db: Path) -> int:
    """Total rows across every ordinary table of a STAGED database copy.

    Safe to run here because the staged file is the backup-API copy, never the live
    store. An unreadable copy contributes 0 rather than failing the snapshot — a
    manifest count is reporting, and reporting must not cost a backup.

    🔴 ``immutable=1``, NOT just ``mode=ro``. Opening a WAL-mode database read-only makes
    SQLite CREATE its ``-shm``/``-wal`` sidecars, and this runs against the STAGED tree
    just before it is tarred — so counting rows put WAL sidecars into the archive that
    the folder copies keep out (``sqlite_files.tree_ignore``). Caught by
    `test_wal_sidecars_never_ride_along`, not by reading the code. ``immutable=1`` is
    correct here (and only here): the staged file came through the backup API, so it is
    fully checkpointed and has no WAL to miss.
    """
    from personalclaw.sqlite_compat import sqlite3 as _sqlite3

    total = 0
    try:
        conn = _sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
    except Exception:  # noqa: BLE001
        return 0
    try:
        names = [
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' " "AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        ]
        for table in names:
            try:
                row = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()
            except Exception:  # noqa: BLE001 — an FTS shadow table can refuse a count
                continue
            total += int(row[0]) if row else 0
    except Exception:  # noqa: BLE001
        return total
    finally:
        conn.close()
    return total


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


def _merge_memory(
    src_db: Path,
    dst_db: Path,
    *,
    left_unchanged: list[str] | None = None,
    label: str = "memory.db",
) -> None:
    """Merge a snapshot's memories into this home's `memory.db`, or into a project's memory
    database (*label*, its home-relative path). A source it cannot read is skipped, and *label*
    goes on *left_unchanged* (as :func:`_merge_sqlite_attach` does)."""
    # Integrity check on source DB before ATTACH
    try:
        from contextlib import closing

        # closing(), like the merge paths above: sqlite3's connection context manager ends
        # the TRANSACTION, not the connection, so a bare `with` leaks the handle until gc.
        with closing(sqlite3.connect(str(src_db))) as check_conn:
            result = check_conn.execute("PRAGMA integrity_check;").fetchone()[0]
        if result != "ok":
            print(f"  ⚠️  Source DB integrity check failed: {result} — skipping merge")
            if left_unchanged is not None:
                left_unchanged.append(label)
            return
    except Exception as e:
        print(f"  ⚠️  Source DB unreadable: {e} — skipping merge")
        if left_unchanged is not None:
            left_unchanged.append(label)
        return

    conn = sqlite3.connect(str(dst_db))
    conn.execute("BEGIN")
    attached = False
    try:
        conn.execute("ATTACH DATABASE ? AS src", (str(src_db),))
        attached = True

        # `contributor` is included only when BOTH databases
        # carry it. Naming it unconditionally made every pre-v9 snapshot fail its whole
        # memory merge — SQLite raises on the missing source column, the handler below
        # logs and SKIPS the table, and the restore reported "imported: 0" while looking
        # like it worked. A restore that silently drops all memory is far worse than one
        # that drops a provenance column, so the column is opportunistic, not required.
        #
        # `embedding_model` rides the same way: the model that wrote each vector. A vector merged
        # without it reads as one with no model recorded — stale until a re-index — so it goes
        # along whenever both sides carry it.
        def _with_optional(base: str, table: str) -> str:
            extra = [c for c in ("contributor", "embedding_model") if _both_have(conn, table, c)]
            return ", ".join([base, *extra])

        for table, cols, where in [
            (
                "semantic_memory",
                _with_optional(
                    "key, value_json, confidence, source, created_at, updated_at, embedding",
                    "semantic_memory",
                ),
                "WHERE is_deleted=0",
            ),
            (
                "episodic_memories",
                _with_optional(
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
    never performed. An imported trigger arrives UNARMED and switched off, and the boot sweep arms
    it here once it is switched on — which is also why importing cannot resurrect a fire that should
    have happened during the move. The rule is `triggers.store.arrived_from_another_home`, the one a
    device sync applies to a peer's trigger store too.

    A home with no store gets the archive's, with every automation in it brought in by the same
    rule: copying the file in whole brought each one in switched on, armed and granted. A home
    that has one is written only when an automation arrives — through the trigger store's own lock
    (``record_files.rewrite``), re-read under it, so an automation the running gateway writes
    meanwhile is neither lost nor written over.
    """
    from personalclaw import record_files
    from personalclaw.triggers.store import STORE_SHAPE, arrived_from_another_home

    src = record_files.read(src_path)
    arriving = [t for t in record_files.records(src_path, STORE_SHAPE) if isinstance(t, dict)]
    imported = 0

    def _bring(dst: Any) -> Any:
        nonlocal imported
        here = dst is not None
        held = STORE_SHAPE.records(dst) if here else None
        rows = [t for t in held or [] if isinstance(t, dict)]
        existing_names = {str(t.get("name") or "") for t in rows}
        existing_ids = {str(t.get("id") or "") for t in rows}
        for trigger in arriving:
            name = str(trigger.get("name") or "")
            if not name or name in existing_names:
                continue
            row = arrived_from_another_home(trigger)
            base = str(row.get("id") or "") or "imported"
            candidate = base
            n = 2
            while candidate in existing_ids:
                candidate = f"{base}-{n}"
                n += 1
            row["id"] = candidate
            existing_ids.add(candidate)
            existing_names.add(name)
            rows.append(row)
            imported += 1
        if here and not imported:
            return None
        outside = {k: v for k, v in src.items() if k != "triggers"} if isinstance(src, dict) else {}
        return STORE_SHAPE.document(dst if here else outside, rows)

    record_files.rewrite(dst_path, _bring, STORE_SHAPE)
    total = len(arriving)
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


def _merge_notifications(
    src_path: Path, dst_path: Path, *, left_unchanged: list[str] | None = None
) -> None:
    """Merge the archive's notifications into the home's, deduped on ``ts`` (each note's id) and
    written in time order: the bell reads the log's end as its newest notes, and the trim at the
    next notification keeps the newest by time (``bounded_log``)."""
    _merge_keyed_jsonl(
        src_path, dst_path, "ts", "Notifications", at="ts", left_unchanged=left_unchanged
    )


def _entry_at(rel: str) -> "StateEntry | None":
    """The inventory entry declared at exactly *rel*, or None."""
    from personalclaw.durability import inventory as inv

    return next((e for e in inv.INVENTORY if e.path == rel), None)


def _records_entry(rel: str) -> "StateEntry | None":
    """The inventory entry for *rel* when it is a store of records (``StateEntry.records``) — a
    file of user records another home's arrive in one at a time — or None."""
    from personalclaw.durability import inventory as inv

    entry = next((e for e in inv.INVENTORY if e.path == rel), None)
    return entry if entry is not None and entry.records is not None else None


def _merge_records(
    snap: Path, pc: Path, rel: str, *, left_unchanged: list[str] | None = None
) -> str | None:
    """Merge the archive's *rel*, a store of records, into this home's: the executor of every
    ``StateEntry.records`` store, for a snapshot's merge and an export archive's import alike.
    Returns the line that says what came, or None when *rel* is no such store, the archive does
    not hold it, or a link is in the way, named on *left_unchanged* (``durability.home_paths``).

    Each record this home does not have comes in, by the store's arrival rule, after the ones it
    has, which stay as they are — the rule a sync brings another machine's in by
    (``durability.reconcile.bring_in``), under the store's own lock. A home with no store gets the
    archive's, every record in it brought in by that rule. Copied in whole, an archive's hooks
    came in switched on and granted; merged as one document, a file the home already had dropped
    every record of the archive's. A file either side cannot read is left as it is: overwriting
    real state with a parse of something not understood is the worse direction.
    """
    from personalclaw.durability.reconcile import bring_in

    entry = _records_entry(rel)
    if entry is None or not (snap / rel).is_file():
        return None
    try:
        came = bring_in(pc, entry, snap / rel)
    except home_paths.LinkInTheWay as link:
        link.put_on(left_unchanged if left_unchanged is not None else [])
        return None
    except ValueError as exc:
        return f"{rel}: left as it is ({exc})"
    if not came:
        return None
    note = f" — {entry.arrival}" if entry.arrival else ""
    return f"{rel}: {came} imported{note}"


def _merge_json_map(src: Path, dst: Path, *, wrapper: str | None = None) -> int:
    """Union a JSON object keyed by entity, live values winning: today the legacy
    ``autonudge.json``, whose ``loops`` are keyed by loop.

    Live values win per key rather than being combined: a key the live home does not have is
    recovery, and a key it has is authoritative. (A machine's counters are not merged at all:
    they are its own account, ``StateEntry.merged_in``.)
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


def _virtual_tables(conn: "sqlite3.Connection", schema: str) -> dict[str, str]:
    """Each virtual table in *schema* and the module it is built on (``fts5``, ``vec0``, …)."""
    found: dict[str, str] = {}
    query = f"SELECT name, sql FROM {schema}.sqlite_master WHERE sql LIKE '%VIRTUAL TABLE%'"
    for name, sql in conn.execute(query):  # noqa: S608 — *schema* is main or src, never input
        module = re.search(r"\bUSING\s+(\w+)", sql or "", re.IGNORECASE)
        found[name] = module.group(1).lower() if module else ""
    return found


def _merge_sqlite_attach(
    src_db: Path, dst_db: Path, label: str, *, left_unchanged: list[str] | None = None
) -> int:
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

    🔴 **Each index is rebuilt by its own module.** The knowledge library keeps a sqlite-vec
    ``vec0`` table beside its FTS5 one, and this sent both FTS5's ``rebuild`` command: the
    statement failed ("no such module: vec0"), the whole library's merge rolled back, and a merge
    restore never brought back a single knowledge item. FTS5 still rebuilds by its command; the
    chunk vector index is rebuilt from the chunk rows by its owner (``knowledge.vector_index``),
    which loads sqlite-vec the way the store does. Where sqlite-vec cannot load, the rows still
    merge and the store's own reconciliation rebuilds the index on its next search.

    🔴 **A table a store numbers itself merges by the key a person knows its rows by.** The knowledge
    library numbers its tags (`INTEGER PRIMARY KEY`, the name unique) and files each item under a
    tag by that number. Another home numbers its own tags from 1 too, so merged by number every
    item of the archive came in filed under this home's tag of the same number: a garden note
    tagged "kitchen". Such a table takes the rows whose key this store lacks under numbers of its
    own, and every reference to it, its own included, follows its row
    (``durability.numbered_rows``). A row of the same name is the same tag, so a repeated merge is
    still a no-op. A table whose rows nothing else names, the learning log's, merges by the
    identity its store gives each row, both databases opened first as the store opens one
    (``durability.merge_source``), so the archive's rows have their identities too.

    `memory.db` keeps its own executor and is NOT routed here: it filters `WHERE is_deleted=0`, so a
    generic all-tables merge would resurrect memories the user deleted. That filter is the reason
    the allowlist exists, not an accident of it.

    What it could not bring in goes on *left_unchanged*: *label* (the store's home-relative path)
    when the store took nothing, and ``label (table)`` for a table it skipped, so a restore's last
    line can say what it left.
    """
    from personalclaw.durability.merge_source import ready_to_merge

    unchanged = left_unchanged if left_unchanged is not None else []
    with ready_to_merge(src_db, dst_db, label, unchanged) as ready:
        return _merge_tables(ready, dst_db, label, unchanged) if ready else 0


def _merge_tables(src_db: Path, dst_db: Path, label: str, unchanged: list[str]) -> int:
    """Take *src_db*'s rows into *dst_db*, table by table (:func:`_merge_sqlite_attach`)."""
    conn = sqlite3.connect(str(dst_db))
    imported = 0
    try:
        # BEGIN IMMEDIATE, not a deferred BEGIN: acquire the destination's write lock UP FRONT.
        # A deferred BEGIN takes no lock until the first INSERT, so a locked destination was only
        # discovered mid-merge — and the per-table `except sqlite3.Error: continue` below then
        # swallowed the "database is locked" error table-by-table, so whether a row landed
        # depended on lock timing (Python's sqlite3 defaults to a 5s busy_timeout). Under CI load
        # that raced: the locked-destination test imported 0 or 1 unpredictably. Taking the lock
        # at BEGIN makes contention fail once, here, falling to the outer skip deterministically.
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("ATTACH DATABASE ? AS src", (str(src_db),))
        virtual = _virtual_tables(conn, "src")
        # A virtual table's shadow tables are `<name>_data`, `_idx`, `_docsize`, `_config`,
        # `_content` (FTS5) or `<name>_chunks`, `_rowids`, `_info`, … (vec0). Matching by prefix
        # covers them without hardcoding either module's internals.
        skip = tuple(virtual) + tuple(v + "_" for v in virtual)
        local = {
            r[0] for r in conn.execute("SELECT name FROM main.sqlite_master WHERE type='table'")
        }
        # Only the tables the live schema has: creating one here would import a shape this build's
        # code cannot read, and the owning module creates its own tables on open. So every name
        # below is this build's, never one the archive chose.
        tables = [
            table
            for (table,) in conn.execute(
                "SELECT name FROM src.sqlite_master WHERE type='table'"
            ).fetchall()
            if not (table.startswith("sqlite_") or table in skip or table.startswith(skip))
            and table in local
        ]
        from personalclaw.durability import numbered_rows

        numbered = numbered_rows.numbered_tables(conn, tables)
        refused: set[str] = set()
        try:
            imported += numbered_rows.merge_numbered(conn, numbered)
        except sqlite3.Error as exc:
            # Without each row's number here, no reference to these tables can follow its row, so
            # neither they nor a table that points into them is merged.
            print(f"  ⚠️  {label}.{', '.join(numbered)}: {exc} — skipped")
            refused = set(numbered)
        for table in tables:
            if table in numbered:
                if table in refused:
                    unchanged.append(f"{label} ({table})")
                continue
            refs = numbered_rows.references(conn, table, numbered)
            if any(target in refused for _column, target in refs):
                print(f"  ⚠️  {label}.{table}: the rows it refers to were not merged — skipped")
                unchanged.append(f"{label} ({table})")
                continue
            before = conn.total_changes
            try:
                if refs:
                    conn.execute(numbered_rows.translated_insert(conn, table, refs))
                else:
                    conn.execute(
                        f'INSERT OR IGNORE INTO main."{table}" SELECT * FROM src."{table}"'
                    )
            except sqlite3.Error as exc:
                # A column-set mismatch between snapshot and live schema. Skip the table, keep the
                # rest — the same call `_merge_memory` makes about its opportunistic `contributor`
                # column, for the same reason: a partial restore beats an aborted one.
                print(f"  ⚠️  {label}.{table}: {exc} — skipped")
                unchanged.append(f"{label} ({table})")
                continue
            imported += conn.total_changes - before
        for view, module in virtual.items():
            if view in local and module == "fts5":
                conn.execute(f'INSERT INTO main."{view}"("{view}") VALUES(\'rebuild\')')
        if imported and "vec0" in _virtual_tables(conn, "main").values():
            from personalclaw.knowledge.vector_index import ChunkVectorIndex

            ChunkVectorIndex(conn).rebuild_all()
        conn.execute("COMMIT")
    except Exception as exc:  # noqa: BLE001
        # ROLLBACK is itself best-effort: when BEGIN IMMEDIATE was the statement that failed
        # (locked destination) no transaction is open, and an unconditional ROLLBACK would raise
        # "no transaction is active" — masking the real skip with a second error.
        # `conn.in_transaction` tells us whether there is anything to roll back.
        if conn.in_transaction:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
        print(f"  ⚠️  {label}: merge failed ({exc}) — left unchanged")
        unchanged.append(label)
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


def _merge_keyed_jsonl(
    src: Path,
    dst: Path,
    key_field: str,
    label: str,
    *,
    at: str,
    left_unchanged: list[str] | None = None,
) -> int:
    """Bring in the rows of `src` that `dst` lacks, deduping on `key_field` (S179), and write the
    log in time order by each row's `at` field (``bounded_log.merge_jsonl``).

    Extracted after a THIRD near-identical copy was needed (`model_calls.jsonl`, whose declared
    `append_dedup` the S178 ratchet demanded an executor for). Three hand-written loops differing
    only in a key name is how two of them start disagreeing — the duplication S175 deleted from the
    run store after finding one copy had silently reverted another.

    In time order, never after the home's own rows: an archive's rows are older, and every reader
    of these logs takes the file's end for its newest — the bell, the model-health tail, "the last
    verdict wins" — while each log's trim kept the last lines and deleted the newest. A log that
    went on changing under the merge is left as it was and named in *left_unchanged*.

    Deliberately NOT used for `security_events.jsonl`: that one carries an HMAC-key precondition,
    and folding a security gate into a generic helper is how the gate gets dropped by a later caller
    who only wanted the dedup.
    """
    if not src.is_file() or not dst.is_file():
        return 0
    try:
        imported = bounded_log.merge_jsonl(src, dst, key=key_field, at=at)
    except OSError as exc:
        print(f"  ⚠️  {label}: {exc}")
        if left_unchanged is not None:
            left_unchanged.append(dst.name)
        return 0
    print(f"  {label} imported: {imported}")
    return imported


def _merge_feedback(src: Path, dst: Path, *, left_unchanged: list[str] | None = None) -> None:
    """Merge `feedback.jsonl`, deduping on the record's own `id` (S178).

    The third `append_dedup` entry with no executor. Unlike the SEL log this carries no HMAC, so
    plain dedup is safe — and unlike the run history it is a single flat file, so there are no
    shards. Keyed on `FeedbackRecord.id` rather than the whole line for the reason
    `_merge_run_history` is: the same record round-trips through a serializer on both sides.

    Deliberately does NOT trim to `feedback._CAP`. That module owns its own retention ("atomic trim
    at 2x cap") and re-implementing the bound here is the duplication S175 deleted from the run
    store after finding one copy had silently reverted the other.
    """
    _merge_keyed_jsonl(src, dst, "id", "Feedback", at="created_at", left_unchanged=left_unchanged)


def _merge_security_events(
    snap: Path, pc: Path, *, left_unchanged: list[str] | None = None
) -> None:
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

    In time order, deduped on ``event_id`` (``bounded_log.merge_jsonl``): the log rotates whole,
    so no row is lost, but its recent-events read and the audit page take the file's end for the
    newest events, and an archive's rows written after the home's own read as what just happened.
    Every record keeps its bytes, so each one's HMAC verifies as it did.
    """
    src = snap / "security_events.jsonl"
    if not src.is_file():
        return
    # Where the home holds the log or its key behind a link, nothing is read or written there.
    left = left_unchanged if left_unchanged is not None else []
    dst = home_paths.landing(pc, "security_events.jsonl", left)
    key = home_paths.landing(pc, "sel_hmac.key", left)
    if dst is None or key is None:
        return

    def _key(p: Path) -> bytes | None:
        try:
            return p.read_bytes()
        except OSError:
            return None

    snap_key, live_key = _key(snap / "sel_hmac.key"), _key(key)
    if not dst.exists():
        # Nothing to merge INTO. The generic store pass already copies a missing file; leaving it
        # to that path keeps one copy-if-missing implementation rather than two.
        return
    if snap_key is None or live_key is None or not hmac.compare_digest(snap_key, live_key):
        print("  Security events: skipped (HMAC key differs — imported rows could not verify)")
        return

    try:
        imported = bounded_log.merge_jsonl(src, dst, key="event_id", at="timestamp")
    except OSError as exc:
        print(f"  ⚠️  Security events: {exc}")
        left.append(dst.name)
        return
    print(f"  Security events imported: {imported}")


def _merge_run_history(
    src_dir: Path, dst_dir: Path, *, left_unchanged: list[str] | None = None
) -> None:
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
    `_index.jsonl`: a shard only the snapshot has is copied whole, and one both have gets the runs
    it lacks, in time order, under the lock the store's own writes take
    (`ScheduleRunStore.merge_in`). *dst_dir* is the home's `cron-history`, as ``home_paths`` gave
    it; a shard behind a link is left as it is and named on *left_unchanged*.

    Deliberately does NOT rotate afterwards. `ScheduleRunStore.rotate_all()` runs at gateway boot
    (S175) and owns that policy; trimming here would apply retention twice with a second copy of the
    rule — the duplication S175 just removed.
    """
    if not src_dir.is_dir():
        return
    from personalclaw.schedule_history import ScheduleRunStore

    try:
        shards, imported = ScheduleRunStore(dst_dir.parent).merge_in(src_dir, left=left_unchanged)
    except OSError as exc:
        print(f"  ⚠️  Run history: {exc}")
        if left_unchanged is not None:
            left_unchanged.append(dst_dir.name)
        return
    print(f"  Run history: {shards} shard(s), {imported} row(s) imported")


def _backup_and_copy(pc: Path, backup: Path, snap: Path, component: str, left: list[str]) -> None:
    """Replace the home's files of *component* with the snapshot's, moving each it has into
    *backup* first; one behind a link is left as it is and named on *left* (``home_paths``)."""
    for f in CORE_FILES.get(component, ()):
        live = home_paths.landing(pc, f, left)
        if live is None:
            continue
        # A database goes aside with the log beside it, so the snapshot's copy never opens with it.
        sqlite_files.move_aside(live, home_paths.home_path(backup, f))
        if (snap / f).is_file():
            if os.path.islink(snap / f):
                print(f"⚠️  Skipping symlinked file from snapshot: {snap / f}")
                continue
            if component == "security":
                # Key material: written 0600 from its first byte, never copied and then tightened.
                atomic_write_bytes(live, (snap / f).read_bytes())
            else:
                home_paths.put_file(snap / f, live)


def _do_replace(snap: Path, pc: Path, components: list[str] | None) -> dict:
    """Replace the home's state with the snapshot's, moving what it displaces into
    ``pre-restore-<ts>/``. Returns what the restore did with app engines, for its result:
    ``engines_kept`` (display names of the apps that kept theirs, :func:`_keep_app_engines`) and
    ``engines_set_aside`` (``{app, bytes}`` of each left in the backup with its app); what it held
    that the snapshot had working (:func:`_hold_what_was_in_flight`); the automations it held
    that the snapshot ran on their own, ``automations_held``, with ``held_from``, where the
    snapshot came from (`triggers.restore_hold`); and ``left_unchanged``, each part it left as
    it is, neither moved aside nor replaced, because a link is in the way (``home_paths``)."""
    from personalclaw.triggers import restore_hold

    # Asked before anything is written: whose snapshot this is, by the home it names.
    held_from = restore_hold.origin(_snapshot_home(snap), pc)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = home_paths.home_path(pc, f"pre-restore-{ts}")
    backup.mkdir(exist_ok=True)
    kept: list[str] = []
    left: list[str] = []
    print("🔄 Replace mode — backing up current state...")

    for comp in ("memory", "crons", "config", "notifications", "security"):
        if _want(components, comp):
            since = len(left)
            _backup_and_copy(pc, backup, snap, comp, left)
            restore_items.said(comp, left, since)

    # The workspace and the skills are set aside as they are, moved and never copied, as every
    # other store is below: a copy took each database in them as bytes, its log at another
    # instant, while anything still had it open, and left out every link in them, which the
    # removal of the folder then deleted.
    for tree in ("workspace", "skills"):
        if not _want(components, tree):
            continue
        since = len(left)
        if (snap / tree).is_dir():
            live = home_paths.landing(pc, tree, left)
            if live is not None:
                sqlite_files.move_aside(live, home_paths.home_path(backup, tree))
                _copytree_safe(snap / tree, live, ignore=sqlite_files.sidecars_in)
        restore_items.said(tree, left, since)

    # 🔴 Every remaining inventory entry — see `_extra_restore_paths`. Replace mode moves
    # the live copy into the pre-restore backup FIRST, so the destructive half stays recoverable
    # exactly as it is for the named components.
    # A NAMED store component selects its own subtree too, so `--components projects` returns the
    # projects without also returning every other store (`_store_selected`).
    restored: list[str] = []
    if _want(components, "everything") or any(
        _store_selected(components, rel) for rel in _extra_restore_paths(snap)
    ):
        since = len(left)
        # A folder before the stores inside it (`workflows` before `workflows/runs.db`): moving
        # the folder moves them with it, and copying the snapshot's brings its copies of them
        # (`_restore_ignore` keeps a store another entry owns), so a store inside one already
        # restored is done. Handled after it, as the inventory's order had it, the folder's copy
        # of the store was moved over the pre-restore copy of this home's, which was lost.
        for rel in sorted(_extra_restore_paths(snap), key=lambda r: r.count("/")):
            if not _store_selected(components, rel):
                continue
            if any(rel.startswith(f"{done}/") for done in restored):
                continue
            live = home_paths.landing(pc, rel, left)
            if live is None:
                continue
            src = snap / rel
            sqlite_files.move_aside(live, home_paths.home_path(backup, rel))
            if src.is_dir():
                _copytree_safe(src, live, ignore=_restore_ignore(rel, src))
            elif src.is_file():
                home_paths.put_file(src, live)
            restored.append(rel)
            if rel == "apps":
                kept = _keep_app_engines(backup / rel, live)
        restore_items.said("stores", left, since)
    held = _hold_what_was_in_flight(pc, restored)

    kept_names = [_app_display_name(pc / "apps" / name) for name in kept]
    if kept_names:
        print(
            f"  ✅ Kept the engine each of these apps had here: {', '.join(kept_names)}. An engine "
            "is built for this machine, so the restore leaves it in place."
        )
    aside = _engines_set_aside(backup / "apps")
    if aside:
        from personalclaw.durability.footprint import human_bytes

        for folder, size in aside:
            print(
                f"  ⚠️  {_app_display_name(folder)}'s engine ({human_bytes(size)}) is in {folder}/ "
                "with the app, which the snapshot does not have. It goes when you delete that "
                "folder."
            )
    if held["runs_held"] or held["loops_held"]:
        print(
            f"  ⏸  Held {len(held['runs_held'])} workflow run(s) and {len(held['loops_held'])} "
            "loop(s) that were working when the snapshot was taken: each waits for you to "
            "resume it."
        )
    # 🔴 Every automation the snapshot ran on its own came back switched on and armed, so a copy
    # restored onto a second machine ran them beside the original, which still did: each brief and
    # digest went out twice. Each waits for a Resume here instead (`triggers.restore_hold`).
    # Not the home's own store, left as it is behind a link: holding it would write through it.
    automations_held: list[str] = []
    if _want(components, "crons") and (snap / "triggers.json").is_file():
        if home_paths.landing(pc, "triggers.json", []) is not None:
            automations_held = restore_hold.hold(pc, held_from)
    if automations_held:
        print(f"  ⏸  {restore_hold.paused_line(len(automations_held), held_from)}")
    try:
        backup.rmdir()
    except OSError:
        print(f"  Previous state saved to: {backup}/")
    print(restore_items.left_unchanged_line(left, "Replace") if left else "✅ Replace complete.")
    return {
        "engines_kept": kept_names,
        "engines_set_aside": [
            {"app": _app_display_name(folder), "bytes": size} for folder, size in aside
        ],
        **held,
        "automations_held": automations_held,
        "held_from": held_from if automations_held else "",
        "left_unchanged": left,
    }


def _say_what_would_pause(snap: Path, pc: Path, components: list[str] | None) -> None:
    """A replace's dry run: how many automations the restore would hold, and why — the line the
    restore itself prints (`_do_replace`), read off the snapshot and nothing written."""
    from personalclaw.triggers import restore_hold

    if not (_want(components, "crons") and (snap / "triggers.json").is_file()):
        return
    count = restore_hold.would_hold(snap / "triggers.json")
    if count:
        held_from = restore_hold.origin(_snapshot_home(snap), pc)
        print(f"  {restore_hold.paused_line(count, held_from, would=True)}")


def _offer_to_resume(pc: Path, done: dict) -> None:
    """After a replace at the terminal, what to do about the automations it held (``done`` is what
    :func:`_do_replace` returned): ask whether to resume them now, when the snapshot is this home's
    own and someone is there to answer; otherwise say where and when to resume them. A snapshot
    from another home, or one that does not say, is never resumed from here: whether that home is
    retired is the owner's to know, and the Triggers page says why each one waits."""
    from personalclaw.triggers import restore_hold
    from personalclaw.triggers.store import TriggerStore

    count = len(done.get("automations_held") or [])
    held_from = str(done.get("held_from") or "")
    if not count:
        return
    if held_from == restore_hold.THIS_HOME and sys.stdin.isatty():
        try:
            answer = input(f"⏸  {restore_hold.resume_question(count)}").strip().lower()
        except EOFError:
            answer = "n"
        if answer in ("", "y", "yes"):
            resumed, kept = restore_hold.resume_all(TriggerStore(base_dir=pc))
            print(f"▶  {restore_hold.resumed_line(len(resumed))}")
            for trigger, why in kept:
                print(f"⚠️  {trigger.name or trigger.id} stays paused: {why}")
            return
    print(f"⏸  {restore_hold.where_to_resume(count, held_from)}")


def _hold_what_was_in_flight(pc: Path, restored: list[str]) -> dict[str, list[str]]:
    """Hold what the stores a replace restore just wrote had in flight: ``runs_held``,
    ``loops_held`` and ``agents_settled``, by id.

    A snapshot is a moment in the past. The watchdogs pick up at start every workflow run and
    loop they find working, and the start settles every agent folder with no tombstone as one
    the last gateway left running — so work the archive held in flight went on here from that
    moment, repeating what it did after it, and on the machine it ran on as well when the archive
    came from another one. And another machine's agent folder names, by its recorded pid, a
    process of this machine's, which the start killed if it had started before the agent did.
    Each store holds its own (``workflows.store.hold_restored``,
    ``loop.store.hold_restored``, ``subagent_persistence.settle_restored``), and nothing starts
    until someone resumes it. Whichever machine took the archive: this machine's own run went on
    after the snapshot too.
    """
    from personalclaw import subagent_persistence
    from personalclaw.loop import store as loop_store
    from personalclaw.workflows import store as run_store

    def restored_store(rel: str) -> bool:
        return any(rel == done or rel.startswith(f"{done}/") for done in restored)

    return {
        "runs_held": run_store.hold_restored(pc) if restored_store("workflows/runs.db") else [],
        "loops_held": loop_store.hold_restored(pc) if restored_store("loop/loops.db") else [],
        "agents_settled": (
            subagent_persistence.settle_restored(pc) if restored_store("subagents") else []
        ),
    }


def home_is_populated(pc: Path) -> list[str]:
    """Declared entries this home already holds — the non-emptiness test (S184).

    🔴 WHY. Restore mode auto-detected on `memory.db` alone: `"merge" if (pc / "memory.db").is_file()
    else "replace"`. A home that has never embedded anything has no `memory.db`, so a home full of
    real state read as EMPTY and defaulted to REPLACE. Driven end to end on a home holding six
    declared entries — tasks, projects, workflows, entity_settings, inbox.json, triggers.json —
    `tasks/mine.json` and the user's automation were moved into `pre-restore-<ts>/` and the
    copies took their place.

    That is recoverable, which is why it is a wrong DEFAULT rather than data loss: the plan's own
    framing is that "the restore people actually perform is onto a machine that already has state …
    and replace-mode restores there destroy the newer half".

    Asks the inventory instead, so any declared store counts. `config.json` alone does not — it is
    written at first boot, so treating it as state would make every fresh install look populated and
    push a genuine first-time restore onto the merge path.
    """
    from personalclaw.durability import inventory as inv

    # `config.json` is written at first boot, so counting it would make every fresh install look
    # populated and push a genuine first-time restore onto the merge path. The other two are
    # machine-local bookkeeping, not user state.
    seeded = {"config.json", "session_map.json", "machine_id"}
    # Secrets are excluded: this list is surfaced over the API, and naming a credential file is
    # needless even though only its EXISTENCE would leak. They also say nothing about whether
    # the home holds work worth protecting, which is the question being asked.
    secret = inv.secret_paths()
    return [
        e.path
        for e in inv.backup_entries()
        if e.path not in seeded
        and not e.derived
        and not e.secret
        and e.path.split("/")[0] not in secret
        and (pc / e.path).exists()
    ]


def merge_plan(snap: Path, pc: Path, components: list[str] | None) -> list[dict]:
    """What a merge WOULD do, per declared entry — the plan `--dry-run` prints (S183).

    🔴 WHY. `--dry-run` printed a raw list of files in the archive: no counts, no per-entry
    strategy, no indication of what is protected. Driven side by side, the dry run listed three
    filenames while the merge imported one notification and one store and left `config.json`
    untouched — so the preview answered a different question from the one a user about to merge
    into their own home is asking. This is gap (2) the plan names against itself: *"`--dry-run`
    prints a raw file list, not a merge plan (no counts, no per-entry strategy, no conflict
    preview)"*.

    Computed from the SAME projections `_do_merge` uses (`_attach_merge_paths`,
    `_extra_restore_paths`, the component gates) rather than by threading a `dry_run` flag through
    twelve merge helpers. Twelve flags are twelve chances for the preview to drift from the act;
    one derivation cannot disagree with itself about which entries participate.

    Each row is `{path, strategy, action, detail}`, where `action` is one of `merge` (both sides
    have it, so rows will be folded), `copy` (only the snapshot has it), `keep-local` (both have
    it and local wins), or `skip` (the snapshot's copy is not taken in: what ran on one machine,
    and its own counters, stay there, `StateEntry.merged_in`).
    """
    from personalclaw.durability import inventory as inv

    rows: list[dict] = []

    def _add(path: str, strategy: str, detail: str = "", *, by_rule: bool = False) -> None:
        # `by_rule`: a store whose rows arrive by a rule (`_merge_triggers`, `_merge_records`) is
        # merged into a home without one too, never copied in whole.
        src, dst = snap / path, pc / path
        if not src.exists():
            return
        if not dst.exists() and not by_rule:
            action = "copy"
        elif strategy == inv.MERGE_REPLACE_ONLY:
            action = "keep-local"
        else:
            action = "merge"
        rows.append({"path": path, "strategy": strategy, "action": action, "detail": detail})

    by_path = {e.path: e for e in inv.INVENTORY}

    if _want(components, "memory"):
        _add("memory.db", inv.MERGE_SQLITE_ATTACH_IGNORE, "4-table allowlist, is_deleted=0 only")
    if _want(components, "crons"):
        _add(
            "triggers.json",
            inv.MERGE_UNION_BY_ID,
            "by name; automations arrive switched off",
            by_rule=True,
        )
        for name in ("event_triggers.json", "crons.json"):
            _add(name, inv.MERGE_UNION_BY_ID, "by job/trigger id")
        _add("cron-history", inv.MERGE_APPEND_DEDUP, "per-shard, dedup on run_id")
    if _want(components, "config"):
        for name in CORE_FILES["config"]:
            if name == "hooks.json":
                continue
            # The contract gap (3) names: an existing config.json is NEVER overwritten. Saying so in
            # the plan is the point — it was true but unstated, so a user could not know it.
            _add(name, inv.MERGE_REPLACE_ONLY, "copy-if-missing; never overwritten")
        _add("hooks.json", inv.MERGE_UNION_BY_ID, "by id; hooks arrive switched off", by_rule=True)
    if _want(components, "notifications"):
        _add("notifications.jsonl", inv.MERGE_APPEND_DEDUP, "dedup on ts")
        _add("feedback.jsonl", inv.MERGE_APPEND_DEDUP, "dedup on id")
        _add("model_calls.jsonl", inv.MERGE_APPEND_DEDUP, "dedup on audit_id")
    if _want(components, "security"):
        for name in CORE_FILES["security"]:
            _add(name, inv.MERGE_REPLACE_ONLY, "copy-if-missing, 0600")
        _add("security_events.jsonl", inv.MERGE_APPEND_DEDUP, "dedup on event_id; HMAC-key gated")
    if _want(components, "workspace"):
        _add("workspace", inv.MERGE_UNION_BY_ID, "tree, no overwrite")
    if _want(components, "skills"):
        _add("skills", inv.MERGE_UNION_BY_ID, "tree, no overwrite")
    if _want(components, "everything"):
        for path in _attach_merge_paths(snap):
            _add(path, inv.MERGE_SQLITE_ATTACH_IGNORE, "every table, INSERT OR IGNORE")
    # The store rows use the SAME selector `_do_merge`/`_do_replace` use, so `--dry-run
    # --components projects` describes the restore that `--components projects` performs. A plan
    # that listed rows the run would skip is worse than no plan: it is a promise about the wrong
    # restore.
    for path in _extra_restore_paths(snap):
        if not _store_selected(components, path):
            continue
        entry = by_path.get(path)
        strategy = entry.merge if entry else inv.MERGE_UNION_BY_ID
        if entry is not None and not entry.merged_in:
            if (snap / path).exists():
                rows.append(
                    {
                        "path": path,
                        "strategy": strategy,
                        "action": "skip",
                        "detail": "this machine's own: the archive's is left out",
                    }
                )
            continue
        if entry is not None and entry.records is not None:
            arrives = "; they arrive switched off" if entry.arrives is not None else ""
            _add(path, strategy, f"by id, one record at a time{arrives}", by_rule=True)
            continue
        if entry is not None and entry.kind == inv.KIND_JSON_ENTITY_DIR:
            # By a rule into a home without the store too when the rule changes what arrives (a
            # workflow's steps, the files that stay on each machine); a copy otherwise.
            by_rule = entry.arrives is not None or bool(entry.machine_local_within)
            _add(
                path,
                strategy,
                "one file at a time, by the rule a sync brings them in",
                by_rule=by_rule,
            )
            continue
        detail = "per-file union" if entry and entry.kind else ""
        if entry is not None and entry.kind == inv.KIND_TREE and entry.partitions:
            detail += "; a database whole, and this home's kept where it has one"
        _add(path, strategy, detail)
    return rows


def _attach_merge_paths(snap: Path) -> list[str]:
    """Declared sqlite stores routed to the generic ATTACH merge (S180's call-site list), and each
    partition of one that the snapshot *snap* holds (`StateEntry.partitions`): a memory
    partition's `learning.db` is merged as `learning.db` is.

    Extracted so `_do_merge` and `merge_plan` cannot disagree about which databases participate — a
    preview that names a different set from the act is worse than no preview.
    """
    try:
        from personalclaw.durability import inventory as inv

        merged = [
            e
            for e in inv.sqlite_entries()
            if e.merge == inv.MERGE_SQLITE_ATTACH_IGNORE and e.path != "memory.db" and e.merged_in
        ]
        ids = {e.id for e in merged}
        return [e.path for e in merged] + [
            rel
            for rel in _partition_paths(snap)
            if getattr(inv.partition_entry(rel), "id", "") in ids
        ]
    except Exception:  # noqa: BLE001 — a restore must work even if this import breaks
        return []


def print_merge_plan(rows: list[dict]) -> None:
    """Render the plan as a table. Grouped by action so the destructive-looking rows are not buried
    among dozens of `copy` lines."""
    if not rows:
        print("  (nothing in this snapshot matches the selected components)")
        return
    order = {"merge": 0, "copy": 1, "keep-local": 2, "skip": 3}
    label = {
        "merge": "MERGE   ",
        "copy": "COPY    ",
        "keep-local": "KEEP    ",
        "skip": "SKIP    ",
    }
    for row in sorted(rows, key=lambda r: (order.get(r["action"], 9), r["path"])):
        detail = f" — {row['detail']}" if row["detail"] else ""
        print(
            f"  {label.get(row['action'], row['action'])} {row['path']:<28} "
            f"[{row['strategy']}]{detail}"
        )
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["action"]] = counts.get(row["action"], 0) + 1
    print("  " + ", ".join(f"{n} {a}" for a, n in sorted(counts.items())))


def _repoint_library_documents(pc: Path) -> None:
    """Point each document a merge brought into the home's knowledge library at its copy in the
    library's files folder now, so it opens at once rather than after the library next opens
    (``knowledge.arrivals.repoint_moved_documents``, which the library's open runs too)."""
    from contextlib import closing

    from personalclaw.knowledge.arrivals import repoint_moved_documents
    from personalclaw.knowledge.store import knowledge_db_path

    # A library the home holds behind a link is never opened here: the merge left it as it is.
    rel = knowledge_db_path(pc, create=False).relative_to(pc).as_posix()
    library = home_paths.landing(pc, rel, [])
    if library is None or not library.is_file():
        return
    try:
        with closing(sqlite3.connect(str(library), isolation_level=None, timeout=10)) as conn:
            repoint_moved_documents(conn, library.parent / "files")
    except (sqlite3.Error, OSError):
        # Not the merge's to fail on: the library points them here when it next opens.
        import logging

        logging.getLogger(__name__).warning(
            "merge: the documents it brought in are pointed here when the library next opens",
            exc_info=True,
        )


def _do_merge(snap: Path, pc: Path, components: list[str] | None) -> list[str]:
    """Merge *snap* into the home at *pc*, and return what it left unchanged (empty when every
    part came in): a store that took nothing, a table it skipped, or a part behind a link
    (``home_paths``). The last line says which, so a restore that left the knowledge library as it
    was never ends "✅ Merge complete."."""
    print("🔀 Merge mode — importing...")
    left: list[str] = []

    if _want(components, "memory") and (snap / "memory.db").is_file():
        since = len(left)
        merge = functools.partial(_merge_memory, left_unchanged=left)
        how = restore_items.merged_or_brought_in(snap, pc, "memory.db", left, merge)
        if how == "copied":
            print("  Memory: copied (no existing memory.db)")
            # Its index comes in only where the home has none: one the home has stays its own.
            restore_items.merged_or_brought_in(
                snap, pc, "memory_index.db", left, lambda _src, _dst: None
            )
        elif not how and len(left) == since:
            left.append("memory.db")  # a folder is at its path: left as it is
        restore_items.said("memory", left, since)

    if _want(components, "crons"):
        since = len(left)
        if (snap / "triggers.json").is_file():
            triggers = home_paths.landing(pc, "triggers.json", left)
            if triggers is not None:
                # Into a home with no store too: every automation arrives by the one rule.
                _merge_triggers(snap / "triggers.json", triggers)
        if restore_items.merged_or_brought_in(
            snap, pc, "event_triggers.json", left, _merge_event_triggers
        ) == ("copied"):
            print("  Event triggers: copied (none existing)")
        if (
            restore_items.merged_or_brought_in(snap, pc, "crons.json", left, _merge_crons)
            == "copied"
        ):
            print("  Legacy crons: copied (no existing crons)")
        restore_items.said("automations", left, since)

    if _want(components, "config"):
        since = len(left)
        for f in CORE_FILES["config"]:
            if f == "hooks.json" or not (snap / f).is_file():
                continue  # hooks are merged below, by the rule a hook from elsewhere arrives by
            d = home_paths.landing(pc, f, left)
            if d is not None and not d.is_file() and sqlite_files.bring_in(snap / f, d):
                print(f"  {f}: restored (was missing)")
        said = _merge_records(snap, pc, "hooks.json", left_unchanged=left)
        if said:
            print(f"  {said}")
        restore_items.said("config", left, since)

    # 🔴 The run history, whose declared `append_dedup` had no executor. Grouped with
    # `crons` because it IS the crons' history: a merge restore that recovered the triggers but not
    # their runs leaves a user with automations and no record of what they ever did.
    if _want(components, "crons") and (snap / "cron-history").is_dir():
        history = home_paths.landing(pc, "cron-history", left)
        if history is not None:
            _merge_run_history(snap / "cron-history", history, left_unchanged=left)

    if _want(components, "notifications"):
        since = len(left)
        merge = functools.partial(_merge_notifications, left_unchanged=left)
        if (
            restore_items.merged_or_brought_in(snap, pc, "notifications.jsonl", left, merge)
            == "copied"
        ):
            print("  Notifications: copied")
        # `feedback.jsonl`, the third declared `append_dedup` with no executor. Grouped here rather
        # than given its own component: both are platform-domain append logs, and a new component
        # name is a CLI surface a user then has to know about.
        if (snap / "feedback.jsonl").is_file():
            feedback = home_paths.landing(pc, "feedback.jsonl", left)
            if feedback is not None:
                _merge_feedback(snap / "feedback.jsonl", feedback, left_unchanged=left)
        # `model_calls.jsonl`, the fourth declared `append_dedup` — demanded by its own ratchet
        # the moment S179 declared the entry. Keyed on `AttemptRecord.audit_id`.
        if (snap / "model_calls.jsonl").is_file():
            calls = home_paths.landing(pc, "model_calls.jsonl", left)
            if calls is not None:
                _merge_keyed_jsonl(
                    snap / "model_calls.jsonl",
                    calls,
                    "audit_id",
                    "Model calls",
                    at="ts",
                    left_unchanged=left,
                )
        restore_items.said("notifications", left, since)

    if _want(components, "security"):
        since = len(left)
        for f in CORE_FILES["security"]:
            if not (snap / f).is_file():
                continue
            d = home_paths.landing(pc, f, left)
            if d is not None and not d.is_file():
                # Key material: written 0600 from its first byte, never copied and then tightened.
                atomic_write_bytes(d, (snap / f).read_bytes())
                print(f"  {f}: restored (was missing)")
        # The SEL audit log, whose declared `append_dedup` had no executor. Placed AFTER the key
        # copy above, because whether the imported rows can verify depends on which key won.
        _merge_security_events(snap, pc, left_unchanged=left)
        restore_items.said("security", left, since)

    if _want(components, "workspace"):
        since = len(left)
        workspace = (
            home_paths.landing(pc, "workspace", left) if (snap / "workspace").is_dir() else None
        )
        if workspace is not None:
            workspace.mkdir(parents=True, exist_ok=True)
            # A project's memories are merged into the partition this home also has, as
            # `memory.db`'s are above; the copy below brings a partition this home lacks.
            for part in _partition_paths(snap, of="memory_db"):
                here = home_paths.landing(pc, part, left)
                if here is not None and here.is_file():
                    _merge_memory(snap / part, here, left_unchanged=left, label=part)
            restore_items.copy_tree_no_overwrite(snap / "workspace", pc, "workspace", left)
        restore_items.said("workspace", left, since)

    if _want(components, "skills"):
        since = len(left)
        skills = home_paths.landing(pc, "skills", left) if (snap / "skills").is_dir() else None
        if skills is not None:
            skills.mkdir(parents=True, exist_ok=True)
            restore_items.copy_tree_no_overwrite(snap / "skills", pc, "skills", left)
        restore_items.said("skills", left, since)

    # 🔴 Every remaining inventory entry. The capture side stages these; neither restore
    # mode read them, so a merge recovered the automations and silently dropped the task board.
    # Gated on `everything` so a targeted `--components memory` stays targeted — but that is also
    # the default (`components is None`), which is the invocation a user in a recovery actually
    # types.
    # 🔴 The six declared sqlite stores whose `sqlite_attach_ignore` had no executor. Runs
    # BEFORE the generic store pass so a DB the live home already holds is MERGED rather than left
    # alone; the pass below then copies any that are missing entirely.
    #
    # Driven off the inventory, not a hardcoded list, so a store declared later merges by default —
    # the same reason capture reads `backup_entries()`. `memory.db` is excluded: its own executor
    # filters `WHERE is_deleted=0`, and a generic all-tables merge would resurrect deleted memories.
    if _want(components, "everything"):
        for rel in _attach_merge_paths(snap):
            if not (snap / rel).is_file():
                continue
            d_db = home_paths.landing(pc, rel, left)
            if d_db is not None and d_db.is_file():
                _merge_sqlite_attach(snap / rel, d_db, rel, left_unchanged=left)

    # `autonudge.json`, the legacy auto-nudge loops, is a map keyed by loop under `loops`: each
    # the live home lacks comes in, and the boot's legacy import brings it into the trigger store
    # switched off (`triggers.legacy_import`). Runs BEFORE the generic store pass so a file the
    # live home already holds is MERGED rather than left alone; that pass copies one missing
    # outright. A store of records (`StateEntry.records` — the inbox, the tags, …) is merged in
    # that pass, one record at a time (`_merge_records`).
    #
    # 🔴 This also merged `spend.json`, `tool_usage.json` and `tokenjuice_savings.json` — the
    # keys the live home lacked came in — and the generic pass copied `durability_state.json`
    # into a home without one. Each is this machine's own account (`StateEntry.merged_in`):
    # another machine's spend counted against this machine's budget caps, and its scheduler
    # marks read as backups this machine had just taken. They are left as they are.
    if _want(components, "everything") and (snap / "autonudge.json").is_file():
        nudges = home_paths.landing(pc, "autonudge.json", left)
        n = _merge_json_map(snap / "autonudge.json", nudges, wrapper="loops") if nudges else 0
        if n:
            print(f"  autonudge.json: {n} imported")

    # `_store_selected` so `--components projects` merges the projects alone — the same gate the
    # replace path uses, asked once so the two modes cannot answer it differently.
    if any(_store_selected(components, rel) for rel in _extra_restore_paths(snap)):
        from personalclaw.durability import inventory as inv
        from personalclaw.durability.reconcile import bring_in_folder

        since = len(left)
        restored = []
        for rel in _extra_restore_paths(snap):
            if not _store_selected(components, rel):
                continue
            src = snap / rel
            entry = _entry_at(rel)
            if entry is not None and not entry.merged_in:
                # What ran on the archived machine and its own counters stay its own: its running
                # runs, loops and agents would be picked up here, its spend counted against this
                # machine's caps (`StateEntry.merged_in`).
                continue
            dst = home_paths.landing(pc, rel, left)
            if dst is None:
                continue
            if entry is not None and entry.kind == inv.KIND_JSON_ENTITY_DIR and src.is_dir():
                # A folder of files, each taken in by the rule a sync takes another machine's in:
                # what its machine's owner allowed there arrives waiting for this one's.
                if bring_in_folder(pc, entry, src, left_unchanged=left):
                    restored.append(rel)
            elif src.is_dir():
                dst.mkdir(parents=True, exist_ok=True)
                restore_items.copy_tree_no_overwrite(src, pc, rel, left, entry_path=rel)
                restored.append(rel)
            elif _records_entry(rel) is not None:
                # A store of records: each of the archive's this home lacks comes in, into a home
                # without one too — never the file copied in whole.
                said = _merge_records(snap, pc, rel, left_unchanged=left)
                if said:
                    print(f"  {said}")
            elif src.is_file() and sqlite_files.bring_in(src, dst):
                # A file the live home does not have. An EXISTING file is left alone: merge
                # mode's contract is that local state wins, and these entries have no
                # field-level merge executor yet (their declared strategies are the 13 the
                # queue tracks) — so copy-if-missing is the honest half, not a silent overwrite.
                restored.append(rel)
        if restored:
            print(f"  Stores: recovered {len(restored)} ({', '.join(sorted(restored)[:6])}…)")
        restore_items.said("stores", left, since)

    if _want(components, "workspace") or _want(components, "everything"):
        # The library's rows and its files are both in now: its documents open here at once.
        _repoint_library_documents(pc)

    if _want(components, "memory") or _want(components, "everything"):
        # Memories and knowledge came in with the vectors their home wrote, and another model's
        # are not searchable here until embedded again: the re-index path re-embeds what the
        # model bound here did not embed, taken by the gateway's watch (`embedding_arrivals`).
        from personalclaw import embedding_arrivals

        embedding_arrivals.arrived()
    print(restore_items.left_unchanged_line(left) if left else "✅ Merge complete.")
    return left


def _running_gateway() -> "LiveGateway | None":
    """The gateway of THIS home that is up, on the socket it actually bound — its port and pid,
    which a refusal names — or ``None``.

    Asks ``gateway_base.live_port()`` — the port a live gateway of this home published
    after binding — instead of ``config.loader.DASHBOARD_PORT``. That constant is the
    import-time ``PERSONALCLAW_PORT``-or-10000 guess, and probing it was wrong in BOTH
    directions (#2539): on a gateway started with ``--port 10188`` it probed a socket
    nobody was listening on and reported "not running" while this very process served the
    request (`DAS-10`, see ``dashboard/handlers/durability.py``); and on a multi-instance
    host it could equally report "running" because a DIFFERENT instance answered on 10000,
    refusing a legitimate restore.

    No live record ⇒ no gateway of this home ⇒ ``None``. The record is written after bind
    and removed on shutdown, and one naming a dead pid is ignored, so this cannot be
    satisfied by a stale file or by a stranger occupying a shared port.
    """
    from personalclaw import gateway_base

    gateway = gateway_base.live_gateway()
    if gateway is None:
        return None
    try:
        with socket.create_connection(("127.0.0.1", gateway.port), timeout=1):
            return gateway
    except OSError:
        return None


def restore_plan(archive: Path, components: list[str] | None) -> dict:
    """The merge plan for `archive`, as data — the API's read-only half (S184).

    Shares `merge_plan()` with the CLI's `--dry-run`, so the endpoint cannot describe a different
    restore from the one the terminal describes. Writes nothing.
    """
    with tempfile.TemporaryDirectory() as work_str:
        work = Path(work_str)
        with tarfile.open(str(archive), "r:gz") as tar:
            tar.extractall(work, filter=_data_filter)
        roots = [p for p in work.iterdir() if p.is_dir()]
        if not roots:
            return {"ok": False, "error": "invalid snapshot format"}
        pc = _pc_dir()
        populated = home_is_populated(pc)
        return {
            "ok": True,
            "snapshot": archive.name,
            "home_populated": bool(populated),
            "existing_stores": sorted(populated),
            "proposed_mode": "merge" if populated else "replace",
            "plan": merge_plan(roots[0], pc, components),
        }


#: What a merge that runs inside the gateway leaves to its next start: the stores it keeps open and
#: indexed — memory's vector index, the search indexes, what it caches — read what a merge wrote
#: into them once it starts again. Both of the dashboard's merges say it.
MERGE_RESTART_NOTE = "Restart the gateway to pick up everything the merge brought in."


def replace_refusal(what: str, path: str, gateway: "LiveGateway | None") -> str:
    """Why a replace is refused while the gateway runs, and the command that does it: the one
    answer the dashboard (``409 gateway_running``) and ``personalclaw restore`` both give.

    One restore rule for both: a merge only fills in what the home lacks, through the same locks
    the running gateway's stores write under, so it runs while the gateway runs, from either; a
    replace moves the live home aside under a gateway that holds that state open — databases,
    caches, stores it writes back — so it runs with the gateway stopped. *what* names the restore
    (``restore``, ``import``); *path* is the archive, quoted for a shell, or a placeholder.

    *gateway* is the running gateway as this home's live record names it, so the refusal says
    which process holds the state open: a gateway started outside the service is one that
    ``personalclaw stop`` may not be the way to stop. ``None`` when no record names one.
    """
    running = (
        f"the running gateway (pid {gateway.pid}, port {gateway.port})"
        if gateway is not None
        else "the running gateway"
    )
    return (
        f"A replace {what} rewrites state {running} holds open. Stop the gateway "
        f"(`personalclaw stop`), then run `personalclaw restore {path} --mode replace`."
    )


def _replace_refused(args: argparse.Namespace, mode: str, what: str) -> bool:
    """Whether this command refuses the restore it was asked for: a replace while this home's
    gateway runs, without ``--force`` — a local operator's decision, taken at the terminal. Says
    why on stderr, in the words the dashboard refuses one in (:func:`replace_refusal`). A merge is
    never refused for a running gateway."""
    if mode != "replace" or getattr(args, "force", False):
        return False
    gateway = _running_gateway()
    if gateway is None:
        return False
    _audit("state_restore_rejected", "reason=gateway_running")
    print(f"❌ {replace_refusal(what, shlex.quote(str(args.snapshot)), gateway)}", file=sys.stderr)
    return True


def restore_merge(archive: Path, components: list[str] | None) -> dict:
    """Merge `archive` into this home: the dashboard's restore.

    A merge only fills in what the home lacks, so it runs inside the gateway, as an archive
    import's merge does. A replace does not run here at all: it rewrites state the gateway holds
    open, so the route refuses one and names `personalclaw restore … --mode replace`, which runs
    with the gateway stopped (`dashboard.handlers.durability._replace_refused`). This used to
    probe for a running gateway first and refuse — and from inside the gateway the probe always
    found one, so a merge from the dashboard never ran.

    ``left_unchanged`` names each part it could not bring in (empty when every part came in), so
    the page never reads a partial merge as a whole one.
    """
    with tempfile.TemporaryDirectory() as work_str:
        work = Path(work_str)
        with tarfile.open(str(archive), "r:gz") as tar:
            tar.extractall(work, filter=_data_filter)
        roots = [p for p in work.iterdir() if p.is_dir()]
        if not roots:
            return {"ok": False, "error": "invalid snapshot format"}
        pc = _pc_dir()
        pc.mkdir(parents=True, exist_ok=True)
        left = _do_merge(roots[0], pc, components)
    _audit("state_restored", f"mode=merge snapshot={archive.name} left_unchanged={len(left)}")
    return {
        "ok": True,
        "mode": "merge",
        "snapshot": archive.name,
        "left_unchanged": left,
        "restart": MERGE_RESTART_NOTE,
    }


def _restore_export_archive(archive: Path, args: argparse.Namespace) -> int:
    """`personalclaw restore <export.zip>`: put an export archive (Settings → Import / Export)
    back into this home — merged, as the dashboard's Import does, or replacing the home, which the
    dashboard refuses while it runs and names this command for. The archive is checked as the
    dashboard checks it (`portability.validate_import_zip`) before anything is written, and
    applied by the same `portability.apply_import_zip`, so the two cannot restore an archive
    differently. A replace is refused while the gateway runs (:func:`_replace_refused`), as the
    dashboard refuses one.
    """
    from personalclaw.portability import apply_import_zip, validate_import_zip

    if args.components:
        print(
            "❌ --components selects parts of a snapshot; an export archive is restored whole",
            file=sys.stderr,
        )
        return 2
    ok, error, manifest = validate_import_zip(archive)
    if not ok:
        print(f"❌ {error}", file=sys.stderr)
        return 1
    pc = _pc_dir()
    populated = home_is_populated(pc)
    mode = args.mode or ("merge" if populated else "replace")
    if args.mode is None and populated:
        print(f"🔀 Home holds {len(populated)} existing store(s) — proposing MERGE mode.")
        print("   Use --mode replace to overwrite instead (previous state is kept aside).")
    checked = "checksums verified" if manifest.get("verified") else "no checksums to verify"
    print(f"📦 Export archive {archive.name} (format v{manifest.get('version')}, {checked})")
    if args.dry_run:
        print(f"\n🔍 Dry run — would import it into {pc} in {mode} mode")
        if mode == "replace":
            print(f"  Current state would be moved to {pc}/pre-restore-<timestamp>/")
        return 0
    if _replace_refused(args, mode, "import"):
        return 1

    pc.mkdir(parents=True, exist_ok=True)
    # A replace says where it set the previous state aside as it does it (`_do_replace`).
    summary = apply_import_zip(archive, mode)
    held = len(summary.get("automations_held") or [])
    # What a merge could not bring in, as a snapshot's merge names it, and what a replace left as
    # it is behind a link: the last line says which, and the command fails, so an import that left
    # the knowledge library as it was never ends as one that brought it in.
    left = list(summary.get("left_unchanged") or [])
    _audit(
        "state_restored",
        f"mode={mode} components=all from={archive.name} left_unchanged={len(left)}"
        + (f" automations_held={held} held_from={summary.get('held_from')}" if held else ""),
    )
    print(
        f"{'⚠️ ' if left else '✅'} Imported ({mode}): "
        f"{', '.join(summary.get('items', [])) or 'nothing to add'}"
    )
    if left:
        print(restore_items.left_unchanged_line(left, "Replace" if mode == "replace" else "Merge"))
    _offer_to_resume(pc, summary)
    print("\n⚠️  Restart personalclaw gateway to pick up changes: personalclaw restart")
    return 1 if left else 0


def restore_main(argv: list[str] | None = None, *, parsed: argparse.Namespace | None = None) -> int:
    if parsed is None:
        p = argparse.ArgumentParser(
            prog="personalclaw-restore",
            description="Restore PersonalClaw state from a snapshot or an export archive.",
        )
        p.add_argument("snapshot", nargs="?")
        p.add_argument("--mode", choices=("replace", "merge"))
        p.add_argument("--dry-run", action="store_true")
        p.add_argument(
            "--force", action="store_true", help="Replace even while the gateway is running"
        )
        p.add_argument("--components")
        p.add_argument("--list-components", action="store_true")
        parsed = p.parse_args(argv)
    args = parsed

    if args.list_components:
        _list_components()
        return 0

    if not args.snapshot:
        # A usage error, so the usage-error status: nothing on the command line to restore.
        print("❌ snapshot file is required (unless --list-components is given)", file=sys.stderr)
        return 2

    snap_path = Path(args.snapshot)
    if not snap_path.is_file():
        print(f"❌ File not found: {snap_path}", file=sys.stderr)
        return 1
    if zipfile.is_zipfile(snap_path):
        return _restore_export_archive(snap_path, args)

    # Parse components
    components: list[str] | None = None
    if args.components:
        components = [c.strip() for c in args.components.split(",")]
        for c in components:
            if c not in VALID_COMPONENTS:
                print(f"❌ Unknown component: {c}\n", file=sys.stderr)
                _list_components(sys.stderr)
                return 1

    pc = _pc_dir()
    # Inventory-based, not `memory.db`-based: any declared store makes this home populated, and a
    # populated home must default to MERGE so a restore cannot displace newer local state.
    populated = home_is_populated(pc)
    mode = args.mode or ("merge" if populated else "replace")
    if args.mode is None and populated:
        print(f"🔀 Home holds {len(populated)} existing store(s) — proposing MERGE mode.")
        print("   Use --mode replace to overwrite instead (previous state is kept aside).")

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
            print("❌ Invalid snapshot format", file=sys.stderr)
            return 1
        snap = snap_dirs[0]

        _print_manifest(snap)
        if components:
            print(f"🔧 Components: {','.join(components)}")

        if args.dry_run:
            print(f"\n🔍 Dry run — would restore to {pc} in {mode} mode")
            if mode == "merge":
                # The PLAN, not a file listing: per-entry strategy and what happens to each. A raw
                # list answered a different question from the one a user about to merge is asking.
                print("Merge plan:")
                print_merge_plan(merge_plan(snap, pc, components))
            else:
                # Replace mode is wholesale by definition, so the honest preview is what travels
                # plus where the current state goes.
                print("Files in snapshot:")
                for f in sorted(snap.rglob("*")):
                    if f.is_file():
                        print(f"  {f.relative_to(snap)}")
                print(f"  Current state would be moved to {pc}/pre-restore-<timestamp>/")
                print("  An app the snapshot brings back keeps the engine it has here.")
                _say_what_would_pause(snap, pc, components)
            return 0
        if _replace_refused(args, mode, "restore"):
            return 1

        pc.mkdir(parents=True, exist_ok=True)
        left: list[str] = []
        done: dict = {}
        if mode == "replace":
            done = _do_replace(snap, pc, components)
            left = list(done.get("left_unchanged") or [])
        else:
            left = _do_merge(snap, pc, components)
        engines = _engines_not_here(snap, components)

    # Integrity check of the memory.db the restore left here: never one behind a link.
    memory = home_paths.landing(pc, "memory.db", []) if _want(components, "memory") else None
    if memory is not None and memory.is_file():
        try:
            from contextlib import closing

            with closing(sqlite3.connect(str(memory))) as conn:
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
    held = len(done.get("automations_held") or [])
    _audit(
        "state_restored",
        f"mode={mode} components={comp_str} from={snap_path.name} left_unchanged={len(left)}"
        + (f" automations_held={held} held_from={done.get('held_from')}" if held else ""),
    )

    for name, has_one in engines:
        if has_one:
            print(
                f"⚠️  {name}'s engine here does not match the version the restore brought back. "
                "Install engine, on the app's card in Settings → Providers, brings it up to date."
            )
            continue
        print(
            f"⚠️  {name} has no engine here: a snapshot leaves engines out, since each one is "
            "built for the machine it runs on. Install it with Install engine, on the app's card "
            "in Settings → Providers."
        )
    _offer_to_resume(pc, done)
    print("\n⚠️  Restart personalclaw gateway to pick up changes: personalclaw restart")
    # A merge that left a part unchanged did not do what it was asked: the failed status, as the
    # integrity check above answers a damaged memory.db, so a script sees it too.
    return 1 if left else 0
