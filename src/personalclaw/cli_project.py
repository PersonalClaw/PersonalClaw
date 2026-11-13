"""`personalclaw project export|import` — one project as a manifest ZIP.

Distinct from `snapshot`, which is whole-home. A user handing a colleague one project has no
business shipping their memory database, and the archive's secret exclusion plus per-entity digests
are what make the narrower artifact safe to send.

Both commands REPORT what did not travel. An export that prints only "wrote 12 entities" hides the
two facts the user has to act on — what was skipped, and which credentials the far side will ask for
— and the archive itself cannot tell them, because the whole point is that the values are absent.
"""

from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path
from typing import Any

from personalclaw.atomic_write import write_private_file
from personalclaw.security import terminal_text
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
        print("⚠️  artifact metadata unavailable; exporting without it", file=sys.stderr)
    try:
        from personalclaw.workflows import store as wf_store

        rows, _total = wf_store.list_runs(project_id=pid, limit=1000)
        runs = [r.to_dict() for r in rows]
    except Exception:  # noqa: BLE001
        print("⚠️  run digests unavailable; exporting without them", file=sys.stderr)
    return artifacts, runs


def _export(args: argparse.Namespace) -> int:
    from personalclaw.config.loader import config_dir

    project = _resolve_project(args.project)
    if project is None:
        print(f"❌ No project matches {args.project!r}", file=sys.stderr)
        return 1

    if args.passphrase and not pa.encryption_available():
        print(
            "❌ Encryption needs the `cryptography` package, which PersonalClaw depends on and "
            "this environment is missing: pip install 'cryptography>=42'",
            file=sys.stderr,
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
        print(
            f"❌ Export refused ({exc.reason}): {terminal_text(str(exc.detail))}", file=sys.stderr
        )
        return 1

    out = (
        Path(args.output)
        if args.output
        else Path.cwd()
        / pa.archive_filename(project.name, project.id, encrypted=bool(args.passphrase))
    )
    # Private from its first byte (`atomic_write.write_private_file`): an unencrypted archive holds
    # the project's brief and context, which is the user's own writing about their work, and a
    # world-readable copy in a shared /tmp is a leak with no upside.
    write_private_file(out, raw)

    size = len(raw)
    human = f"{size // 1024}K" if size < 1024 * 1024 else f"{size / 1024 / 1024:.1f}M"
    print(f"✅ Exported {terminal_text(str(project.name))} → {out} ({human})")
    print(
        f"   {len(plan.entries)} entities · {plan.artifact_count} artifact(s) · "
        f"{plan.run_count} run(s)"
    )
    if plan.skipped:
        print(f"   ⤺ {len(plan.skipped)} left behind:")
        for line in plan.skipped[:10]:
            line = terminal_text(str(line))
            print(f"     · {line}")
        if len(plan.skipped) > 10:
            print(f"     · … and {len(plan.skipped) - 10} more")
    if plan.secrets_present:
        # Named, never carried. An importer that does not know a credential is expected produces a
        # project that looks complete and fails on its first run.
        plan.secrets_present = [terminal_text(str(name)) for name in plan.secrets_present]
        print(
            f"   🔑 {len(plan.secrets_present)} credential(s) must be re-entered on the far side: "
            f"{', '.join(sorted(plan.secrets_present))}"
        )
    if args.passphrase:
        print("   🔒 Encrypted (AES-GCM). Without the passphrase this archive cannot be read.")
    return 0


def _refused_by_a_link(link: Exception) -> int:
    print(
        f"❌ {terminal_text(str(link))}, so no project can be imported here until the link is gone.",
        file=sys.stderr,
    )
    return 1


def _as_shown(plan: Any) -> Any:
    """A copy of *plan* for the lines that show it: its project's name and the credential names it
    expects as the terminal is given them (:func:`terminal_text`)."""
    shown = copy.copy(plan)
    shown.project_name = terminal_text(str(plan.project_name))
    shown.secrets_expected = [terminal_text(str(name)) for name in plan.secrets_expected]
    return shown


def _import(args: argparse.Namespace) -> int:
    from personalclaw.config.loader import config_dir
    from personalclaw.durability.home_paths import LinkInTheWay
    from personalclaw.tasks.hierarchy import HierarchyStore
    from personalclaw.workflows.project_export import import_summary

    archive = Path(args.archive).expanduser()
    if not archive.is_file():
        print(f"❌ No such archive: {archive}", file=sys.stderr)
        return 1

    try:
        # Asked before the store is read: reading its projects makes the ones it starts with.
        pa.projects_folder(config_dir())
    except LinkInTheWay as link:
        return _refused_by_a_link(link)
    store = HierarchyStore()
    existing = [p.name for p in store.list_projects()]
    try:
        plan, extracted = pa.read_archive_plan(
            archive, existing_names=existing, passphrase=args.passphrase or ""
        )
    except pa.ArchiveRefused as exc:
        print(f"❌ Refused ({exc.reason}): {terminal_text(str(exc.detail))}", file=sys.stderr)
        return 1
    except pa.EncryptionUnavailable as exc:
        print(f"❌ {terminal_text(str(exc))}", file=sys.stderr)
        return 1

    # What the lines below show is the archive's own words: its project's name and the credential
    # names it expects, as the terminal is given them. The import takes the plan as it was read.
    read, plan = plan, _as_shown(plan)

    def show(stream: Any, **counted: Any) -> None:
        """The plan's summary and the rows it refused, on *stream*."""
        print(import_summary(plan, **counted), file=stream)
        if plan.refused:
            print(f"   ✖ {len(plan.refused)} refused:", file=stream)
            for issue in plan.refused[:10]:
                print(
                    f"     · {terminal_text(str(issue.path))}: {terminal_text(str(issue.message))}",
                    file=stream,
                )
            if len(plan.refused) > 10:
                print(f"     · … and {len(plan.refused) - 10} more", file=stream)

    if args.dry_run:
        show(sys.stdout, preview=True)
        print("   (dry run — nothing was written)")
        return 0
    # A real import prints no plan before it writes: a refusal leaves stdout empty, and what it
    # did is said once it has done it.
    if not plan.ok:
        show(sys.stderr, preview=True)
        print("❌ Nothing importable in this archive.", file=sys.stderr)
        return 1

    try:
        created, written, left = pa.import_project(read, extracted, store=store, home=config_dir())
    except LinkInTheWay as link:
        return _refused_by_a_link(link)
    show(sys.stdout, written=len(written), left=left)
    print(
        f"✅ Imported as {terminal_text(str(created.name))} ({created.id}) — {len(written)} "
        "entities written"
    )
    if left:
        print(f"   ⚠️  Left unchanged: {terminal_text(str('; '.join(left)))}", file=sys.stderr)
    if plan.secrets_expected:
        print(
            f"   🔑 Re-enter {len(plan.secrets_expected)} credential(s): "
            f"{', '.join(plan.secrets_expected)}"
        )
    # What it left unchanged was not imported, so the exit says so, as `restore`'s does.
    return 1 if left else 0


def project_main(args: argparse.Namespace) -> int:
    """``personalclaw project export|import``; the parser refuses a bare ``project``."""
    if getattr(args, "project_command", "") == "export":
        return _export(args)
    return _import(args)
