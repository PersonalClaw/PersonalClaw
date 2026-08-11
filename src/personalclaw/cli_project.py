"""`personalclaw project export|import` — one project as a manifest ZIP (S54, C9).

Distinct from `snapshot`, which is whole-home. A user handing a colleague one project has no
business shipping their memory database, and the archive's secret exclusion plus per-entity digests
are what make the narrower artifact safe to send.

Both commands REPORT what did not travel. An export that prints only "wrote 12 entities" hides the
two facts the user has to act on — what was skipped, and which credentials the far side will ask for
— and the archive itself cannot tell them, because the whole point is that the values are absent.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from personalclaw.workflows import project_archive as pa


def _resolve_project(ident: str) -> Any | None:
    """A project by id, else by exact name. Both, because a user types whichever they can see."""
    from personalclaw.tasks.hierarchy import HierarchyStore

    store = HierarchyStore()
    found = store.get_project(ident)
    if found is not None:
        return found
    return store.get_project_by_name(ident)


def _gather(pid: str) -> tuple[list[dict], list[dict]]:
    """Artifact metadata + run digests. One unreadable store must not cost the whole export."""
    artifacts: list[dict] = []
    runs: list[dict] = []
    try:
        from personalclaw.artifacts import registry as artifact_registry

        provider = artifact_registry.get_provider()
        if provider is not None:
            artifacts = [a.to_dict() for a in provider.list(project_id=pid)]
    except Exception:  # noqa: BLE001
        print("⚠️  artifact metadata unavailable; exporting without it")
    try:
        from personalclaw.workflows import store as wf_store

        rows, _total = wf_store.list_runs(project_id=pid, limit=1000)
        runs = [r.to_dict() for r in rows]
    except Exception:  # noqa: BLE001
        print("⚠️  run digests unavailable; exporting without them")
    return artifacts, runs


def _export(args: argparse.Namespace) -> int:
    from personalclaw.config.loader import config_dir

    project = _resolve_project(args.project)
    if project is None:
        print(f"❌ No project matches {args.project!r}")
        return 1

    if args.passphrase and not pa.encryption_available():
        print(
            "❌ Encryption needs the optional `cryptography` extra: "
            "pip install 'personalclaw[oauth2]'"
        )
        return 1

    artifacts, runs = _gather(project.id)
    project_root = config_dir() / "projects" / project.id
    try:
        raw, plan = pa.export_project_archive(
            project.id,
            project_root=project_root,
            project_name=project.name,
            artifacts=artifacts,
            runs=runs,
            passphrase=args.passphrase or "",
        )
    except pa.ArchiveRefused as exc:
        print(f"❌ Export refused ({exc.reason}): {exc.detail}")
        return 1

    out = (
        Path(args.output)
        if args.output
        else Path.cwd()
        / pa.archive_filename(project.name, project.id, encrypted=bool(args.passphrase))
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(raw)
    # 0600: an unencrypted archive holds the project's brief and context, which is the user's own
    # writing about their work, and a world-readable copy in a shared /tmp is a leak with no upside.
    out.chmod(0o600)

    size = len(raw)
    human = f"{size // 1024}K" if size < 1024 * 1024 else f"{size / 1024 / 1024:.1f}M"
    print(f"✅ Exported {project.name} → {out} ({human})")
    print(
        f"   {len(plan.entries)} entities · {plan.artifact_count} artifact(s) · "
        f"{plan.run_count} run(s)"
    )
    if plan.skipped:
        print(f"   ⤺ {len(plan.skipped)} left behind:")
        for line in plan.skipped[:10]:
            print(f"     · {line}")
        if len(plan.skipped) > 10:
            print(f"     · … and {len(plan.skipped) - 10} more")
    if plan.secrets_present:
        # Named, never carried. An importer that does not know a credential is expected produces a
        # project that looks complete and fails on its first run.
        print(
            f"   🔑 {len(plan.secrets_present)} credential(s) must be re-entered on the far side: "
            f"{', '.join(sorted(plan.secrets_present))}"
        )
    if args.passphrase:
        print("   🔒 Encrypted (AES-GCM). Without the passphrase this archive cannot be read.")
    return 0


def _import(args: argparse.Namespace) -> int:
    from personalclaw.config.loader import config_dir
    from personalclaw.tasks.hierarchy import HierarchyStore
    from personalclaw.workflows.project_export import import_summary

    archive = Path(args.archive).expanduser()
    if not archive.is_file():
        print(f"❌ No such archive: {archive}")
        return 1

    store = HierarchyStore()
    existing = [p.name for p in store.list_projects()]
    try:
        plan, extracted = pa.read_archive_plan(
            archive, existing_names=existing, passphrase=args.passphrase or ""
        )
    except pa.ArchiveRefused as exc:
        print(f"❌ Refused ({exc.reason}): {exc.detail}")
        return 1
    except pa.EncryptionUnavailable as exc:
        print(f"❌ {exc}")
        return 1

    print(import_summary(plan))
    if plan.refused:
        print(f"   ✖ {len(plan.refused)} refused:")
        for issue in plan.refused[:10]:
            print(f"     · {issue.path}: {issue.message}")
        if len(plan.refused) > 10:
            print(f"     · … and {len(plan.refused) - 10} more")

    if args.dry_run:
        print("   (dry run — nothing was written)")
        return 0
    if not plan.ok:
        print("❌ Nothing importable in this archive.")
        return 1

    created = store.create_project(plan.project_name)
    written = pa.commit_import(plan, extracted, project_root=config_dir() / "projects" / created.id)
    print(f"✅ Imported as {created.name} ({created.id}) — {len(written)} entities written")
    if plan.secrets_expected:
        print(
            f"   🔑 Re-enter {len(plan.secrets_expected)} credential(s): "
            f"{', '.join(plan.secrets_expected)}"
        )
    return 0


def project_main(args: argparse.Namespace) -> int:
    command = getattr(args, "project_command", "") or ""
    if command == "export":
        return _export(args)
    if command == "import":
        return _import(args)
    print("Usage: personalclaw project {export|import} …")
    return 1
